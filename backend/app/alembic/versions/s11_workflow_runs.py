"""S11 workflow_runs + step_runs: M005/S02 run history substrate

Revision ID: s11_workflow_runs
Revises: s10_workflows
Create Date: 2026-04-28 15:35:00.000000

Lands the per-run / per-step history half of the M005 engine. ``workflow_runs``
records the trigger + lifecycle of a single dispatch; ``step_runs`` records
the per-step snapshot, stdout/stderr, exit_code, and error_class. R018
(forever-debuggable history) is the storage contract — stdout/stderr are
persisted in full and the rest of the system never logs them.

Schema:

- workflow_runs
  - id UUID PK — opaque internal handle, surfaced as ``run_id`` to clients.
  - workflow_id UUID NOT NULL FK→workflows(id) ON DELETE CASCADE — parent
    workflow definition. CASCADE so deleting a workflow drops its runs;
    orphan run history is meaningless once the definition is gone.
  - team_id UUID NOT NULL FK→team(id) ON DELETE CASCADE — owning team.
    Denormalized off the workflow so the run-detail route can authorize
    by team membership in a single query without joining ``workflows``;
    CASCADE so a team-delete drops its run history too.
  - trigger_type VARCHAR(32) NOT NULL CHECK IN ('button', 'webhook',
    'schedule', 'manual', 'admin_manual') — what fired the run. S02 only
    writes 'button'; the other discriminators are reserved for S04/S05.
  - triggered_by_user_id UUID NULL FK→user(id) ON DELETE SET NULL — the
    user who clicked the button (for trigger_type='button'). NULL for
    webhook/schedule triggers. SET NULL on user delete: losing the user
    must not destroy the audit trail of runs already executed.
  - target_user_id UUID NULL FK→user(id) ON DELETE SET NULL — the user
    whose workspace container is the dispatch target. For S02
    scope='user' this equals triggered_by_user_id; S03's round-robin
    fills it with the dispatched-to user. SET NULL on user delete with
    the same rationale.
  - trigger_payload JSONB NOT NULL DEFAULT '{}' — free-form trigger data.
    For S02's ``_direct_*`` workflows this is ``{"prompt": "<user text>"}``.
  - status VARCHAR(32) NOT NULL DEFAULT 'pending' CHECK IN ('pending',
    'running', 'succeeded', 'failed', 'cancelled') — lifecycle state.
    The Celery dispatcher transitions pending→running→succeeded|failed.
    'cancelled' is reserved for S05's admin-cancel route.
  - error_class VARCHAR(64) NULL — discriminator on failure: one of
    'missing_team_secret', 'team_secret_decrypt_failed',
    'orchestrator_exec_failed', 'cli_nonzero', 'worker_crash',
    'dispatch_failed'. NULL on success / before terminal transition.
    Stored as VARCHAR rather than CHECK-bound because S03/S04/S05 will
    add discriminators (e.g. 'webhook_validation_failed'); the route
    layer is the source of truth for the closed set.
  - started_at TIMESTAMPTZ NULL — set on transition into 'running'.
  - finished_at TIMESTAMPTZ NULL — set on transition into terminal state.
  - duration_ms BIGINT NULL — finished_at − started_at in ms; persisted
    so the UI can render without recomputing on every poll.
  - last_heartbeat_at TIMESTAMPTZ NULL — bumped by the worker while the
    run is in 'running'. Reserved for S05's orphan-recovery beat task;
    S02 sets it on transition into running.
  - created_at TIMESTAMPTZ NOT NULL DEFAULT NOW() — dispatch timestamp.

- step_runs
  - id UUID PK.
  - workflow_run_id UUID NOT NULL FK→workflow_runs(id) ON DELETE CASCADE
    — parent run. CASCADE so a run delete drops its step rows.
  - step_index INTEGER NOT NULL — copy of WorkflowStep.step_index at
    dispatch time. Persisted on the row (not derived from snapshot) so
    the run-detail query can ORDER BY step_index without parsing JSON.
  - snapshot JSONB NOT NULL — full WorkflowStep row at dispatch time
    (action, config, etc.). The contract is forever-frozen: editing the
    parent WorkflowStep after dispatch must not change the historical
    record. JSONB lets S03 add per-step fields without ALTERing this
    column.
  - status VARCHAR(32) NOT NULL DEFAULT 'pending' CHECK IN ('pending',
    'running', 'succeeded', 'failed', 'skipped') — lifecycle. 'skipped'
    is reserved for S05's conditional-step semantics.
  - stdout TEXT NOT NULL DEFAULT '' — full merged stdout from the
    executor (in S02 the orchestrator returns the script-q-/dev/null
    merged stream). R018 says we keep this forever.
  - stderr TEXT NOT NULL DEFAULT '' — same contract; for the script
    discipline this stays empty in the happy path and carries error
    text on failures.
  - exit_code INTEGER NULL — CLI exit code; NULL until the step
    completes.
  - error_class VARCHAR(64) NULL — same discriminator set as
    workflow_runs.error_class. Propagated up to the parent run on
    failure.
  - duration_ms BIGINT NULL — finished_at − started_at in ms.
  - started_at TIMESTAMPTZ NULL.
  - finished_at TIMESTAMPTZ NULL.
  - created_at TIMESTAMPTZ NOT NULL DEFAULT NOW() — dispatch timestamp
    of the step row insert.
  - UNIQUE (workflow_run_id, step_index) — within a run, step indexes
    are dense and unique. Mirrors the workflow_steps shape.

Indexes:
- ix_workflow_runs_team_id_created_at on (team_id, created_at DESC) —
  backs S05's per-team history list.
- ix_workflow_runs_status on (status) — backs S05's "any in_progress?"
  recovery scan.
- ix_step_runs_workflow_run_id on (workflow_run_id) — backs the
  run-detail FK lookup.

Downgrade drops both tables in dependency order (step_runs first because
of the FK).
"""
import logging

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "s11_workflow_runs"
down_revision = "s10_workflows"
branch_labels = None
depends_on = None


