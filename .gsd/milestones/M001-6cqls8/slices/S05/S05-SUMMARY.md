---
id: S05
parent: M001-6cqls8
milestone: M001-6cqls8
provides:
  - ["GET /api/v1/admin/teams (paginated, system-admin gated)", "GET /api/v1/admin/teams/{team_id}/members (cross-team bypass)", "POST /api/v1/admin/users/{user_id}/promote-system-admin (idempotent)", "frontend/src/lib/auth-guards.ts::requireSystemAdmin TanStack Router guard helper", "/admin/teams paginated UI route", "/admin/teams/$teamId read-only members view", "PromoteSystemAdminDialog wired into UserActionsMenu in /admin", "Conditional 'All Teams' sidebar entry for system admins"]
requires:
  - slice: S01
    provides: UserRole enum, get_current_active_superuser dep, get_current_user cookie dep
  - slice: S02
    provides: Team model, TeamMember model, TeamMembersPublic response shape, GET /teams
  - slice: S03
    provides: Members management endpoint shape (re-used types/queries on the FE; admin endpoints intentionally do NOT reuse the membership helpers)
affects:
  []
key_files:
  - ["backend/app/api/routes/admin.py", "backend/app/api/main.py", "backend/tests/api/routes/test_admin_teams.py", "frontend/src/lib/auth-guards.ts", "frontend/src/routes/_layout/admin.tsx", "frontend/src/routes/_layout/admin.teams.tsx", "frontend/src/routes/_layout/admin.teams_.$teamId.tsx", "frontend/src/components/Admin/AdminTeamsColumns.tsx", "frontend/src/components/Admin/PromoteSystemAdminDialog.tsx", "frontend/src/components/Admin/UserActionsMenu.tsx", "frontend/src/components/Sidebar/AppSidebar.tsx", "frontend/src/client/sdk.gen.ts", "frontend/src/client/types.gen.ts", "frontend/src/client/schemas.gen.ts", "frontend/tests/admin-teams.spec.ts"]
key_decisions:
  - ["Gate the entire admin router with router-level dependencies=[Depends(get_current_active_superuser)] rather than per-route — every admin endpoint needs the same gate, and router-level form prevents an ungated endpoint slipping in later.", "Bypass the per-team membership helpers (_assert_caller_is_team_member / _assert_caller_is_team_admin) entirely in admin.py — system admin must inspect any team's roster regardless of membership; reusing the helpers would defeat the bypass.", "Promote endpoint is idempotent and logs already_admin using str(bool).lower() (lowercase 'true'/'false') so grep-based log inspection matches the slice observability contract literally.", "Centralize the system-admin route gate as `requireSystemAdmin` in frontend/src/lib/auth-guards.ts; refactor the existing /admin route to consume it and reuse for /admin/teams + /admin/teams/$teamId — proves the abstraction and avoids drift.", "Use a trailing-underscore-opt-out route name (admin.teams_.$teamId.tsx, MEM048) for the members view so the page replaces the parent layout, mirroring teams_.$teamId.tsx; no new endpoint introduced for fetching the team name in this slice.", "Members view at /admin/teams/$teamId is read-only — promote/demote/remove member actions stay on the team-level UI. System-admin scope is observation + system_admin promotion, not arbitrary team mutation.", "Playwright spec uses isolated browser.newContext({ storageState: { cookies: [], origins: [] } }) when seeding extra users via signupViaUI so the Set-Cookie response doesn't stomp the superuser session on the test page (MEM029 pattern applied to Playwright contexts)."]
patterns_established:
  - ["Idempotent role-mutation endpoint pattern: read target → branch on current value → only write on change → always return 200 with the (possibly unchanged) resource → log the no-op flag in lowercase string form (already_admin=true|false) for grep-friendly observability.", "Reusable TanStack Router guard pattern: `requireSystemAdmin({ context, location })` reads the cached current user via context.queryClient.ensureQueryData and throws redirect({ to: '/' }) on role mismatch — wired via `beforeLoad: requireSystemAdmin` on each protected route.", "Router-level FastAPI gate pattern: when every route in a module shares the same auth gate, declare it as `dependencies=[Depends(...)]` on APIRouter rather than on each handler — DRY + defends against accidentally adding an ungated route.", "Cross-bypass admin endpoint pattern: do NOT import per-resource membership helpers in admin.py routers; bypass is the contract."]
