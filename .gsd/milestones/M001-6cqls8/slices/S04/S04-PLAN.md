# S04: Frontend: auth + team dashboard

**Goal:** Convert the React frontend from localStorage JWT to httpOnly cookie auth, regenerate the OpenAPI client, and ship the M001 team-collaboration UI: a Teams Dashboard listing the caller's teams with role badges, a Create-Team modal, an Invite-link UI with copy-to-clipboard and an /invite/{code} acceptance route, and a Members list with promote/demote/remove controls — all working end-to-end at a 375px mobile viewport against the real S03 backend.
**Demo:** User can log in, see their teams dashboard, create a team, copy an invite link, and manage members — all working on a 375px mobile viewport in the browser

## Must-Haves

- **Demo:** A logged-out user opens the app on a 375px viewport, signs up, lands on the Teams Dashboard showing their auto-created personal team with an "admin" badge, clicks Create Team, names it "Engineering" and sees it appear in the list. They click Invite, copy the URL. A second user opens that URL in another browser, is prompted to log in (or sign up + redirect back), accepts the invite and now sees both their personal team and the joined "Engineering" team (member badge). Back as the inviter, they promote the new user to admin, then demote them, then remove them. Personal teams have no Invite/Create-shaped controls. Logout clears the session cookie and bounces to /login.
- **Must-haves:**
- M1: httpOnly-cookie auth: `OpenAPI.WITH_CREDENTIALS=true`, `OpenAPI.TOKEN` removed, `useAuth` calls `/auth/login`, `/auth/signup`, `/auth/logout`, and the route guard treats "current user query succeeds" as the truth source rather than `localStorage.getItem('access_token')`.
- M2: OpenAPI client regenerated against the current backend so `TeamsService` (read/create/invite/join/updateMemberRole/removeMember), `AuthService` (login/signup/logout), and `UserPublic.role: 'user' | 'system_admin'` all exist; no leftover `is_superuser` references in `src/`.
- M3: Teams Dashboard at `/_layout/teams` (and the existing `/` index links to it / replaces it) lists teams from `GET /api/v1/teams` with name, role badge ('admin' / 'member'), 'Personal' chip when `is_personal`, and an empty state.
- M4: Create-Team modal calls `POST /api/v1/teams`, optimistic-or-invalidate refresh of the teams query, validation errors rendered inline.
- M5: Per-team Invite UI: Invite button on non-personal admin teams calls `POST /teams/{id}/invite`, shows the returned URL, supports copy-to-clipboard with a success toast and visible "Copied" state.
- M6: `/invite/{code}` route POSTs `/teams/join/{code}`. Logged-out user is redirected to `/login?next=/invite/<code>` and bounced back after login. 404/410/409 responses surface as user-readable errors.
- M7: Members tab on a non-personal team lists members with role badges, admin-only role-toggle (PATCH role), admin-only remove (DELETE) with confirm dialog. Last-admin (400) and personal-team (400) backend errors are surfaced as toasts and do not crash the UI.
- M8: 375px mobile viewport: every flow above is usable — touch targets ≥40px, no horizontal scroll, modals/dialogs and copy-link UI fit on screen. Verified by a Playwright `Mobile Chrome` project.
- **Threat surface (Q3):**
- Abuse: invite URL is a bearer token. UI never logs the raw code, never embeds it in browser history beyond the `/invite/{code}` route, and never shows it to non-members of the team it was issued for. Copy-link is the explicit action.
- Data exposure: No team name/slug logged in the browser console. `GET /api/v1/teams` already filters server-side to caller membership — the UI must NOT call `/admin/*` endpoints from non-admin pages.
- Input trust: Team name is bounded server-side (1..255). Frontend mirrors the bound to give friendly errors but treats server-side as authoritative.
- Cookie auth: cross-origin XHR must use `withCredentials`/`OpenAPI.WITH_CREDENTIALS=true`; no token ever lands in localStorage. Logout must call `/auth/logout` (not just clear cookies client-side, which is impossible for httpOnly).
- **Requirement impact (Q4):**
- Touched: R022 (mobile usability) — every flow added must be usable at 375px. Validates R022 partially (M001 portion).
- Re-verified: R001 (signup creates personal team) and R002/R003 (team CRUD) — covered by integration tests in S02/S03; FE wires through.
- Decisions revisited: D001 (cookie auth — confirms FE consumes it correctly), D008 (React Query for server state — extends to teams cache).

