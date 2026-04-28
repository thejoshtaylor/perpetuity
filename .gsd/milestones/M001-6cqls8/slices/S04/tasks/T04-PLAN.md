---
estimated_steps: 18
estimated_files: 7
skills_used: []
---

# T04: Members list with promote/demote/remove controls

Complete the team-detail page with a Members list and admin-only promote/demote/remove controls. Surfaces the S03 PATCH role and DELETE member endpoints, with last-admin and personal-team errors handled as toasts.

**What changes:**

1. **Members listing.** The backend has no `GET /teams/{id}/members` endpoint yet. Two options:
   - **Option A (chosen):** Add `GET /api/v1/teams/{team_id}/members` to `backend/app/api/routes/teams.py` returning `[{user_id, email, full_name, role}]` for callers who are members of the team (404/403 otherwise). Mirror the precondition pattern: a new `_assert_caller_is_team_member` helper next to `_assert_caller_is_team_admin`. Add backend integration tests in `backend/tests/api/routes/test_members.py` (extend the existing file): 1 happy path, 1 non-member 403, 1 unknown-team 404. Regenerate the OpenAPI client (`bun run generate-client`).
   - Option B (rejected): client-side merge with `/users/` is admin-only and leaks all users. Option A is cleanly scoped.

2. **Members component `src/components/Teams/MembersList.tsx`** — `useSuspenseQuery({ queryKey: ['team', teamId, 'members'], queryFn: () => TeamsService.readTeamMembers({ teamId }) })`. Render a list with avatar/initials, name+email, and a Role badge. For each row, when the *caller* is admin and the row is not the caller themselves, show a `DropdownMenu` (already in ui/) with: 'Promote to admin' / 'Demote to member' (whichever applies, hidden when no-op) and 'Remove from team'. Wire 'Remove' to a confirm dialog.

3. **Mutations.**
   - Promote/demote: `useMutation` calling `TeamsService.updateMemberRole({ teamId, userId, requestBody: { role } })`. On success: invalidate `['team', teamId, 'members']` and `['teams']`; toast 'Role updated'. On 400 'Cannot demote the last admin' → toast verbatim. On 403 → toast 'Only team admins can change roles'.
   - Remove: `useMutation` calling `TeamsService.removeMember({ teamId, userId })`. On success: invalidate same keys; toast 'Member removed'. On 400 'Cannot remove the last admin' or 'Cannot remove members from personal teams' → toast the backend `detail` verbatim.

4. **Wire into team detail route `src/routes/_layout/teams.$teamId.tsx`** (T03 stubbed it). Layout: header (team name, role badge, Personal chip), Invite section (T03), Members section. When `is_personal === true`, omit Invite section AND omit row-level controls (the only member is the owner).

5. **Confirm dialog `src/components/Teams/RemoveMemberConfirm.tsx`** — uses ui/dialog, requires the user to type the member's email or a fixed phrase 'remove' to enable the destructive button. Accessible: focus-trapped, Escape closes, primary button has `data-testid="remove-member-confirm"`.

**Mobile sizing:** Member rows stack avatar+name on mobile, role badge + dropdown trigger on a second row. DropdownMenu uses Radix's mobile-friendly positioning. Confirm dialog is full-width on small screens.

**Failure modes (Q5):**
- Stale members cache after another admin removed someone → React Query refetch on focus catches it; the next mutation will get the up-to-date list. If a stale row's mutation 404s, toast 'Member already removed' and refetch.
- Backend MEM035 fix (PATCH role returns expired-ORM team) is in place — no FE workaround needed.
- Personal team removal attempts → backend rejects with 400; FE never surfaces the button there.

**Negative tests (Q7):** see T05's E2E coverage of last-admin protection.

**Skill activation note:** caveman skill not available; skills_used: [].

## Inputs

- `backend/app/api/routes/teams.py`
- `backend/app/models.py`
- `backend/tests/api/routes/test_members.py`
- `frontend/src/client/sdk.gen.ts`
- `frontend/src/client/types.gen.ts`
- `frontend/src/routes/_layout/teams.$teamId.tsx`
- `frontend/src/components/ui/dialog.tsx`
- `frontend/src/components/ui/dropdown-menu.tsx`
- `frontend/src/components/ui/badge.tsx`

## Expected Output

- `backend/app/api/routes/teams.py`
- `backend/tests/api/routes/test_members.py`
- `frontend/src/client/sdk.gen.ts`
- `frontend/src/client/types.gen.ts`
- `frontend/src/components/Teams/MembersList.tsx`
- `frontend/src/components/Teams/RemoveMemberConfirm.tsx`
- `frontend/src/routes/_layout/teams.$teamId.tsx`

## Verification

cd backend && uv run pytest tests/api/routes/test_members.py -v && cd ../frontend && bun run lint && bun run build && rg -n 'data-testid="member-row"|data-testid="member-actions"|data-testid="remove-member-confirm"' src/components/Teams/ ; test $? -eq 0

## Observability Impact

New backend GET /teams/{id}/members emits INFO log `members_listed team_id=<uuid> caller_id=<uuid> count=<n>` (mirrors S03 logging conventions; no team name logged per MEM028). Toasts surface every error verbatim from backend detail.