observability_surfaces:
  - ["Structured INFO log `admin_teams_listed actor_id=<uuid> skip=<n> limit=<n> count=<n>` on every successful list call; asserted via caplog in test_admin_teams.py.", "Structured INFO log `admin_team_members_listed actor_id=<uuid> team_id=<uuid> count=<n>` on every successful members read; asserted via caplog.", "Structured INFO log `system_admin_promoted actor_id=<uuid> target_user_id=<uuid> already_admin=<true|false>` on every promote call (mutating and no-op); asserted via caplog with both literal substrings.", "DB inspection surfaces: `SELECT id, name, is_personal FROM team ORDER BY created_at DESC LIMIT N;` reproduces /admin/teams response; `SELECT role FROM \"user\" WHERE id = '<uuid>';` reproduces a promote outcome.", "HTTP failure visibility: 403 from `get_current_active_superuser` carries the standard 'doesn't have enough privileges' detail (part of the contract); 404 carries 'Team not found' / 'User not found'; 401 from missing cookie."]
drill_down_paths:
  []
duration: ""
verification_result: passed
completed_at: 2026-04-25T03:22:57.245Z
blocker_discovered: false
---

# S05: System admin panel

**System admin panel ships: paginated /admin/teams, cross-team members view, idempotent promote-to-system-admin — all gated by router-level get_current_active_superuser and proven end-to-end by 15 backend integration tests and 2 Playwright specs.**

## What Happened

S05 closes M001 by exercising the role gate end-to-end. Three new backend endpoints land in `backend/app/api/routes/admin.py`, gated at the router level with `dependencies=[Depends(get_current_active_superuser)]` so the role check fires before any handler logic and an ungated endpoint cannot be added by accident. The endpoints are: (a) `GET /api/v1/admin/teams?skip=0&limit=100` — paginated, ordered by `Team.created_at DESC`, returns `{data: [TeamPublic, ...], count}` where count is the unfiltered system-wide total (count + offset/limit pattern from `users.py::read_users`). (b) `GET /api/v1/admin/teams/{team_id}/members` — deliberately bypasses the per-team membership helpers (`_assert_caller_is_team_member`, `_assert_caller_is_team_admin`) so system admin can inspect any team's roster; reuses `TeamMembersPublic` so the frontend can share types/queries. (c) `POST /api/v1/admin/users/{user_id}/promote-system-admin` — idempotent: reads target, branches on `target.role == UserRole.system_admin`, only writes when promotion is needed, always returns 200 with the (possibly unchanged) `UserPublic`. Logs `already_admin=true` on the no-op path and `already_admin=false` on the mutating path, using `str(bool).lower()` so log greps match the slice contract substring exactly. Demotion is intentionally not exposed (out of scope).

The frontend regenerated the OpenAPI client (`AdminService` now exposes `readAllTeams`, `readAdminTeamMembers`, `promoteSystemAdmin`) and added a reusable TanStack Router guard `requireSystemAdmin` at `frontend/src/lib/auth-guards.ts` that runs `context.queryClient.ensureQueryData({ queryKey: ['currentUser'], queryFn: UsersService.readUserMe })` and throws `redirect({ to: '/' })` if the role isn't `system_admin`. The existing `/admin` route was refactored to consume the helper, proving the abstraction. Two new routes were added: `/admin/teams` (sibling of `/admin`) renders a paginated DataTable of every team with name, slug, personal-or-not badge, created date, and a `View members` link; `useSearch`/`useNavigate` drive `skip`/`limit` (default 0/20), Prev disabled when `skip=0`, Next disabled when `data.data.length < limit`. The trailing-underscore-opt-out route `/admin/teams_/$teamId` (per MEM048) hosts the read-only members view, reusing the visual shape of `MembersList` without the promote/demote/remove actions. The `PromoteSystemAdminDialog` (built on the project's `AlertDialog`) confirms with the exact copy `Promote {email} to system admin? They will gain access to every team and the admin panel.` and on success invalidates `['users']` and shows the toast `Promoted to system admin`. It's wired into `UserActionsMenu` as a new entry that's only visible when (a) current user is `system_admin`, (b) the target isn't already `system_admin`, and (c) the target isn't the current user. The sidebar gains a conditional `All Teams` entry under the `Admin` link, only when `currentUser.role === 'system_admin'`.

