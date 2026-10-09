"""Docker inspection (FR-014, BR-018, US-041).

``docker.inspect`` (P0) parses Dockerfiles and Compose files statically and can optionally
ask the Docker CLI read-only questions (``docker compose config``, ``docker ps``).
Anything that *executes* containers goes through ``shell.run``, where the classifier maps
it to ``docker.exec`` (P3): it always waits for approval and runs nothing if rejected.
"""

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, ClassVar, Final

import yaml
from pydantic import BaseModel, ConfigDict, Field

from panos_core.repository.walk import iter_project_files
from panos_core.shell.environment import build_child_env
from panos_core.shell.process import run_process
from panos_core.tools.contract import (
    ActionPlan,
    PlanRejectedError,
    SideEffect,
    Tool,
    ToolContext,
    ToolSpec,
)

_COMPOSE_NAMES: Final = frozenset(
    {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}
)
_DOCKERFILE_RE: Final = re.compile(r"^(?:Dockerfile(?:\..+)?|.+\.Dockerfile)$", re.I)
_MAX_FILE: Final = 512 * 1024


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DockerInspectInput(_Model):
    live: bool = False  # also query the Docker CLI (read-only commands only)


class DockerfileInfo(_Model):
    path: str
    stages: list[str] = Field(default_factory=list)
    base_images: list[str] = Field(default_factory=list)
    exposed_ports: list[str] = Field(default_factory=list)
    user: str | None = None
    healthcheck: bool = False


class ComposeService(_Model):
    name: str
    image: str | None = None
    build: str | None = None
    ports: list[str] = Field(default_factory=list)
    volumes: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    privileged: bool = False


class ComposeInfo(_Model):
    path: str
    services: list[ComposeService] = Field(default_factory=list)
    error: str | None = None


class DockerInspectOutput(_Model):
    dockerfiles: list[DockerfileInfo] = Field(default_factory=list)
    compose: list[ComposeInfo] = Field(default_factory=list)
    docker_cli: bool = False
    live: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


def parse_dockerfile(rel: str, text: str) -> DockerfileInfo:
    info = DockerfileInfo(path=rel)
    # Join line continuations, drop comments.
    logical = re.sub(r"\\\r?\n", " ", text)
    for line in logical.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        instr, args = parts[0].upper(), parts[1:]
        if instr == "FROM" and args:
            image = next((a for a in args if not a.startswith("--")), "")
            info.base_images.append(image)
            if len(args) >= 3 and args[-2].upper() == "AS":
                info.stages.append(args[-1])
        elif instr == "EXPOSE":
            info.exposed_ports += args
        elif instr == "USER" and args:
            info.user = args[0]
        elif instr == "HEALTHCHECK":
            info.healthcheck = not (args and args[0].upper() == "NONE")
    return info


def _strings(value: Any) -> list[str]:
    if isinstance(value, list):
        return [json.dumps(v) if isinstance(v, dict) else str(v) for v in value]
    if isinstance(value, dict):
        return [str(k) for k in value]
    return []


def parse_compose(rel: str, text: str) -> ComposeInfo:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        return ComposeInfo(path=rel, error=f"invalid YAML: {str(exc)[:200]}")
    services = data.get("services") if isinstance(data, dict) else None
    if not isinstance(services, dict):
        return ComposeInfo(path=rel, error="no services section")
    out = []
    for name, svc in services.items():
        svc = svc if isinstance(svc, dict) else {}
        build = svc.get("build")
        out.append(
            ComposeService(
                name=str(name),
                image=str(svc["image"]) if svc.get("image") else None,
                build=(build.get("context", ".") if isinstance(build, dict) else str(build))
                if build
                else None,
                ports=_strings(svc.get("ports")),
                volumes=_strings(svc.get("volumes")),
                depends_on=_strings(svc.get("depends_on")),
                privileged=bool(svc.get("privileged", False)),
            )
        )
    return ComposeInfo(path=rel, services=out)


class DockerInspectTool(Tool[DockerInspectInput, DockerInspectOutput]):
    spec: ClassVar[ToolSpec] = ToolSpec(
        name="docker.inspect",
        version="1",
        description="Inspect Dockerfiles, Compose files and (optionally) Docker state.",
        input_model=DockerInspectInput,
        output_model=DockerInspectOutput,
        capabilities=frozenset({"docker.inspect"}),
        side_effect=SideEffect.NONE,
        timeout_s=60,
        cancellable=True,
        idempotent=True,
        privacy="Reads container configuration inside the project; nothing leaves the machine.",
    )

    async def plan(self, inp: DockerInspectInput, ctx: ToolContext) -> ActionPlan:
        if ctx.project is None:
            raise PlanRejectedError("no active project")
        return ActionPlan(
            action="docker.inspect",
            target=ctx.project.path,
            summary="Inspect Docker configuration" + (" and state" if inp.live else ""),
            payload={"live": inp.live},
        )

    async def _execute(
        self, inp: DockerInspectInput, ctx: ToolContext, plan: ActionPlan
    ) -> DockerInspectOutput:
        root = Path(plan.target)
        out = DockerInspectOutput(docker_cli=shutil.which("docker") is not None)
        for f in iter_project_files(root):
            name = Path(f.rel).name
            if f.size > _MAX_FILE:
                continue
            if _DOCKERFILE_RE.match(name):
                out.dockerfiles.append(
                    parse_dockerfile(f.rel, f.path.read_text("utf-8", "replace"))
                )
            elif name.lower() in _COMPOSE_NAMES:
                out.compose.append(parse_compose(f.rel, f.path.read_text("utf-8", "replace")))
        for d in out.dockerfiles:
            if d.user in (None, "root", "0"):
                out.warnings.append(f"{d.path}: container runs as root")
            out.warnings += [
                f"{d.path}: base image {img!r} is not pinned"
                for img in d.base_images
                if ":" not in img and "@" not in img and img.lower() != "scratch"
            ]
        for c in out.compose:
            out.warnings += [
                f"{c.path}: service {s.name} is privileged" for s in c.services if s.privileged
            ]
        if inp.live and out.docker_cli:
            out.live = await self._live(root, ctx, bool(out.compose))
        return out

    @staticmethod
    async def _live(root: Path, ctx: ToolContext, compose: bool) -> dict[str, Any]:
        docker = shutil.which("docker") or "docker"
        env, _ = build_child_env(os.environ, {})
        queries = {"ps": [docker, "ps", "--format", "{{json .}}"]}
        if compose:
            queries["compose_config"] = [docker, "compose", "config", "--services"]
        results: dict[str, Any] = {}
        for key, argv in queries.items():
            r = await run_process(
                argv, cwd=root, env=env, timeout_s=20, cancel=ctx.cancel, max_output_bytes=200_000
            )
            results[key] = (
                r.stdout.strip().splitlines()
                if r.exit_code == 0
                else {"error": (r.stderr or "failed").strip()[:300]}
            )
        return results
