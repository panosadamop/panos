"""job workflow kind, input, state, result and status reason

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-09 13:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("job") as batch:
        batch.add_column(
            sa.Column("kind", sa.String(length=64), nullable=False, server_default="coding")
        )
        batch.add_column(sa.Column("input", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("state", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("result", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("status_reason", sa.Text(), nullable=False, server_default=""))
    with op.batch_alter_table("checkpoint") as batch:
        batch.create_index("ix_checkpoint_job_id", ["job_id"])


def downgrade() -> None:
    with op.batch_alter_table("checkpoint") as batch:
        batch.drop_index("ix_checkpoint_job_id")
    with op.batch_alter_table("job") as batch:
        for column in ("status_reason", "result", "state", "input", "kind"):
            batch.drop_column(column)
