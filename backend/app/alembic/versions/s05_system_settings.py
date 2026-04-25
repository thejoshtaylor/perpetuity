"""S05 system_settings: generic key/value store for admin-tunable settings

Revision ID: s05_system_settings
Revises: s04_workspace_volume
Create Date: 2026-04-25 04:00:00.000000

Creates the `system_settings` table that backs the system-wide admin settings
API landing in M002/S03 (D015). Generic key/value: any future system-tunable
knob lives here, keyed by a short string and carrying a JSONB payload. The
canonical first key is `workspace_volume_size_gb`, which replaces the
orchestrator's hardcoded `default_volume_size_gb=4`.

Schema:

- key VARCHAR(255) NOT NULL PRIMARY KEY — short identifier
  (e.g. 'workspace_volume_size_gb'); PK on `key` covers the only lookup pattern,
  so no additional indexes are needed
- value JSONB NOT NULL — opaque payload; per-key validators in the API layer
  enforce shape; logs MUST NOT echo this column verbatim because future keys
  could carry secrets
- updated_at TIMESTAMPTZ NULL (default applied by app via get_datetime_utc)

No FKs — system_settings is system-wide, not user-scoped.

Downgrade is fully reversible: drops the table (which removes the PK index
implicitly).
"""
import logging

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "s05_system_settings"
down_revision = "s04_workspace_volume"
branch_labels = None
depends_on = None


logger = logging.getLogger("alembic.runtime.migration.s05")


def upgrade():
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )
    logger.info(
        "S05 migration: created system_settings table (key PK, value JSONB)"
    )


def downgrade():
    op.drop_table("system_settings")
    logger.info("S05 downgrade: dropped system_settings table")
