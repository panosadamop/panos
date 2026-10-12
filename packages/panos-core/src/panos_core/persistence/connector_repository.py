"""SQL repository for connector accounts and knowledge collections (M8, ADR-026)."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from panos_core.domain.connectors import ConnectorAccount, KnowledgeCollection
from panos_core.persistence.orm import ConnectorAccountRow, KnowledgeCollectionRow


def _fields(row: Any, model: type[Any]) -> dict[str, Any]:
    return {name: getattr(row, name) for name in model.model_fields}


class SqlConnectorRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ accounts
    async def save_account(self, account: ConnectorAccount) -> None:
        row = await self._session.get(ConnectorAccountRow, account.id)
        data = account.model_dump(mode="python")
        if row is None:
            self._session.add(ConnectorAccountRow(**data))
        else:
            for key, value in data.items():
                setattr(row, key, value)
        await self._session.flush()

    async def get_account(self, account_id: str) -> ConnectorAccount | None:
        row = await self._session.get(ConnectorAccountRow, account_id)
        return ConnectorAccount(**_fields(row, ConnectorAccount)) if row else None

    async def accounts(self, kind: str | None = None) -> Sequence[ConnectorAccount]:
        stmt = select(ConnectorAccountRow).order_by(ConnectorAccountRow.created_at)
        if kind is not None:
            stmt = stmt.where(ConnectorAccountRow.kind == kind)
        return [
            ConnectorAccount(**_fields(r, ConnectorAccount))
            for r in await self._session.scalars(stmt)
        ]

    async def delete_account(self, account_id: str) -> None:
        row = await self._session.get(ConnectorAccountRow, account_id)
        if row is not None:
            await self._session.delete(row)
            await self._session.flush()

    # ------------------------------------------------------------------ collections
    async def save_collection(self, collection: KnowledgeCollection) -> None:
        row = await self._session.get(KnowledgeCollectionRow, collection.id)
        data = collection.model_dump(mode="python")
        if row is None:
            self._session.add(KnowledgeCollectionRow(**data))
        else:
            for key, value in data.items():
                setattr(row, key, value)
        await self._session.flush()

    async def get_collection(self, ref: str) -> KnowledgeCollection | None:
        """By id or by name."""
        row = await self._session.get(KnowledgeCollectionRow, ref)
        if row is None:
            stmt = select(KnowledgeCollectionRow).where(KnowledgeCollectionRow.name == ref)
            row = (await self._session.scalars(stmt)).first()
        return KnowledgeCollection(**_fields(row, KnowledgeCollection)) if row else None

    async def collections(self) -> Sequence[KnowledgeCollection]:
        stmt = select(KnowledgeCollectionRow).order_by(KnowledgeCollectionRow.name)
        return [
            KnowledgeCollection(**_fields(r, KnowledgeCollection))
            for r in await self._session.scalars(stmt)
        ]

    async def delete_collection(self, collection_id: str) -> None:
        row = await self._session.get(KnowledgeCollectionRow, collection_id)
        if row is not None:
            await self._session.delete(row)
            await self._session.flush()
