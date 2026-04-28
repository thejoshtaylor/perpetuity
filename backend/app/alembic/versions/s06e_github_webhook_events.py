"""S06e github_webhook_events + webhook_rejections: M004/S05 webhook receiver substrate

Revision ID: s06e_github_webhook_events
Revises: s06d_projects_and_push_rules
Create Date: 2026-04-27 17:50:00.000000

Backs the M004/S05 GitHub webhook receiver. The route at
``POST /api/v1/github/webhooks`` HMAC-verifies incoming deliveries against
the Fernet-decrypted ``github_app_webhook_secret`` row in ``system_settings``
(key constant: ``GITHUB_APP_WEBHOOK_SECRET_KEY``) and, on success, persists
the verified delivery to ``github_webhook_events`` before invoking the
no-op ``dispatch_github_event`` hook (M004 stub; M005 fills the body).

Schema:

- github_webhook_events
  - id UUID PK — opaque internal handle
  - installation_id BIGINT NULL FK→github_app_installations(installation_id)
    ON DELETE SET NULL — losing an installation must not destroy the audit
    trail of webhooks GitHub already sent us, so we NULL the column instead
    of cascading
  - event_type VARCHAR(64) NOT NULL — GitHub's ``X-GitHub-Event`` header
  - delivery_id VARCHAR(64) NOT NULL UNIQUE — GitHub's
    ``X-GitHub-Delivery`` header. UNIQUE is the storage-layer enforcement
    of GitHub's 24h retry idempotency contract (D025 / MEM229); the route
    relies on the DB to enforce it via ``INSERT ... ON CONFLICT DO NOTHING``
  - payload JSONB NOT NULL — full request body, kept for replay/debug;
    never logged (only persisted)
  - received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
  - dispatch_status VARCHAR(32) NOT NULL DEFAULT 'noop' — outcome of the
    dispatch hook ('noop' in M004; M005 widens to 'ok'/'failed')
  - dispatch_error TEXT NULL — short scrubbed error on dispatch failure

- webhook_rejections
  - id UUID PK
  - delivery_id VARCHAR(64) NULL — header may be absent on a malformed
    request; we still want a row
  - signature_present BOOLEAN NOT NULL — was ``X-Hub-Signature-256`` set?
  - signature_valid BOOLEAN NOT NULL — did HMAC compare succeed? (always
    FALSE for a row in this table by construction)
  - source_ip VARCHAR(64) NOT NULL — client IP, for abuse forensics
  - received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()

Downgrade drops ``webhook_rejections`` first then ``github_webhook_events``
(no FK between them, but mirrors the s06d ordering for consistency).
"""
import logging

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "s06e_github_webhook_events"
down_revision = "s06d_projects_and_push_rules"
branch_labels = None
depends_on = None


logger = logging.getLogger("alembic.runtime.migration.s06e")


def upgrade():
    op.create_table(
        "github_webhook_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("delivery_id", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "dispatch_status",
            sa.String(length=32),
            server_default=sa.text("'noop'"),
            nullable=False,
        ),
        sa.Column("dispatch_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["github_app_installations.installation_id"],
            name="fk_github_webhook_events_installation_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_github_webhook_events"),
        sa.UniqueConstraint(
            "delivery_id", name="uq_github_webhook_events_delivery_id"
        ),
    )
    logger.info(
        "S06e migration: created github_webhook_events (UUID PK, "
        "installation_id BIGINT FK SET NULL, UNIQUE(delivery_id) for "
        "GitHub 24h retry idempotency, payload JSONB, "
        "dispatch_status DEFAULT 'noop')"
    )

    op.create_table(
        "webhook_rejections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("delivery_id", sa.String(length=64), nullable=True),
        sa.Column("signature_present", sa.Boolean(), nullable=False),
        sa.Column("signature_valid", sa.Boolean(), nullable=False),
        sa.Column("source_ip", sa.String(length=64), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_webhook_rejections"),
    )
    logger.info(
        "S06e migration: created webhook_rejections (UUID PK, delivery_id "
        "NULLABLE, signature_present/signature_valid BOOL, source_ip)"
    )


def downgrade():
    op.drop_table("webhook_rejections")
    op.drop_table("github_webhook_events")
    logger.info(
        "S06e downgrade: dropped webhook_rejections and "
        "github_webhook_events tables"
    )
