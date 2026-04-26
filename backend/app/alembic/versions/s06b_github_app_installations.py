"""S06b github_app_installations: per-team GitHub App installation rows

Revision ID: s06b_github_app_installations
Revises: s06_system_settings_sensitive
Create Date: 2026-04-25 17:30:00.000000

Backs the M004/S02 per-team GitHub connection flow. After a team admin walks
through the GitHub App install handshake, the install-callback persists one
row here scoped to the originating team. The orchestrator looks up the row by
team_id when minting installation tokens (App JWT → /app/installations/{id}/
access_tokens) and caches the resulting token in Redis for ~50 minutes.

Schema:

- id UUID PK — opaque internal handle (not the GitHub installation id)
- team_id UUID NOT NULL FK→team(id) ON DELETE CASCADE — owning team; if the
  team is deleted, the installation row goes with it (the GitHub App
  installation itself isn't revoked here — that's the operator's call)
- installation_id BIGINT NOT NULL UNIQUE — the GitHub installation id;
  BIGINT because GitHub installation ids are int64; UNIQUE because the same
  installation can only be claimed by one team at a time
- account_login VARCHAR(255) NOT NULL — the GitHub org/user login the app was
  installed onto (denormalized for UI; refreshed on token mint via /app
  metadata if it ever changes)
- account_type VARCHAR(64) NOT NULL CHECK IN ('Organization','User') —
  GitHub returns one of these two values; the CHECK keeps malformed inserts
  out of the table
- created_at TIMESTAMPTZ NOT NULL DEFAULT NOW() — server-side default so the
  install-callback doesn't have to round-trip the timestamp

No index on team_id beyond the FK — the orchestrator looks up by team_id
exactly once per clone, and the cardinality is small (one team rarely has
more than a handful of installations). If that pattern grows we can add
ix_github_app_installations_team_id later.

Downgrade drops the table; the UNIQUE and CHECK constraints go with it.
"""
import logging

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "s06b_github_app_installations"
down_revision = "s06_system_settings_sensitive"
branch_labels = None
depends_on = None


logger = logging.getLogger("alembic.runtime.migration.s06b")


def upgrade():
    op.create_table(
        "github_app_installations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("account_login", sa.String(length=255), nullable=False),
        sa.Column("account_type", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["team.id"],
            name="fk_github_app_installations_team_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_github_app_installations"),
        sa.UniqueConstraint(
            "installation_id", name="uq_github_app_installations_installation_id"
        ),
        sa.CheckConstraint(
            "account_type IN ('Organization', 'User')",
            name="ck_github_app_installations_account_type",
        ),
    )
    logger.info(
        "S06b migration: created github_app_installations "
        "(UUID PK, team FK CASCADE, installation_id BIGINT UNIQUE, "
        "account_type CHECK in Organization|User)"
    )


def downgrade():
    op.drop_table("github_app_installations")
    logger.info("S06b downgrade: dropped github_app_installations table")
