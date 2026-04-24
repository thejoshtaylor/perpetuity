"""S03 team invites: team_invite table + unique ix_team_invite_code

Revision ID: s03_team_invites
Revises: s02_team_columns
Create Date: 2026-04-24 17:00:00.000000

Creates the `team_invite` table that backs the invite/accept flow added in
S03. Columns:

- id UUID PK
- code VARCHAR(64) NOT NULL UNIQUE (indexed as ix_team_invite_code)
- team_id UUID NOT NULL FK team.id ON DELETE CASCADE
- created_by UUID NOT NULL FK user.id ON DELETE CASCADE
- expires_at TIMESTAMPTZ NOT NULL
- used_at TIMESTAMPTZ NULL
- used_by UUID NULL FK user.id ON DELETE SET NULL
- created_at TIMESTAMPTZ NULL (default now via app-level get_datetime_utc)

Unique index on `code` is created with an explicit name (ix_team_invite_code)
so downgrade can drop it by name — MEM025 pattern.

Downgrade is fully reversible: drops the index first, then the table.
"""
import logging

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "s03_team_invites"
down_revision = "s02_team_columns"
branch_labels = None
depends_on = None


logger = logging.getLogger("alembic.runtime.migration.s03")


def upgrade():
    op.create_table(
        "team_invite",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["team_id"], ["team.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["created_by"], ["user.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["used_by"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_team_invite_code", "team_invite", ["code"], unique=True
    )
    op.create_index(
        op.f("ix_team_invite_team_id"), "team_invite", ["team_id"], unique=False
    )
    op.create_index(
        op.f("ix_team_invite_created_by"),
        "team_invite",
        ["created_by"],
        unique=False,
    )
    logger.info("S03 migration: created team_invite table + ix_team_invite_code")


def downgrade():
    op.drop_index(op.f("ix_team_invite_created_by"), table_name="team_invite")
    op.drop_index(op.f("ix_team_invite_team_id"), table_name="team_invite")
    op.drop_index("ix_team_invite_code", table_name="team_invite")
    op.drop_table("team_invite")
    logger.info("S03 downgrade: dropped team_invite table and indexes")