## Proof Level

- This slice proves: final-assembly — this slice closes the M001 user-facing loop. Real runtime is required (Vite dev server + FastAPI backend + Postgres). UAT-style Playwright tests at the real entrypoint are the verification gate.

## Integration Closure

- Upstream surfaces consumed: `/api/v1/auth/{signup,login,logout}` (S01), `/api/v1/users/me` (S01), `/api/v1/teams` GET+POST (S02), `/api/v1/teams/{id}/invite` (S03), `/api/v1/teams/join/{code}` (S03), `/api/v1/teams/{id}/members/{uid}/role` PATCH (S03), `/api/v1/teams/{id}/members/{uid}` DELETE (S03).
- New wiring introduced: `OpenAPI.WITH_CREDENTIALS=true`, `useAuth` cookie-mode hook, `TeamsService` calls in dashboard / create / invite / members views, `/invite/{code}` route, mobile Playwright project.
- What remains before M001 is end-to-end usable: S05 (system admin panel) — independent of S04. After S04, a normal user's full collaboration loop is usable in a browser.

## Verification

- Runtime signals: browser console emits structured warnings on auth failure (`auth_redirect reason=401`); React Query cache keys (`['currentUser']`, `['teams']`, `['team', id, 'members']`) follow a predictable scheme so devtools introspection is trivial.
- Inspection surfaces: TanStack Router devtools (already mounted) for route state; React Query devtools (already mounted) for cache/fetch state; Playwright trace viewer on first-retry failure (already configured) for E2E debug.
- Failure visibility: API error toasts surface backend `detail` strings verbatim (S03 errors are already user-readable). 401 anywhere triggers a single redirect to `/login` from `main.tsx` query-cache `onError`, replacing the existing `localStorage.removeItem` logic.
- Redaction constraints: invite codes are NEVER logged to console (mirrors backend MEM028 / S03 `_code_hash` rule). Email addresses are not logged client-side either.

## Tasks

- [x] **T01: Regenerate OpenAPI client + flip frontend to httpOnly cookie auth** `est:2h`
  Convert the frontend off localStorage JWT and onto httpOnly cookies (D001/MEM001/MEM023), and regenerate the OpenAPI client from the current backend so Teams + Auth + role-based User types are available downstream. This task is the foundation for T02–T05; nothing else compiles until this lands.

**What changes:**

1. **Regenerate the client.** Start the backend (`cd backend && uv run fastapi run --reload app/main.py` or hit the existing dev server) so it serves `/api/v1/openapi.json`. From `frontend/`, run the existing repo helper if there is one, else: `curl http://localhost:8000/api/v1/openapi.json -o openapi.json && bun run generate-client`. Verify the regenerated `src/client/sdk.gen.ts` now exposes `AuthService` (signup, login, logout), `TeamsService` (readTeams, createTeam, inviteToTeam, joinTeam, updateMemberRole, removeMember), and that `UserPublic` in `src/client/types.gen.ts` has `role: 'user' | 'system_admin'` instead of `is_superuser`.

2. **Flip OpenAPI runtime config in `src/main.tsx`.** Set `OpenAPI.WITH_CREDENTIALS = true`. Delete `OpenAPI.TOKEN = async () => localStorage.getItem('access_token') || ''`. In the QueryCache/MutationCache `onError`, replace `localStorage.removeItem('access_token')` with a no-op (cookies are httpOnly — the redirect alone is the action) — leave the `window.location.href = '/login'` redirect on 401/403.

3. **Rewrite `src/hooks/useAuth.ts`.** Remove `isLoggedIn` (export keep-as-deprecated-alias if needed by routes — see step 4). Replace with: `useQuery({ queryKey: ['currentUser'], queryFn: UsersService.readUserMe, retry: false })` (always enabled). `loginMutation` calls `AuthService.login({ requestBody: { email, password } })` then `queryClient.invalidateQueries({ queryKey: ['currentUser'] })` then `navigate({ to: '/' })`. `signUpMutation` calls `AuthService.signup` (which already issues a session cookie per the backend signup route) and on success invalidates `['currentUser']` and navigates to `/`. `logout()` becomes `await AuthService.logout(); queryClient.removeQueries(); navigate({ to: '/login' })`.

