"""S15 workflow_operational_caps: add max_concurrent_runs and max_runs_per_hour

Revision ID: s15_workflow_operational_caps
Revises: s14_webhook_delivery_id
Create Date: 2026-04-29 03:00:00.000000

Adds two nullable Integer columns to ``workflows`` for operational cap
enforcement (T02 checks them at dispatch time) and a composite index on
``workflow_runs (workflow_id, status, created_at DESC)`` for efficient cap
counting queries.

- ``max_concurrent_runs``: INTEGER NULLABLE — when set, T02 rejects new
  dispatches that would exceed this many simultaneously running runs.
- ``max_runs_per_hour``: INTEGER NULLABLE — when set, T02 rejects new
  dispatches that would exceed this many runs in the sliding 60-minute window.
- Composite index ``ix_workflow_runs_workflow_id_status_created_at``:
  (workflow_id, status, created_at DESC) — used by the cap enforcement
  COUNT queries so they scan only relevant partitions.

Downgrade removes the composite index then the two columns.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "s15_workflow_operational_caps"
down_revision = "s14_webhook_delivery_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column("max_concurrent_runs", sa.Integer(), nullable=True),
    )
    op.add_column(
        "workflows",
        sa.Column("max_runs_per_hour", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_workflow_runs_workflow_id_status_created_at",
        "workflow_runs",
        ["workflow_id", "status", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_runs_workflow_id_status_created_at",
        table_name="workflow_runs",
    )
    op.drop_column("workflows", "max_runs_per_hour")
    op.drop_column("workflows", "max_concurrent_runs")
