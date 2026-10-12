"""SQL repository for estimate baselines, actuals and calibrations (M10, ADR-031)."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from panos_core.domain.effort import Calibration, EffortActual, EstimateBaseline
from panos_core.persistence.orm import CalibrationRow, EffortActualRow, EstimateBaselineRow


def _fields(row: Any, model: type[Any]) -> dict[str, Any]:
    return {name: getattr(row, name) for name in model.model_fields}


class SqlEffortRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _save(self, row_type: type[Any], entity: Any) -> None:
        row = await self._session.get(row_type, entity.id)
        data = entity.model_dump(mode="python")
        if row is None:
            self._session.add(row_type(**data))
        else:
            for key, value in data.items():
                setattr(row, key, value)
        await self._session.flush()

    # ------------------------------------------------------------------ baselines
    async def add_baseline(self, baseline: EstimateBaseline) -> None:
        await self._save(EstimateBaselineRow, baseline)

    async def next_baseline_version(self, project_id: str) -> int:
        current = await self._session.scalar(
            select(func.max(EstimateBaselineRow.version)).where(
                EstimateBaselineRow.project_id == project_id
            )
        )
        return int(current or 0) + 1

    async def baselines(self, project_id: str | None = None) -> Sequence[EstimateBaseline]:
        stmt = select(EstimateBaselineRow).order_by(
            EstimateBaselineRow.project_id, EstimateBaselineRow.version
        )
        if project_id:
            stmt = stmt.where(EstimateBaselineRow.project_id == project_id)
        rows = await self._session.scalars(stmt)
        return [EstimateBaseline(**_fields(r, EstimateBaseline)) for r in rows]

    # ------------------------------------------------------------------ actuals
    async def save_actual(self, actual: EffortActual) -> None:
        await self._save(EffortActualRow, actual)

    async def get_actual(self, actual_id: str) -> EffortActual | None:
        row = await self._session.get(EffortActualRow, actual_id)
        return EffortActual(**_fields(row, EffortActual)) if row else None

    async def actual_by_ref(self, source: str, external_ref: str) -> EffortActual | None:
        row = await self._session.scalar(
            select(EffortActualRow).where(
                EffortActualRow.source == source, EffortActualRow.external_ref == external_ref
            )
        )
        return EffortActual(**_fields(row, EffortActual)) if row else None

    async def actuals(
        self,
        *,
        project_id: str | None = None,
        status: str | None = None,
        source: str | None = None,
        limit: int = 1000,
    ) -> Sequence[EffortActual]:
        stmt = select(EffortActualRow).order_by(EffortActualRow.work_date.desc()).limit(limit)
        if project_id:
            stmt = stmt.where(EffortActualRow.project_id == project_id)
        if status:
            stmt = stmt.where(EffortActualRow.status == status)
        if source:
            stmt = stmt.where(EffortActualRow.source == source)
        rows = await self._session.scalars(stmt)
        return [EffortActual(**_fields(r, EffortActual)) for r in rows]

    # ------------------------------------------------------------------ calibrations
    async def save_calibration(self, calibration: Calibration) -> None:
        await self._save(CalibrationRow, calibration)

    async def get_calibration(self, calibration_id: str) -> Calibration | None:
        row = await self._session.get(CalibrationRow, calibration_id)
        return Calibration(**_fields(row, Calibration)) if row else None

    async def calibrations(self, *, status: str | None = None) -> Sequence[Calibration]:
        stmt = select(CalibrationRow).order_by(CalibrationRow.created_at.desc())
        if status:
            stmt = stmt.where(CalibrationRow.status == status)
        rows = await self._session.scalars(stmt)
        return [Calibration(**_fields(r, Calibration)) for r in rows]
