---
estimated_steps: 1
estimated_files: 3
skills_used: []
---

# T01: Add notifications + notification_preferences alembic migration and SQLModel models

Create the persistence substrate for the in-app notification channel. New alembic revision `s07_notifications` (down_revision = `s06e_github_webhook_events`) creates two tables. `notifications`: id UUID PK, user_id FK→user(id) ON DELETE CASCADE, kind VARCHAR(64) NOT NULL with a CHECK IN the seven enum values, payload JSONB NOT NULL DEFAULT '{}', read_at TIMESTAMPTZ NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), source_team_id UUID NULL FK→team(id) ON DELETE SET NULL, source_project_id UUID NULL FK→projects(id) ON DELETE SET NULL, source_workflow_run_id UUID NULL — NO FK because workflow_run table does not exist yet; a comment in the migration notes the FK-add is deferred to whichever future slice ships the workflow engine. Indexes: (user_id, created_at DESC) and a partial index on (user_id, read_at) WHERE read_at IS NULL for unread_count perf. `notification_preferences`: user_id UUID NOT NULL FK→user(id) ON DELETE CASCADE, workflow_id UUID NULL — NO FK target yet, same deferral note; event_type VARCHAR(64) NOT NULL with CHECK IN the seven kinds; in_app BOOLEAN NOT NULL DEFAULT TRUE; push BOOLEAN NOT NULL DEFAULT FALSE; created_at + updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(). Uniqueness via `CREATE UNIQUE INDEX ix_notification_preferences_pk ON notification_preferences(user_id, COALESCE(workflow_id, '00000000-0000-0000-0000-000000000000'::uuid), event_type)` because Postgres PRIMARY KEY does not accept a COALESCE expression. Downgrade drops both tables in dependency order. SQLModel additions in `backend/app/models.py`: `class NotificationKind(str, enum.Enum)` with the seven values: workflow_run_started, workflow_run_succeeded, workflow_run_failed, workflow_step_completed, team_invite_accepted, project_created, system; `class Notification(SQLModel, table=True)`, `class NotificationPublic(SQLModel)`, `class NotificationsPublic(SQLModel)` (data: list[NotificationPublic], count: int), `class NotificationPreference(SQLModel, table=True)`, `class NotificationPreferencePublic(SQLModel)`, `class NotificationPreferencePut(SQLModel)`. Follow the s06d_projects_and_push_rules migration shape (header docstring describing schema, logger.getLogger('alembic.runtime.migration.s07'), upgrade/downgrade body). Defaults are NOT seeded into the upgrade — they are merged at read time in T02's preferences GET so existing users start with 'no row → use default' state and rows are only created when they explicitly toggle. Migration test: `backend/tests/migrations/test_s07_notifications_migration.py` mirroring s01_migration's autouse session-release fixture (commits, expires_all, closes, engine.dispose) per MEM016 to avoid the alembic-DDL deadlock. Asserts both tables and the partial unread-count index exist; asserts the unique-index workflow_id NULL coalesce contract; asserts downgrade is symmetric.

## Inputs

- ``backend/app/alembic/versions/s06e_github_webhook_events.py``
- ``backend/app/alembic/versions/s06d_projects_and_push_rules.py``
- ``backend/app/models.py``
- ``backend/tests/migrations/test_s01_migration.py``

## Expected Output

- ``backend/app/alembic/versions/s07_notifications.py``
- ``backend/app/models.py``
- ``backend/tests/migrations/test_s07_notifications_migration.py``

## Verification

cd backend && set -a && source ../.env && set +a && uv run alembic upgrade head && uv run pytest tests/migrations/test_s07_notifications_migration.py -x && uv run python -c "from app.models import Notification, NotificationPreference, NotificationKind; assert NotificationKind.system == 'system' and NotificationKind.team_invite_accepted == 'team_invite_accepted' and NotificationKind.workflow_run_failed == 'workflow_run_failed'"

## Observability Impact

Signals added: a single `logger.info('s07_notifications upgrade complete tables=2 indexes=3')` from the migration body so an operator running `alembic upgrade head` can grep the output for the row. How a future agent inspects this: `alembic current` shows s07 as head; `psql -c '\d notifications'` and `psql -c '\d notification_preferences'` describe the tables; the partial index is visible via `\di+ ix_notifications_unread_count`. Failure state exposed: alembic upgrade aborts on any DDL error and rolls back the transaction; the migration test surfaces the deadlock case (MEM016) explicitly.
