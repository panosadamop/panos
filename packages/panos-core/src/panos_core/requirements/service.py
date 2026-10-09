"""Requirements service: versions, hierarchical approval, change control, traceability.

ADR-014 semantics:

* **Keys** are allocated per project and type (``FR-007``); acceptance criteria are numbered
  under their story (``AC-US003-02``). Keys never change; content lives in versions.
* **Editing** creates a new DRAFT version. If an approved version existed, every downstream
  item (descendants by parent, and items that trace to it) is marked ``stale`` and an
  impact-analysis task is created (``on_change`` hook).
* **Approving** a key approves its latest version and supersedes the previously approved
  one. With ``cascade`` (default) every descendant is approved at its current version,
  except excluded keys and their subtrees; each gets its own record with
  ``cascaded_from_id`` (AC-US031-01..03).
* Approving clears ``stale``; so does an explicit ``clear_stale`` after impact review.
"""

from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from panos_core.audit import AuditService
from panos_core.domain.errors import DomainError
from panos_core.domain.ids import new_id
from panos_core.domain.requirements import (
    EVIDENCE_RE,
    KEY_RE,
    Requirement,
    RequirementApproval,
    RequirementStatus,
    RequirementType,
    TraceLink,
    TraceRelation,
    key_number,
)
from panos_core.persistence.unit_of_work import UnitOfWorkFactory

S = RequirementStatus
ChangeHook = Callable[[str, str, list[str]], Awaitable[None]]  # project, changed key, stale

# Expected downstream coverage per type, used to report traceability gaps (AC-US032-02).
EXPECTED_CHILDREN: Final[dict[RequirementType, tuple[RequirementType, ...]]] = {
    RequirementType.BR: (RequirementType.FR,),
    RequirementType.FR: (RequirementType.EPIC, RequirementType.FEAT, RequirementType.US),
    RequirementType.EPIC: (RequirementType.FEAT, RequirementType.US),
    RequirementType.FEAT: (RequirementType.US,),
    RequirementType.US: (RequirementType.AC,),
    RequirementType.AC: (RequirementType.TC,),
}


