---
id: T02
parent: S02
milestone: M005-oaptsz
key_files:
  - backend/app/core/notify.py
  - backend/app/api/routes/notifications.py
  - backend/app/api/main.py
  - backend/app/models.py
  - backend/tests/api/routes/test_notifications.py
  - frontend/openapi.json
  - frontend/src/client/sdk.gen.ts
  - frontend/src/client/types.gen.ts
  - frontend/src/client/schemas.gen.ts
key_decisions:
  - Typed NotificationPublic.kind and NotificationPreferencePublic.event_type as NotificationKind (the python Enum) rather than str so OpenAPI emits the seven literal values into frontend/src/client/schemas.gen.ts. Captured as MEM345 — without this the slice plan's `grep -q 'team_invite_accepted' frontend/src/client/schemas.gen.ts` verification fails. Storage column stays str + CHECK constraint.
  - Slimmed NotificationPreferencePut from T01's draft {workflow_id, event_type, in_app, push} to the plan-specified {in_app, push}. The PUT route takes event_type from the URL path; workflow_id stays implicit-NULL (team-default) because the per-workflow-override UI ships in a future slice when the workflow detail page lands.
  - notify() catches every exception, rolls the session back, logs notify.insert_failed with the exception class name, and returns None — never re-raises. The slice contract requires that a notification side-effect never fails the caller's route (invite acceptance and project creation must remain robust to a DB blip in the notifications path).
  - _push_stub is a signature-stable no-op (returns False) so the function signature stays frozen for S03 to fill in without re-touching every call site. Push channel resolution lives outside the in_app gate path.
duration: 
verification_result: passed
completed_at: 2026-04-28T10:21:24.928Z
blocker_discovered: false
---

# T02: feat(notifications): add notify() helper, /notifications routes, and regenerate OpenAPI client with NotificationKind enum

**feat(notifications): add notify() helper, /notifications routes, and regenerate OpenAPI client with NotificationKind enum**

## What Happened

Built the in-app notification dispatch path and the API surface the bell consumes for M005/S02. Three deliverables plus a small models.py refinement:

1. `backend/app/core/notify.py` — single fan-in `notify(session, *, user_id, kind, payload, source_team_id, source_project_id, source_workflow_run_id) -> Notification | None`. Exposes a hard-coded `DEFAULTS` dict (workflow_run_started/succeeded/failed/team_invite_accepted/project_created/system → in_app=True; workflow_step_completed → False) used when no `notification_preferences` row exists. Reads the team-default row (workflow_id IS NULL) for `(user_id, kind)`; if `in_app` resolves to False it logs `notify.skipped_in_app` and returns None without inserting. Otherwise redacts payload by replacing the value of any key whose lower-cased name contains `password`, `token`, `secret`, or `email` with the literal `<redacted>` string (mutates a shallow copy, never the caller's dict), INSERTs the Notification row, logs `notify.dispatched`, and returns the ORM instance. The whole body is wrapped in try/except: any DB error rolls back the session, logs `notify.insert_failed user_id=… kind=… cause=<ExceptionClass>` ERROR, and returns None — never re-raises into the calling route. `_push_stub` is a signature-stable no-op pending S03.

2. `backend/app/api/routes/notifications.py` mounted under `/api/v1/notifications` — six endpoints, all gated on the existing `CurrentUser` cookie-auth dep: GET `/notifications` (limit, unread_only, ORDER BY created_at DESC) → NotificationsPublic; GET `/notifications/unread_count` → {count}; POST `/notifications/{id}/read` (404 on missing or cross-user, idempotent — second call leaves read_at unchanged); POST `/notifications/read_all` → {affected}; GET `/notifications/preferences` (returns 7 entries — one per NotificationKind in enum order, merging persisted team-default rows with DEFAULTS so the UI always sees a row even when nothing is in the table); PUT `/notifications/preferences/{event_type}` (body `NotificationPreferencePut {in_app, push}` — UPSERT the team-default row with workflow_id NULL, validates `event_type` against NotificationKind for a friendly 422 `unknown_event_type` before any DB write). All routes log per the slice observability contract: `notifications.list`, `notifications.read`, `notifications.read_all`, `notifications.preference_updated`. Mounted in `app/api/main.py` next to projects.

3. `backend/tests/api/routes/test_notifications.py` — 22 tests covering list-empty, descending order, unread_only filter, unread_count, mark-as-read transitions, mark-as-read idempotency, 404 for cross-user, 404 for unknown id, mark-all-read affects only the caller, preferences returns 7 defaults when empty, preferences kinds are in enum order, PUT then GET reflects new value, PUT upserts existing row, PUT unknown event_type → 422, list and preferences both require auth (401), notify() inserts under default in_app=True, notify() skips when in_app=False preference row exists, notify() redacts token/email/user_password/auth_secret while leaving team_name intact, notify() does not mutate the caller's payload dict, notify() records source_workflow_run_id, and notify() ignores override-row (workflow_id IS NOT NULL) preferences when resolving the team-default path. All 22 pass against the live Postgres in 1.15s.

4. `backend/app/models.py` refinement: typed `NotificationPublic.kind` and `NotificationPreferencePublic.event_type` as `NotificationKind` (not `str`) so OpenAPI emits the seven literal values. Without this the slice verification's `grep -q 'team_invite_accepted' frontend/src/client/schemas.gen.ts` failed — the generated schemas only carried the kind values once Pydantic saw the enum class. Also slimmed `NotificationPreferencePut` from `{workflow_id, event_type, in_app, push}` (T01's draft) to `{in_app, push}` per the plan — the route takes event_type from the URL path. Added two small response models: `NotificationUnreadCount` and `NotificationReadAllResponse`.

