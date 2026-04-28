---
estimated_steps: 1
estimated_files: 3
skills_used: []
---

# T04: Build /admin/teams/$teamId members view + PromoteSystemAdminDialog wired into /admin users list

Two additions. (1) `frontend/src/routes/_layout/admin.teams_.$teamId.tsx` (trailing-underscore opt-out per MEM048 so the page replaces the parent layout, mirroring `teams_.$teamId.tsx`). Guarded by `requireSystemAdmin`. Uses `useSuspenseQuery` against `AdminService.readAdminTeamMembers({ teamId })`. Renders: page heading 'Team members' with the team's name (fetched via the existing teams list query if cached, else display the teamId; do not introduce a new endpoint), a back link to `/admin/teams`, and a list of `{email, full_name, role}` per member — reuse the visual shape of `frontend/src/components/Teams/MembersList.tsx` but read-only (no promote/demote/remove actions in this view; that is out of scope). (2) Add `frontend/src/components/Admin/PromoteSystemAdminDialog.tsx` — a confirm dialog (use the project's existing `AlertDialog` from `components/ui/alert-dialog.tsx` if present, else `Dialog`) that calls `AdminService.promoteSystemAdmin({ userId })` on confirm, invalidates `['users']` query on success, and surfaces a success toast 'Promoted to system admin'. Wire it into `frontend/src/components/Admin/UserActionsMenu.tsx` as a new dropdown entry 'Promote to system admin' — only shown when (a) `currentUser.role === 'system_admin'`, (b) target user's role is NOT already `system_admin`, and (c) target is not the current user (system admins do not need to self-promote). The confirm copy is exactly: 'Promote {email} to system admin? They will gain access to every team and the admin panel.' with action button label 'Promote'.

## Inputs

- ``frontend/src/routes/_layout/teams_.$teamId.tsx` — pattern for trailing-underscore detail routes (MEM048/MEM053)`
- ``frontend/src/components/Teams/MembersList.tsx` — visual shape for the read-only member list`
- ``frontend/src/components/Admin/UserActionsMenu.tsx` — existing dropdown to extend`
- ``frontend/src/components/Teams/RemoveMemberConfirm.tsx` — reference for confirm-dialog component shape`
- ``frontend/src/lib/auth-guards.ts` — provides `requireSystemAdmin` (T02 output)`
- ``frontend/src/client/sdk.gen.ts` — provides `AdminService.readAdminTeamMembers` and `AdminService.promoteSystemAdmin` (T02 output)`

## Expected Output

- ``frontend/src/routes/_layout/admin.teams_.$teamId.tsx` — new read-only members view at /admin/teams/$teamId`
- ``frontend/src/components/Admin/PromoteSystemAdminDialog.tsx` — confirm dialog component`
- ``frontend/src/components/Admin/UserActionsMenu.tsx` — adds 'Promote to system admin' entry visible only when target is non-admin and not self`

## Verification

cd frontend && bun run lint && bun run build

## Observability Impact

None — pure UI consuming the existing logged backend endpoint.
