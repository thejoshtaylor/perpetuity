"""S17 github_user_oauth_tokens: per-user Fernet-encrypted GitHub OAuth token storage

Revision ID: s17_github_user_oauth_tokens
Revises: s16_workflow_run_rejected_status
Create Date: 2026-05-12 00:00:00.000000

Lands the per-user GitHub OAuth access + refresh token boundary that M006
needs. Each Perpetuity user gets at most one row (keyed on user_id); a
reinstall on a different GitHub account overwrites the previous row via
ON CONFLICT (user_id) DO UPDATE at the application layer.

The access and refresh tokens are stored as Fernet ciphertext in BYTEA
columns and decrypted only at call-site (never held in memory longer than
a single HTTP request and never logged in full).

Schema:

- user_id UUID NOT NULL FK→user(id) ON DELETE CASCADE — owning Perpetuity
  user. PK. CASCADE so a user deletion drops the token row; orphan
  ciphertext that cannot be attributed to a live user is useless.
- installation_id BIGINT NOT NULL — GitHub App installation id recorded
  at install-callback time. Used by the orchestrator route to confirm
  which install the token is associated with.
- github_user_id BIGINT NOT NULL — GitHub user id resolved via GET
  /user at install time. Allows "wrong user reinstalled" detection in
  error messages.
- access_token_encrypted BYTEA NOT NULL — Fernet ciphertext of the
  GitHub user-to-server access token (ghu_...). Decrypted per-call only.
- refresh_token_encrypted BYTEA NOT NULL — Fernet ciphertext of the
  GitHub refresh token (ghr_...). Decrypted per-call only.
- access_token_expires_at TIMESTAMPTZ NOT NULL — when the access token
  expires (now() + expires_in seconds from the token-exchange response).
  The refresh helper checks this column before deciding whether to call
  the token-refresh endpoint.
- refresh_token_expires_at TIMESTAMPTZ NOT NULL — when the refresh token
  expires (~6 months). Expiry triggers the reinstall flow.
- scope VARCHAR(255) NOT NULL — the OAuth scopes granted (e.g. "repo,
  read:user"). Stored as a comma-separated string matching GitHub's
  token-exchange response format.
- created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
- updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW() — bumped on every upsert
  by the application layer (the storage default covers the first insert).

PK on user_id (single-column). FK CASCADE rides on the PK column.
No additional indexes — every read path is a PK lookup by user_id.

Downgrade drops the table. The PK + FK + defaults go with it.
"""
import logging

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "s17_github_user_oauth_tokens"
down_revision = "s16_workflow_run_rejected_status"
branch_labels = None
depends_on = None


logger = logging.getLogger("alembic.runtime.migration.s17")


def upgrade():
    op.create_table(
        "github_user_oauth_tokens",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("github_user_id", sa.BigInteger(), nullable=False),
        sa.Column("access_token_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column(
            "access_token_expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "refresh_token_expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("scope", sa.String(length=255), nullable=False),
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
            ["user_id"],
            ["user.id"],
            name="fk_github_user_oauth_tokens_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_github_user_oauth_tokens"),
    )
    logger.info(
        "S17 migration: created github_user_oauth_tokens "
        "(PK user_id, user FK CASCADE, "
        "access_token_encrypted/refresh_token_encrypted BYTEA NOT NULL, "
        "access/refresh expiry timestamps, scope VARCHAR(255), "
        "created_at/updated_at TIMESTAMPTZ DEFAULT NOW())"
    )


def downgrade():
    op.drop_table("github_user_oauth_tokens")
    logger.info("S17 downgrade: dropped github_user_oauth_tokens")