4. **Convert route guards.** In `src/routes/_layout.tsx` `beforeLoad`, replace `if (!isLoggedIn())` with a server-truth check: `try { await queryClient.ensureQueryData({ queryKey: ['currentUser'], queryFn: UsersService.readUserMe }) } catch { throw redirect({ to: '/login', search: { next: location.pathname } }) }`. Pass the `queryClient` via `createRouter({ context: { queryClient } })` in `main.tsx` (the standard TanStack pattern). Apply the inverse logic in `src/routes/login.tsx` and `src/routes/signup.tsx` `beforeLoad`: if currentUser query succeeds, redirect to `/`. Remove all `isLoggedIn`/`localStorage.getItem('access_token')` references except in tests for the duration of T01 — the `Redirects to /login when token is wrong` test will be deleted in step 7 because tokens are no longer used.

5. **Replace `is_superuser` with `role === 'system_admin'`** in (a) `src/components/Sidebar/AppSidebar.tsx` (`currentUser?.role === 'system_admin'`), (b) `src/routes/_layout/admin.tsx` `beforeLoad`, (c) `src/routes/_layout/settings.tsx` (the `finalTabs` computation). For Admin user CRUD UI (`AddUser.tsx`, `EditUser.tsx`, `columns.tsx`): map the form's `is_superuser` boolean to `role: is_superuser ? 'system_admin' : 'user'` on submit, and render the Admin/User badge by `row.original.role === 'system_admin'`. Keep the boolean checkbox UI shape — it's friendlier than a 2-option select; the conversion is at the boundary. Do not deepen the admin UI revamp here — S05 owns it.

6. **Update `src/main.tsx`** to expose `queryClient` on the router context (required by step 4). Pattern: `const router = createRouter({ routeTree, context: { queryClient } })`; in `__root.tsx` declare `Route.context()` accordingly.

7. **Update Playwright tests for cookie auth.** (a) `tests/auth.setup.ts` already uses `page.context().storageState({ path: authFile })` which captures cookies — works unchanged after the FE switch. (b) Delete the `Redirects to /login when token is wrong` test in `tests/login.spec.ts` (it sets `localStorage.access_token` which no longer exists). (c) The `Logged-out user cannot access protected routes` test expects `/settings` to bounce to `/login` after logout — with cookie auth this still works because the route guard's `currentUser` query will 401. (d) Run `bunx playwright test --project=chromium login.spec.ts auth.setup.ts` and confirm green.

**Failure modes (Q5):**
- Backend returns 401 mid-session → redirect to `/login` via the existing query-cache `onError` (already in place in `main.tsx`). On 4xx during login submission, the error toast surfaces backend `detail` and the form stays on `/login`.
- Backend unreachable → fetch throws; React Query retries are off for `currentUser` (`retry: false`) so the `_layout` guard fails fast and bounces to `/login`. Acceptable degradation.
- Stale storageState (cookie expired across runs) → Playwright `auth.setup` regenerates per project run.

**Negative tests (Q7):**
- Login with wrong password → backend returns 400 "Incorrect email or password"; verified by existing test (`Log in with invalid password`).
- Hitting `/_layout/index` while logged out → redirect to `/login`; verified by `Logged-out user cannot access protected routes` after logout.
- The `Redirects to /login when token is wrong` test is removed (no longer applicable in cookie mode); document the removal in the task SUMMARY for forensic traceability.

**Mobile constraint:** No layout changes here, but T05's mobile project must run against the post-T01 codebase — keep this task UI-neutral.

