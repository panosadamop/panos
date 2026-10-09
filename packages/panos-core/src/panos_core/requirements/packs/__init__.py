"""Stack knowledge packs and offline scaffolding (BR-008, US-021, FR-007 steps 5-7).

Packs are declarative data (``stacks.yaml``): detection keywords, conventions, discovered
commands and Jinja2 file templates. Scaffolding renders a pack (plus add-ons) into a
project directory without network access and never overwrites existing files.
"""

import re
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, StrictUndefined

_ENV = Environment(undefined=StrictUndefined, keep_trailing_newline=True, autoescape=False)  # noqa: S701 - source files, not HTML


@dataclass(frozen=True)
class Addon:
    id: str
    name: str
    keywords: tuple[str, ...]
    conventions: tuple[str, ...]
    files: dict[str, str]


@dataclass(frozen=True)
class StackPack:
    id: str
    name: str
    family: str
    keywords: tuple[str, ...]
    languages: tuple[str, ...]
    frameworks: tuple[str, ...]
    default_addons: tuple[str, ...]
    preferred_ide: str
    commands: dict[str, list[list[str]]]
    conventions: tuple[str, ...]
    files: dict[str, str] = field(default_factory=dict)


@cache
def _data() -> dict[str, Any]:
    text = resources.files(__package__).joinpath("stacks.yaml").read_text(encoding="utf-8")
    data: dict[str, Any] = yaml.safe_load(text)
    return data


@cache
def packs() -> dict[str, StackPack]:
    return {
        p["id"]: StackPack(
            id=p["id"],
            name=p["name"],
            family=p["family"],
            keywords=tuple(p["keywords"]),
            languages=tuple(p["languages"]),
            frameworks=tuple(p["frameworks"]),
            default_addons=tuple(p.get("default_addons", [])),
            preferred_ide=p.get("preferred_ide", ""),
            commands=p.get("commands", {}),
            conventions=tuple(p.get("conventions", [])),
            files=dict(p.get("files", {})),
        )
        for p in _data()["packs"]
    }


@cache
def addons() -> dict[str, Addon]:
    return {
        a["id"]: Addon(
            id=a["id"],
            name=a["name"],
            keywords=tuple(a["keywords"]),
            conventions=tuple(a.get("conventions", [])),
            files=dict(a.get("files", {})),
        )
        for a in _data()["addons"]
    }


def _mentions(text: str, keyword: str) -> bool:
    return re.search(rf"(?<![\w.-]){re.escape(keyword)}(?![\w-])", text, re.I) is not None


def detect_stack(text: str) -> tuple[str | None, list[str]]:
    """The pack and add-ons a brief explicitly asks for (US-021). The most specific pack
    wins (Vaadin beats Spring Boot; NestJS beats React when both are named)."""
    hits = [p for p in packs().values() if any(_mentions(text, k) for k in p.keywords)]
    priority = [
        "java-vaadin",
        "node-nestjs",
        "php-laravel",
        "python-django",
        "python-fastapi",
        "java-spring-boot",
        "react-typescript",
    ]
    hits.sort(key=lambda p: priority.index(p.id) if p.id in priority else 99)
    found_addons = [a.id for a in addons().values() if any(_mentions(text, k) for k in a.keywords)]
    return (hits[0].id if hits else None), found_addons


def package_name(project_name: str) -> str:
    """A valid identifier for Python packages / Java packages from a project name."""
    cleaned = re.sub(r"[^0-9a-zA-Z]+", "_", project_name).strip("_").lower() or "app"
    return f"app_{cleaned}" if cleaned[0].isdigit() else cleaned


def render_text(text: str, *, name: str) -> str:
    """Render a pack string (e.g. a convention mentioning ``{{ package }}``) for a project."""
    return _ENV.from_string(text).render(name=name, package=package_name(name), description=name)


@dataclass
class ScaffoldResult:
    pack: str
    addons: list[str]
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # existed already: never overwritten


def render_files(
    pack_id: str, addon_ids: list[str], *, name: str, description: str
) -> dict[str, str]:
    """Relative path → content for a pack plus add-ons (later entries never override)."""
    if pack_id not in packs():
        raise KeyError(f"unknown stack pack {pack_id!r}")
    ctx = {
        "name": re.sub(r"[^0-9a-zA-Z._-]+", "-", name).strip("-").lower() or "app",
        "package": package_name(name),
        "description": description or name,
    }
    out: dict[str, str] = {}
    templates = list(packs()[pack_id].files.items())
    for addon_id in addon_ids:
        templates += list(addons()[addon_id].files.items())
    for raw_path, template in templates:
        path = _ENV.from_string(raw_path).render(**ctx)
        if path not in out:
            out[path] = _ENV.from_string(template).render(**ctx)
    return out


def scaffold(
    root: Path, pack_id: str, addon_ids: list[str], *, name: str, description: str
) -> ScaffoldResult:
    result = ScaffoldResult(pack=pack_id, addons=list(addon_ids))
    root = root.resolve()
    for rel, content in render_files(
        pack_id, addon_ids, name=name, description=description
    ).items():
        target = (root / rel).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"template path escapes the project: {rel}")
        if target.exists():
            result.skipped.append(rel)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8"))
        result.created.append(rel)
    return result


__all__ = [
    "Addon",
    "ScaffoldResult",
    "StackPack",
    "addons",
    "detect_stack",
    "package_name",
    "packs",
    "render_files",
    "render_text",
    "scaffold",
]
