---
id: T03
parent: S01
milestone: M005-sqm8et
key_files:
  - backend/app/api/routes/team_secrets.py
  - backend/app/api/main.py
  - backend/app/main.py
  - backend/tests/api/test_team_secrets_routes.py
key_decisions:
  - Inlined `_assert_team_member` / `_assert_team_admin` guards in routes/team_secrets.py (rather than calling `team_access.assert_caller_is_team_admin`) because the shared helper bakes `Only team admins can invite` into its 403 detail string and the slice plan locks this surface on `team_admin_required` / `not_team_member` for the frontend's role-aware UI. Captured as MEM411 (architecture).
  - Added two global exception handlers in app/main.py — TeamSecretDecryptError → 503 `team_secret_decrypt_failed` and MissingTeamSecretError → 404 `team_secret_not_set` — mirroring M004's SystemSettingDecryptError handler. The decrypt-failure handler emits the slice-plan-locked ERROR log line before responding so a downstream caller that swallows the exception still leaves the corruption signal in logs.
  - PUT response uses TeamSecretStatus (the same shape GET-single returns) so the frontend's React Query cache update can use the PUT response verbatim without a follow-up GET — this matches the slice plan's `has_value: true` contract on PUT and avoids widening the surface with a separate response model.
  - Three observability assertions in the route tests: success log on PUT/DELETE includes team_id + key but NO value / `sk-` / `sk-ant-` substring; failed PUT (validator) MUST NOT emit the success log line. The negative assertion guards a future refactor that might move `logger.info` above the validator boundary.
duration: 
verification_result: passed
completed_at: 2026-04-28T21:52:31.888Z
blocker_discovered: false
---

# T03: Added team-admin team_secrets API router (PUT/GET single+list/DELETE) with global TeamSecretDecryptError handler and 27 route tests covering admin-gate, validator failure modes, and value-redacted observability logs.

**Added team-admin team_secrets API router (PUT/GET single+list/DELETE) with global TeamSecretDecryptError handler and 27 route tests covering admin-gate, validator failure modes, and value-redacted observability logs.**

## What Happened

Built `backend/app/api/routes/team_secrets.py` exposing PUT/GET-single/GET-list/DELETE under `/api/v1/teams/{team_id}/secrets/{key}`. The router calls into T02's service helpers (`set_team_secret`, `delete_team_secret`, `list_team_secret_status`) so the encryption discipline lives entirely in one module — routes never touch `decrypt_setting` directly. PUT validates via `set_team_secret` and translates `InvalidTeamSecretValueError.reason` into the 400 `invalid_value_shape` body's `hint` field (shape-only token, never the value). GET-list returns one `TeamSecretStatus` per registered key in declaration order so the frontend can render rows for "not set" keys without a second round-trip; the DTO has no `value` field at all so a refactor cannot accidentally widen the response.

Registered `team_secrets.router` in `backend/app/api/main.py` immediately after `teams.router` (same `/teams` prefix, no path collisions — secrets paths are `/{team_id}/secrets/...` only). Added two new global exception handlers in `backend/app/main.py` mirroring M004's `SystemSettingDecryptError` pattern: `TeamSecretDecryptError` → 503 `team_secret_decrypt_failed` with team_id + key (never the ciphertext) emitted as ERROR log; `MissingTeamSecretError` → 404 `team_secret_not_set` so downstream callers (S02+) that bubble the helper's exception unguarded see the same error shape the GET-single route returns.

Key architectural decision (captured as MEM411 architecture memory): the team-admin/member guards in `routes/team_secrets.py` are inlined rather than calling `app.api.team_access.assert_caller_is_team_admin`. The shared helper bakes `Only team admins can invite` into its 403 detail — but the slice plan locks this surface on `{detail: 'team_admin_required'}` (frontend role-aware UI disambiguates on that key). Widening the shared helper would leak invite-specific copy into AI-credentials responses. Inlined guards return structured `{detail: 'team_admin_required'}` / `{detail: 'not_team_member'}` / `{detail: 'team_not_found'}` bodies. The membership SQL shape (select TeamMember by team+user) matches `team_access.py` exactly, so this is duplication of a security-primitive boundary, not divergence.

Test coverage in `backend/tests/api/test_team_secrets_routes.py` (27 tests, all green): 401 without cookie on PUT/GET; admin can PUT both registered keys; PUT response is the same `TeamSecretStatus` shape GET returns and contains no `value`; the persisted row's `value_encrypted` is real Fernet ciphertext (asserted no plaintext / no `sk-ant-` prefix bytes inside it); non-admin member → 403 `team_admin_required`; outsider → 403 `not_team_member`; unknown team → 404 `team_not_found`; unregistered key → 400 `unregistered_key`; bad-prefix → 400 `invalid_value_shape` with `hint='bad_prefix'`; too-short → `hint='too_short'`; PUT-replace bumps `updated_at`; GET-single returns `{key, has_value, sensitive, updated_at}` with no `value` after PUT, 404 on missing row, 400 on unregistered key, member can read, non-member 403; GET-list returns rows for both registered keys when empty, reflects set rows, isolates per-team; DELETE 204 then 404 on second call (idempotency-shape per slice plan), non-admin 403, unregistered key 400. Three observability assertions: PUT emits `team_secret_set` with team_id + key and NO value/`sk-`/`sk-ant-` substring; DELETE emits `team_secret_deleted` likewise; failed PUT (validator) does NOT emit the success log line.

Pinned the test-only Fernet key (`q14YMz9s4jrbfD29GvcRfe_4krg82w6_mPWUu_y3LTo=`) in an autouse fixture matching T02's helper tests, with `_load_key.cache_clear()` before+after so the env swap takes effect deterministically. Cleaned `team_secrets` rows in another autouse fixture so the session-scoped `db` fixture stays clean across tests.

Verified the gate's exact command (`cd backend && uv run pytest tests/api/test_team_secrets_routes.py -v`) → 27 passed in 1.7s. Re-ran the broader `tests/api/test_team_secrets_helpers.py + routes/test_teams.py + routes/test_admin_teams.py + routes/test_admin_settings.py` to verify no regressions: 136 passed. The 10 pre-existing `test_sessions.py` / `test_voice.py` failures in the wider `tests/api/` run reproduce on `git stash` (orchestrator on port 64109 is down — environment dependency, not caused by this task).

## Verification

Ran the slice-locked verification command exactly: `cd backend && uv run pytest tests/api/test_team_secrets_routes.py -v` → 27 passed. Routes registered cleanly (`PUT/GET/GET/DELETE /api/v1/teams/{team_id}/secrets[/{key}]`) verified by importing `app.main` and inspecting `app.routes`. Full-suite confidence: 136 passed across helpers + routes + adjacent admin/team suites with zero regressions vs main. Pre-existing `test_sessions.py`/`test_voice.py` failures reproduce without my changes (orchestrator service dependency).

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && uv run pytest tests/api/test_team_secrets_routes.py -v` | 0 | pass | 1730ms |
| 2 | `uv run pytest tests/api/test_team_secrets_helpers.py tests/api/test_team_secrets_routes.py tests/api/routes/test_teams.py tests/api/routes/test_admin_teams.py tests/api/routes/test_admin_settings.py` | 0 | pass | 3970ms |
| 3 | `uv run python -c 'from app.main import app; ...' (route registration check)` | 0 | pass | 1200ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `backend/app/api/routes/team_secrets.py`
- `backend/app/api/main.py`
- `backend/app/main.py`
- `backend/tests/api/test_team_secrets_routes.py`
