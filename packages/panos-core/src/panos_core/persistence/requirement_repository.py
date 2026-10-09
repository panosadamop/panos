"""SQL repository for requirements, approvals, trace links, interviews and skills (M6)."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from panos_core.domain.errors import EntityNotFoundError
from panos_core.domain.requirements import (
    GeneratedSkill,
    Interview,
    Requirement,
    RequirementApproval,
    RequirementType,
    TraceLink,
)
from panos_core.persistence.orm import (
    GeneratedSkillRow,
    InterviewRow,
    RequirementApprovalRow,
    RequirementRow,
    TraceLinkRow,
)


def _fields(row: Any, model: type[Any]) -> dict[str, Any]:
    return {name: getattr(row, name) for name in model.model_fields}


class SqlRequirementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ versions
    async def add(self, requirement: Requirement) -> None:
        self._session.add(RequirementRow(**requirement.model_dump()))
        await self._session.flush()

    async def update(self, requirement: Requirement) -> None:
        """Status/stale changes of a version row (content stays immutable)."""
        row = await self._session.get(RequirementRow, requirement.id)
        if row is None:
            raise EntityNotFoundError("requirement", requirement.id)
        row.status, row.stale = requirement.status, requirement.stale

    async def get(self, requirement_id: str) -> Requirement | None:
        row = await self._session.get(RequirementRow, requirement_id)
        return Requirement(**_fields(row, Requirement)) if row else None

    async def latest(self, project_id: str, key: str) -> Requirement | None:
        stmt = (
            select(RequirementRow)
            .where(RequirementRow.project_id == project_id, RequirementRow.key == key)
            .order_by(RequirementRow.version.desc())
            .limit(1)
        )
        row = (await self._session.scalars(stmt)).first()
        return Requirement(**_fields(row, Requirement)) if row else None

    async def history(self, project_id: str, key: str) -> Sequence[Requirement]:
        stmt = (
            select(RequirementRow)
            .where(RequirementRow.project_id == project_id, RequirementRow.key == key)
            .order_by(RequirementRow.version)
        )
        return [Requirement(**_fields(r, Requirement)) for r in await self._session.scalars(stmt)]

    async def list_latest(
        self, project_id: str, *, type: RequirementType | None = None
    ) -> Sequence[Requirement]:
        latest = (
            select(RequirementRow.key, func.max(RequirementRow.version).label("v"))
            .where(RequirementRow.project_id == project_id)
            .group_by(RequirementRow.key)
            .subquery()
        )
        stmt = (
            select(RequirementRow)
            .join(
                latest,
                (RequirementRow.key == latest.c.key) & (RequirementRow.version == latest.c.v),
            )
            .where(RequirementRow.project_id == project_id)
        )
        if type is not None:
            stmt = stmt.where(RequirementRow.type == type.value)
        rows = await self._session.scalars(stmt.order_by(RequirementRow.key))
        return [Requirement(**_fields(r, Requirement)) for r in rows]

    async def keys_with_prefix(self, project_id: str, prefix: str) -> Sequence[str]:
        stmt = (
            select(RequirementRow.key)
            .where(RequirementRow.project_id == project_id, RequirementRow.key.like(f"{prefix}%"))
            .distinct()
        )
        return list(await self._session.scalars(stmt))

    # ------------------------------------------------------------------ approvals
    async def add_approval(self, approval: RequirementApproval) -> None:
        self._session.add(RequirementApprovalRow(**approval.model_dump()))
        await self._session.flush()

    async def approvals(self, project_id: str, key: str) -> Sequence[RequirementApproval]:
        stmt = (
            select(RequirementApprovalRow)
            .where(
                RequirementApprovalRow.project_id == project_id, RequirementApprovalRow.key == key
            )
            .order_by(RequirementApprovalRow.decided_at)
        )
        return [
            RequirementApproval(**_fields(r, RequirementApproval))
            for r in await self._session.scalars(stmt)
        ]

    # ------------------------------------------------------------------ trace links
    async def add_link(self, link: TraceLink) -> None:
        self._session.add(TraceLinkRow(**link.model_dump()))
        await self._session.flush()

    async def delete_link(
        self, project_id: str, source_key: str, target: str, relation: str
    ) -> int:
        result = await self._session.execute(
            delete(TraceLinkRow).where(
                TraceLinkRow.project_id == project_id,
                TraceLinkRow.source_key == source_key,
                TraceLinkRow.target == target,
                TraceLinkRow.relation == relation,
            )
        )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def links(self, project_id: str) -> Sequence[TraceLink]:
        stmt = select(TraceLinkRow).where(TraceLinkRow.project_id == project_id)
        return [TraceLink(**_fields(r, TraceLink)) for r in await self._session.scalars(stmt)]

    # ------------------------------------------------------------------ interviews
    async def add_interview(self, interview: Interview) -> None:
        self._session.add(
            InterviewRow(
                **interview.model_dump(mode="json")
                | {"created_at": interview.created_at, "updated_at": interview.updated_at}
            )
        )
        await self._session.flush()

    async def get_interview(self, interview_id: str) -> Interview | None:
        row = await self._session.get(InterviewRow, interview_id)
        return Interview.model_validate(_fields(row, Interview)) if row else None

    async def update_interview(self, interview: Interview) -> None:
        row = await self._session.get(InterviewRow, interview.id)
        if row is None:
            raise EntityNotFoundError("interview", interview.id)
        data = interview.model_dump(mode="json")
        row.status, row.ambiguities = data["status"], data["ambiguities"]
        row.questions, row.explicit_stack = data["questions"], data["explicit_stack"]
        row.updated_at = interview.updated_at

    async def interviews(self, project_id: str) -> Sequence[Interview]:
        stmt = (
            select(InterviewRow)
            .where(InterviewRow.project_id == project_id)
            .order_by(InterviewRow.created_at)
        )
        return [
            Interview.model_validate(_fields(r, Interview))
            for r in await self._session.scalars(stmt)
        ]

    # ------------------------------------------------------------------ skills
    async def get_skill(self, project_id: str, name: str) -> GeneratedSkill | None:
        stmt = select(GeneratedSkillRow).where(
            GeneratedSkillRow.project_id == project_id, GeneratedSkillRow.name == name
        )
        row = (await self._session.scalars(stmt)).first()
        return GeneratedSkill(**_fields(row, GeneratedSkill)) if row else None

    async def save_skill(self, skill: GeneratedSkill) -> None:
        row = await self._session.get(GeneratedSkillRow, skill.id)
        if row is None:
            self._session.add(GeneratedSkillRow(**skill.model_dump()))
        else:
            for key, value in skill.model_dump().items():
                setattr(row, key, value)
        await self._session.flush()

    async def skills(self, project_id: str) -> Sequence[GeneratedSkill]:
        stmt = select(GeneratedSkillRow).where(GeneratedSkillRow.project_id == project_id)
        return [
            GeneratedSkill(**_fields(r, GeneratedSkill)) for r in await self._session.scalars(stmt)
        ]
