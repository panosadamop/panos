"""Effort service: baselines, actuals, review, calibration suggestions (ADR-031, M10-01..05).

Every change is audited. Calibration uses only *approved human* actuals of *finished*
stories that have a baseline; machine metrics from jobs are reported separately.
"""

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from typing import Any, Final

from panos_core.audit import AuditService
from panos_core.config.settings import EstimationSettings
from panos_core.domain import Project
from panos_core.domain.clock import utc_now
from panos_core.domain.effort import (
    ActualStatus,
    Calibration,
    CalibrationStatus,
    EffortActual,
    EffortSource,
    EstimateBaseline,
)
from panos_core.domain.errors import DomainError
from panos_core.domain.models import Job, RepositorySnapshot
from panos_core.domain.requirements import KEY_RE
from panos_core.effort.calibration import OVERALL, StoryOutcome, calibrate
from panos_core.persistence import UnitOfWorkFactory
from panos_core.requirements.estimation import Estimate

PROJECT_KEY: Final = "*"  # job metrics not tied to a requirement


class EffortError(DomainError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def technology_from(stack_pack: str | None, snapshot: RepositorySnapshot | None) -> str:
    """The approved stack pack, else the detected main framework or language."""
    if stack_pack:
        return stack_pack.lower()
    stack = snapshot.stack if snapshot else {}
    for key in ("frameworks", "languages"):
        values = stack.get(key) or []
        if values:
            first = values[0]
            name = first.get("name") if isinstance(first, dict) else first
            if name:
                return str(name).lower()
    return "unknown"


class EffortService:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, audit: AuditService, settings: EstimationSettings
    ) -> None:
        self._uow_factory = uow_factory
        self._audit = audit
        self._settings = settings

    # ------------------------------------------------------------------ calibration in use
    async def active_calibration(self, technology: str) -> Calibration | None:
        async with self._uow_factory() as uow:
            approved = list(await uow.effort.calibrations(status=CalibrationStatus.APPROVED.value))
        by_scope = {c.scope: c for c in approved}
        return by_scope.get(f"technology:{technology}") or by_scope.get(OVERALL)

    async def hours_per_point(self, technology: str) -> tuple[float, Calibration | None]:
        calibration = await self.active_calibration(technology)
        if calibration is None:
            return self._settings.hours_per_point, None
        return calibration.hours_per_point, calibration

    # ------------------------------------------------------------------ baselines
    async def record_baseline(
        self,
        project: Project,
        estimate: Estimate,
        *,
        technology: str,
        hours_per_point: float,
        calibration: Calibration | None,
        actor: str = "user",
    ) -> EstimateBaseline:
        async with self._uow_factory() as uow:
            baseline = EstimateBaseline(
                project_id=project.id,
                version=await uow.effort.next_baseline_version(project.id),
                technology=technology,
                hours_per_point=hours_per_point,
                calibration_id=calibration.id if calibration else None,
                stories=[
                    {"key": s.key, "points": s.points, "hours": s.hours} for s in estimate.stories
                ],
            )
            await uow.effort.add_baseline(baseline)
            await uow.commit()
        await self._audit.record(
            actor=actor,
            event_type="effort.baseline_recorded",
            action="effort.baseline",
            project_id=project.id,
            metadata={
                "version": baseline.version,
                "stories": len(baseline.stories),
                "hours_per_point": hours_per_point,
                "calibration": baseline.calibration_id,
            },
        )
        return baseline

    # ------------------------------------------------------------------ actuals
    async def log(
        self,
        project: Project,
        requirement_key: str,
        hours: float,
        *,
        role: str = "developer",
        work_date: date | None = None,
        note: str = "",
        completes: bool = False,
        actor: str = "user",
    ) -> EffortActual:
        """Hours logged by the owner: approved at once (the owner is the approver)."""
        key = requirement_key.strip().upper()
        if not KEY_RE.match(key):
            raise EffortError(
                "invalid", f"{requirement_key!r} is not a requirement key like US-003"
            )
        async with self._uow_factory() as uow:
            if await uow.requirements.latest(project.id, key) is None:
                raise EffortError("not_found", f"{key} is not a requirement of {project.name}")
        actual = EffortActual(
            project_id=project.id,
            requirement_key=key,
            source=EffortSource.MANUAL,
            hours=round(hours, 2),
            role=role,
            work_date=work_date or date.today(),  # noqa: DTZ011 - the user's local day
            note=note,
            completes=completes,
            status=ActualStatus.APPROVED,
            logged_by=actor,
            decided_by=actor,
            decided_at=utc_now(),
        )
        await self._save(actual, "effort.logged", actor)
        return actual

    async def add_imported(self, actual: EffortActual, *, actor: str) -> bool:
        """A tracker or job actual; ``False`` when already imported (same external ref)."""
        async with self._uow_factory() as uow:
            if actual.external_ref and await uow.effort.actual_by_ref(
                actual.source.value, actual.external_ref
            ):
                return False
        await self._save(actual, f"effort.imported.{actual.source.value}", actor)
        return True

    async def _save(self, actual: EffortActual, event: str, actor: str) -> None:
        async with self._uow_factory() as uow:
            await uow.effort.save_actual(actual)
            await uow.commit()
        await self._audit.record(
            actor=actor,
            event_type=event,
            action="effort.actual",
            project_id=actual.project_id,
            metadata={
                "actual": actual.id,
                "key": actual.requirement_key,
                "hours": actual.hours,
                "source": actual.source.value,
                "status": actual.status.value,
            },
        )

    async def decide(self, actual_id: str, *, approve: bool, actor: str) -> EffortActual:
        async with self._uow_factory() as uow:
            actual = await uow.effort.get_actual(actual_id)
            if actual is None:
                raise EffortError("not_found", f"no actual {actual_id!r}")
            if actual.status != ActualStatus.PENDING:
                raise EffortError("not_pending", f"{actual_id} is already {actual.status}")
            actual.status = ActualStatus.APPROVED if approve else ActualStatus.REJECTED
            actual.decided_by, actual.decided_at = actor, utc_now()
            await uow.effort.save_actual(actual)
            await uow.commit()
        await self._audit.record(
            actor=actor,
            event_type="effort.decided",
            action="effort.actual",
            project_id=actual.project_id,
            metadata={"actual": actual.id, "status": actual.status.value},
        )
        return actual

    async def actuals(
        self,
        project: Project | None = None,
        *,
        status: str | None = None,
        source: str | None = None,
    ) -> Sequence[EffortActual]:
        async with self._uow_factory() as uow:
            return await uow.effort.actuals(
                project_id=project.id if project else None, status=status, source=source
            )

    async def record_job(
        self, job: Job, requirement_keys: list[str], tokens: int, cost: float
    ) -> int:
        """Machine metrics of a finished job, one actual per requirement it implemented."""
        if job.project_id is None:
            return 0
        keys = requirement_keys or [PROJECT_KEY]
        share = 1 / len(keys)
        runtime = float(job.state.get("active_s", 0.0))
        added = 0
        for key in keys:
            actual = EffortActual(
                project_id=job.project_id,
                requirement_key=key,
                source=EffortSource.JOB,
                role="panos",
                work_date=(job.completed_at or utc_now()).date(),
                external_ref=f"{job.id}:{key}",
                machine={
                    "runtime_s": round(runtime * share, 1),
                    "tokens": round(tokens * share),
                    "cost_usd": round(cost * share, 6),
                },
                status=ActualStatus.APPROVED,  # measured, not claimed
                logged_by="system",
                decided_by="system",
                decided_at=utc_now(),
            )
            added += await self.add_imported(actual, actor="system")
        return added

    # ------------------------------------------------------------------ report
    async def outcomes(self, projects: dict[str, str] | None = None) -> list[StoryOutcome]:
        """Finished stories with a baseline (projects: id → technology override)."""
        async with self._uow_factory() as uow:
            baselines = list(await uow.effort.baselines())
            actuals = [
                a
                for a in await uow.effort.actuals(status=ActualStatus.APPROVED.value, limit=100_000)
                if a.source != EffortSource.JOB
            ]
        by_story: dict[tuple[str, str], list[EffortActual]] = defaultdict(list)
        for a in actuals:
            by_story[(a.project_id, a.requirement_key)].append(a)
        result: list[StoryOutcome] = []
        for (project_id, key), entries in sorted(by_story.items()):
            if not any(e.completes for e in entries):
                continue
            first = min(e.created_at for e in entries)
            candidates = [
                b
                for b in baselines
                if b.project_id == project_id and any(s["key"] == key for s in b.stories)
            ]
            if not candidates:
                continue
            before = [b for b in candidates if b.created_at <= first]
            baseline = (
                max(before, key=lambda b: b.version)
                if before
                else min(candidates, key=lambda b: b.version)
            )
            story = next(s for s in baseline.stories if s["key"] == key)
            result.append(
                StoryOutcome(
                    project_id=project_id,
                    key=key,
                    technology=(projects or {}).get(project_id, baseline.technology),
                    points=int(story["points"]),
                    estimated_hours=float(story["hours"]),
                    actual_hours=round(sum(e.hours for e in entries), 2),
                )
            )
        return result

    async def report(self, project: Project) -> dict[str, Any]:
        outcomes = [o for o in await self.outcomes() if o.project_id == project.id]
        async with self._uow_factory() as uow:
            actuals = list(await uow.effort.actuals(project_id=project.id, limit=100_000))
            baselines = list(await uow.effort.baselines(project.id))
        human = [a for a in actuals if a.source != EffortSource.JOB]
        machine = [a for a in actuals if a.source == EffortSource.JOB]
        per_story: dict[str, float] = defaultdict(float)
        for a in human:
            if a.status == ActualStatus.APPROVED:
                per_story[a.requirement_key] += a.hours
        return {
            "project": project.name,
            "baselines": len(baselines),
            "latest_baseline": baselines[-1].model_dump(mode="json") if baselines else None,
            "logged_hours": round(sum(per_story.values()), 2),
            "pending": sum(1 for a in human if a.status == ActualStatus.PENDING),
            "per_story": {k: round(v, 2) for k, v in sorted(per_story.items())},
            "finished": [o.__dict__ | {"ratio": round(o.ratio, 3)} for o in outcomes],
            "machine": {
                "jobs": len({(a.external_ref or "").split(":")[0] for a in machine}),
                "runtime_s": round(sum(a.machine.get("runtime_s", 0) for a in machine), 1),
                "tokens": int(sum(a.machine.get("tokens", 0) for a in machine)),
                "cost_usd": round(sum(a.machine.get("cost_usd", 0) for a in machine), 6),
            },
        }

    # ------------------------------------------------------------------ suggestions
    async def suggest(self, *, actor: str = "user") -> list[Calibration]:
        """Compute suggestions from approved data; nothing changes until one is approved."""
        results = calibrate(
            await self.outcomes(), min_stories=self._settings.calibration_min_stories
        )
        made: list[Calibration] = []
        async with self._uow_factory() as uow:
            for old in await uow.effort.calibrations(status=CalibrationStatus.SUGGESTED.value):
                old.status = CalibrationStatus.SUPERSEDED
                await uow.effort.save_calibration(old)
            for r in results:
                calibration = Calibration(
                    scope=r.scope,
                    factor=r.factor,
                    hours_per_point=r.hours_per_point,
                    base_hours_per_point=self._settings.hours_per_point,
                    stories=r.stories,
                    spread=r.spread,
                    bias=r.bias,
                    mape=r.mape,
                    basis=r.basis,
                )
                await uow.effort.save_calibration(calibration)
                made.append(calibration)
            await uow.commit()
        await self._audit.record(
            actor=actor,
            event_type="effort.calibration_suggested",
            action="effort.calibration",
            metadata={
                "suggestions": [
                    {"scope": c.scope, "hours_per_point": c.hours_per_point, "stories": c.stories}
                    for c in made
                ]
            },
        )
        return made

    async def decide_calibration(
        self, calibration_id: str, *, approve: bool, actor: str
    ) -> Calibration:
        async with self._uow_factory() as uow:
            calibration = await uow.effort.get_calibration(calibration_id)
            if calibration is None:
                raise EffortError("not_found", f"no calibration {calibration_id!r}")
            if calibration.status != CalibrationStatus.SUGGESTED:
                raise EffortError("not_pending", f"{calibration_id} is {calibration.status}")
            if approve:
                for current in await uow.effort.calibrations(
                    status=CalibrationStatus.APPROVED.value
                ):
                    if current.scope == calibration.scope:
                        current.status = CalibrationStatus.SUPERSEDED
                        await uow.effort.save_calibration(current)
            calibration.status = (
                CalibrationStatus.APPROVED if approve else CalibrationStatus.REJECTED
            )
            calibration.decided_by, calibration.decided_at = actor, utc_now()
            await uow.effort.save_calibration(calibration)
            await uow.commit()
        await self._audit.record(
            actor=actor,
            event_type="effort.calibration_decided",
            action="effort.calibration",
            metadata={
                "calibration": calibration.id,
                "scope": calibration.scope,
                "status": calibration.status.value,
                "hours_per_point": calibration.hours_per_point,
            },
        )
        return calibration

    async def calibrations(self, *, status: str | None = None) -> Sequence[Calibration]:
        async with self._uow_factory() as uow:
            return await uow.effort.calibrations(status=status)