logger = logging.getLogger("alembic.runtime.migration.s11")


_TRIGGER_TYPE_VALUES = (
    "button",
    "webhook",
    "schedule",
    "manual",
    "admin_manual",
)
_TRIGGER_TYPE_CHECK = "trigger_type IN (" + ", ".join(
    f"'{v}'" for v in _TRIGGER_TYPE_VALUES
) + ")"

_RUN_STATUS_VALUES = (
    "pending",
    "running",
    "succeeded",
    "failed",
    "cancelled",
)
_RUN_STATUS_CHECK = "status IN (" + ", ".join(
    f"'{v}'" for v in _RUN_STATUS_VALUES
) + ")"

_STEP_STATUS_VALUES = (
    "pending",
    "running",
    "succeeded",
    "failed",
    "skipped",
)
_STEP_STATUS_CHECK = "status IN (" + ", ".join(
    f"'{v}'" for v in _STEP_STATUS_VALUES
) + ")"


def upgrade():
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column(
            "trigger_type", sa.String(length=32), nullable=False
        ),
        sa.Column(
            "triggered_by_user_id", sa.Uuid(), nullable=True
        ),
        sa.Column("target_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "trigger_payload",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "error_class", sa.String(length=64), nullable=True
        ),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "finished_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column(
            "last_heartbeat_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
            name="fk_workflow_runs_workflow_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["team.id"],
            name="fk_workflow_runs_team_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["triggered_by_user_id"],
            ["user.id"],
            name="fk_workflow_runs_triggered_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["target_user_id"],
            ["user.id"],
            name="fk_workflow_runs_target_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_runs"),
        sa.CheckConstraint(
            _TRIGGER_TYPE_CHECK,
            name="ck_workflow_runs_trigger_type",
        ),
        sa.CheckConstraint(
            _RUN_STATUS_CHECK,
            name="ck_workflow_runs_status",
        ),
    )
    op.create_index(
        "ix_workflow_runs_team_id_created_at",
        "workflow_runs",
        ["team_id", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_workflow_runs_status",
        "workflow_runs",
        ["status"],
        unique=False,
    )

    op.create_table(
        "step_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column(
            "snapshot",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "stdout",
            sa.Text(),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column(
            "stderr",
            sa.Text(),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column(
            "error_class", sa.String(length=64), nullable=True
        ),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "finished_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["workflow_runs.id"],
            name="fk_step_runs_workflow_run_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_step_runs"),
        sa.UniqueConstraint(
            "workflow_run_id",
            "step_index",
            name="uq_step_runs_workflow_run_id_step_index",
        ),
        sa.CheckConstraint(
            _STEP_STATUS_CHECK,
            name="ck_step_runs_status",
        ),
    )
    op.create_index(
        "ix_step_runs_workflow_run_id",
        "step_runs",
        ["workflow_run_id"],
        unique=False,
    )
    logger.info(
        "s11_workflow_runs upgrade complete tables=2 indexes=3 "
        "(workflow_runs + step_runs; trigger_type CHECK 5-valued, "
        "run status 5-valued, step status 5-valued)"
    )


def downgrade():
    op.drop_index(
        "ix_step_runs_workflow_run_id",
        table_name="step_runs",
    )
    op.drop_table("step_runs")
    op.drop_index(
        "ix_workflow_runs_status",
        table_name="workflow_runs",
    )
    op.drop_index(
        "ix_workflow_runs_team_id_created_at",
        table_name="workflow_runs",
    )
    op.drop_table("workflow_runs")
    logger.info(
        "s11_workflow_runs downgrade: dropped step_runs and workflow_runs"
    )
