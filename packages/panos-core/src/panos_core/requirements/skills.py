"""Project-specific Claude skills from approved facts only (BR-013, FR-030, US-033, M6-09).

Three skills per project, written to ``<project>/.claude/skills/<name>/SKILL.md``:

* ``<slug>-architecture`` — approved ADRs (incl. the approved stack) and approved NFRs;
* ``<slug>-standards`` — conventions and commands of the approved stack, approved rules;
* ``<slug>-domain`` — approved business/functional requirements and data entities.

Only APPROVED, non-stale items are used. Each skill's ``facts_hash`` covers exactly the
facts it was built from; when it changes the skill is regenerated with ``version + 1``,
otherwise the file is left alone.
"""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from panos_core.domain.requirements import Requirement, RequirementStatus, RequirementType
from panos_core.requirements.packs import addons, packs, render_text


@dataclass(frozen=True)
class SkillDraft:
    name: str
    description: str
    body: str
    source_keys: list[str]
    facts_hash: str


def slug(project_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", project_name.lower()).strip("-")[:40] or "project"


def approved_facts(items: list[Requirement]) -> list[Requirement]:
    return sorted(
        (i for i in items if i.status == RequirementStatus.APPROVED and not i.stale),
        key=lambda i: i.key,
    )


def _hash(facts: list[Requirement], extra: Any = None) -> str:
    payload = [
        {"key": f.key, "v": f.version, "title": f.title, "body": f.body, "attributes": f.attributes}
        for f in facts
    ]
    raw = json.dumps({"facts": payload, "extra": extra}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _bullets(items: list[Requirement], fmt: Callable[[Requirement], str]) -> str:
    return "\n".join(fmt(i) for i in items) or "- _None approved yet._"


def stack_of(facts: list[Requirement], project_name: str = "project") -> dict[str, Any] | None:
    """The approved stack decision (an ADR carrying a ``pack`` attribute)."""
    for item in facts:
        if item.type == RequirementType.ADR and item.attributes.get("pack") in packs():
            pack = packs()[item.attributes["pack"]]
            chosen = [a for a in item.attributes.get("addons", []) if a in addons()]
            return {
                "pack": pack.id,
                "name": pack.name,
                "addons": chosen,
                "conventions": [
                    render_text(c, name=project_name)
                    for c in [
                        *pack.conventions,
                        *(c for a in chosen for c in addons()[a].conventions),
                    ]
                ],
                "commands": pack.commands,
                "frameworks": list(pack.frameworks),
                "key": item.key,
                "version": item.version,
            }
    return None


def build_skills(project_name: str, items: list[Requirement]) -> list[SkillDraft]:
    facts = approved_facts(items)
    name = slug(project_name)
    stack = stack_of(facts, project_name)
    adrs = [f for f in facts if f.type == RequirementType.ADR]
    nfrs = [f for f in facts if f.type == RequirementType.NFR]
    rules = [f for f in facts if f.type == RequirementType.RULE]
    domain = [f for f in facts if f.type in (RequirementType.BR, RequirementType.FR)]
    data = [f for f in facts if f.type == RequirementType.DATA]

    def footer(src: list[Requirement]) -> str:
        return (
            "\n\n---\nBuilt by Panos from approved items: "
            + (", ".join(f"{s.key}@v{s.version}" for s in src) or "none")
            + "."
        )

    drafts = []
    arch_body = (
        f"# {project_name} architecture\n\nFollow these approved decisions when designing or "
        "changing code. Do not introduce technologies that contradict them; propose an ADR "
        "change instead.\n\n## Decisions\n"
        + _bullets(adrs, lambda a: f"- **{a.key}** {a.title}" + (f": {a.body}" if a.body else ""))
        + "\n\n## Quality requirements\n"
        + _bullets(
            nfrs, lambda n: f"- **{n.key}** ({n.attributes.get('category', 'NFR')}) {n.title}"
        )
    )
    drafts.append(
        SkillDraft(
            name=f"{name}-architecture",
            description=f"Approved architecture and quality requirements of {project_name}. Use "
            "when designing, reviewing or changing its structure.",
            body=arch_body + footer(adrs + nfrs),
            source_keys=[f.key for f in adrs + nfrs],
            facts_hash=_hash(adrs + nfrs),
        )
    )

    std_lines = [f"# {project_name} coding standards\n"]
    if stack:
        std_lines.append(
            f"Stack: **{stack['name']}**"
            + (f" with {', '.join(stack['addons'])}" if stack["addons"] else "")
            + ".\n\n## Conventions"
        )
        std_lines += [f"- {c}" for c in stack["conventions"]]
        std_lines.append("\n## Commands (run before reporting work as done)")
        std_lines += [
            f"- {kind}: " + ", ".join(f"`{' '.join(c)}`" for c in cmds)
            for kind, cmds in stack["commands"].items()
        ]
    else:
        std_lines.append("_No approved stack yet._")
    std_lines.append(
        "\n## Business rules\n"
        + _bullets(rules, lambda r: f"- **{r.key}** {r.title}" + (f": {r.body}" if r.body else ""))
    )
    std_src = ([f for f in adrs if stack and f.key == stack["key"]]) + rules
    drafts.append(
        SkillDraft(
            name=f"{name}-standards",
            description=f"Coding conventions, commands and business rules for {project_name}. Use "
            "when writing or reviewing code in this project.",
            body="\n".join(std_lines) + footer(std_src),
            source_keys=[f.key for f in std_src],
            facts_hash=_hash(std_src, stack),
        )
    )

    dom_body = (
        f"# {project_name} domain\n\nApproved scope. Treat anything not listed here as out of "
        "scope until it is approved.\n\n## Requirements\n"
        + _bullets(domain, lambda d: f"- **{d.key}** {d.title}")
        + "\n\n## Data entities\n"
        + _bullets(
            data,
            lambda d: (
                f"- **{d.title}**: "
                + ", ".join(
                    str(f.get("name"))
                    for f in d.attributes.get("fields", [])
                    if isinstance(f, dict)
                )
            ),
        )
    )
    drafts.append(
        SkillDraft(
            name=f"{name}-domain",
            description=f"Approved business scope, functional requirements and data model of "
            f"{project_name}. Use when implementing or discussing features.",
            body=dom_body + footer(domain + data),
            source_keys=[f.key for f in domain + data],
            facts_hash=_hash(domain + data),
        )
    )
    return drafts


def render_skill_file(draft: SkillDraft, version: int) -> str:
    return (
        f"---\nname: {draft.name}\ndescription: {draft.description}\n"
        f"metadata:\n  version: {version}\n  facts_hash: {draft.facts_hash}\n"
        f"  generated_by: panos\n---\n\n{draft.body}\n"
    )
