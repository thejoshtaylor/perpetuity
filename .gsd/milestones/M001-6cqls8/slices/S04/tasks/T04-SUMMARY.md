---
id: T04
parent: S04
milestone: M001-6cqls8
key_files:
  - backend/app/api/routes/teams.py
  - backend/app/models.py
  - backend/tests/api/routes/test_members.py
  - frontend/src/client/sdk.gen.ts
  - frontend/src/client/types.gen.ts
  - frontend/src/client/schemas.gen.ts
  - frontend/openapi.json
  - frontend/src/components/Teams/MembersList.tsx
  - frontend/src/components/Teams/RemoveMemberConfirm.tsx
  - frontend/src/routes/_layout/teams.$teamId.tsx
key_decisions:
  - Backend GET /teams/{id}/members chosen over client-side merge — /users/ is admin-only and would leak full user roster.
  - Added `_assert_caller_is_team_member` helper next to `_assert_caller_is_team_admin` so read-only endpoints can drop the admin requirement while keeping identical 404→403 ordering.
  - RemoveMemberConfirm accepts both the member's email and the literal phrase 'remove' as valid confirmation tokens — either enables the destructive button.
  - Invalidate ['team', teamId, 'members'] AND ['teams'] on every mutation success — keeps the dashboard role badge in sync with detail-page role changes.
  - Use callerIsAdmin && !is_personal to gate row-level controls: personal teams have only one member (the owner) and cannot have members removed.
duration: 
verification_result: passed
completed_at: 2026-04-25T00:23:43.347Z
blocker_discovered: false
---

# T04: Added GET /teams/{id}/members backend endpoint and frontend MembersList with promote/demote/remove dropdown plus type-to-confirm RemoveMemberConfirm dialog

**Added GET /teams/{id}/members backend endpoint and frontend MembersList with promote/demote/remove dropdown plus type-to-confirm RemoveMemberConfirm dialog**

## What Happened

Implemented the final S04 task: a Members list with admin-only promote/demote/remove controls wired into /teams/$teamId.

Backend: Added GET /api/v1/teams/{team_id}/members returning {data: [{user_id, email, full_name, role}], count}. Introduced a `_assert_caller_is_team_member` helper paired with the existing `_assert_caller_is_team_admin` so read-only access can drop the admin requirement while keeping the same 404/403 ordering (404 for missing team, 403 for non-members). The endpoint uses a single SELECT JOIN on User × TeamMember filtered by team_id (no N+1, ordered by email). Added two new SQLModel response shapes (`TeamMemberPublic`, `TeamMembersPublic`) in app/models.py. Logging mirrors S03 conventions: `members_listed team_id=<uuid> caller_id=<uuid> count=<n>` (no team name, no email).

Tests: Extended backend/tests/api/routes/test_members.py with 3 new cases — happy-path roster (admin sees their own row + invited member with correct roles), non-member 403, unknown team 404. All 12 tests pass (9 prior + 3 new).

Frontend client: Regenerated via scripts/generate-client.sh — `TeamsService.readTeamMembers` and `TeamMemberPublic`/`TeamMembersPublic` types now exist in src/client/{sdk,types,schemas}.gen.ts.

Frontend UI:
- MembersList.tsx: useSuspenseQuery with key ['team', teamId, 'members']. Renders avatar (initials fallback), name (full_name preferred, falls back to email), email subline, RoleBadge, and an actions DropdownMenu shown only when callerIsAdmin && !isSelf. Mobile-friendly stacking (avatar+name on one row, badge+menu on another at <sm breakpoint). Promote/demote items hide the no-op direction. Mutations invalidate ['team', teamId, 'members'] and ['teams']; toasts surface backend `detail` verbatim on 400, friendlier strings on 403, and a refetch+'Member already removed' toast on 404.
- RemoveMemberConfirm.tsx: typed-confirm dialog requiring the user to type either the member's email or the literal phrase 'remove' before the destructive button enables. Uses ui/dialog (Radix focus-trap, Escape closes), autoFocus on the input, primary button has data-testid="remove-member-confirm".
- routes/_layout/teams.$teamId.tsx: replaced the T04 placeholder paragraph with a Members section. Pulls the caller's id from useSuspenseQuery({ queryKey: ['currentUser'] }) — _layout's beforeLoad already ensures the cache is populated. When team.is_personal is true, both the Invite section and the row-level controls are omitted (showMemberControls = adminRole && !is_personal).

Verification: All four verification checks from T04-PLAN passed — pytest (12/12), lint (0 issues), tsc+vite build (no type errors), and the testid grep (member-row, member-actions, remove-member-confirm). The earlier gate failure (`bun run build` exit 1) traced to running the script from the repo root where no build script exists; the slice plan's verification block explicitly cd's into frontend first, so this is the contract that holds.

Decisions:
- Chose Option A (new GET endpoint) over Option B (client-side merge with /users/) because /users/ is admin-only and would leak the global user roster.
- Type-to-confirm accepts BOTH the member email and the fixed phrase 'remove' so non-keyboard-confident users have an alternative; either is sufficient to enable the destructive button.
- 404 in the promote/demote/remove mutations triggers a refetch + 'Member already removed' toast (matches the FailureModes section in T04-PLAN).

## Verification

Ran the full T04-PLAN verification command: backend pytest (12 passed), frontend lint (clean), frontend build (tsc + vite build succeed, 2245 modules transformed), and rg testid grep (all three required testids present). Backend tests cover happy-path, non-member 403, and unknown-team 404 for the new GET endpoint. Frontend type-checks pass under tsc -p tsconfig.build.json with the regenerated client types.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && uv run pytest tests/api/routes/test_members.py -v` | 0 | ✅ pass | 940ms |
| 2 | `cd frontend && bun run lint` | 0 | ✅ pass | 1200ms |
| 3 | `cd frontend && bun run build` | 0 | ✅ pass | 1840ms |
| 4 | `rg -n 'data-testid="member-row"|data-testid="member-actions"|data-testid="remove-member-confirm"' frontend/src/components/Teams/` | 0 | ✅ pass | 30ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `backend/app/api/routes/teams.py`
- `backend/app/models.py`
- `backend/tests/api/routes/test_members.py`
- `frontend/src/client/sdk.gen.ts`
- `frontend/src/client/types.gen.ts`
- `frontend/src/client/schemas.gen.ts`
- `frontend/openapi.json`
- `frontend/src/components/Teams/MembersList.tsx`
- `frontend/src/components/Teams/RemoveMemberConfirm.tsx`
- `frontend/src/routes/_layout/teams.$teamId.tsx`
