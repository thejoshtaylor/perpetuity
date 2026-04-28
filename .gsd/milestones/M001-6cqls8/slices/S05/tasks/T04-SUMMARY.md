---
id: T04
parent: S05
milestone: M001-6cqls8
key_files:
  - frontend/src/routes/_layout/admin.teams_.$teamId.tsx
  - frontend/src/components/Admin/PromoteSystemAdminDialog.tsx
  - frontend/src/components/Admin/UserActionsMenu.tsx
  - frontend/src/routeTree.gen.ts
key_decisions:
  - Used the existing project `Dialog` primitive rather than introducing an AlertDialog dependency — `AlertDialog` isn't shipped in `components/ui/` and `RemoveMemberConfirm.tsx` already establishes Dialog as the project's confirm-dialog idiom.
  - Resolve team name for the heading from the existing `['admin', 'teams', ...]` query cache (falls back to raw teamId) rather than adding a `GET /admin/teams/{id}` endpoint — the slice plan explicitly forbids introducing a new endpoint here, and the cache hit path is the common one (admin clicks View members straight from the list).
  - Kept `UserActionsMenu`'s existing early-return for self instead of restructuring the component — the new Promote entry is gated only by currentUser-is-system-admin and target-not-already-admin, with self-exclusion already enforced one level up. Avoids touching unrelated Edit/Delete behavior for self-rows.
  - After adding the new TanStack file route, ran `bunx vite build` once before `bun run build` — MEM060 documents that `tsc -p tsconfig.build.json` runs first in the build script and fails on stale `routeTree.gen.ts` because the Vite plugin is what regenerates it.
duration: 
verification_result: passed
completed_at: 2026-04-25T01:26:34.298Z
blocker_discovered: false
---

# T04: Add /admin/teams/$teamId members view and PromoteSystemAdminDialog wired into /admin users dropdown

**Add /admin/teams/$teamId members view and PromoteSystemAdminDialog wired into /admin users dropdown**

## What Happened

Shipped two frontend additions that close S05 end-to-end. (1) `frontend/src/routes/_layout/admin.teams_.$teamId.tsx` — read-only members view at `/admin/teams/$teamId`, guarded by `requireSystemAdmin`, fed by `useSuspenseQuery` over `AdminService.readAdminTeamMembers({ teamId })`. The page renders a 'Team members' heading with the team name resolved opportunistically from the cached `['admin', 'teams', ...]` query (falls back to the raw teamId, no new endpoint introduced), a Back-to-teams `<Link>` to `/admin/teams`, and a list of `{full_name, email, role}` rows reusing the visual shape of `Teams/MembersList.tsx` minus all promote/demote/remove controls (out of scope here). (2) `frontend/src/components/Admin/PromoteSystemAdminDialog.tsx` — confirm dialog built on the existing `Dialog` primitive (no `AlertDialog` exists in this project) that calls `AdminService.promoteSystemAdmin({ userId })`, invalidates the `['users']` query on success, and surfaces 'Promoted to system admin' via `useCustomToast`. Dialog copy is exactly the contract string: 'Promote {email} to system admin? They will gain access to every team and the admin panel.' with action button label 'Promote'. (3) `frontend/src/components/Admin/UserActionsMenu.tsx` — added a 'Promote to system admin' dropdown entry that mounts the dialog. Conditions: rendered only when `currentUser.role === 'system_admin'` AND target's role !== 'system_admin'. The 'not self' rule is already enforced by the menu's existing early-return when `user.id === currentUser?.id` — kept that intact rather than restructure the component.

Auto-fix attempt 1 cause: the verification gate ran `bun run build` and failed because adding a new TanStack file route requires `bunx vite build` once to regenerate `routeTree.gen.ts` before `bun run build` (which runs `tsc -p tsconfig.build.json && vite build`) succeeds — tsc fires before vite has a chance to regenerate the tree (MEM060). Ran `bunx vite build` once, then re-ran `bun run lint` and `bun run build` in `frontend/` — both clean.

Routing: TanStack file-based routing converts `admin.teams_.$teamId.tsx` to route id `/_layout/admin/teams_/$teamId` and URL `/admin/teams/$teamId` — the trailing-underscore on `teams_` opts the `$teamId` segment out of nesting under any `teams` layout (mirroring `teams_.$teamId.tsx` from MEM048/MEM053). routeTree.gen.ts confirms `'/_layout/admin/teams_/$teamId': typeof LayoutAdminTeamsTeamIdRoute`.

## Verification

Ran the slice-level verification command verbatim: `cd frontend && bun run lint && bun run build`. Lint reported 'No fixes applied' (clean across 84 files). Build emitted both `admin.teams_._teamId-CqOf38Ch.js` and `admin_.teams-BVJbmXie.js` chunks, exit 0. routeTree.gen.ts confirmed updated with the new entry. All AdminService SDK methods (`readAdminTeamMembers`, `promoteSystemAdmin`) resolved against the T02-regenerated client without type errors. Pure-UI task — no backend or observability changes; relies on the structured INFO logs already shipped in T01 backend endpoints.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd frontend && bun run lint` | 0 | pass | 1100ms |
| 2 | `cd frontend && bunx vite build` | 0 | pass | 2000ms |
| 3 | `cd frontend && bun run build` | 0 | pass | 4000ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `frontend/src/routes/_layout/admin.teams_.$teamId.tsx`
- `frontend/src/components/Admin/PromoteSystemAdminDialog.tsx`
- `frontend/src/components/Admin/UserActionsMenu.tsx`
- `frontend/src/routeTree.gen.ts`