**Skill activation note:** The planner attempted to activate the `caveman` skill per step 2 of the prompt, but it is not in the user-invocable skill list for this environment; record `skills_used: []` and proceed.
  - Files: `frontend/src/client/sdk.gen.ts`, `frontend/src/client/types.gen.ts`, `frontend/src/client/schemas.gen.ts`, `frontend/src/client/core/OpenAPI.ts`, `frontend/src/main.tsx`, `frontend/src/hooks/useAuth.ts`, `frontend/src/routes/_layout.tsx`, `frontend/src/routes/login.tsx`, `frontend/src/routes/signup.tsx`, `frontend/src/routes/__root.tsx`, `frontend/src/components/Sidebar/AppSidebar.tsx`, `frontend/src/routes/_layout/admin.tsx`, `frontend/src/routes/_layout/settings.tsx`, `frontend/src/components/Admin/AddUser.tsx`, `frontend/src/components/Admin/EditUser.tsx`, `frontend/src/components/Admin/columns.tsx`, `frontend/tests/login.spec.ts`
  - Verify: cd frontend && bun run lint && bun run build && rg -n 'is_superuser|access_token|localStorage' src/ tests/ | rg -v 'src/client/(sdk|types|schemas)\.gen\.ts' && bunx playwright test --project=chromium tests/login.spec.ts tests/auth.setup.ts; test $? -eq 0

- [x] **T02: Build Teams Dashboard route with role badges and empty state** `est:1.5h`
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
  - Files: `frontend/src/routes/_layout/teams.tsx`, `frontend/src/routes/_layout/teams.$teamId.tsx`, `frontend/src/routes/_layout/index.tsx`, `frontend/src/components/Sidebar/AppSidebar.tsx`, `frontend/src/routeTree.gen.ts`
  - Verify: cd frontend && bun run lint && bun run build && rg -n 'data-testid="team-card"|data-testid="role-badge"|data-testid="create-team-button"' src/routes/_layout/teams.tsx; test $? -eq 0

- [x] **T03: Create-Team modal + invite-link UI + /invite/{code} acceptance route** `est:2h`
  Wire the team-creation and invite flows. After this task the inviter can create a team, generate an invite URL, and the invitee can accept it from the browser. Personal teams must hide both Invite and (where applicable) Create-shaped controls; T03 must respect the backend's structural rules (D003) without re-implementing them.

**What changes:**

1. **Create Team modal in `src/components/Teams/CreateTeamDialog.tsx`** using `@radix-ui/react-dialog` (already installed) wrapped via `src/components/ui/dialog.tsx`. Form: single `name` field (zod min(1).max(255), trim). Submit calls `TeamsService.createTeam({ requestBody: { name } })`. On success: invalidate `['teams']` query, close dialog, toast 'Team created'. On 4xx: show `error.body.detail` inline. Wire the existing `data-testid="create-team-button"` (T02 stub) to open the dialog.

