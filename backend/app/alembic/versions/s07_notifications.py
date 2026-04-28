"""S07 notifications + notification_preferences: M005/S02 in-app channel substrate

Revision ID: s07_notifications
Revises: s06e_github_webhook_events
Create Date: 2026-04-28 03:00:00.000000

Backs the M005/S02 in-app notification channel. Two tables persist the
notification stream and the per-user routing preferences. The
``app.core.notify(user_id, kind, payload, source_*)`` helper inserts into
``notifications`` only when the resolved preference for (user, workflow_id,
event_type) has ``in_app=True``; the route handlers read from
``notifications`` for list / mark-read / unread-count and from
``notification_preferences`` for the settings-page toggles.

Schema:

- notifications
  - id UUID PK — opaque internal handle
  - user_id UUID NOT NULL FK→user(id) ON DELETE CASCADE — recipient. CASCADE
    so deleting a user purges their notification stream.
  - kind VARCHAR(64) NOT NULL CHECK IN (
      'workflow_run_started','workflow_run_succeeded','workflow_run_failed',
      'workflow_step_completed','team_invite_accepted','project_created',
      'system'
    ) — discriminator for rendering and preference matching. The CHECK is
    the storage-layer guard; ``NotificationKind`` is the application-layer
    enum (kept in sync — see ``backend/app/models.py``).
  - payload JSONB NOT NULL DEFAULT '{}' — free-form rendering data. The
    ``notify()`` helper redacts any key whose lowercase name contains
    ``email``, ``token``, ``secret``, or ``password`` before insert.
  - read_at TIMESTAMPTZ NULL — NULL = unread. Set by the mark-read route.
  - created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
  - source_team_id UUID NULL FK→team(id) ON DELETE SET NULL — losing the
    source team must not destroy the audit trail of notifications already
    delivered, so we NULL the column instead of cascading.
  - source_project_id UUID NULL FK→projects(id) ON DELETE SET NULL — same
    reasoning as source_team_id.
  - source_workflow_run_id UUID NULL — NO FK because the ``workflow_run``
    table does not exist yet. The FK-add is deferred to whichever future
    slice ships the workflow engine; this column is forward-compatible.

  Indexes:
  - ix_notifications_user_id_created_at (user_id, created_at DESC) — backs
    the panel's chronological listing.
  - ix_notifications_unread_count (user_id, read_at) WHERE read_at IS NULL
    — partial index, backs the badge query without scanning read rows.

- notification_preferences
  - id UUID PK — synthetic ORM handle. The business uniqueness contract is
    the UNIQUE INDEX described below; the PK exists so SQLAlchemy ORM has
    a non-nullable identity column to map (workflow_id is NULL for
    team-default rows, which would otherwise break ORM identity).
  - user_id UUID NOT NULL FK→user(id) ON DELETE CASCADE — owning user.
  - workflow_id UUID NULL — NO FK target yet (same workflow_run deferral as
    ``source_workflow_run_id``). NULL = team-default for that event_type;
    a specific UUID = per-workflow override (UI ships in a later slice).
  - event_type VARCHAR(64) NOT NULL CHECK IN the seven kinds above.
  - in_app BOOLEAN NOT NULL DEFAULT TRUE — route the event to the bell?
  - push BOOLEAN NOT NULL DEFAULT FALSE — reserved for later push channel.
  - created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
  - updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()

  Business uniqueness via a UNIQUE INDEX on (user_id, COALESCE(workflow_id,
  '00000000-0000-0000-0000-000000000000'::uuid), event_type). A standard
  PRIMARY KEY / UNIQUE CONSTRAINT cannot wrap a COALESCE expression — but a
  CREATE UNIQUE INDEX can. The all-zeros UUID stands in for "team-default"
  so two NULL workflow rows for the same (user, event_type) collide.

Defaults are NOT seeded into upgrade(): no row → "use default" is the
read-time semantics in T02's preferences GET. Rows are only created when a
user explicitly toggles a preference, keeping the preferences table small
for users who never visit the settings tab.

Downgrade drops both tables in dependency order (mirrors s06d/s06e).
"""
import logging

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "s07_notifications"
down_revision = "s06e_github_webhook_events"
branch_labels = None
depends_on = None


logger = logging.getLogger("alembic.runtime.migration.s07")


_NOTIFICATION_KIND_VALUES = (
    "workflow_run_started",
    "workflow_run_succeeded",
    "workflow_run_failed",
    "workflow_step_completed",
    "team_invite_accepted",
    "project_created",
    "system",
)
_NOTIFICATION_KIND_CHECK = "kind IN (" + ", ".join(
    f"'{v}'" for v in _NOTIFICATION_KIND_VALUES
) + ")"
_PREFERENCE_KIND_CHECK = "event_type IN (" + ", ".join(
    f"'{v}'" for v in _NOTIFICATION_KIND_VALUES
) + ")"


def upgrade():
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "read_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("source_team_id", sa.Uuid(), nullable=True),
        sa.Column("source_project_id", sa.Uuid(), nullable=True),
        # NOTE: source_workflow_run_id has NO FK — workflow_run table does
        # not exist yet. The FK-add is deferred to whichever future slice
        # ships the workflow engine.
        sa.Column("source_workflow_run_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name="fk_notifications_user_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_team_id"],
            ["team.id"],
            name="fk_notifications_source_team_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_project_id"],
            ["projects.id"],
            name="fk_notifications_source_project_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
        sa.CheckConstraint(
            _NOTIFICATION_KIND_CHECK,
            name="ck_notifications_kind",
        ),
    )
    op.create_index(
        "ix_notifications_user_id_created_at",
        "notifications",
        ["user_id", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_notifications_unread_count",
        "notifications",
        ["user_id", "read_at"],
        unique=False,
        postgresql_where=sa.text("read_at IS NULL"),
    )

    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        # NOTE: workflow_id has NO FK target yet — same workflow_run
        # deferral as source_workflow_run_id above. NULL = team-default.
        sa.Column("workflow_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column(
            "in_app",
            sa.Boolean(),
            server_default=sa.text("TRUE"),
            nullable=False,
        ),
        sa.Column(
            "push",
            sa.Boolean(),
            server_default=sa.text("FALSE"),
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
            ["user_id"],
            ["user.id"],
            name="fk_notification_preferences_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notification_preferences"),
        sa.CheckConstraint(
            _PREFERENCE_KIND_CHECK,
            name="ck_notification_preferences_event_type",
        ),
    )
    # Postgres PRIMARY KEY does not accept a COALESCE expression, so we
    # encode the (user_id, workflow_id-or-team-default, event_type)
    # uniqueness via a UNIQUE INDEX on the COALESCE(workflow_id, zero-uuid)
    # form. The all-zeros UUID stands in for "team-default".
    op.execute(
        """
        CREATE UNIQUE INDEX ix_notification_preferences_pk
        ON notification_preferences (
            user_id,
            COALESCE(workflow_id, '00000000-0000-0000-0000-000000000000'::uuid),
            event_type
        )
        """
    )
    logger.info(
        "s07_notifications upgrade complete tables=2 indexes=3"
    )


def downgrade():
    op.drop_index(
        "ix_notification_preferences_pk",
        table_name="notification_preferences",
    )
    op.drop_table("notification_preferences")
    op.drop_index(
        "ix_notifications_unread_count",
        table_name="notifications",
    )
    op.drop_index(
        "ix_notifications_user_id_created_at",
        table_name="notifications",
    )
    op.drop_table("notifications")
    logger.info(
        "s07_notifications downgrade: dropped notification_preferences "
        "and notifications tables"
    )
