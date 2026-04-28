---
estimated_steps: 1
estimated_files: 3
skills_used: []
---

# T01: Add backend admin router with paginated teams list, team members, and promote-to-system-admin endpoint + integration tests

Create a new `backend/app/api/routes/admin.py` module exposing three endpoints under the `/admin` prefix, all gated by `get_current_active_superuser`. (1) `GET /api/v1/admin/teams?skip=0&limit=100` returns `{data: [TeamPublic, ...], count: int}` — paginated, ordered by `created_at DESC`, count is the unfiltered total via a separate `select(func.count())` query. (2) `GET /api/v1/admin/teams/{team_id}/members` returns `TeamMembersPublic` (same response model as the S03 endpoint) but skips the team-member check — system admin can inspect any team's roster including teams they aren't a member of. 404 if team missing. (3) `POST /api/v1/admin/users/{user_id}/promote-system-admin` flips the target user's `role` to `UserRole.system_admin`. 404 if user missing. Idempotent: if already system_admin, return 200 with the existing UserPublic and log `already_admin=true` (no DB write). Cannot demote — that is out of scope for this slice (note in the plan; future work). Register the new router in `backend/app/api/main.py`. Implementation notes: do NOT reuse S03 helpers (`_assert_caller_is_team_admin`/`_assert_caller_is_team_member`) — admin endpoints must bypass per-team membership. For listing, follow the existing pagination pattern in `app/api/routes/users.py::read_users` (count query + skip/limit). Emit INFO logs as documented in slice observability. Tests: (a) `backend/tests/api/routes/test_admin_teams.py` — 200 happy paths for list + members + promote; 403 for regular user on each endpoint (use a fresh signed-up user, not the seeded superuser); pagination skip/limit (create 3+ teams, assert skip=1 returns the rest with correct count); admin sees personal AND non-personal teams; admin lists members of a team they are NOT a member of (proves the bypass); 404 on missing team_id and missing user_id for promote; promote idempotency (call twice, assert single 200 + log says already_admin=true on the second); promote correctly flips a `user`-role target to `system_admin` and the mutation persists (re-fetch via /users/me after re-login, or directly via DB). Use the existing `superuser_token_cookies` fixture from `tests/conftest.py` if present, else use the `firstSuperuser` credentials via `/api/v1/auth/login` to obtain cookies. Multi-user tests follow MEM029 (detached cookie jars).

## Inputs

- ``backend/app/api/routes/teams.py` — patterns for response shapes (TeamPublic, TeamMembersPublic), TeamMember/User join, log line shapes, 404→403 ordering`
- ``backend/app/api/routes/users.py` — pagination pattern (count + skip/limit), `get_current_active_superuser` usage`
- ``backend/app/api/deps.py` — `get_current_active_superuser` already enforces `current_user.role == UserRole.system_admin``
- ``backend/app/api/main.py` — router registration site`
- ``backend/app/models.py` — TeamPublic, TeamMembersPublic, TeamMemberPublic, UserPublic, UserRole`
- ``backend/tests/api/routes/test_users.py` — superuser-cookie test setup pattern`
- ``backend/tests/api/routes/test_members.py` — _signup helper + multi-cookie-jar test pattern (MEM029)`

## Expected Output

- ``backend/app/api/routes/admin.py` — new module with admin router + 3 endpoints + structured logging`
- ``backend/app/api/main.py` — registers admin router via `api_router.include_router(admin.router)``
- ``backend/tests/api/routes/test_admin_teams.py` — at least 10 integration tests covering happy paths, 403 non-admin gates, pagination, missing-resource 404s, and promote idempotency`

## Verification

cd backend && uv run pytest tests/api/routes/test_admin_teams.py -v

## Observability Impact

Adds INFO logs `admin_teams_listed`, `admin_team_members_listed`, `system_admin_promoted` (with `already_admin=<bool>` flag) — UUIDs only, no PII. A future agent debugging an admin action can grep `system_admin_promoted target_user_id=<uuid>` to see whether the flip happened or was a no-op. The 403 path emits no extra log; the existing `get_current_active_superuser` exception is the diagnostic surface.
