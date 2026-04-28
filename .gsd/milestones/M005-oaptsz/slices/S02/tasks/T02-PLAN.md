---
estimated_steps: 1
estimated_files: 8
skills_used: []
---

# T02: Implement notify() helper, notifications + preferences REST routes, regenerate OpenAPI client

Build the in-app notification dispatch path and the API surface the bell consumes. Create `backend/app/core/notify.py` exposing `notify(session: Session, *, user_id: UUID, kind: NotificationKind, payload: dict | None = None, source_team_id: UUID | None = None, source_project_id: UUID | None = None, source_workflow_run_id: UUID | None = None) -> Notification | None`. Behavior: (1) compute the effective preference by reading `notification_preferences` for `(user_id, workflow_id IS NULL, event_type=kind.value)` — there is no workflow_run→workflow lookup wired today (no engine), so source_workflow_run_id is recorded on the row but not used for preference resolution; if no preference row exists, fall back to the hard-coded DEFAULTS dict ({workflow_run_started: in_app=True, workflow_run_succeeded: in_app=True, workflow_run_failed: in_app=True, workflow_step_completed: in_app=False, team_invite_accepted: in_app=True, project_created: in_app=True, system: in_app=True}); (2) if effective in_app is False, log `notify.skipped_in_app user_id=<uuid> kind=<kind> reason=preference_off` INFO and return None without inserting; (3) redact payload by replacing the value of any key whose lowercase name contains 'password', 'token', 'secret', or 'email' with the literal string '<redacted>' before insert (mutate a copy, not the caller's dict); (4) INSERT the notifications row, log `notify.dispatched user_id=<uuid> kind=<kind> notification_id=<uuid> in_app=true source_team_id=<uuid|none> source_workflow_run_id=<uuid|none>`, return the Notification ORM instance. Wrap the entire body in try/except — on DB error log `notify.insert_failed user_id=<uuid> kind=<kind> cause=<exception_class>` ERROR, return None, NEVER re-raise (callers must not fail because of notification side-effects). Push channel is a no-op stub returning False — S03 fills it; the function signature stays stable. Create `backend/app/api/routes/notifications.py` with these endpoints (all use the existing `CurrentUser` cookie-auth dep): GET /notifications (query params: `limit: int = 50`, `unread_only: bool = False`; ORDER BY created_at DESC; returns NotificationsPublic), GET /notifications/unread_count (returns {count: int}), POST /notifications/{notification_id}/read (404 if not owned by current user, set read_at=NOW() if NULL, return updated NotificationPublic), POST /notifications/read_all (UPDATE notifications SET read_at=NOW() WHERE user_id=current_user.id AND read_at IS NULL, return {affected: int}), GET /notifications/preferences (returns list[NotificationPreferencePublic] merged with the hard-coded DEFAULTS dict so the UI always sees one entry per kind even when no row exists; team-default rows only — workflow_id IS NULL filter applied; the seven kinds are returned in a deterministic order matching NotificationKind), PUT /notifications/preferences/{event_type} (body: NotificationPreferencePut {in_app: bool, push: bool}; UPSERT the team-default row with workflow_id NULL; emits `notifications.preference_updated`). Mount the router in `backend/app/api/main.py` alongside the existing routers. Tests under `backend/tests/api/routes/test_notifications.py`: list returns empty initially; mark-as-read transitions read_at; mark-all-read affects only the calling user's unread rows (not other users'); preferences GET returns 7 entries with defaults when no rows exist; PUT then GET shows the new value; notify() helper with in_app preference off does NOT insert; notify() with payload {'token': 'xxx', 'email': 'a@b.com', 'team_name': 'Foo'} stores '<redacted>' for token + email and 'Foo' for team_name. Regenerate the OpenAPI client via `bash scripts/generate-client.sh` so `frontend/src/client/sdk.gen.ts` exposes the typed NotificationsService.

## Inputs

- ``backend/app/models.py``
- ``backend/app/api/main.py``
- ``backend/app/api/deps.py``
- ``backend/app/api/routes/notifications.py``
- ``backend/tests/conftest.py``
- ``scripts/generate-client.sh``

## Expected Output

- ``backend/app/core/notify.py``
- ``backend/app/api/routes/notifications.py``
- ``backend/app/api/main.py``
- ``backend/tests/api/routes/test_notifications.py``
- ``frontend/openapi.json``
- ``frontend/src/client/sdk.gen.ts``
- ``frontend/src/client/types.gen.ts``
- ``frontend/src/client/schemas.gen.ts``

## Verification

cd backend && set -a && source ../.env && set +a && uv run pytest tests/api/routes/test_notifications.py -x && bash scripts/generate-client.sh && grep -q 'NotificationsService\|notifications' frontend/src/client/sdk.gen.ts && grep -q 'team_invite_accepted' frontend/src/client/schemas.gen.ts

## Observability Impact

Signals added: `notify.dispatched`, `notify.skipped_in_app`, `notify.insert_failed` (ERROR) from `backend/app/core/notify.py`; `notifications.list`, `notifications.read`, `notifications.read_all`, `notifications.preference_updated` from `backend/app/api/routes/notifications.py`. How a future agent inspects this: tail backend logs for `notify.*` and `notifications.*` prefixes; query `psql -c "SELECT user_id,kind,read_at,created_at FROM notifications ORDER BY created_at DESC LIMIT 50"` for forensic state. Failure state exposed: insert_failed includes the exception class, so a recurring constraint violation (e.g. an unknown kind slipping past the enum CHECK) is grep-stable.
