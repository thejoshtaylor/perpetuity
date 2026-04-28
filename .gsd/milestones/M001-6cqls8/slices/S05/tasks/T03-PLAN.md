---
estimated_steps: 1
estimated_files: 3
skills_used: []
---

# T03: Build /admin/teams paginated list page

Add `frontend/src/routes/_layout/admin.teams.tsx` (so the URL is `/admin/teams`, sibling of `/admin`). Wrap the route with `beforeLoad: requireSystemAdmin` from T02. Component uses `useSuspenseQuery` against `AdminService.readAllTeams({ skip, limit })` with `skip` and `limit` from `useSearch` (default skip=0, limit=20). Render: page heading 'All Teams' + subtitle 'System admin: every team in the workspace.' Table columns (use the existing `DataTable` from `frontend/src/components/Common/DataTable`): Name, Slug, Personal? (badge), Created, Members count (call site-note: omit count from initial table to keep the response simple — show only `name`, `slug`, `is_personal` badge, `created_at` formatted, plus a 'View members' link to `/admin/teams/$teamId`). Pagination controls: Prev/Next buttons that update the URL search via TanStack Router's `useNavigate({ search: ... })`. Disable Prev when skip=0; disable Next when `data.data.length < limit`. Build the table data and column defs in a small co-located file under `frontend/src/components/Admin/AdminTeamsColumns.tsx` (following the existing `frontend/src/components/Admin/columns.tsx` pattern). Add a sidebar entry conditionally (only when `currentUser.role === 'system_admin'`) — extend `frontend/src/components/Sidebar/AppSidebar.tsx` to push `{ icon: Shield, title: 'All Teams', path: '/admin/teams' }` next to the existing 'Admin' entry. Empty state: render `<Card>` with 'No teams in the system yet.' Suspense fallback: skeleton table.

## Inputs

- ``frontend/src/routes/_layout/admin.tsx` — reference for `beforeLoad` shape and DataTable usage`
- ``frontend/src/lib/auth-guards.ts` — provides `requireSystemAdmin` (T02 output)`
- ``frontend/src/client/sdk.gen.ts` — provides `AdminService` (T02 output)`
- ``frontend/src/components/Common/DataTable.tsx` — shared table component`
- ``frontend/src/components/Admin/columns.tsx` — pattern for column-def files`
- ``frontend/src/components/Sidebar/AppSidebar.tsx` — sidebar item registration site`

## Expected Output

- ``frontend/src/routes/_layout/admin.teams.tsx` — new route at /admin/teams gated by requireSystemAdmin, renders paginated team list`
- ``frontend/src/components/Admin/AdminTeamsColumns.tsx` — column defs for the admin teams table`
- ``frontend/src/components/Sidebar/AppSidebar.tsx` — adds 'All Teams' sidebar item for system admins`

## Verification

cd frontend && bun run lint && bun run build

## Observability Impact

None — pure UI consuming the existing logged backend endpoint.
