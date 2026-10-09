"""Markdown artifact generators (FR-008, FR-029/BR-028 Markdown part, M6-05).

Documents are rendered with Jinja2 from the requirement store, so they always show keys,
versions, lifecycle state and staleness. They are derived output: regenerate rather than
edit. OpenAPI is emitted as a YAML skeleton from API items.
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Final

import yaml
from jinja2 import Environment, PackageLoader, StrictUndefined

from panos_core.domain.requirements import Requirement, RequirementType, TraceLink
from panos_core.requirements.estimation import Estimate

DOCUMENTS: Final = {
    "BRD.md": "brd.md.j2",
    "FRD.md": "frd.md.j2",
    "NFR.md": "nfr.md.j2",
    "ARCHITECTURE.md": "architecture.md.j2",
    "ERD.md": "erd.md.j2",
    "BACKLOG.md": "backlog.md.j2",
    "ACCEPTANCE_CRITERIA.md": "acceptance.md.j2",
    "TEST_CASES.md": "tests.md.j2",
    "ESTIMATES.md": "estimates.md.j2",
    "SPRINT_PLAN.md": "sprints.md.j2",
    "RISKS.md": "risks.md.j2",
}

_ENV = Environment(
    loader=PackageLoader("panos_core.requirements", "templates"),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
    autoescape=False,  # noqa: S701 - Markdown, not HTML
)


@dataclass(frozen=True)
class ArtifactContext:
    project: str
    items: list[Requirement]
    links: list[TraceLink]
    estimate: Estimate | None = None
    stack: dict[str, Any] | None = None  # {pack, name, addons, conventions, commands}


def _label(item: Requirement) -> str:
    flags = [f"v{item.version}", item.status.value] + (["STALE"] if item.stale else [])
    return f"{item.key} — {item.title} ({', '.join(flags)})"


_ENV.filters["label"] = _label


def _ctx(c: ArtifactContext) -> dict[str, Any]:
    by_type: dict[str, list[Requirement]] = defaultdict(list)
    children: dict[str, list[Requirement]] = defaultdict(list)
    for item in sorted(c.items, key=lambda i: i.key):
        by_type[item.type.value].append(item)
        if item.parent_key:
            children[item.parent_key].append(item)
    links_to: dict[str, list[TraceLink]] = defaultdict(list)
    for link in c.links:
        links_to[link.target].append(link)
    return {
        "project": c.project,
        "t": by_type,
        "children": children,
        "links_to": links_to,
        "estimate": c.estimate,
        "stack": c.stack,
        "approved": sum(1 for i in c.items if i.status.value == "APPROVED"),
        "total": len(c.items),
    }


def openapi(c: ArtifactContext) -> str:
    paths: dict[str, dict[str, Any]] = defaultdict(dict)
    for api in sorted((i for i in c.items if i.type == RequirementType.API), key=lambda i: i.key):
        method = str(api.attributes.get("method", "get")).lower()
        path = str(api.attributes.get("path", f"/{api.key.lower()}"))
        op: dict[str, Any] = {
            "operationId": api.key,
            "summary": api.title,
            "description": api.body or api.title,
            "x-requirement": f"{api.key}@v{api.version}",
            "responses": {"200": {"description": "OK"}},
        }
        if api.parent_key:
            op["x-traces"] = [api.parent_key]
        paths[path][method] = op
    schemas = {
        d.title.replace(" ", ""): {
            "type": "object",
            "properties": {
                str(f.get("name")): {"type": _json_type(str(f.get("type", "string")))}
                for f in d.attributes.get("fields", [])
                if isinstance(f, dict)
            },
        }
        for d in c.items
        if d.type == RequirementType.DATA
    }
    doc = {
        "openapi": "3.1.0",
        "info": {"title": c.project, "version": "0.1.0"},
        "paths": dict(paths),
        "components": {"schemas": schemas},
    }
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def _json_type(t: str) -> str:
    t = t.lower()
    return {
        "int": "integer",
        "integer": "integer",
        "float": "number",
        "decimal": "number",
        "number": "number",
        "bool": "boolean",
        "boolean": "boolean",
    }.get(t, "string")


def render_all(c: ArtifactContext) -> dict[str, str]:
    """File name → content for every artifact (``openapi.yaml`` included)."""
    ctx = _ctx(c)
    out = {name: _ENV.get_template(template).render(**ctx) for name, template in DOCUMENTS.items()}
    out["openapi.yaml"] = openapi(c)
    return out
