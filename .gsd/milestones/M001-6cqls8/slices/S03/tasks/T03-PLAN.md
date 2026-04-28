---
estimated_steps: 48
estimated_files: 2
skills_used: []
---

# T03: Add PATCH /teams/{team_id}/members/{user_id}/role + DELETE /teams/{team_id}/members/{user_id} with last-admin guard

Add two new endpoints to the teams router (same file: `backend/app/api/routes/teams.py`) for membership management.

Endpoint contracts:

`PATCH /api/v1/teams/{team_id}/members/{user_id}/role` — body `{role: 'admin'|'member'}`.
- 404 if team missing; 404 if target user has no TeamMember row on that team.
- 403 if caller is not an admin on that team.
- 400 'Cannot demote the last admin' if target membership.role == admin, body.role == member, and the team has exactly one admin.
- 200 with updated TeamWithRole on success.

`DELETE /api/v1/teams/{team_id}/members/{user_id}`
- 404 if team missing; 404 if target membership missing.
- 403 if caller not admin.
- 400 'Cannot remove the last admin' if target is an admin and the team has only one admin.
- 400 'Cannot remove members from personal teams' if team.is_personal — personal teams only have one member by construction, and deleting the personal-team owner is destructive.
- 204 on success (no body) — `Response(status_code=204)`.

Implementation notes:
- Use a single helper `_assert_caller_is_team_admin(session, team_id, caller_id) -> Team` at module scope that does the 404 + 403 check and returns the Team. Both new endpoints and the existing invite endpoint can call this; refactor `invite_to_team` to use it (low-risk cleanup — only if the diff stays small and all existing tests pass).
- The last-admin check: `admin_count = session.exec(select(func.count()).select_from(TeamMember).where(TeamMember.team_id == team_id, TeamMember.role == TeamRole.admin)).one()` — one query, no N+1.
- Role updates: fetch TeamMember, mutate `.role`, `session.add` + commit + refresh — standard SQLModel pattern.
- Do NOT allow a user to modify their own role via PATCH on themselves — that is covered by the last-admin guard for demotion; self-promotion from member to admin is naturally blocked because only admins can call PATCH (so a member calling PATCH on themselves gets 403 before the role-update logic).
- Structured logs: `member_role_changed team_id=<uuid> target_user_id=<uuid> old_role=<str> new_role=<str> actor_id=<uuid>` and `member_removed team_id=<uuid> target_user_id=<uuid> actor_id=<uuid>`.
- Register `MemberRoleUpdate` request body (defined in T01 models.py addition) with field `role: TeamRole` and no other fields — FastAPI's Pydantic validator handles unknown-value rejection.

Must-haves:
- Both endpoints reject calls from non-admins with 403 BEFORE running any mutation.
- Last-admin guard is a precondition check — it must run BEFORE the mutation, not after + compensate.
- Personal-team DELETE blocked with 400 (not 403 — the caller IS the admin; the server refuses the destructive op).
- Reuse `_assert_caller_is_team_admin` — do not duplicate the check inline in each endpoint.
- Emit structured logs with UUID-only data.

Steps:
1. Refactor existing `invite_to_team` in `teams.py` to call `_assert_caller_is_team_admin` — verify `pytest tests/api/routes/test_teams.py -v` stays green.
2. Add `@router.patch('/{team_id}/members/{user_id}/role')` handler using `MemberRoleUpdate` body.
3. Add `@router.delete('/{team_id}/members/{user_id}', status_code=204)` handler.
4. Manual smoke: `cd backend && uv run python -c 'from app.main import app; paths=sorted(r.path for r in app.routes if "members" in getattr(r,"path",""))  ; print(paths)'` lists both new paths.
5. Run `cd backend && uv run pytest tests/ -v` — T04 adds the endpoint tests; at this point only the existing S01/S02 tests must stay green.

Failure Modes:
| Dependency | On error | On timeout | On malformed response |
|---|---|---|---|
| Postgres (update/delete TeamMember) | rollback + re-raise → 500; log `member_update_tx_rollback` or `member_remove_tx_rollback` | N/A | N/A |

Load Profile:
- Shared resources: DB connection pool, one admin-count query + one mutation per request.
- Per-operation cost: at most 2 SELECT + 1 UPDATE/DELETE per request.
- 10x breakpoint: for a team with hundreds of admins the admin-count query remains O(1) thanks to the partial equality predicate + indexed team_id column.

Negative Tests:
- Malformed inputs: PATCH with `{role: 'owner'}` → 422 (invalid enum). PATCH with empty body → 422. Non-UUID team_id or user_id → 422.
- Error paths: PATCH as non-admin → 403. DELETE on personal team → 400. DELETE the only admin → 400. PATCH demoting only admin to member → 400.
- Boundary conditions: PATCH to the same role (idempotent no-op) → 200 with unchanged role; team with 2 admins → DELETE one → 204; remaining admin is untouched.

Observability Impact:
- Signals added: `member_role_changed`, `member_removed`, `member_update_tx_rollback`, `member_remove_tx_rollback`.
- Future agent inspects via: `SELECT user_id, role FROM team_member WHERE team_id = <uuid>;` shows current admins; grep logs for `actor_id=<uuid>` to see who changed what.
- Failure state exposed: HTTP 400 detail strings carry the specific guard name (`last admin`, `personal teams`); logs carry team_id + target_user_id so a future agent can correlate the HTTP error to the exact row.

## Inputs

- ``backend/app/api/routes/teams.py` — existing invite_to_team endpoint and admin-membership lookup pattern to extract into `_assert_caller_is_team_admin``
- ``backend/app/models.py` — TeamMember + TeamRole + MemberRoleUpdate (from T01)`
- ``backend/app/crud.py` — helper layout conventions`

## Expected Output

- ``backend/app/api/routes/teams.py` — new PATCH role and DELETE member endpoints + refactored `_assert_caller_is_team_admin` helper`
- ``backend/app/crud.py` — optional helper for last-admin count if endpoint body would exceed ~50 lines; otherwise the inline query is fine`

## Verification

cd backend && uv run python -c 'from app.main import app; paths={getattr(r,"path","") for r in app.routes}; assert "/api/v1/teams/{team_id}/members/{user_id}/role" in paths; assert "/api/v1/teams/{team_id}/members/{user_id}" in paths' && uv run pytest tests/api/routes/test_teams.py -v

## Observability Impact

INFO `member_role_changed team_id=<uuid> target_user_id=<uuid> old_role=<str> new_role=<str> actor_id=<uuid>` and `member_removed team_id=<uuid> target_user_id=<uuid> actor_id=<uuid>`. WARNING `member_update_tx_rollback` / `member_remove_tx_rollback` on DB failure. Future agent inspects via `team_member` table state + log grep by actor_id.