2. **Invite UI in `src/components/Teams/InviteButton.tsx`** — Button visible only on non-personal teams to admins (the backend will 403 otherwise; we hide for clarity). Click calls `TeamsService.inviteToTeam({ teamId })`. On success, the response `{ code, url, expires_at }` is rendered in a small panel: `url` in a read-only `Input` + a Copy button using the existing `useCopyToClipboard` hook. Toast 'Copied' on copy success. The panel shows `expires in 7 days` (computed from `expires_at`). Never call `console.log(invite.code)` or `console.log(invite.url)` (mirrors backend MEM028 and the slice's redaction constraint).

3. **Wire into team detail route `src/routes/_layout/teams.$teamId.tsx`** (T02 stubbed it). Fetch the team via `TeamsService.readTeams()` then `find(t => t.id === teamId)` (no per-team GET endpoint exists — keep the cache lookup simple). If not found / no membership: 404 component. Render team name, role badge, and `<InviteButton />` when role==='admin' and !is_personal.

4. **/invite/{code} acceptance route — `src/routes/invite.$code.tsx`** (top-level, NOT under `_layout`). `beforeLoad`: `try { await queryClient.ensureQueryData({ queryKey: ['currentUser'], ... }) } catch { throw redirect({ to: '/login', search: { next: location.href } }) }`. Component: on mount, runs `TeamsService.joinTeam({ code })` via a mutation. On success: invalidate `['teams']`, toast 'Joined <team.name>', redirect to `/teams/${team.id}`. On 404 → 'Invite not found' card with link back to `/teams`. On 410 → 'This invite has expired or already been used'. On 409 → 'You are already a member' + redirect to that team after 2s. Display loading spinner while the mutation runs.

5. **Login redirect handling.** `src/routes/login.tsx` `beforeLoad` already redirects logged-in users to `/`; extend the `loginMutation.onSuccess` in `useAuth` to honor `?next=` from the URL: `const next = new URLSearchParams(location.search).get('next') || '/'; navigate({ to: next })`. Same for signup. Sanitize `next` to start with `/` to prevent open-redirect.

**Threat surface (Q3):** Open-redirect via `?next=`: only honor relative paths matching `^/[^/]`. Invite codes never logged. Copy-to-clipboard uses `navigator.clipboard.writeText` which requires HTTPS or localhost — Vite dev meets this, document as known limitation if deployed to non-https origin.

**Failure modes (Q5):**
- POST /teams returns 409 (slug conflict) → toast 'Name conflict, try another' and keep modal open.
- POST /teams/{id}/invite returns 403 (caller not admin) → toast 'Only team admins can invite' and remove the button (defensive — the UI shouldn't surface it but stale React Query data could).
- POST /teams/join/{code} returns 404/410/409 → distinct toasts per status, never crash.
- Clipboard API unavailable → fallback to `document.execCommand('copy')` on a hidden textarea OR show the URL in a manually-selectable field with text 'Copy manually'.

**Negative tests (Q7):** invalid invite code path — see T05.

**Skill activation note:** caveman skill not available; skills_used: [].
  - Files: `frontend/src/components/Teams/CreateTeamDialog.tsx`, `frontend/src/components/Teams/InviteButton.tsx`, `frontend/src/routes/_layout/teams.tsx`, `frontend/src/routes/_layout/teams.$teamId.tsx`, `frontend/src/routes/invite.$code.tsx`, `frontend/src/hooks/useAuth.ts`, `frontend/src/routeTree.gen.ts`
  - Verify: cd frontend && bun run lint && bun run build && rg -n 'data-testid="invite-button"|data-testid="copy-invite-url"|data-testid="create-team-submit"' src/components/Teams/ src/routes/ ; test $? -eq 0

- [x] **T04: Members list with promote/demote/remove controls** `est:1.5h`
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
  - Files: `backend/app/api/routes/teams.py`, `backend/tests/api/routes/test_members.py`, `frontend/src/client/sdk.gen.ts`, `frontend/src/client/types.gen.ts`, `frontend/src/components/Teams/MembersList.tsx`, `frontend/src/components/Teams/RemoveMemberConfirm.tsx`, `frontend/src/routes/_layout/teams.$teamId.tsx`
  - Verify: cd backend && uv run pytest tests/api/routes/test_members.py -v && cd ../frontend && bun run lint && bun run build && rg -n 'data-testid="member-row"|data-testid="member-actions"|data-testid="remove-member-confirm"' src/components/Teams/ ; test $? -eq 0

- [x] **T05: Mobile-Chrome Playwright project + end-to-end teams.spec covering full slice demo** `est:2h`
  Add the slice's verification gate: a Playwright `Mobile Chrome` project at 375px and a full-flow `tests/teams.spec.ts` E2E spec that drives the demo in the slice goal. This is the slice's objective stopping condition — green here means S04 ships.

**What changes:**

1. **Add a mobile project to `frontend/playwright.config.ts`.** Uncomment / add: `{ name: 'mobile-chrome', use: { ...devices['Pixel 5'], storageState: 'playwright/.auth/user.json' }, dependencies: ['setup'] }`. Add a no-auth variant for signup flows: `{ name: 'mobile-chrome-no-auth', use: { ...devices['Pixel 5'], storageState: { cookies: [], origins: [] } } }`. Don't disrupt the existing chromium project — append.

2. **Create `frontend/tests/teams.spec.ts`** with the following scenarios. Each test must run on both `chromium` and `mobile-chrome` projects (Playwright runs each project automatically). Use `tests/utils/random.ts` for random emails / passwords.

   - **`test('signup creates personal team and lands on dashboard')`** — fresh signup, assert URL is `/teams` (or `/`), assert at least one team card visible with 'admin' badge and 'Personal' chip, assert greeting text 'Welcome back, nice to see you again!' visible.

   - **`test('user creates a team and sees it in the list')`** — logged-in user clicks `[data-testid=create-team-button]`, fills name 'Engineering', submits, assert toast and a card with text 'Engineering' and 'admin' badge.

   - **`test('admin generates invite link and copies it')`** — open team detail, click `[data-testid=invite-button]`, assert URL field appears, click copy, assert 'Copied' toast and the URL has shape `${baseURL}/invite/<code>`.

   - **`test('second user accepts invite via /invite/{code}')`** — user A creates team + invite, copies URL. New browser context (unauthenticated) signs up user B, navigates to invite URL, asserts redirect to `/teams/${teamId}` and the team appears in B's team list as 'member'.

   - **`test('admin promotes then demotes a member')`** — same setup; user A opens team detail, opens member-row dropdown for user B, clicks 'Promote to admin', asserts B's role badge becomes 'admin'. Then clicks 'Demote to member', asserts it becomes 'member'.

   - **`test('cannot demote the last admin')`** — user A on a team where they are the sole admin tries to demote themselves (UI hides this button; verify it's NOT in the DOM). Then via direct mutation in `page.evaluate` calling the API: assert backend rejects with 400 (this verifies the UI's defensive-removal isn't masking a real backend bug).

   - **`test('admin removes a member')`** — type confirmation phrase, click confirm, assert member disappears from list and toast 'Member removed'.

   - **`test('expired/unknown invite shows error')`** — navigate to `/invite/totally-bogus`, assert visible 'Invite not found' message.

   - **`test('logout clears cookie and bounces to login')`** — already covered by login.spec; add a mobile-only assertion that the user-menu trigger is reachable on a 375px viewport.

   - **`test('375px viewport: no horizontal scroll on /teams')`** — set `await page.setViewportSize({ width: 375, height: 812 })`, navigate, assert `await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)`.

3. **Tests/utils — extend `frontend/tests/utils/`** with a `signup(page, email, password, fullName)` helper if not already present, and a `createTeamFromUI(page, name)` helper to keep specs DRY.

4. **Run gate.** `cd frontend && bunx playwright test --project=chromium --project=mobile-chrome --project=mobile-chrome-no-auth`. All tests pass. On retry-failure Playwright captures a trace under `test-results/` for forensic debug (already configured).

**Threat surface:** No new backend changes here; FE-only test code.

**Failure modes (Q5):** Vite dev server slow to boot on CI → existing `webServer.reuseExistingServer` handles local; CI gives the existing 60s default. If flake happens, we extend in a follow-up — do not retry-pattern over real bugs.

**Mobile-target verification:** the `375px viewport: no horizontal scroll` test is the R022 hook. If it fails, the responsive layout isn't actually mobile-ready and T02–T04 must be revised.

**Skill activation note:** caveman skill not available; skills_used: [].
  - Files: `frontend/playwright.config.ts`, `frontend/tests/teams.spec.ts`, `frontend/tests/utils/teams.ts`
  - Verify: cd frontend && bun run lint && bunx playwright test --project=chromium --project=mobile-chrome --project=mobile-chrome-no-auth tests/teams.spec.ts; test $? -eq 0

## Files Likely Touched

- frontend/src/client/sdk.gen.ts
- frontend/src/client/types.gen.ts
- frontend/src/client/schemas.gen.ts
- frontend/src/client/core/OpenAPI.ts
- frontend/src/main.tsx
- frontend/src/hooks/useAuth.ts
- frontend/src/routes/_layout.tsx
- frontend/src/routes/login.tsx
- frontend/src/routes/signup.tsx
- frontend/src/routes/__root.tsx
- frontend/src/components/Sidebar/AppSidebar.tsx
- frontend/src/routes/_layout/admin.tsx
- frontend/src/routes/_layout/settings.tsx
- frontend/src/components/Admin/AddUser.tsx
- frontend/src/components/Admin/EditUser.tsx
- frontend/src/components/Admin/columns.tsx
- frontend/tests/login.spec.ts
- frontend/src/routes/_layout/teams.tsx
- frontend/src/routes/_layout/teams.$teamId.tsx
- frontend/src/routes/_layout/index.tsx
- frontend/src/routeTree.gen.ts
- frontend/src/components/Teams/CreateTeamDialog.tsx
- frontend/src/components/Teams/InviteButton.tsx
- frontend/src/routes/invite.$code.tsx
- backend/app/api/routes/teams.py
- backend/tests/api/routes/test_members.py
- frontend/src/components/Teams/MembersList.tsx
- frontend/src/components/Teams/RemoveMemberConfirm.tsx
- frontend/playwright.config.ts
- frontend/tests/teams.spec.ts
- frontend/tests/utils/teams.ts
