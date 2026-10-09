# ADR-004: Persistence: SQLite + SQLAlchemy 2 + Alembic

- **Status:** Accepted
- **Date:** 2026-10-08
- **Traces:** DATA_MODEL, NFR-MNT-001, NFR-REL-001

## Context
Local state must be durable, transactional and migratable. The control plane will use PostgreSQL later. Domain code must stay independent of the ORM.

## Decision
- SQLite in WAL mode with `foreign_keys=ON` and `busy_timeout` set.
- SQLAlchemy 2.0 async with aiosqlite. Alembic migrations ship inside the package (`panos_core/persistence/migrations/`), so the node can upgrade its own database at startup, including from a frozen (PyInstaller) build. *(Amended 2026-10-08 during M1; originally `/migrations`.)*
- **Domain entities** are plain Pydantic/dataclass models in `panos_core.domain`.
- **ORM** models and mappers live in `panos_core.persistence`, behind repository ports and a unit-of-work.
- Separate database files for state (`panos.db`) and the knowledge index (`knowledge.db`, ADR-017).
- Database files live in `platformdirs` user data dirs, never in synced folders.

## Alternatives considered
SQLModel (couples domain models to ORM tables); raw SQL (more boilerplate, no migration tooling).

## Consequences
- Repository `add` methods flush immediately so inserts follow call order. Without ORM relationships, SQLAlchemy does not order inserts by foreign key. Constraint violations therefore surface at `add`, not at `commit`.
Mapping code between domain and ORM is extra work but keeps the domain portable to PostgreSQL. Migrations are tested up/down in CI.
