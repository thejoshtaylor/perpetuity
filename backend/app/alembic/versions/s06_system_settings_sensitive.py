"""S06 system_settings sensitive: encrypted columns for secret-bearing keys

Revision ID: s06_system_settings_sensitive
Revises: s05_system_settings
Create Date: 2026-04-25 17:00:00.000000

Extends the M002/S05 `system_settings` table so it can carry sensitive values
(GitHub App private key, webhook secret, future SMTP password, ...) alongside
the existing JSONB-backed plain values. The split is intentional: encrypted
ciphertext lives in `value_encrypted BYTEA`, plain JSONB lives in `value`, and
no row writes both. Per-key metadata flags (`sensitive`, `has_value`) keep the
admin GET handler from having to peek into either column to render its public
shape.

Schema changes:

- ADD `value_encrypted BYTEA NULL` — Fernet ciphertext for sensitive rows.
  NULL for non-sensitive rows and for sensitive rows that haven't been seeded.
- ADD `sensitive BOOLEAN NOT NULL DEFAULT FALSE` — set per-key by the
  validators registry; flips the storage column and the public shape.
- ADD `has_value BOOLEAN NOT NULL DEFAULT FALSE` — true when either `value`
  or `value_encrypted` is populated. The API layer sets it on PUT/generate;
  this column lets `GET /admin/settings` render `has_value` without reading
  the ciphertext.
- ALTER `value` to NULLABLE — sensitive rows store NULL there. Existing
  M002 keys (`workspace_volume_size_gb`, `idle_timeout_seconds`) keep
  non-null `value` payloads and `sensitive=false`, so back-compat holds.

Downgrade reverses cleanly. No backfill is needed because:

- Existing M002 rows never set `value_encrypted` so dropping it is a no-op.
- `sensitive` and `has_value` server-default to FALSE; existing rows take
  that default on upgrade and the columns vanish on downgrade.
- All M002 rows have non-null `value` today, so re-tightening `value` to
  NOT NULL on downgrade can't fail.
"""
import logging

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "s06_system_settings_sensitive"
down_revision = "s05_system_settings"
branch_labels = None
depends_on = None


logger = logging.getLogger("alembic.runtime.migration.s06")


def upgrade():
    op.add_column(
        "system_settings",
        sa.Column("value_encrypted", sa.LargeBinary(), nullable=True),
    )
    logger.info("S06 migration: added system_settings.value_encrypted (BYTEA NULL)")

    op.add_column(
        "system_settings",
        sa.Column(
            "sensitive",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    logger.info(
        "S06 migration: added system_settings.sensitive (BOOL NOT NULL default false)"
    )

    op.add_column(
        "system_settings",
        sa.Column(
            "has_value",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    logger.info(
        "S06 migration: added system_settings.has_value (BOOL NOT NULL default false)"
    )

    op.alter_column("system_settings", "value", nullable=True)
    logger.info(
        "S06 migration: relaxed system_settings.value to NULLABLE (sensitive rows store NULL there)"
    )


def downgrade():
    op.alter_column("system_settings", "value", nullable=False)
    logger.info("S06 downgrade: re-tightened system_settings.value to NOT NULL")

    op.drop_column("system_settings", "has_value")
    logger.info("S06 downgrade: dropped system_settings.has_value")

    op.drop_column("system_settings", "sensitive")
    logger.info("S06 downgrade: dropped system_settings.sensitive")

    op.drop_column("system_settings", "value_encrypted")
    logger.info("S06 downgrade: dropped system_settings.value_encrypted")
