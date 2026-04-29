"""S14 webhook_delivery_id: add idempotency column to workflow_runs

Revision ID: s14_webhook_delivery_id
Revises: s13_workflow_crud_extensions
Create Date: 2026-04-29 01:00:00.000000

Adds `webhook_delivery_id VARCHAR(64) UNIQUE NULL` to `workflow_runs`.

- Set only when `trigger_type='webhook'`; NULL for all other trigger types.
- The UNIQUE constraint ensures a given GitHub delivery_id can only produce
  one WorkflowRun row, regardless of how many times dispatch is called.
  A full (non-partial) unique index is simplest — NULL values do not
  participate in the uniqueness check in PostgreSQL, so non-webhook runs
  naturally coexist with their NULL column values.
- Downgrade drops the unique constraint and the column.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "s14_webhook_delivery_id"
down_revision = "s13_workflow_crud_extensions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_runs",
        sa.Column(
            "webhook_delivery_id",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_workflow_runs_webhook_delivery_id",
        "workflow_runs",
        ["webhook_delivery_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_workflow_runs_webhook_delivery_id",
        "workflow_runs",
        type_="unique",
    )
    op.drop_column("workflow_runs", "webhook_delivery_id")
