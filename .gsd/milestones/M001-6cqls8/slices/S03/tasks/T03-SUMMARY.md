---
id: T03
parent: S03
milestone: M001-6cqls8
key_files:
  - backend/app/api/routes/teams.py
key_decisions:
  - Same-role PATCH is treated as a 200 no-op (skip DB write, still emit member_role_changed with old==new) rather than 304/400 — matches idempotent-PATCH semantics and keeps the client contract simple.
  - Precondition ordering for DELETE is caller-is-admin → team-is-personal → target-exists → last-admin. Personal-team guard precedes target lookup because `is_personal` is a structural property of the team; no sense checking membership rows on a team where the operation is categorically refused.
  - `_assert_caller_is_team_admin` returns the Team (not just raises) so the caller avoids a second `session.get(Team, ...)` round-trip. Retained the existing `"Only team admins can invite"` detail string verbatim to avoid churning test expectations.
  - Admin count is an aggregate `select(func.count()).select_from(TeamMember)` — one query, no row fetch, no N+1. Indexed on (team_id) already.
duration: 
verification_result: passed
completed_at: 2026-04-24T23:36:38.096Z
blocker_discovered: false
---

# T03: Add PATCH /teams/{id}/members/{uid}/role + DELETE /teams/{id}/members/{uid} with last-admin guard and refactored admin-precondition helper

**Add PATCH /teams/{id}/members/{uid}/role + DELETE /teams/{id}/members/{uid} with last-admin guard and refactored admin-precondition helper**

## What Happened

Wired the two membership-management endpoints into `backend/app/api/routes/teams.py` and extracted the shared team-exists + caller-is-admin precondition into a module-level `_assert_caller_is_team_admin` helper. `invite_to_team` was refactored onto the helper as a low-risk cleanup — the existing 19 test_teams cases all still pass, confirming the 404/403 branch ordering and detail strings (`"Only team admins can invite"`, `"Team not found"`) are preserved.

PATCH handler accepts a `MemberRoleUpdate` body (`role: TeamRole`), returns `TeamWithRole`, and runs the last-admin precondition BEFORE mutating: if `old_role == admin and new_role == member and admin_count <= 1` we raise 400 "Cannot demote the last admin". FastAPI's Pydantic validator rejects unknown enum values (e.g. `"owner"`) with 422 automatically. Same-role PATCH is a no-op that still returns 200 with the unchanged role — we skip the DB round-trip when `old_role == new_role` but still emit the `member_role_changed` log (old/new equal → idempotent audit trail).

DELETE handler returns 204 on success with an explicit `Response(status_code=204)`. Ordering of preconditions matters: caller-is-admin (404/403) → team-is-personal (400) → target-exists (404) → target-is-last-admin (400). The personal-team 400 comes before the target-membership 404 because personal teams only have one member by construction (the owner), so "delete a member of a personal team" is structurally malformed rather than a missing-row case.

The last-admin count is a single aggregate query via `_team_admin_count` using `select(func.count()).select_from(TeamMember).where(team_id=X, role=admin)` — O(1) regardless of team size (the `team_id` index plus the equality on role makes this a plain index-backed count). Both mutation handlers wrap commit in try/except with `session.rollback()` + structured `member_update_tx_rollback` / `member_remove_tx_rollback` WARNING logs before re-raising, mirroring the S02 `signup_tx_rollback` / `invite_accept_tx_rollback` observability convention.

Verification gate mismatch note: the gate that fired on attempt 1 ran `uv run pytest tests/api/routes/test_teams.py` from the repo root, where no `tests/` dir exists (they live under `backend/tests/`). The authoritative verification commands in the task plan both `cd backend &&` first; running them from `backend/` produces `19 passed` on test_teams and `106 passed` on the full suite. Future gate config for this slice should include the `cd backend &&` prefix (or the gate should honor a per-project cwd hint).

## Verification

Ran the task-plan verification block end-to-end from `backend/`:

1. `uv run python -c 'from app.main import app; paths={getattr(r,"path","") for r in app.routes}; assert "/api/v1/teams/{team_id}/members/{user_id}/role" in paths; assert "/api/v1/teams/{team_id}/members/{user_id}" in paths'` — both new paths registered (exit 0).

2. `uv run pytest tests/api/routes/test_teams.py -v` — 19/19 passed. The `_assert_caller_is_team_admin` refactor preserves behavior: invite 404/403/personal-team paths and detail strings (`"Team not found"`, `"Only team admins can invite"`, `"Cannot invite to personal teams"`) remain identical.

3. `uv run pytest tests/` — full suite 106/106 passed. No regressions in signup, auth, users, items, or migrations.

T04 will add the new-endpoint integration tests (happy-path, last-admin 400, personal-team DELETE 400, 422 unknown-enum, cross-team isolation, etc.) — per the slice plan, T03 ships the handlers and leaves test authorship to T04.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && uv run python -c 'from app.main import app; paths={getattr(r,"path","") for r in app.routes}; assert "/api/v1/teams/{team_id}/members/{user_id}/role" in paths; assert "/api/v1/teams/{team_id}/members/{user_id}" in paths'` | 0 | pass | 1200ms |
| 2 | `cd backend && uv run pytest tests/api/routes/test_teams.py -v` | 0 | pass | 1430ms |
| 3 | `cd backend && uv run pytest tests/` | 0 | pass | 5740ms |

## Deviations

None material. Added a small `_team_admin_count` helper next to `_assert_caller_is_team_admin` rather than inlining the same `select(func.count()).select_from(TeamMember)` query twice — keeps both handlers readable and stays well under the plan's "~50 lines" threshold that would have triggered moving this into `crud.py`.

## Known Issues

Verification gate path mismatch: when invoked without a `cd backend &&` prefix, `pytest tests/api/routes/test_teams.py` exits 4 with "file or directory not found" because the tests live under `backend/tests/`. The authoritative verification commands in the task plan already prefix `cd backend &&`; the gate's derived command dropped it. Not a code issue — recommend the gate config for this slice (and the rest of the backend slices) be updated to honor the task plan's cwd.

## Files Created/Modified

- `backend/app/api/routes/teams.py`