class RequirementsError(DomainError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass
class NewItem:
    """Input for bulk creation; ``ref``/``parent_ref`` let a batch refer to its own items."""

    type: RequirementType
    title: str
    body: str = ""
    parent_key: str | None = None
    parent_ref: str | None = None
    ref: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    source: list[str] = field(default_factory=list)


@dataclass
class TraceNode:
    key: str
    type: str
    title: str
    version: int
    status: str
    stale: bool
    children: list["TraceNode"] = field(default_factory=list)
    links: list[dict[str, str]] = field(default_factory=list)  # {relation, key|evidence}
    evidence: list[str] = field(default_factory=list)


def _prefix(type_: RequirementType, parent_key: str | None) -> str:
    if type_ == RequirementType.AC:
        if not parent_key or not parent_key.startswith("US-"):
            raise RequirementsError("invalid_parent", "acceptance criteria belong to a story")
        return f"AC-US{parent_key.split('-')[1]}-"
    return f"{type_.value}-"


class RequirementsService:
    def __init__(self, uow_factory: UnitOfWorkFactory, audit: AuditService) -> None:
        self._uow_factory = uow_factory
        self._audit = audit
        self._on_change: ChangeHook | None = None

    def set_change_hook(self, hook: ChangeHook) -> None:
        self._on_change = hook

    # ------------------------------------------------------------------ queries
    async def get(self, project_id: str, key: str) -> Requirement:
        async with self._uow_factory() as uow:
            item = await uow.requirements.latest(project_id, key)
        if item is None:
            raise RequirementsError("not_found", f"requirement {key} not found")
        return item

    async def history(self, project_id: str, key: str) -> Sequence[Requirement]:
        async with self._uow_factory() as uow:
            return await uow.requirements.history(project_id, key)

    async def approvals(self, project_id: str, key: str) -> Sequence[RequirementApproval]:
        async with self._uow_factory() as uow:
            return await uow.requirements.approvals(project_id, key)

    async def list_latest(
        self,
        project_id: str,
        *,
        type: RequirementType | None = None,
        status: RequirementStatus | None = None,
    ) -> list[Requirement]:
        async with self._uow_factory() as uow:
            items = await uow.requirements.list_latest(project_id, type=type)
        return [i for i in items if status is None or i.status == status]

    async def links(self, project_id: str) -> Sequence[TraceLink]:
        async with self._uow_factory() as uow:
            return await uow.requirements.links(project_id)

    # ------------------------------------------------------------------ creation
    async def create(
        self,
        project_id: str,
        type: RequirementType,
        title: str,
        *,
        body: str = "",
        parent_key: str | None = None,
        attributes: dict[str, Any] | None = None,
        source: list[str] | None = None,
        actor: str = "user",
    ) -> Requirement:
        [item] = await self.create_many(
            project_id,
            [
                NewItem(
                    type=type,
                    title=title,
                    body=body,
                    parent_key=parent_key,
                    attributes=attributes or {},
                    source=source or [],
                )
            ],
            actor=actor,
        )
        return item

    async def create_many(
        self, project_id: str, items: Sequence[NewItem], *, actor: str = "user"
    ) -> list[Requirement]:
        """Create items in one transaction (parents first; ``parent_ref`` resolved)."""
        created: list[Requirement] = []
        refs: dict[str, str] = {}
        async with self._uow_factory() as uow:
            counters: dict[str, int] = {}
            for spec in items:
                parent = refs.get(spec.parent_ref or "") if spec.parent_ref else spec.parent_key
                if spec.parent_ref and parent is None:
                    raise RequirementsError("invalid_parent", f"unknown ref {spec.parent_ref}")
                if (
                    parent is not None
                    and parent not in refs.values()
                    and await uow.requirements.latest(project_id, parent) is None
                ):
                    raise RequirementsError("invalid_parent", f"parent {parent} not found")
                prefix = _prefix(spec.type, parent)
                if prefix not in counters:
                    existing = await uow.requirements.keys_with_prefix(project_id, prefix)
                    counters[prefix] = max(
                        (
                            key_number(k)
                            for k in existing
                            if KEY_RE.match(k) and k.startswith(prefix)
                        ),
                        default=0,
                    )
                counters[prefix] += 1
                width = 2 if spec.type == RequirementType.AC else 3
                key = f"{prefix}{counters[prefix]:0{width}d}"
                item = Requirement(
                    project_id=project_id,
                    key=key,
                    type=spec.type,
                    parent_key=parent,
                    title=spec.title,
                    body=spec.body,
                    attributes=spec.attributes,
                    source=spec.source,
                    created_by=actor,
                )
                await uow.requirements.add(item)
                created.append(item)
                if spec.ref:
                    refs[spec.ref] = key
            await uow.commit()
        await self._audit.record(
            actor=actor,
            event_type="requirements.created",
            action="requirements.create",
            project_id=project_id,
            metadata={"keys": [c.key for c in created][:200], "count": len(created)},
        )
        return created

    # ------------------------------------------------------------------ change control
    async def edit(
        self,
        project_id: str,
        key: str,
        *,
        actor: str,
        title: str | None = None,
        body: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> tuple[Requirement, list[str]]:
        """New DRAFT version. Returns it and the keys marked stale (if any)."""
        async with self._uow_factory() as uow:
            history = await uow.requirements.history(project_id, key)
            if not history:
                raise RequirementsError("not_found", f"requirement {key} not found")
            latest = history[-1]
            was_approved = any(v.status in (S.APPROVED, S.SUPERSEDED) for v in history)
            new = latest.model_copy(
                update={
                    "id": new_id("requirement"),
                    "version": latest.version + 1,
                    "status": S.DRAFT,
                    "stale": False,
                    "title": title if title is not None else latest.title,
                    "body": body if body is not None else latest.body,
                    "attributes": attributes if attributes is not None else latest.attributes,
                    "created_by": actor,
                }
            )
            await uow.requirements.add(new)
            if latest.status in (S.DRAFT, S.PENDING_APPROVAL):
                # Never approved: the old draft is simply replaced.
                await uow.requirements.update(latest.model_copy(update={"status": S.SUPERSEDED}))
            stale: list[str] = []
            if was_approved:
                stale = await self._mark_downstream_stale(uow, project_id, key)
            await uow.commit()
        await self._audit.record(
            actor=actor,
            event_type="requirement.edited",
            action="requirement.edit",
            project_id=project_id,
            metadata={"key": key, "version": new.version, "stale": stale[:200]},
        )
        if stale and self._on_change is not None:
            await self._on_change(project_id, key, stale)
        return new, stale

    async def _mark_downstream_stale(self, uow: Any, project_id: str, key: str) -> list[str]:
        items = {i.key: i for i in await uow.requirements.list_latest(project_id)}
        links = await uow.requirements.links(project_id)
        stale = [k for k in _downstream(key, items.values(), links) if k in items]
        for k in stale:
            item = items[k]
            if not item.stale and item.status != S.SUPERSEDED:
                await uow.requirements.update(item.model_copy(update={"stale": True}))
        return stale

    async def clear_stale(
        self, project_id: str, keys: Iterable[str], *, actor: str, reason: str
    ) -> list[str]:
        """Impact reviewed: the listed items stay valid as they are."""
        cleared = []
        async with self._uow_factory() as uow:
            for key in keys:
                item = await uow.requirements.latest(project_id, key)
                if item is not None and item.stale:
                    await uow.requirements.update(item.model_copy(update={"stale": False}))
                    cleared.append(key)
            await uow.commit()
        await self._audit.record(
            actor=actor,
            event_type="requirement.stale_cleared",
            action="requirement.clear_stale",
            project_id=project_id,
            metadata={"keys": cleared, "reason": reason},
        )
        return cleared

    # ------------------------------------------------------------------ approval
    async def submit(self, project_id: str, key: str, *, actor: str) -> Requirement:
        async with self._uow_factory() as uow:
            item = await uow.requirements.latest(project_id, key)
            if item is None:
                raise RequirementsError("not_found", f"requirement {key} not found")
            if item.status != S.DRAFT:
                raise RequirementsError("invalid_state", f"{key} is {item.status}")
            item = item.model_copy(update={"status": S.PENDING_APPROVAL})
            await uow.requirements.update(item)
            await uow.commit()
        return item

    async def approve(
        self,
        project_id: str,
        key: str,
        *,
        actor: str,
        comments: str = "",
        cascade: bool = True,
        exclude: Iterable[str] = (),
    ) -> list[RequirementApproval]:
        excluded = set(exclude)
        records: list[RequirementApproval] = []
        async with self._uow_factory() as uow:
            items = {i.key: i for i in await uow.requirements.list_latest(project_id)}
            root = items.get(key)
            if root is None:
                raise RequirementsError("not_found", f"requirement {key} not found")
            if key in excluded:
                raise RequirementsError("invalid", "cannot exclude the item being approved")
            unknown = excluded - set(items)
            if unknown:
                raise RequirementsError("not_found", f"unknown excluded keys: {sorted(unknown)}")
            parent_record = await self._decide(
                uow, root, S.APPROVED, actor, comments, None, sorted(excluded)
            )
            records.append(parent_record)
            if cascade:
                for child in _descendants(key, items.values(), skip=excluded):
                    records.append(
                        await self._decide(
                            uow, items[child], S.APPROVED, actor, comments, parent_record.id, []
                        )
                    )
            await uow.commit()
        await self._audit.record(
            actor=actor,
            event_type="requirement.approved",
            action="requirement.approve",
            project_id=project_id,
            metadata={
                "key": key,
                "version": root.version,
                "cascaded": len(records) - 1,
                "excluded": sorted(excluded),
                "comments": comments,
            },
        )
        return records

    async def reject(
        self, project_id: str, key: str, *, actor: str, comments: str = ""
    ) -> RequirementApproval:
        async with self._uow_factory() as uow:
            item = await uow.requirements.latest(project_id, key)
            if item is None:
                raise RequirementsError("not_found", f"requirement {key} not found")
            record = await self._decide(uow, item, S.REJECTED, actor, comments, None, [])
            await uow.commit()
        await self._audit.record(
            actor=actor,
            event_type="requirement.rejected",
            action="requirement.reject",
            project_id=project_id,
            metadata={"key": key, "version": item.version, "comments": comments},
        )
        return record

    @staticmethod
    async def _decide(
        uow: Any,
        item: Requirement,
        decision: RequirementStatus,
        actor: str,
        comments: str,
        cascaded_from: str | None,
        excluded: list[str],
    ) -> RequirementApproval:
        if decision == S.APPROVED:
            for old in await uow.requirements.history(item.project_id, item.key):
                if old.status == S.APPROVED and old.id != item.id:
                    await uow.requirements.update(old.model_copy(update={"status": S.SUPERSEDED}))
        await uow.requirements.update(
            item.model_copy(
                update={
                    "status": decision,
                    "stale": False if decision == S.APPROVED else item.stale,
                }
            )
        )
        record = RequirementApproval(
            project_id=item.project_id,
            requirement_id=item.id,
            key=item.key,
            version=item.version,
            decision=decision,
            decided_by=actor,
            comments=comments,
            cascaded_from_id=cascaded_from,
            excluded_keys=excluded,
        )
        await uow.requirements.add_approval(record)
        return record

    # ------------------------------------------------------------------ trace links
    async def link(
        self, project_id: str, source_key: str, target: str, relation: TraceRelation, *, actor: str
    ) -> TraceLink:
        async with self._uow_factory() as uow:
            if await uow.requirements.latest(project_id, source_key) is None:
                raise RequirementsError("not_found", f"requirement {source_key} not found")
            if KEY_RE.match(target) and await uow.requirements.latest(project_id, target) is None:
                raise RequirementsError("not_found", f"requirement {target} not found")
            if not KEY_RE.match(target) and not EVIDENCE_RE.match(target):
                raise RequirementsError("invalid", f"invalid link target {target!r}")
            existing = [
                link
                for link in await uow.requirements.links(project_id)
                if (link.source_key, link.target, link.relation) == (source_key, target, relation)
            ]
            if existing:
                return existing[0]
            link = TraceLink(
                project_id=project_id,
                source_key=source_key,
                target=target,
                relation=relation,
                created_by=actor,
            )
            await uow.requirements.add_link(link)
            await uow.commit()
        return link

    async def unlink(
        self, project_id: str, source_key: str, target: str, relation: TraceRelation
    ) -> bool:
        async with self._uow_factory() as uow:
            removed = await uow.requirements.delete_link(
                project_id, source_key, target, relation.value
            )
            await uow.commit()
        return removed > 0

    # ------------------------------------------------------------------ traceability
    async def traceability(self, project_id: str, root_key: str | None = None) -> dict[str, Any]:
        """Tree from each BR (or ``root_key``) downwards, with links, evidence and gaps."""
        async with self._uow_factory() as uow:
            items = {i.key: i for i in await uow.requirements.list_latest(project_id)}
            links = await uow.requirements.links(project_id)
        children: dict[str, list[str]] = defaultdict(list)
        for item in items.values():
            if item.parent_key:
                children[item.parent_key].append(item.key)
        incoming: dict[str, list[TraceLink]] = defaultdict(list)
        outgoing: dict[str, list[TraceLink]] = defaultdict(list)
        for link in links:
            outgoing[link.source_key].append(link)
            if KEY_RE.match(link.target):
                incoming[link.target].append(link)

        def build(key: str, seen: set[str]) -> TraceNode:
            item = items[key]
            node = TraceNode(
                key=key,
                type=item.type.value,
                title=item.title,
                version=item.version,
                status=item.status.value,
                stale=item.stale,
            )
            seen.add(key)
            for child in sorted(children.get(key, [])):
                if child not in seen:
                    node.children.append(build(child, seen))
            for link in incoming.get(key, []):
                node.links.append({"relation": link.relation.value, "key": link.source_key})
            for link in outgoing.get(key, []):
                if EVIDENCE_RE.match(link.target):
                    node.evidence.append(link.target)
                else:
                    node.links.append({"relation": link.relation.value, "key": link.target})
            return node

        if root_key is not None:
            if root_key not in items:
                raise RequirementsError("not_found", f"requirement {root_key} not found")
            roots = [root_key]
        else:
            roots = sorted(k for k, i in items.items() if i.type == RequirementType.BR)
            if not roots:  # no business requirements yet: start from top-level items
                roots = sorted(k for k, i in items.items() if i.parent_key is None)
        trees = [build(r, set()) for r in roots]
        gaps = _gaps(items, children, incoming, outgoing)
        orphans = sorted(
            k
            for k, i in items.items()
            if i.parent_key is None
            and i.type in (RequirementType.FR, RequirementType.US)
            and not outgoing.get(k)
        )
        return {
            "roots": [_node_dict(t) for t in trees],
            "gaps": gaps if root_key is None else [g for g in gaps if g["key"] in _keys(trees)],
            "orphans": orphans,
            "stale": sorted(k for k, i in items.items() if i.stale),
            "counts": {
                t.value: sum(1 for i in items.values() if i.type == t)
                for t in RequirementType
                if any(i.type == t for i in items.values())
            },
        }

    async def check_ready(self, project_id: str, keys: Iterable[str]) -> list[str]:
        """Problems that block implementation of ``keys`` (ADR-014): unknown keys are
        ignored, but known ones must be APPROVED and not stale."""
        problems = []
        async with self._uow_factory() as uow:
            for key in sorted(set(keys)):
                item = await uow.requirements.latest(project_id, key)
                if item is None:
                    continue
                if item.status != S.APPROVED:
                    problems.append(f"{key} is {item.status.value} (v{item.version})")
                elif item.stale:
                    problems.append(f"{key} is stale (an upstream item changed)")
        return problems


# ---------------------------------------------------------------------- graph helpers
def _descendants(key: str, items: Iterable[Requirement], *, skip: set[str]) -> list[str]:
    children: dict[str, list[str]] = defaultdict(list)
    for item in items:
        if item.parent_key:
            children[item.parent_key].append(item.key)
    out: list[str] = []
    stack = list(reversed(sorted(children.get(key, []))))
    while stack:
        k = stack.pop()
        if k in skip or k in out:
            continue  # excluded subtrees are skipped entirely
        out.append(k)
        stack.extend(reversed(sorted(children.get(k, []))))
    return out


def _downstream(key: str, items: Iterable[Requirement], links: Iterable[TraceLink]) -> list[str]:
    """Descendants plus items that trace *to* an affected item, transitively."""
    items = list(items)
    links = list(links)
    seen: list[str] = []
    frontier = [key]
    while frontier:
        current = frontier.pop()
        nxt = _descendants(current, items, skip=set())
        nxt += [link.source_key for link in links if link.target == current]
        for k in nxt:
            if k != key and k not in seen:
                seen.append(k)
                frontier.append(k)
    return seen


def _gaps(
    items: dict[str, Requirement],
    children: dict[str, list[str]],
    incoming: dict[str, list[TraceLink]],
    outgoing: dict[str, list[TraceLink]],
) -> list[dict[str, str]]:
    gaps = []
    for key, item in sorted(items.items()):
        expected = EXPECTED_CHILDREN.get(item.type)
        if not expected:
            continue
        has_child = any(items[c].type in expected for c in children.get(key, []) if c in items)
        has_link = any(
            link.source_key in items and items[link.source_key].type in expected
            for link in incoming.get(key, [])
        )
        if item.type == RequirementType.AC:
            has_link = has_link or any(
                link.relation.value == "verifies" for link in incoming.get(key, [])
            )
        if not (has_child or has_link):
            gaps.append({"key": key, "missing": "/".join(t.value for t in expected)})
    return gaps


def _node_dict(node: TraceNode) -> dict[str, Any]:
    return {
        "key": node.key,
        "type": node.type,
        "title": node.title,
        "version": node.version,
        "status": node.status,
        "stale": node.stale,
        "links": node.links,
        "evidence": node.evidence,
        "children": [_node_dict(c) for c in node.children],
    }


def _keys(nodes: Iterable[TraceNode]) -> set[str]:
    out: set[str] = set()
    for node in nodes:
        out.add(node.key)
        out |= _keys(node.children)
    return out
