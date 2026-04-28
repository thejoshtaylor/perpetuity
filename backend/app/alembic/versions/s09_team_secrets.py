"""S09 team_secrets: per-team Fernet-encrypted credential storage

Revision ID: s09_team_secrets
Revises: s08_push_subscriptions
Create Date: 2026-04-28 14:25:00.000000

Lands the per-team credential boundary M005 needs. Each team gets at most
one row per registered key (e.g. ``claude_api_key``, ``openai_api_key``);
the ciphertext lives in ``value_encrypted BYTEA`` and never round-trips
back to the UI. Reuses the same Fernet/SYSTEM_SETTINGS_ENCRYPTION_KEY
discipline as M004's ``system_settings`` (decrypt-only-at-call-site, loud
503 on InvalidToken) — see S01-PLAN.md Goal.

Schema:

- team_id UUID NOT NULL FK→team(id) ON DELETE CASCADE — owning team. PK
  half. CASCADE so a team delete drops every secret with it; the
  encrypted blobs would otherwise be orphans no caller can decrypt
  (different team_id).
- key VARCHAR(64) NOT NULL — registered key name. PK half. Bounded to
  64 because the registry is a closed set of short identifiers
  (``claude_api_key``, ``openai_api_key``, future M006 ``github_pat``).
- value_encrypted BYTEA NOT NULL — Fernet ciphertext. NOT NULL because
  this table only stores set secrets; ``has_value=false`` is represented
  by row absence (idempotent DELETE returns 404).
- has_value BOOLEAN NOT NULL DEFAULT TRUE — mirrors the
  ``system_settings`` shape so the GET status DTO can render without
  peeking at the ciphertext column. Always TRUE for a row that exists;
  the column lets future ``cleared but tracked`` rows be added without
  schema churn.
- sensitive BOOLEAN NOT NULL DEFAULT TRUE — every M005 key is sensitive;
  the column is here so the public DTO can surface the flag directly
  rather than re-deriving it from the registry on every read.
- created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
- updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW() — bumped on every PUT
  by the API layer (the storage default just covers the first insert).

Composite PK on (team_id, key). The FK CASCADE rides on the team_id PK
half. No additional indexes — every read path is a PK lookup.

Downgrade drops the table. The PK + FK + defaults go with it.
"""
import logging

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "s09_team_secrets"
down_revision = "s08_push_subscriptions"
branch_labels = None
depends_on = None


logger = logging.getLogger("alembic.runtime.migration.s09")


def upgrade():
    op.create_table(
        "team_secrets",
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column(
            "has_value",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "sensitive",
            sa.Boolean(),
            server_default=sa.text("true"),
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
            name="fk_team_secrets_team_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "team_id", "key", name="pk_team_secrets"
        ),
    )
    logger.info(
        "S09 migration: created team_secrets "
        "(composite PK (team_id, key), team FK CASCADE, "
        "value_encrypted BYTEA NOT NULL, has_value/sensitive BOOL DEFAULT true)"
    )


def downgrade():
    op.drop_table("team_secrets")
    logger.info("S09 downgrade: dropped team_secrets")
