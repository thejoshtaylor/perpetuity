---
estimated_steps: 19
estimated_files: 17
skills_used: []
---

# T01: Regenerate OpenAPI client + flip frontend to httpOnly cookie auth

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

## Inputs

- `frontend/src/client/sdk.gen.ts`
- `frontend/src/client/types.gen.ts`
- `frontend/src/client/core/OpenAPI.ts`
- `frontend/src/main.tsx`
- `frontend/src/hooks/useAuth.ts`
- `frontend/src/routes/_layout.tsx`
- `frontend/src/routes/login.tsx`
- `frontend/src/routes/signup.tsx`
- `frontend/src/routes/__root.tsx`
- `frontend/src/components/Sidebar/AppSidebar.tsx`
- `frontend/src/routes/_layout/admin.tsx`
- `frontend/src/routes/_layout/settings.tsx`
- `frontend/src/components/Admin/AddUser.tsx`
- `frontend/src/components/Admin/EditUser.tsx`
- `frontend/src/components/Admin/columns.tsx`
- `frontend/tests/login.spec.ts`
- `backend/app/api/routes/auth.py`
- `backend/app/api/routes/teams.py`
- `backend/app/models.py`

## Expected Output

- `frontend/src/client/sdk.gen.ts`
- `frontend/src/client/types.gen.ts`
- `frontend/src/client/schemas.gen.ts`
- `frontend/src/client/core/OpenAPI.ts`
- `frontend/src/main.tsx`
- `frontend/src/hooks/useAuth.ts`
- `frontend/src/routes/_layout.tsx`
- `frontend/src/routes/login.tsx`
- `frontend/src/routes/signup.tsx`
- `frontend/src/routes/__root.tsx`
- `frontend/src/components/Sidebar/AppSidebar.tsx`
- `frontend/src/routes/_layout/admin.tsx`
- `frontend/src/routes/_layout/settings.tsx`
- `frontend/src/components/Admin/AddUser.tsx`
- `frontend/src/components/Admin/EditUser.tsx`
- `frontend/src/components/Admin/columns.tsx`
- `frontend/tests/login.spec.ts`

## Verification

cd frontend && bun run lint && bun run build && rg -n 'is_superuser|access_token|localStorage' src/ tests/ | rg -v 'src/client/(sdk|types|schemas)\.gen\.ts' && bunx playwright test --project=chromium tests/login.spec.ts tests/auth.setup.ts; test $? -eq 0

## Observability Impact

401 redirect path consolidated in main.tsx onError. Removes localStorage state — auth state lives only in httpOnly cookie + ['currentUser'] React Query cache, which is inspectable via React Query devtools (already mounted).
