---
estimated_steps: 11
estimated_files: 5
skills_used: []
---

# T02: Build Teams Dashboard route with role badges and empty state

Add the primary post-login landing experience: a Teams Dashboard listing the caller's teams with role badges, a 'Personal' chip on personal teams, sorted by created_at desc (matches backend), with an empty state that points the user to Create Team (T03 will wire that button up). This is the visible payload of M001 — without it the user can't see that the personal-team bootstrap (S02) actually worked.

**What changes:**

1. **Add `src/routes/_layout/teams.tsx`** as a TanStack file route. `loader` (or `useSuspenseQuery`) calls `TeamsService.readTeams()` and returns `{ data: TeamWithRole[], count: number }` from the backend's `{data, count}` envelope. Render a list/grid: each team is a Card with `team.name`, a Badge showing the user's `role` ('admin' colored variant, 'member' subtle variant), and a 'Personal' Badge when `team.is_personal`. Sort is server-side; do not re-sort client-side. Mobile-first: stack as a single column on small screens, 2-col on `md:`.

2. **Update `src/routes/_layout/index.tsx`** to redirect to `/teams` (or render the same Teams component). Easiest: change `Dashboard` to `<Navigate to="/teams" replace />` via TanStack `redirect()` in `beforeLoad`. Keep the existing `Welcome back, nice to see you again!` copy on the dashboard so the existing Playwright login.spec assertion still passes — render it on the `/teams` page header (e.g. `<p>Welcome back, nice to see you again!</p>` above the team list), or on a small dashboard panel above the list.

3. **Empty state** — when `data.length === 0` (which should never happen post-S02 because every signup creates a personal team, but handle it for robustness): show a card with text 'No teams yet' and a placeholder where T03's Create Team button will land. The button itself can be a stub with `disabled` and `data-testid="create-team-button"` — T03 wires the click handler.

4. **Team-card click target** — clicking a team card navigates to `/teams/$teamId` (T04 builds that route). For T02 you can stub the route file with a placeholder component so the link works without a 404.

5. **Sidebar entry** — add a 'Teams' item to `src/components/Sidebar/AppSidebar.tsx` `baseItems` (icon: `Users` from lucide-react) pointing at `/teams`. Reorder so Teams comes before Items.

6. **Generate the route tree.** TanStack auto-generates `routeTree.gen.ts` via the Vite plugin in dev; run `bun run dev` once or `bunx tsr generate` if the helper exists, then commit the regenerated tree.

**Mobile sizing:** Target 375px viewport. Cards span full width with `min-h-20`, role badge inline with name on a single line (truncate name with `truncate max-w-[60%]` if it would wrap). Tap target ≥40px (the whole card is the link).

**Failure modes:** API 401 → handled by main.tsx onError (T01). Empty list → empty state. Network failure → React Query default error UI shows a retry-friendly message; this slice does not need bespoke offline handling.

**Skill activation note:** caveman skill not available in this environment; skills_used: [].

## Inputs

- `frontend/src/client/sdk.gen.ts`
- `frontend/src/client/types.gen.ts`
- `frontend/src/routes/_layout.tsx`
- `frontend/src/routes/_layout/index.tsx`
- `frontend/src/components/Sidebar/AppSidebar.tsx`
- `frontend/src/components/ui/card.tsx`
- `frontend/src/components/ui/badge.tsx`

## Expected Output

- `frontend/src/routes/_layout/teams.tsx`
- `frontend/src/routes/_layout/teams.$teamId.tsx`
- `frontend/src/routes/_layout/index.tsx`
- `frontend/src/components/Sidebar/AppSidebar.tsx`
- `frontend/src/routeTree.gen.ts`

## Verification

cd frontend && bun run lint && bun run build && rg -n 'data-testid="team-card"|data-testid="role-badge"|data-testid="create-team-button"' src/routes/_layout/teams.tsx; test $? -eq 0
