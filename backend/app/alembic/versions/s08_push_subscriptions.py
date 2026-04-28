"""S08 push_subscriptions: M005/S03 Web Push channel substrate

Revision ID: s08_push_subscriptions
Revises: s07_notifications
Create Date: 2026-04-28 04:30:00.000000

Backs the M005/S03 Web Push notification channel. A single ``push_subscriptions``
table persists every browser/device that has granted notification permission
and registered with the backend via POST /push/subscribe. The
``app.core.notify`` push fan-out (T05) reads rows here when a user's resolved
preference for (workflow_id, event_type) has ``push=True``.

Schema:

- push_subscriptions
  - id UUID PK — opaque internal handle.
  - user_id UUID NOT NULL FK→user(id) ON DELETE CASCADE — owning user.
    A single user can have many rows: phone + laptop + tablet each register
    independently. CASCADE so deleting a user purges every device.
  - endpoint TEXT NOT NULL — the Mozilla Push Service / FCM / APNs Web URL
    the browser handed us at subscribe time. Treated as opaque secret in
    log surfaces (only sha256[:8] is ever emitted).
  - keys JSONB NOT NULL — the ``{p256dh, auth}`` blob extracted from the
    browser ``PushSubscription.toJSON()`` shape. Required by the pywebpush
    encryption pipeline.
  - user_agent VARCHAR(500) NULL — best-effort device hint for the operator
    "manage my devices" UI; truncated to 500 chars at the API boundary.
  - created_at TIMESTAMPTZ NOT NULL DEFAULT NOW().
  - last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW() — bumped on each
    successful pywebpush delivery; lets the operator UI render "active in
    last 30d" affordances.
  - last_status_code INTEGER NULL — last HTTP status from the upstream push
    service. NULL until the dispatcher has fired at least once.
  - consecutive_failures INTEGER NOT NULL DEFAULT 0 — incremented on 5xx;
    pruned at 5 by the dispatcher (T04). Reset to 0 on a 2xx delivery.
  - UNIQUE(user_id, endpoint) — same browser re-subscribing is an upsert,
    not a duplicate.

Indexes:
  - ix_push_subscriptions_user_id (user_id) — backs the dispatcher's per-user
    fan-out lookup. The UNIQUE(user_id, endpoint) constraint already creates
    a btree usable for ``WHERE user_id = ?`` lookups, but a plain user_id
    index keeps the planner honest and matches the hot-path access pattern.

Downgrade drops index then table (mirrors s07's ordering).
"""
import logging

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "s08_push_subscriptions"
down_revision = "s07_notifications"
branch_labels = None
depends_on = None


logger = logging.getLogger("alembic.runtime.migration.s08")


def upgrade():
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column(
            "keys",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name="fk_push_subscriptions_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_push_subscriptions"),
        sa.UniqueConstraint(
            "user_id",
            "endpoint",
            name="uq_push_subscriptions_user_id_endpoint",
        ),
    )
    op.create_index(
        "ix_push_subscriptions_user_id",
        "push_subscriptions",
        ["user_id"],
        unique=False,
    )
    logger.info(
        "s08_push_subscriptions upgrade complete tables=1 indexes=1"
    )


def downgrade():
    op.drop_index(
        "ix_push_subscriptions_user_id",
        table_name="push_subscriptions",
    )
    op.drop_table("push_subscriptions")
    logger.info(
        "s08_push_subscriptions downgrade: dropped push_subscriptions table"
    )
