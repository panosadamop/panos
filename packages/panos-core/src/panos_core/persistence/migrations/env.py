"""Alembic environment. Migrations ship inside the package so the node can upgrade itself."""

from alembic import context
from sqlalchemy import create_engine, pool

from panos_core.persistence.orm import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url")
    assert url is not None  # noqa: S101
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        # SQLite needs batch mode for ALTER TABLE operations.
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