Verification was done at every layer. Backend: `backend/tests/api/routes/test_admin_teams.py` (15 tests) covers the envelope shape, personal+non-personal visibility, 403 for normal users on each endpoint, 401 for unauthenticated callers, pagination skip/limit returning disjoint pages with the expected newest-first ordering, structured-log assertions on the `app.api.routes.admin` logger for all three endpoints (caplog captures the literal `admin_teams_listed`/`admin_team_members_listed`/`system_admin_promoted` lines), the cross-team bypass (admin reading members of a team they're not a member of), 404 on missing team_id and user_id, idempotent promote (two calls, second logs `already_admin=true`), first-call logs `already_admin=false`, and role-flip persistence via re-fetching `/users/me` with the target's own session cookie. Tests used the `superuser_cookies` fixture and the MEM029 detached-cookie-jar pattern (`client.cookies.clear()` between users) for multi-user scenarios. Frontend e2e: `frontend/tests/admin-teams.spec.ts` runs against the real backend on :8001 and the Vite dev server. Spec 1 seeds two extra users via `signupViaUI` in isolated `browser.newContext({ storageState: { cookies: [], origins: [] } })` contexts (so the superuser session on the test page isn't stomped), then on the superuser page asserts the heading `All Teams`, asserts at least 3 `admin-teams-row` rows render, drills into a signup's personal-team `view-members-link`, asserts the members view shows that user's email, navigates to `/admin`, opens the actions menu for the second signup, clicks `promote-system-admin`, confirms via `confirm-promote`, asserts the `Promoted to system admin` toast, and asserts the row's role badge updates to `Admin` (locator scoped to `span[data-slot="badge"]` inside the user row, not loose `getByText('Admin')`, to avoid matching the seeded full-name cell). Spec 2 forces an empty `storageState`, signs up a fresh user, hits `/admin/teams`, and waits for the URL to leave `/admin/*` — proving `requireSystemAdmin` redirects.

R002 ("UserRole enum on User; TeamRole enum on TeamMember; roles enforced at API layer") is now fully validated. With S01 establishing the enums and replacing `is_superuser` in the API layer, S03 enforcing TeamRole on invite/role/remove endpoints, and S05 exercising the system-admin route gate end-to-end across REST + UI, every M001 success criterion is provable in a browser: signup → personal team → create team → invite → accept → manage roles → system admin sees all teams.

## Verification

All slice-level checks pass. (1) `cd backend && uv run pytest tests/api/routes/test_admin_teams.py -v` — **15 passed in 0.63s**. Covers: GET /admin/teams happy path + envelope shape, personal+non-personal visibility, 403 for normal user, 401 unauthenticated, pagination skip/limit with disjoint pages and newest-first ordering, structured INFO log assertions for all three endpoints via caplog on `app.api.routes.admin`, cross-team members bypass (admin reading a team they're not a member of), 404 on missing team_id, 404 on missing user_id, idempotent promote (two calls; second logs `already_admin=true`), first-call logs `already_admin=false`, role flip persists when re-fetching `/users/me` with target's own session cookie. (2) `cd frontend && VITE_API_URL=http://localhost:8001 bunx playwright test admin-teams.spec.ts --project=chromium` — **3 passed in 7.6s** (1 setup + 2 specs). Spec 1 (`system admin sees all teams and promotes a user`) traverses every endpoint admin.py exposes (list teams, list members of a team, promote target user) so a regression in any of them would fail it; asserts the `Promoted to system admin` toast and the role-badge flip to `Admin`. Spec 2 (`non-admin redirected away from /admin/teams`) signs up a fresh non-admin user in an empty-storage-state context, hits `/admin/teams`, and asserts the URL leaves `/admin/*` — proves the `requireSystemAdmin` guard fires. (3) `cd frontend && bun run lint` — clean (biome check on 85 files in 48ms). Backend was started on :8001 (port 8000 is reserved by an unrelated Docker container per MEM046) using `uv run fastapi run --port 8001 --reload app/main.py` with `.env` loaded; the Vite dev server was auto-launched by Playwright's `webServer` block on :5173. The slice's structured INFO logs (`admin_teams_listed actor_id=... skip=... limit=... count=...`, `admin_team_members_listed actor_id=... team_id=... count=...`, `system_admin_promoted actor_id=... target_user_id=... already_admin=true|false`) are exercised live in the happy-path Playwright run and are mechanically asserted by three caplog tests in test_admin_teams.py — no email or team name in logs (UUIDs only), matching S03's redaction posture.

## Requirements Advanced

None.

## Requirements Validated

- R002 — S05 closes the role-gate validation: 15 backend integration tests in test_admin_teams.py prove 200 for system_admin, 403 for non-admin, 401 unauthenticated, pagination, idempotent promote (with already_admin=true|false log assertions), 404s, and cross-team bypass; Playwright admin-teams.spec.ts proves browser-level happy path (paginated /admin/teams, drill-down, promote-via-confirm-dialog, role badge flip) and the requireSystemAdmin redirect for non-admins. Combined with S01 (enum + is_superuser replacement) and S03 (TeamRole enforcement on invite/role/remove), R002 is fully validated.

## New Requirements Surfaced

None.

## Requirements Invalidated or Re-scoped

None.

## Operational Readiness

None.

## Deviations

None. Implementation matches the slice plan tasks T01–T05 exactly. The slice plan's note that T05 'may need to amend' T03/T04 component files turned out to be unnecessary — every required data-testid (`admin-teams-row`, `view-members-link`, `promote-system-admin`, `promote-system-admin-dialog`, `confirm-promote`) was wired in proactively during T03/T04, so T05 only added the spec file.

## Known Limitations

No demote endpoint — a user can be promoted to system_admin but not demoted back to user via this API; deferred as out-of-scope for this slice (note this in M001 wrap-up). The /admin/teams/$teamId members view is read-only — to mutate team members, system admins still navigate to /teams/$teamId where the team-admin actions live (system admins are not auto-team-admins; this is intentional). The /admin/teams table omits a member-count column to keep the response shape simple — adding it would require either a join in the list query or an N+1 pattern; deferred until a real product need surfaces.

## Follow-ups

If/when system_admin role demotion becomes a product requirement, add POST /api/v1/admin/users/{id}/demote-system-admin with explicit safeguards (cannot demote self, cannot demote the last system admin) and a paired confirm dialog. Consider showing a member-count column on /admin/teams once a perf/UX driver exists.

## Files Created/Modified

- `backend/app/api/routes/admin.py` — New router exposing /admin/teams (paginated), /admin/teams/{id}/members (cross-team bypass), and /admin/users/{id}/promote-system-admin (idempotent); router-level get_current_active_superuser dep; structured INFO logs.
- `backend/app/api/main.py` — Registered the new admin router (alphabetized import).
- `backend/tests/api/routes/test_admin_teams.py` — 15 integration tests: envelope shape, 200/403/401 matrix, pagination, idempotency, log assertions, cross-team bypass, 404s, role-flip persistence.
- `frontend/src/lib/auth-guards.ts` — New shared `requireSystemAdmin` TanStack Router guard helper using context.queryClient.ensureQueryData + redirect.
- `frontend/src/routes/_layout/admin.tsx` — Refactored to consume requireSystemAdmin (removed inlined guard) — proves the helper abstraction.
- `frontend/src/routes/_layout/admin.teams.tsx` — Paginated /admin/teams page using AdminService.readAllTeams + DataTable; Prev/Next via useSearch/useNavigate.
- `frontend/src/routes/_layout/admin.teams_.$teamId.tsx` — Read-only members view at /admin/teams/{teamId} (trailing-underscore opt-out replaces parent layout per MEM048).
- `frontend/src/components/Admin/AdminTeamsColumns.tsx` — Column defs + row types for the /admin/teams DataTable (Name, Slug, Personal badge, Created, View members link).
- `frontend/src/components/Admin/PromoteSystemAdminDialog.tsx` — AlertDialog confirm wrapper around AdminService.promoteSystemAdmin; invalidates ['users'] and toasts on success.
- `frontend/src/components/Admin/UserActionsMenu.tsx` — Added 'Promote to system admin' dropdown entry guarded by current user's role + target's role + self-check.
- `frontend/src/components/Sidebar/AppSidebar.tsx` — Conditional 'All Teams' entry visible only to system admins.
- `frontend/src/client/sdk.gen.ts` — Regenerated — adds AdminService with readAllTeams, readAdminTeamMembers, promoteSystemAdmin.
- `frontend/src/client/types.gen.ts` — Regenerated request/response types for the admin endpoints.
- `frontend/src/client/schemas.gen.ts` — Regenerated schemas.
- `frontend/tests/admin-teams.spec.ts` — Two Playwright specs: 'system admin sees all teams and promotes a user' and 'non-admin redirected away from /admin/teams'.
