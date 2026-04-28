# S05: System admin panel — UAT

**Milestone:** M001-6cqls8
**Written:** 2026-04-25T03:22:57.245Z

# S05 UAT — System admin panel

## Preconditions
- Backend running on `http://localhost:8001` (`cd backend && uv run fastapi run --port 8001 --reload app/main.py`).
- Frontend dev server reachable on `http://localhost:5173` (`cd frontend && bun run dev`).
- Seeded superuser exists: `FIRST_SUPERUSER` / `FIRST_SUPERUSER_PASSWORD` from `.env` (role = `system_admin`).
- Postgres reachable via the backend; clean fixture data is fine — UAT seeds the additional users it needs.

## Test Cases

### TC1 — Superuser sees every team in `/admin/teams`
Steps:
1. From a fresh browser, log in at `/login` with the seeded superuser credentials.
2. Open the sidebar; observe an `All Teams` link rendered under `Admin`.
3. Click `All Teams` → URL becomes `/admin/teams`.

Expected:
- Page heading `All Teams` and subtitle `System admin: every team in the workspace.` are visible.
- Table renders rows for every team in the system (at minimum the seeded superuser's personal team). Columns: Name, Slug, Personal? badge, Created, View members link.
- Personal teams are clearly badged; non-personal teams are not.
- The query string contains `skip=0&limit=20` (or no values, defaulting to 0/20).
- An `admin_teams_listed actor_id=<uuid> skip=0 limit=20 count=<N>` line appears in backend logs.

### TC2 — Pagination Prev/Next behaviour
Preconditions: at least 21 teams exist (seed extras by signing up additional users via `/signup` in incognito tabs — each signup creates a personal team).

Steps:
1. As superuser navigate to `/admin/teams`.
2. Note the `count` returned and the rendered row count (≤ 20).
3. Click `Next`.

Expected:
- URL search updates (`skip=20`).
- Table rerenders with the next page; rows do not overlap with the first page (newest-first by `created_at DESC`).
- `Prev` becomes enabled. Clicking it returns to page 1 with `skip=0`.
- When the rendered page contains fewer than 20 rows (last page), `Next` is disabled.
- When `skip=0`, `Prev` is disabled.

### TC3 — Drill into any team's members (cross-team bypass)
Steps:
1. As superuser at `/admin/teams`, locate a team the superuser is **not** a member of (e.g. another user's personal team).
2. Click that row's `View members` link.

Expected:
- URL becomes `/admin/teams/<teamId>` and the page replaces the parent layout (trailing-underscore-opt-out route).
- Heading `Team members` is visible. A back link to `/admin/teams` is present.
- The list shows that team's members (email, full_name, role) — **read-only** (no promote/demote/remove buttons).
- Backend log: `admin_team_members_listed actor_id=<uuid> team_id=<teamId> count=<N>`.
- No 403 — the membership check is correctly bypassed for system admin.

### TC4 — Promote a regular user to system admin via confirm dialog
Preconditions: at least one non-admin user account exists (sign one up at `/signup` if needed).

Steps:
1. As superuser navigate to `/admin` (the existing users data table).
2. Locate the target non-admin user; open their actions dropdown.
3. Observe the entry `Promote to system admin` (visible only when current user is system admin, target is not already system admin, and target is not the current user).
4. Click it. A confirmation dialog appears with copy: `Promote <email> to system admin? They will gain access to every team and the admin panel.`
5. Click `Promote`.

Expected:
- Toast `Promoted to system admin` appears.
- The target user's row updates: role badge inside `span[data-slot="badge"]` flips to `Admin`.
- Backend log: `system_admin_promoted actor_id=<superuser_uuid> target_user_id=<target_uuid> already_admin=false`.
- The promote dropdown entry no longer appears for that user (already system admin guard).

### TC5 — Idempotent promote (already system admin)
Steps:
1. After TC4 succeeds, log out and log back in as the just-promoted user.
2. Verify they can now reach `/admin` and `/admin/teams`.
3. Log back in as the original superuser. Use TC4's flow against another existing system_admin user — the dropdown entry must not appear (UI guard); to exercise the API, post `POST /api/v1/admin/users/<system_admin_id>/promote-system-admin` directly (e.g. via a HTTP client with the superuser cookies).

Expected:
- Direct POST returns `200` with the unchanged `UserPublic` (no DB mutation).
- Backend log: `system_admin_promoted actor_id=<superuser_uuid> target_user_id=<target_uuid> already_admin=true`.

### TC6 — Non-admin is redirected from `/admin/teams`
Steps:
1. In a clean incognito context, sign up a fresh user at `/signup` (or log in as any user with `role=user`).
2. Navigate directly to `/admin/teams`.

Expected:
- The `requireSystemAdmin` guard fires; the URL ends up at `/` (root) — the user never sees the panel.
- Same redirect when navigating directly to `/admin/teams/<any-team-id>`.
- Same redirect when navigating directly to `/admin`.

### TC7 — API-layer 403 for non-admin (without UI)
Steps:
1. Authenticate as a non-admin user (cookies set).
2. Issue:
   - `GET /api/v1/admin/teams`
   - `GET /api/v1/admin/teams/<any-existing-id>/members`
   - `POST /api/v1/admin/users/<any-existing-id>/promote-system-admin`

Expected:
- All three return `403 Forbidden` with the standard `get_current_active_superuser` "doesn't have enough privileges" detail.
- No structured-log lines for `admin_*` events are emitted (the gate fires before the handler).

### TC8 — Unauthenticated requests fail closed
Steps:
1. With no session cookie, hit each admin endpoint listed in TC7.

Expected:
- All three return `401 Unauthorized` (cookie auth dependency rejects before the role check).

### TC9 — 404 on missing IDs
Steps:
1. As superuser, request:
   - `GET /api/v1/admin/teams/<random-uuid>/members`
   - `POST /api/v1/admin/users/<random-uuid>/promote-system-admin`

Expected:
- Both return `404` with the appropriate `Team not found` / `User not found` detail.

### TC10 — Sidebar entry conditional rendering
Steps:
1. As superuser, observe sidebar — `All Teams` entry is present under `Admin`.
2. Log out, log in as a regular user, observe sidebar.

Expected:
- Regular user does NOT see `All Teams`. (Both `Admin` and `All Teams` are gated on `currentUser.role === 'system_admin'`.)

## Out of Scope
- Demoting a system admin back to user (no endpoint exists by design).
- Editing team metadata from `/admin/teams` (read + drill-down only).
- Removing/promoting/demoting members from `/admin/teams/<teamId>` (read-only view; team-level mutations stay on `/teams/<teamId>` for team admins).
