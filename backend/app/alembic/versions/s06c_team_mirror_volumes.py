"""S06c team_mirror_volumes: per-team mirror container state-of-record

Revision ID: s06c_team_mirror_volumes
Revises: s06b_github_app_installations
Create Date: 2026-04-25 19:30:00.000000

Backs the M004/S03 per-team mirror container lifecycle. Each team has at
most one mirror container running ``git daemon`` on port 9418 backed by a
durable volume of bare repos. The orchestrator's ensure-spinup path
upserts a row here on first call, the idle reaper inspects ``last_idle_at``
+ ``always_on`` to decide whether to kill the container, and the team-admin
PATCH /api/v1/teams/{id}/mirror endpoint flips ``always_on``. The row
itself outlives the container — ``container_id`` goes NULL after a reap;
``volume_path`` stays put so the next ensure remounts the same /repos.

Schema:

- id UUID PK — opaque internal handle
- team_id UUID NOT NULL UNIQUE FK→team(id) ON DELETE CASCADE — owning
  team. UNIQUE because we run at most one mirror per team. Cascade so a
  team delete drops the row (the running container is the orchestrator's
  problem to clean up; this is just the durable state)
- volume_path VARCHAR(512) NOT NULL UNIQUE — absolute host-side path to
  the team's bare-repo volume root. UUID-keyed by construction in the
  orchestrator so it never embeds PII
- container_id VARCHAR(64) NULL — short docker container id while the
  mirror is running; NULL between reaps and ensure calls
- last_started_at TIMESTAMPTZ NULL — set on each ensure-spinup; NULL
  before the first ensure
- last_idle_at TIMESTAMPTZ NULL — bumped by the orchestrator's
  activity-tracker; the reaper compares NOW() - last_idle_at against the
  resolved ``mirror_idle_timeout_seconds`` window
- always_on BOOLEAN NOT NULL DEFAULT FALSE — admin opt-out from the
  reaper. Toggled via PATCH /api/v1/teams/{id}/mirror
- created_at TIMESTAMPTZ NOT NULL DEFAULT NOW() — server-side default

No index beyond the FK + UNIQUE on team_id; orchestrator and reaper both
look up by team_id (PK lookup via the UNIQUE constraint).

Downgrade drops the table. The UNIQUE/FK go with it.
"""
import logging

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "s06c_team_mirror_volumes"
down_revision = "s06b_github_app_installations"
branch_labels = None
depends_on = None


logger = logging.getLogger("alembic.runtime.migration.s06c")


def upgrade():
    op.create_table(
        "team_mirror_volumes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("volume_path", sa.String(length=512), nullable=False),
        sa.Column("container_id", sa.String(length=64), nullable=True),
        sa.Column(
            "last_started_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "last_idle_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "always_on",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["team.id"],
            name="fk_team_mirror_volumes_team_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_team_mirror_volumes"),
        sa.UniqueConstraint(
            "team_id", name="uq_team_mirror_volumes_team_id"
        ),
        sa.UniqueConstraint(
            "volume_path", name="uq_team_mirror_volumes_volume_path"
        ),
    )
    logger.info(
        "S06c migration: created team_mirror_volumes "
        "(UUID PK, team FK CASCADE UNIQUE, volume_path UNIQUE, "
        "always_on BOOL DEFAULT false)"
    )


def downgrade():
    op.drop_table("team_mirror_volumes")
    logger.info("S06c downgrade: dropped team_mirror_volumes")
