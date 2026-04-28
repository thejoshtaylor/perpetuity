---
id: T01
parent: S02
milestone: M005-oaptsz
key_files:
  - backend/app/alembic/versions/s07_notifications.py
  - backend/app/models.py
  - backend/tests/migrations/test_s07_notifications_migration.py
key_decisions:
  - Added a synthetic `id` UUID PK to `notification_preferences` so SQLModel ORM has a non-nullable identity column (workflow_id is NULL for team-default rows). The plan-listed COALESCE UNIQUE INDEX still owns the business uniqueness contract; the synthetic PK is purely an ORM concession.
  - Encoded the seven-value CHECK constraints with literal 'kind IN (...)' / 'event_type IN (...)' SQL strings rather than a Postgres ENUM type. Matches the s06d/s06e style and avoids the down_revision DROP TYPE complications a real ENUM would introduce later when adding new kinds.
  - Used `JSONB` (not generic JSON) for `notifications.payload` to match the existing `GitHubWebhookEvent.payload` precedent and to enable a future GIN index on payload keys without another migration.
  - Source-of-truth FK deferral: `source_workflow_run_id` and `notification_preferences.workflow_id` are UUID columns with NO FK because the `workflow_run` table does not exist yet. Documented inline; FK-add deferred to whichever future slice ships the workflow engine.
duration: 
verification_result: passed
completed_at: 2026-04-28T10:14:25.685Z
blocker_discovered: false
---

# T01: feat(notifications): add s07 alembic migration and SQLModel models for notifications + notification_preferences

**feat(notifications): add s07 alembic migration and SQLModel models for notifications + notification_preferences**

## What Happened

Created the persistence substrate for the M005/S02 in-app notification channel. Three deliverables:

1. **`backend/app/alembic/versions/s07_notifications.py`** — new alembic revision with `down_revision = "s06e_github_webhook_events"`. `upgrade()` builds two tables. `notifications`: `id` UUID PK, `user_id` FK→user CASCADE, `kind` VARCHAR(64) NOT NULL with a CHECK pinning it to the seven enum values, `payload` JSONB NOT NULL DEFAULT `'{}'::jsonb`, `read_at` TIMESTAMPTZ NULL, `created_at` TIMESTAMPTZ NOT NULL DEFAULT NOW(), `source_team_id` FK→team SET NULL, `source_project_id` FK→projects SET NULL, `source_workflow_run_id` UUID NULL with no FK (the `workflow_run` table doesn't exist yet — comment in the migration body documents the deferred FK-add). Two indexes: `ix_notifications_user_id_created_at` (user_id, created_at DESC) for the chronological panel, and the partial `ix_notifications_unread_count` (user_id, read_at) WHERE read_at IS NULL for the badge. `notification_preferences`: synthetic `id` UUID PK (added on top of the plan's listed columns so SQLModel ORM has a non-nullable identity column — the business uniqueness contract is the COALESCE-aware UNIQUE INDEX, not the PK), `user_id` FK→user CASCADE, `workflow_id` UUID NULL (NO FK, same workflow_run deferral), `event_type` VARCHAR(64) NOT NULL with a parallel CHECK on the same seven kinds, `in_app` BOOLEAN NOT NULL DEFAULT TRUE, `push` BOOLEAN NOT NULL DEFAULT FALSE, plus the standard timestamps. Uniqueness via raw `CREATE UNIQUE INDEX ix_notification_preferences_pk ON notification_preferences (user_id, COALESCE(workflow_id, '00000000-0000-0000-0000-000000000000'::uuid), event_type)` because Postgres PRIMARY KEY/UNIQUE CONSTRAINT can't wrap a COALESCE expression. `downgrade()` drops the indexes and tables in dependency order. Migration body emits the `s07_notifications upgrade complete tables=2 indexes=3` info log per the plan's Observability Impact section. No defaults are seeded — the read-time merge in T02's preferences GET handles the no-row-means-default semantics so the table stays empty for users who never visit the settings tab.

2. **`backend/app/models.py`** — appended `NotificationKind(str, enum.Enum)` with the seven values (workflow_run_started, workflow_run_succeeded, workflow_run_failed, workflow_step_completed, team_invite_accepted, project_created, system); `Notification(SQLModel, table=True)`, `NotificationPublic`, `NotificationsPublic` (data + count); `NotificationPreference(SQLModel, table=True)`, `NotificationPreferencePublic`, `NotificationPreferencePut` (the route's PUT body). Used the existing `JSONB` import for `payload` (matched `GitHubWebhookEvent`'s pattern) and the `CheckConstraint`/`Column`/`DateTime` imports already in scope. Annotated `NotificationPreference.id` as the synthetic ORM PK and documented the COALESCE UNIQUE INDEX as the business uniqueness contract so the next agent doesn't re-architect it.

