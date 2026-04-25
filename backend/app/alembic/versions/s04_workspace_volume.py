"""S04 workspace_volume: per-(user, team) loopback-ext4 volume row

Revision ID: s04_workspace_volume
Revises: s03_team_invites
Create Date: 2026-04-25 03:30:00.000000

Creates the `workspace_volume` table that backs the per-workspace loopback-ext4
volume hard-cap landed in M002/S02 (D014). One row per (user, team), holding
the effective per-volume cap and the host-side .img path.

Schema:

- id UUID PK (default uuid4 at the app layer; Postgres has no default — the
  ORM/SQL caller supplies the id)
- user_id UUID NOT NULL FK user.id ON DELETE CASCADE
- team_id UUID NOT NULL FK team.id ON DELETE CASCADE
- size_gb INTEGER NOT NULL — effective per-volume cap; 1..256 enforced at the
  app layer, not via a CHECK constraint (S03 admin API owns range validation)
- img_path VARCHAR(512) NOT NULL UNIQUE — on-disk .img file path; uniqueness
  is the canonical 'one volume per file' invariant
- created_at TIMESTAMPTZ NULL (default applied by app via get_datetime_utc)

Constraints / indexes:

- uq_workspace_volume_user_team — exactly one volume per (user, team), the
  D004/MEM004 invariant
- ix_workspace_volume_user_id, ix_workspace_volume_team_id — explicit names so
  downgrade can drop them deterministically (MEM025 pattern)
- ix_workspace_volume_img_path — auto-created by UNIQUE on img_path; downgrade
  drops the table which removes implicit indexes

Downgrade is fully reversible: drops both named btree indexes first, then drops
the table (which removes the FKs and the implicit unique index on img_path).
"""
import logging

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "s04_workspace_volume"
down_revision = "s03_team_invites"
branch_labels = None
depends_on = None


logger = logging.getLogger("alembic.runtime.migration.s04")


def upgrade():
    op.create_table(
        "workspace_volume",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("size_gb", sa.Integer(), nullable=False),
        sa.Column("img_path", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["team.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("img_path", name="uq_workspace_volume_img_path"),
        sa.UniqueConstraint(
            "user_id", "team_id", name="uq_workspace_volume_user_team"
        ),
    )
    op.create_index(
        "ix_workspace_volume_user_id",
        "workspace_volume",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_workspace_volume_team_id",
        "workspace_volume",
        ["team_id"],
        unique=False,
    )
    logger.info(
        "S04 migration: created workspace_volume table + uq_workspace_volume_user_team"
    )


def downgrade():
    op.drop_index("ix_workspace_volume_team_id", table_name="workspace_volume")
    op.drop_index("ix_workspace_volume_user_id", table_name="workspace_volume")
    op.drop_table("workspace_volume")
    logger.info("S04 downgrade: dropped workspace_volume table and indexes")
