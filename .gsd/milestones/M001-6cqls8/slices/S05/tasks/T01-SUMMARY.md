---
id: T01
parent: S05
milestone: M001-6cqls8
key_files:
  - backend/app/api/routes/admin.py
  - backend/app/api/main.py
  - backend/tests/api/routes/test_admin_teams.py
key_decisions:
  - Router-level `dependencies=[Depends(get_current_active_superuser)]` rather than per-route — every endpoint in admin.py needs the same gate, and the router-level form prevents adding an ungated endpoint later by accident.
  - Promote endpoint logs `already_admin` using `str(bool).lower()` so the literal substring matches the slice contract (`already_admin=true` / `already_admin=false`) — Python's default `True`/`False` would break grep-based log inspection.
  - Bypass team-membership helpers entirely (do not import `_assert_caller_is_team_member`) — system admin must read any team's roster regardless of membership, and reusing the helper would defeat the bypass.
duration: 
verification_result: passed
completed_at: 2026-04-25T01:13:34.546Z
blocker_discovered: false
---

# T01: Add /admin router with paginated teams list, cross-team members view, and idempotent promote-system-admin endpoint plus 15 integration tests

**Add /admin router with paginated teams list, cross-team members view, and idempotent promote-system-admin endpoint plus 15 integration tests**

## What Happened

Created `backend/app/api/routes/admin.py` exposing three endpoints under the `/admin` prefix, all gated at the router level via `dependencies=[Depends(get_current_active_superuser)]` so the role check fires before any handler logic.

(1) `GET /admin/teams?skip=0&limit=100` — follows the count + offset/limit pattern from `users.py::read_users`. Orders by `Team.created_at DESC`, returns `{data: [TeamPublic, ...], count: int}` where count is the unfiltered system-wide total. Used `col(Team.created_at).desc()` for the SQLModel-friendly ORDER BY.

(2) `GET /admin/teams/{team_id}/members` — deliberately bypasses the per-team membership helpers (`_assert_caller_is_team_member` / `_assert_caller_is_team_admin`) from teams.py since system admin must inspect any team. 404 'Team not found' if missing. Reuses `TeamMembersPublic` so the response matches the S03 endpoint shape — frontend can reuse the same query/types.

(3) `POST /admin/users/{user_id}/promote-system-admin` — idempotent: reads target, branches on `target.role == UserRole.system_admin`, only writes when promotion is needed. Always returns 200 with the (possibly unchanged) `UserPublic`. Logs `already_admin=true` on the no-op path, `already_admin=false` on the mutating path. Demotion is intentionally not exposed (out of scope per the slice plan).

Registered the router in `backend/app/api/main.py` next to the teams router. Added `admin` to the alphabetized import list.

Wrote `backend/tests/api/routes/test_admin_teams.py` with 15 integration tests using the existing `superuser_cookies` fixture from conftest plus the MEM029 `_signup` helper pattern (detached cookie jars, `client.cookies.clear()` between users). Coverage: envelope shape, personal+non-personal visibility, 403 for normal user, 401 unauthenticated, pagination skip/limit with disjoint pages and newest-first ordering, structured-log assertions for all three endpoints, cross-team bypass proof (admin reading members of a team they're not on), 404 on missing team_id and missing user_id, idempotent promote (two calls, second logs `already_admin=true`), first-call logs `already_admin=false`, and role-flip persistence verified via re-fetching `/users/me` with the target's own session cookie (no re-login needed since the JWT carries user_id and role is read fresh per request).

Two notable design decisions worth surfacing: (a) Used router-level `dependencies=[Depends(get_current_active_superuser)]` rather than per-route — every route in this module requires the same gate, so the router-level form is more DRY and prevents accidentally adding an ungated endpoint later. (b) Logged `already_admin` as the lowercase string `"true"`/`"false"` (via `str(...).lower()`) rather than Python's default `"True"`/`"False"` so log greps match the slice contract exactly as written.

## Verification

Ran the slice-prescribed verification: `cd backend && uv run pytest tests/api/routes/test_admin_teams.py -v` — all 15 tests pass in 0.69s. Also ran the full route suite (`uv run pytest tests/api/routes/`) to confirm zero regressions: 123 passed in 6.32s. Structured-log shape is asserted directly in three of the tests via `caplog` against the `app.api.routes.admin` logger, so the slice's observability contract is mechanically enforced — not just claimed.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && uv run pytest tests/api/routes/test_admin_teams.py -v` | 0 | ✅ pass | 690ms |
| 2 | `cd backend && uv run pytest tests/api/routes/` | 0 | ✅ pass | 6320ms |

## Deviations

None — implementation matches the task plan exactly. The plan suggested that the role-flip persistence test could re-fetch /users/me 'after re-login, or directly via DB'; the simpler form (re-use the same cookie jar, since session JWT decodes user_id and role is read from the row each request) was sufficient and matches existing test_users.py patterns.

## Known Issues

None for T01. T02 (regenerate OpenAPI client + add `requireSystemAdmin` route guard helper) will pick up the new endpoints. The frontend cannot consume these routes until T02 runs `bun run generate-client` — that's expected per the slice decomposition.

## Files Created/Modified

- `backend/app/api/routes/admin.py`
- `backend/app/api/main.py`
- `backend/tests/api/routes/test_admin_teams.py`