3. **`backend/tests/migrations/test_s07_notifications_migration.py`** — new module mirroring the s06d test's MEM016 autouse fixture pattern (commit + expire + close the session-scoped `db`, then `engine.dispose()` to free pool connections before alembic DDL, with a `_restore_head_after` that always re-upgrades on teardown). Twelve test functions covering: column shape and FK delete actions on both tables; indexes exist (chronological with `created_at DESC`, partial `WHERE read_at IS NULL`, COALESCE UNIQUE); the team-default collision contract (two NULL-workflow_id rows for the same (user, event_type) raise IntegrityError); the override coexistence contract (NULL row + UUID-override row for the same (user, event_type) coexist); CHECK constraint rejection of bad `kind` and bad `event_type`; parametrized acceptance of all seven kinds; user-delete cascading to both tables; team-delete SET-NULL'ing `source_team_id` while preserving the notification row; downgrade dropping both tables; round-trip schema-byte-identical assertion. 19 collected tests after parametrization, all green.

**Deviations from the plan:**

- Added a synthetic `id` UUID PK column to `notification_preferences` (not in the plan's column list). Reason: SQLModel `table=True` requires `primary_key=True` on at least one Field, but the plan's only candidate composite (user_id, workflow_id, event_type) has a NULL-able `workflow_id` — SQLAlchemy ORM rejects NULL identity. The synthetic `id` keeps the ORM working without weakening the COALESCE UNIQUE INDEX uniqueness contract; route upserts SELECT by (user_id, workflow_id, event_type) and UPDATE-or-INSERT, never by `id`. Documented the choice inline in both the migration docstring and the model class.

- Captured MEM344 documenting the host-side `POSTGRES_PORT` drift (dev `.env` says 55432, `compose.override.yml` publishes 5432). Future agents running `alembic upgrade` from the host will hit "connection refused 127.0.0.1:55432" without the override; the workaround is `export POSTGRES_PORT=5432` before the alembic call.

**Slice verification status (T01-relevant only):** Migration upgrade clean, partial unread-count index visible via `pg_indexes`, tables describable via `\d`. Runtime API logs / poll-tick / redaction live in T02-T05.

## Verification

Ran the task plan's three-command verification pipeline against the live Postgres (with `POSTGRES_PORT=5432` overriding the dev `.env`'s 55432 to match the published compose port — see captured MEM344). All three pass: `uv run alembic upgrade head` applies s07_notifications and emits the `tables=2 indexes=3` info log; `uv run pytest tests/migrations/test_s07_notifications_migration.py -x` runs 19 tests in 0.91s with 19 passed; `uv run python -c "from app.models import Notification, NotificationPreference, NotificationKind; assert NotificationKind.system == 'system' and NotificationKind.team_invite_accepted == 'team_invite_accepted' and NotificationKind.workflow_run_failed == 'workflow_run_failed'"` exits 0. Cross-checked the live schema with `docker exec perpetuity-db-1 psql ...` — `\d notifications` shows the expected nine columns, three indexes (pk + chronological DESC + partial unread-count WHERE read_at IS NULL), the seven-value `ck_notifications_kind` CHECK, and the three FKs with the expected ON DELETE actions (CASCADE on user, SET NULL on team and project). `\d notification_preferences` confirms the synthetic `id` PK, the parallel seven-value `ck_notification_preferences_event_type` CHECK, `in_app DEFAULT TRUE` / `push DEFAULT FALSE`, and the COALESCE-aware `ix_notification_preferences_pk` UNIQUE INDEX with the literal `'00000000-0000-0000-0000-000000000000'::uuid` sentinel. `alembic current` returns `s07_notifications (head)`.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && POSTGRES_PORT=5432 uv run alembic upgrade head` | 0 | ✅ pass | 2400ms |
| 2 | `cd backend && POSTGRES_PORT=5432 uv run pytest tests/migrations/test_s07_notifications_migration.py -x` | 0 | ✅ pass (19 passed in 0.91s) | 910ms |
| 3 | `cd backend && POSTGRES_PORT=5432 uv run python -c "from app.models import Notification, NotificationPreference, NotificationKind; assert NotificationKind.system == 'system' and NotificationKind.team_invite_accepted == 'team_invite_accepted' and NotificationKind.workflow_run_failed == 'workflow_run_failed'"` | 0 | ✅ pass | 850ms |
| 4 | `cd backend && POSTGRES_PORT=5432 uv run alembic current` | 0 | ✅ pass (s07_notifications (head)) | 820ms |
| 5 | `docker exec perpetuity-db-1 psql -U postgres -d app -c '\d notifications'` | 0 | ✅ pass (9 cols, 3 indexes incl. partial WHERE read_at IS NULL, CHECK + 3 FKs) | 150ms |
| 6 | `docker exec perpetuity-db-1 psql -U postgres -d app -c '\d notification_preferences'` | 0 | ✅ pass (8 cols, COALESCE UNIQUE INDEX present, CHECK on event_type) | 120ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `backend/app/alembic/versions/s07_notifications.py`
- `backend/app/models.py`
- `backend/tests/migrations/test_s07_notifications_migration.py`
