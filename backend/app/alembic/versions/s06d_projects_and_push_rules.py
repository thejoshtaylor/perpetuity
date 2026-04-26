"""S06d projects + project_push_rules: per-team GitHub-linked projects

Revision ID: s06d_projects_and_push_rules
Revises: s06c_team_mirror_volumes
Create Date: 2026-04-25 22:00:00.000000

Backs the M004/S04 projects domain. A team admin creates a `projects` row by
linking a GitHub repo (via the team's installation) and giving it a name; the
system creates a default `project_push_rules` row at mode=manual_workflow.
The user-facing materialize / open path lands in T03; this revision is the
persistence substrate every subsequent S04 task reads from.

Schema:

- projects
  - id UUID PK — opaque internal handle
  - team_id UUID NOT NULL FK→team(id) ON DELETE CASCADE — owning team
  - installation_id BIGINT NOT NULL FK→github_app_installations(installation_id)
    ON DELETE RESTRICT — pin the source-of-truth GitHub installation. RESTRICT
    so we never silently lose the linkage if an admin deletes the install row
    while projects still reference it (the admin must remove the projects
    first or move them to a different installation)
  - github_repo_full_name VARCHAR(512) NOT NULL — `owner/repo` form for clone
  - name VARCHAR(255) NOT NULL — admin-chosen short name; the workspace dir
    is named after this
  - last_push_status VARCHAR(32) NULL — auto-push outcome ('ok'|'failed'|NULL)
  - last_push_error TEXT NULL — short scrubbed stderr on failure
  - created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
  - UNIQUE (team_id, name) — prevents duplicate names within a team

- project_push_rules
  - project_id UUID PK FK→projects(id) ON DELETE CASCADE — 1:1 with project
  - mode VARCHAR(32) NOT NULL CHECK IN ('auto','rule','manual_workflow')
  - branch_pattern VARCHAR(255) NULL — required for mode='rule'
  - workflow_id VARCHAR(255) NULL — required for mode='manual_workflow'
  - created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
  - updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()

Downgrade drops both tables in dependency order. The FK CASCADE on the
project_push_rules → projects edge is what lets a project delete cascade to
its rule row without an explicit DELETE in the route.
"""
import logging

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "s06d_projects_and_push_rules"
down_revision = "s06c_team_mirror_volumes"
branch_labels = None
depends_on = None


logger = logging.getLogger("alembic.runtime.migration.s06d")


def upgrade():
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "github_repo_full_name", sa.String(length=512), nullable=False
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "last_push_status", sa.String(length=32), nullable=True
        ),
        sa.Column("last_push_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["team.id"],
            name="fk_projects_team_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["github_app_installations.installation_id"],
            name="fk_projects_installation_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_projects"),
        sa.UniqueConstraint("team_id", "name", name="uq_projects_team_id_name"),
    )

    op.create_table(
        "project_push_rules",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column(
            "branch_pattern", sa.String(length=255), nullable=True
        ),
        sa.Column("workflow_id", sa.String(length=255), nullable=True),
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
            ["project_id"],
            ["projects.id"],
            name="fk_project_push_rules_project_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "project_id", name="pk_project_push_rules"
        ),
        sa.CheckConstraint(
            "mode IN ('auto', 'rule', 'manual_workflow')",
            name="ck_project_push_rules_mode",
        ),
    )
    logger.info(
        "S06d migration: created projects (UUID PK, team FK CASCADE, "
        "installation_id BIGINT FK RESTRICT, UNIQUE(team_id,name)) and "
        "project_push_rules (project_id PK FK CASCADE, mode CHECK in "
        "auto|rule|manual_workflow)"
    )


def downgrade():
    op.drop_table("project_push_rules")
    op.drop_table("projects")
    logger.info(
        "S06d downgrade: dropped project_push_rules and projects tables"
    )