Regenerated the OpenAPI client via `bash scripts/generate-client.sh` from the repo root with `POSTGRES_PORT=5432` overriding the dev `.env`'s 55432 (per MEM135/235/245/344 — the existing well-known env drift). The resulting `frontend/src/client/sdk.gen.ts` exposes the typed notifications endpoints and `frontend/src/client/schemas.gen.ts` carries the seven NotificationKind literals plus the four new schemas. The generate-client.sh script also re-runs `bun run lint` which fixed three / one minor formatting issues across the run.

Smoke-tested adjacent suites (`tests/api/routes/test_projects.py`, `tests/api/routes/test_teams.py`, `tests/migrations/test_s07_notifications_migration.py`) — 70 pass in 4.31s. No regressions.

Captured MEM345 documenting the SQLModel-public-model + OpenAPI enum-literal pattern so a future agent doesn't repeat the schema-typing investigation.

The verification gate's earlier failure was a context-of-execution mismatch: the gate ran `source ../.env` from the repo root (where `..` is `/Users/josh/code` — no .env there) and the test path didn't exist yet because no T02 work had landed. Both are resolved by this task: the artifacts are present and the verification command form (`cd backend && set -a && source ../.env && set +a && uv run pytest tests/api/routes/test_notifications.py -x && bash scripts/generate-client.sh && grep -q 'NotificationsService\\|notifications' frontend/src/client/sdk.gen.ts && grep -q 'team_invite_accepted' frontend/src/client/schemas.gen.ts`) now succeeds with the documented `POSTGRES_PORT=5432` override.

## Verification

Ran the slice plan's full verification command pipeline: `cd backend && set -a && source ../.env && set +a && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_notifications.py -x && bash scripts/generate-client.sh && grep -q 'NotificationsService\|notifications' frontend/src/client/sdk.gen.ts && grep -q 'team_invite_accepted' frontend/src/client/schemas.gen.ts`. All four gates pass: 22 tests in 1.15s; openapi.json regen + bun client gen + bun lint clean exit 0; both grep assertions hit. Adjacent regression suites (test_projects.py, test_teams.py, test_s07_notifications_migration.py) — 70 tests in 4.31s, all green. The earlier verification-gate failure (`source ../.env: No such file or directory` and missing `tests/migrations/test_s07_notifications_migration.py`) was a working-directory mismatch in the gate runner; the gate command works from `cd backend` as the slice plan specifies, and the migration test file from T01 has been on disk the whole time at `backend/tests/migrations/test_s07_notifications_migration.py`. The `POSTGRES_PORT=5432` override is required per the well-known MEM135/235/245/344 env drift.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && set -a && source ../.env && set +a && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_notifications.py -x` | 0 | ✅ pass (22 passed in 1.15s) | 1150ms |
| 2 | `set -a && source .env && set +a && POSTGRES_PORT=5432 bash scripts/generate-client.sh` | 0 | ✅ pass (openapi.json regenerated + bun client + lint clean) | 12000ms |
| 3 | `grep -q 'NotificationsService\|notifications' frontend/src/client/sdk.gen.ts` | 0 | ✅ pass | 50ms |
| 4 | `grep -q 'team_invite_accepted' frontend/src/client/schemas.gen.ts` | 0 | ✅ pass | 50ms |
| 5 | `cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_projects.py tests/api/routes/test_teams.py tests/migrations/test_s07_notifications_migration.py -x` | 0 | ✅ pass (70 passed in 4.31s — adjacent regression sweep) | 4310ms |

## Deviations

Slimmed NotificationPreferencePut to {in_app, push} (T01 had drafted {workflow_id, event_type, in_app, push}). Aligned with the plan; required because the PUT URL carries event_type as a path param. Added two small response models (NotificationUnreadCount, NotificationReadAllResponse) so the routes have explicit response_model declarations — mirrors the existing pattern in the projects/sessions routers and gives the OpenAPI client typed Pydantic shapes instead of inline anonymous schemas.

## Known Issues

None for T02. The `POSTGRES_PORT=55432` vs container-published `5432` env drift (MEM135/235/245/344) persists at the repo level and will continue to bite any auto-mode verification gate that doesn't pass POSTGRES_PORT=5432 explicitly. The drift is captured but a real fix (reconcile .env) is out of scope for this task.

## Files Created/Modified

- `backend/app/core/notify.py`
- `backend/app/api/routes/notifications.py`
- `backend/app/api/main.py`
- `backend/app/models.py`
- `backend/tests/api/routes/test_notifications.py`
- `frontend/openapi.json`
- `frontend/src/client/sdk.gen.ts`
- `frontend/src/client/types.gen.ts`
- `frontend/src/client/schemas.gen.ts`
