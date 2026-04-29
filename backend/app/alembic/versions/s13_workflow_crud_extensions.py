"""S13 workflow CRUD extensions: form_schema, scope targets, per-step container, cancellation audit

Revision ID: s13_workflow_crud_extensions
Revises: s12_seed_direct_workflows
Create Date: 2026-04-28 20:00:00.000000

Schema additions for the S03 workflow CRUD + run engine:

workflows table:
  - form_schema JSONB NOT NULL DEFAULT '{}'::jsonb — trigger form descriptor.
    Shape: {"fields": [{"name", "label", "kind": "string"|"text"|"number",
    "required": bool}]}. Rendered as an inline form when the workflow button
    is clicked on the dashboard.
  - target_user_id UUID NULL FK→user(id) ON DELETE SET NULL — only
    meaningful when scope='team_specific'; the dispatcher pins this user's
    workspace instead of round-robining.
  - round_robin_cursor BIGINT NOT NULL DEFAULT 0 — monotonic pick counter
    for scope='round_robin'. Incremented atomically by the dispatcher;
    the target member index is cursor mod len(team_members).

workflow_steps table:
  - target_container VARCHAR(32) NOT NULL DEFAULT 'user_workspace'
    CHECK IN ('user_workspace', 'team_mirror') — per-step container
    override. 'team_mirror' is reserved for S04 (team-mirror executor)
    but the column lands now so S04 does not need an ALTER.

workflow_runs table:
  - cancelled_by_user_id UUID NULL FK→user(id) ON DELETE SET NULL — audit
    for user-initiated cancellations. SET NULL preserves the run audit
    trail even if the user is later deleted.
  - cancelled_at TIMESTAMPTZ NULL — timestamp of the cancellation request.
    Persisted alongside status='cancelling' so operators can measure
    cancellation-to-stop latency.

Downgrade reverses all column additions. The existing system_owned rows
(s12) survive intact — the new columns carry server-side defaults.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "s13_workflow_crud_extensions"
down_revision = "s12_seed_direct_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # workflows — three new columns                                        #
    # ------------------------------------------------------------------ #
    op.add_column(
        "workflows",
        sa.Column(
            "form_schema",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "workflows",
        sa.Column(
            "target_user_id",
            sa.UUID(),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_workflows_target_user_id",
        "workflows",
        "user",
        ["target_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "workflows",
        sa.Column(
            "round_robin_cursor",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )

    # ------------------------------------------------------------------ #
    # workflow_steps — target_container column + CHECK constraint          #
    # ------------------------------------------------------------------ #
    op.add_column(
        "workflow_steps",
        sa.Column(
            "target_container",
            sa.String(32),
            nullable=False,
            server_default="user_workspace",
        ),
    )
    op.create_check_constraint(
        "ck_workflow_steps_target_container",
        "workflow_steps",
        "target_container IN ('user_workspace', 'team_mirror')",
    )

    # ------------------------------------------------------------------ #
    # workflow_runs — cancellation audit columns                           #
    # ------------------------------------------------------------------ #
    op.add_column(
        "workflow_runs",
        sa.Column(
            "cancelled_by_user_id",
            sa.UUID(),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_workflow_runs_cancelled_by_user_id",
        "workflow_runs",
        "user",
        ["cancelled_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "workflow_runs",
        sa.Column(
            "cancelled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    # workflow_runs — cancellation audit
    op.drop_constraint(
        "fk_workflow_runs_cancelled_by_user_id", "workflow_runs", type_="foreignkey"
    )
    op.drop_column("workflow_runs", "cancelled_at")
    op.drop_column("workflow_runs", "cancelled_by_user_id")

    # workflow_steps — target_container
    op.drop_constraint(
        "ck_workflow_steps_target_container", "workflow_steps", type_="check"
    )
    op.drop_column("workflow_steps", "target_container")

    # workflows — round_robin_cursor, target_user_id, form_schema
    op.drop_column("workflows", "round_robin_cursor")
    op.drop_constraint(
        "fk_workflows_target_user_id", "workflows", type_="foreignkey"
    )
    op.drop_column("workflows", "target_user_id")
    op.drop_column("workflows", "form_schema")
