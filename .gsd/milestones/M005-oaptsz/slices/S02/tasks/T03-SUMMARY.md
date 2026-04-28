---
id: T03
parent: S02
milestone: M005-oaptsz
key_files:
  - backend/app/api/routes/teams.py
  - backend/app/api/routes/projects.py
  - backend/app/api/routes/notifications.py
  - backend/app/models.py
  - backend/tests/api/routes/test_teams.py
  - backend/tests/api/routes/test_projects.py
  - backend/tests/api/routes/test_notifications.py
  - frontend/src/client/sdk.gen.ts
  - frontend/src/client/types.gen.ts
  - frontend/openapi.json
key_decisions:
  - Adapted plan's `[TeamRole.admin, TeamRole.owner]` recipient filter to `[TeamRole.admin]` only — TeamRole in this codebase is enum {member, admin}; there is no owner role. Logged as a deviation; the plan's intent (notify the team's escalation cohort) is preserved.
  - Snapshot the route's response model BEFORE calling notify(), not after. Captured as MEM346 — notify() commits internally, which expires every ORM-tracked object on the session, so a later `team.model_dump()` or `_project_to_public(project)` returns empty/stale fields and crashes the FastAPI response validator. Applied to both join_team (team) and create_project (project).
  - POST /notifications/test surfaces 500 `system_channel_suppressed` when notify() returns None instead of silently 200ing. Lets an operator distinguish 'I broke the wiring' from 'I opted out the recipient' — matches the helper's documented in_app=False return path.
  - Wrapped both notify call-sites in route-level try/except that logs `invite_accept_notify_failed` / `project_create_notify_failed` at WARNING. notify() already swallows DB errors, but a defense-in-depth catch keeps the route's contract intact even if a future bug in notify() somehow re-raises.
duration: 
verification_result: passed
completed_at: 2026-04-28T10:29:46.072Z
blocker_discovered: false
---

# T03: feat(notifications): wire notify() at team_invite_accepted + project_created and add system-admin /notifications/test trigger

**feat(notifications): wire notify() at team_invite_accepted + project_created and add system-admin /notifications/test trigger**

## What Happened

Plugged `notify()` into the two live event sites and added the system-admin seed trigger so the bell renders real content.

**teams.py / join_team**: After the existing `invite_accepted` INFO log and before returning, the route now calls `notify(session, user_id=current_user.id, kind=NotificationKind.team_invite_accepted, payload={'team_id': str(team.id), 'team_name': team.name}, source_team_id=team.id)`. The notify call is wrapped in try/except that logs `invite_accept_notify_failed` at WARNING — defense in depth on top of notify()'s own swallow-and-log contract. Critically, the `TeamWithRole` response is constructed BEFORE the notify call: notify() commits internally, which expires every ORM-tracked object on the session including `team` and `membership`, and a later `team.model_dump()` would return an empty dict and crash the response_model validator. Captured this as MEM346.

**projects.py / create_project**: After the `project_created` log, the route now snapshots `_project_to_public(project)` (same expiry hazard), then queries `TeamMember` rows whose role is in `[TeamRole.admin]` and fans out one `notify(...)` per admin recipient with `kind=NotificationKind.project_created` and a payload of project_id/project_name/team_id/repo plus source_team_id/source_project_id. The plan listed `[TeamRole.admin, TeamRole.owner]` but `TeamRole` in this codebase is just `{member, admin}` — adapted to local reality per the executor escalation rules.

**notifications.py**: Added `POST /api/v1/notifications/test` gated on `Depends(get_current_active_superuser)`. Body is `NotificationTestTrigger(user_id: UUID | None = None, message: str = 'System test notification')`; resolves user_id default to the calling admin's id, fires `notify(...)` with `kind=NotificationKind.system`, and returns the created `NotificationPublic`. If notify returns None (operator suppressed the system channel for the recipient), surfaces 500 `system_channel_suppressed` so the operator can distinguish wiring bugs from opted-out users. Emits `notifications.test_triggered actor_id=<uuid> target_user_id=<uuid>` per the slice's observability contract.

**Models**: Added `NotificationTestTrigger` SQLModel after `NotificationReadAllResponse`.

**Tests**:
- `test_teams.py::test_invite_accept_creates_notification` — signs up admin + joiner, accepts invite, asserts exactly one `team_invite_accepted` row exists for the joiner with `payload['team_name'] == 'NotifySeed'` and `source_team_id == team_id`.
- `test_projects.py::test_project_create_notifies_admins` — creates a team, promotes a second user to admin, joins a third as member, creates a project, asserts exactly the two admin user_ids received a `project_created` row and the member user_id did not. Also extended `_clean_projects_state` to wipe Notification rows so cross-test pollution doesn't bleed in.
- `test_notifications.py::test_notifications_test_endpoint_creates_system_kind` — non-superuser POST returns 403; superuser POST returns 200 with kind=system payload, and the row is visible via GET /notifications.

**OpenAPI client regen**: `frontend/src/client/sdk.gen.ts` now has `triggerTestNotification` and `NotificationsTriggerTestNotificationData/Response` types; the URL `/api/v1/notifications/test` is exposed.

**Verification**: All three new tests pass (3 passed in 0.49s). Slice-wide regression sweep (test_teams + test_projects + test_notifications + test_invites + test_admin_teams + test_members + test_s07_notifications_migration) — 132 passed, 0 failed in 7.67s. No regressions.

## Verification

Ran the slice plan's full verification command from `cd backend`: `set -a && source ../.env && set +a && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_teams.py::test_invite_accept_creates_notification tests/api/routes/test_projects.py::test_project_create_notifies_admins tests/api/routes/test_notifications.py::test_notifications_test_endpoint_creates_system_kind -x` — 3 passed in 0.49s. Then `bash scripts/generate-client.sh` from repo root — openapi.json regen + bun client gen + bun lint clean exit 0. Then `grep -q 'notificationsTest\|notifications.test\|notifications_test' frontend/src/client/sdk.gen.ts` — exit 0. Adjacent regression sweep across teams/projects/notifications/invites/admin_teams/members/s07_notifications_migration — 132 passed in 7.67s, no regressions. The `POSTGRES_PORT=5432` override is required per MEM135/235/245/344 env drift (.env pins 55432 but the published port is 5432).

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && set -a && source ../.env && set +a && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_teams.py::test_invite_accept_creates_notification tests/api/routes/test_projects.py::test_project_create_notifies_admins tests/api/routes/test_notifications.py::test_notifications_test_endpoint_creates_system_kind -x` | 0 | ✅ pass (3 passed in 0.49s) | 490ms |
| 2 | `set -a && source .env && set +a && POSTGRES_PORT=5432 bash scripts/generate-client.sh` | 0 | ✅ pass (openapi.json + bun client + bun lint clean) | 12000ms |
| 3 | `grep -q 'notificationsTest\|notifications.test\|notifications_test' frontend/src/client/sdk.gen.ts` | 0 | ✅ pass | 50ms |
| 4 | `cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_teams.py tests/api/routes/test_projects.py tests/api/routes/test_notifications.py tests/api/routes/test_invites.py tests/api/routes/test_admin_teams.py tests/api/routes/test_members.py tests/migrations/test_s07_notifications_migration.py` | 0 | ✅ pass (132 passed in 7.67s — slice regression sweep) | 7670ms |

## Deviations

Plan listed `[TeamRole.admin, TeamRole.owner]` for the project_created recipient filter; this codebase's `TeamRole` enum is `{member, admin}` with no `owner`. Filtered on `[TeamRole.admin]` only — the test asserts both admins receive notifications and the plain member does not, which is the spirit of the plan's recipient-cohort intent. Also extended `test_projects.py::_clean_projects_state` to wipe `Notification` rows alongside push_rules/projects/installations so the new test runs deterministically against the leaked session-scope db fixture.</deviations>
<parameter name="knownIssues">The .env file pins `POSTGRES_PORT=55432` but the published Docker container port is `5432`. Both T02 and T03 verification require an explicit `POSTGRES_PORT=5432` override; the verification gate's bare `source ../.env` step fails in isolation because the gate splits on `&&` and runs each command in a fresh shell. This drift is captured in MEM135/235/245/344 and is out of scope for this task — a real fix would reconcile .env with docker-compose.yml.

## Known Issues

None.

## Files Created/Modified

- `backend/app/api/routes/teams.py`
- `backend/app/api/routes/projects.py`
- `backend/app/api/routes/notifications.py`
- `backend/app/models.py`
- `backend/tests/api/routes/test_teams.py`
- `backend/tests/api/routes/test_projects.py`
- `backend/tests/api/routes/test_notifications.py`
- `frontend/src/client/sdk.gen.ts`
- `frontend/src/client/types.gen.ts`
- `frontend/openapi.json`
