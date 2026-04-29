"""S16: add 'rejected' to workflow_runs status check constraint

Revision ID: s16_workflow_run_rejected_status
Revises: s15_workflow_operational_caps
Create Date: 2026-04-29 03:15:00.000000

The T02 cap enforcement inserts a WorkflowRun row with status='rejected' and
error_class='cap_exceeded' when a concurrent or hourly cap is exceeded. The
s11 migration's check constraint only allowed 5 values; this migration drops
and recreates it to include 'rejected'.

The downgrade path removes 'rejected' from the constraint — any existing
'rejected' rows would violate the restored constraint, but the downgrade is
only expected in test / development environments where the table is clean.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "s16_workflow_run_rejected_status"
down_revision = "s15_workflow_operational_caps"
branch_labels = None
depends_on = None

_OLD_CHECK = (
    "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')"
)
_NEW_CHECK = (
    "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled', 'rejected')"
)
_CONSTRAINT_NAME = "ck_workflow_runs_status"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT_NAME, "workflow_runs", type_="check")
    op.create_check_constraint(_CONSTRAINT_NAME, "workflow_runs", _NEW_CHECK)


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT_NAME, "workflow_runs", type_="check")
    op.create_check_constraint(_CONSTRAINT_NAME, "workflow_runs", _OLD_CHECK)
