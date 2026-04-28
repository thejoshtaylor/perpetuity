"""S10 workflows + workflow_steps: M005/S02 workflow definition substrate

Revision ID: s10_workflows
Revises: s09_team_secrets
Create Date: 2026-04-28 15:30:00.000000

Lands the workflow-definition half of the M005 engine. Two tables persist
the per-team workflow registry and its ordered step rows. S02 only ships
the auto-seeded ``_direct_claude`` / ``_direct_codex`` system workflows
(each with exactly one step), but the schema is shaped to accommodate
S03's full CRUD + multi-step + scope-dispatch without migration.

Schema:

- workflows
  - id UUID PK — opaque internal handle.
  - team_id UUID NOT NULL FK→team(id) ON DELETE CASCADE — owning team.
    CASCADE so a team delete drops every workflow + run history with it
    (orphan run history is meaningless).
  - name VARCHAR(255) NOT NULL — e.g. ``_direct_claude``, ``_direct_codex``,
    or a user-given title in S03. The leading-underscore convention
    reserves the ``_direct_*`` namespace for system-owned rows.
  - description TEXT NULL — free-form admin description, surfaced in S03's
    CRUD UI.
  - scope VARCHAR(32) NOT NULL DEFAULT 'user' CHECK IN
    ('user', 'team', 'round_robin') — dispatch target shape. S02 only uses
    'user' (run inherits the triggering user's workspace container);
    S03 wires 'team' (round-robin across team members) and other modes.
  - system_owned BOOLEAN NOT NULL DEFAULT FALSE — flips TRUE for the
    auto-seeded ``_direct_claude`` / ``_direct_codex`` rows so S03's CRUD
    UI can filter them out (D028: system workflows are surfaced as
    dashboard buttons, not as editable rows).
  - created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
  - updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW() — bumped by the API on
    every mutation; the storage default just covers the first insert.
  - UNIQUE (team_id, name) — duplicate seed attempts must fail loudly so
    re-running the auto-seed migration is safe and so S03's CRUD cannot
    silently shadow a system workflow.

- workflow_steps
  - id UUID PK — opaque internal handle.
  - workflow_id UUID NOT NULL FK→workflows(id) ON DELETE CASCADE — parent
    workflow. CASCADE so a workflow delete drops its step rows in one shot.
  - step_index INTEGER NOT NULL — ordering within the parent. S02 always
    writes 0 (each system workflow has one step); S03's multi-step shape
    populates 0..N-1 in declaration order.
  - action VARCHAR(64) NOT NULL CHECK IN ('claude', 'codex', 'shell', 'git')
    — discriminator for the executor dispatch table. S02 ships only
    ``claude`` and ``codex`` executors; ``shell`` / ``git`` are reserved
    for S03 (the migration installs the CHECK so S03's seed can land
    without ALTERing the constraint).
  - config JSONB NOT NULL DEFAULT '{}' — free-form per-action config. For
    S02 the auto-seed payload is ``{"prompt_template": "{prompt}"}``;
    S03 layers in form-field metadata, ``target_container`` overrides,
    ``{prev.stdout}`` substitution refs, etc.
  - created_at, updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
  - UNIQUE (workflow_id, step_index) — within a workflow, step indexes
    are dense and unique. Two rows at index=0 would silently break the
    executor's ordered-iteration contract.

Sibling-table choice (not JSONB on ``workflows``) is deliberate: S03 wants
to ALTER and add per-step columns (``target_container``, ``timeout_s``,
``on_failure``) without touching the JSON shape. JSONB ``config`` covers
the action-specific tail.

Downgrade drops the two tables in dependency order (steps first because of
the FK). Forward and reverse are both safe to repeat without leaving
dangling state.
"""
import logging

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "s10_workflows"
down_revision = "s09_team_secrets"
branch_labels = None
depends_on = None


logger = logging.getLogger("alembic.runtime.migration.s10")


_WORKFLOW_SCOPE_VALUES = ("user", "team", "round_robin")
_WORKFLOW_SCOPE_CHECK = "scope IN (" + ", ".join(
    f"'{v}'" for v in _WORKFLOW_SCOPE_VALUES
) + ")"

_WORKFLOW_STEP_ACTION_VALUES = ("claude", "codex", "shell", "git")
_WORKFLOW_STEP_ACTION_CHECK = "action IN (" + ", ".join(
    f"'{v}'" for v in _WORKFLOW_STEP_ACTION_VALUES
) + ")"


def upgrade():
    op.create_table(
        "workflows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "scope",
            sa.String(length=32),
            server_default=sa.text("'user'"),
            nullable=False,
        ),
        sa.Column(
            "system_owned",
            sa.Boolean(),
            server_default=sa.text("FALSE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["team.id"],
            name="fk_workflows_team_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workflows"),
        sa.UniqueConstraint(
            "team_id", "name", name="uq_workflows_team_id_name"
        ),
        sa.CheckConstraint(
            _WORKFLOW_SCOPE_CHECK,
            name="ck_workflows_scope",
        ),
    )
    op.create_index(
        "ix_workflows_team_id",
        "workflows",
        ["team_id"],
        unique=False,
    )

    op.create_table(
        "workflow_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column(
            "config",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
            name="fk_workflow_steps_workflow_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_steps"),
        sa.UniqueConstraint(
            "workflow_id",
            "step_index",
            name="uq_workflow_steps_workflow_id_step_index",
        ),
        sa.CheckConstraint(
            _WORKFLOW_STEP_ACTION_CHECK,
            name="ck_workflow_steps_action",
        ),
    )
    op.create_index(
        "ix_workflow_steps_workflow_id",
        "workflow_steps",
        ["workflow_id"],
        unique=False,
    )
    logger.info(
        "s10_workflows upgrade complete tables=2 indexes=2 "
        "(workflows + workflow_steps; scope CHECK 3-valued; action CHECK 4-valued)"
    )


def downgrade():
    op.drop_index(
        "ix_workflow_steps_workflow_id",
        table_name="workflow_steps",
    )
    op.drop_table("workflow_steps")
    op.drop_index(
        "ix_workflows_team_id",
        table_name="workflows",
    )
    op.drop_table("workflows")
    logger.info(
        "s10_workflows downgrade: dropped workflow_steps and workflows"
    )
