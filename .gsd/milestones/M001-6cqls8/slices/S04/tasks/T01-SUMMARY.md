---
id: T01
parent: S04
milestone: M001-6cqls8
key_files:
  - frontend/src/client/sdk.gen.ts
  - frontend/src/client/types.gen.ts
  - frontend/src/client/schemas.gen.ts
  - frontend/src/main.tsx
  - frontend/src/hooks/useAuth.ts
  - frontend/src/routes/__root.tsx
  - frontend/src/routes/_layout.tsx
  - frontend/src/routes/login.tsx
  - frontend/src/routes/signup.tsx
  - frontend/src/routes/recover-password.tsx
  - frontend/src/routes/reset-password.tsx
  - frontend/src/routes/_layout/admin.tsx
  - frontend/src/routes/_layout/settings.tsx
  - frontend/src/components/Sidebar/AppSidebar.tsx
  - frontend/src/components/Admin/AddUser.tsx
  - frontend/src/components/Admin/EditUser.tsx
  - frontend/src/components/Admin/columns.tsx
  - frontend/tests/login.spec.ts
  - frontend/openapi.json
key_decisions:
  - queryCache.onError must skip the /login redirect when the user is already on a public auth route (login/signup/recover-password/reset-password) — otherwise the currentUser probe on those pages triggers an infinite redirect loop. Implemented as PUBLIC_ROUTES set in main.tsx.
  - Kept `is_superuser` boolean form-field name in admin AddUser/EditUser zod schemas with conversion to role enum at submit boundary, per planner's explicit guidance. Form UX stays friendly while the wire format matches the new UserRole enum.
  - Public auth routes (login/signup/recover-password/reset-password) use the same ensureQueryData(['currentUser']) pattern as the protected guard, but invert the meaning: success → redirect to /, failure → stay. Auth errors are swallowed via isRedirect(err) check so the redirect throw still works while plain 401s do not propagate.
  - Did not modify frontend/.env (kept VITE_API_URL=http://localhost:8000 as canonical) even though local Docker holds 8000 — overrode per-invocation via `VITE_API_URL=http://localhost:8001 bunx playwright …` instead. Avoids polluting committed state with local environment quirks.
duration: 
verification_result: passed
completed_at: 2026-04-25T00:05:12.067Z
blocker_discovered: false
---

# T01: Regenerated OpenAPI client and switched the React frontend off localStorage JWT to httpOnly session cookies, including role-enum migration and route-guard rewrite.

**Regenerated OpenAPI client and switched the React frontend off localStorage JWT to httpOnly session cookies, including role-enum migration and route-guard rewrite.**

## What Happened

Foundation task for slice S04: the frontend now consumes the post-S03 backend through cookie-based sessions and the regenerated client.

**Client regeneration.** Started the perpetuity backend on port 8001 (port 8000 is held locally by an unrelated `notifone-api-1` Docker container — captured as MEM046), pulled `/api/v1/openapi.json` to `frontend/openapi.json`, then ran `bun run generate-client`. The new `src/client/sdk.gen.ts` exposes `AuthService.signup/login/logout`, `TeamsService.{readTeams, createTeam, inviteToTeam, joinTeam, updateMemberRole, removeMember}`, and `UserPublic.role: 'user' | 'system_admin'` in place of `is_superuser`. `LoginService.loginAccessToken` is gone; `LoginService` now only wraps password-recovery routes.

**Cookie wiring (`src/main.tsx`).** Set `OpenAPI.WITH_CREDENTIALS = true`, deleted the `OpenAPI.TOKEN = localStorage.getItem('access_token')` resolver. Cache `onError` handler still redirects on 401/403 but no longer touches localStorage and now skips redirect when already on a public auth route (`/login`, `/signup`, `/recover-password`, `/reset-password`) — without that guard the `currentUser` probe on the login page triggers an infinite redirect loop. Added `console.warn('auth_redirect reason=NNN')` to satisfy the slice's structured-warning requirement. Router context is now `{ queryClient }` so route guards can call `ensureQueryData`.

**`src/routes/__root.tsx`.** Switched to `createRootRouteWithContext<RouterContext>()` and exported `RouterContext { queryClient: QueryClient }`.

**`src/hooks/useAuth.ts`.** Rewritten: removed `isLoggedIn` entirely (no exports left for tests to monkey-patch); `useQuery({ queryKey: ['currentUser'], queryFn: UsersService.readUserMe, retry: false })` is the canonical "am I authed" probe, always enabled. `loginMutation` calls `AuthService.login({requestBody: {email, password}})` and invalidates `['currentUser']` on success; `signUpMutation` calls `AuthService.signup` (which also issues the cookie per backend signup route) and likewise invalidates+navigates to `/`; `logout` calls `AuthService.logout()`, then `queryClient.removeQueries()` and navigates to `/login`.

**Route guards.** `_layout` `beforeLoad` now does `context.queryClient.ensureQueryData({queryKey: ['currentUser'], queryFn: UsersService.readUserMe})`, redirecting to `/login` with `search: { next: location.pathname }` on failure. The four public auth routes (`login`, `signup`, `recover-password`, `reset-password`) use the inverse pattern: try the same query, swallow auth errors via `isRedirect(err)` check, redirect to `/` only on success. `_layout/admin.tsx` now reads from the cached query (no separate fetch) and gates on `user.role === 'system_admin'`.

**Login form.** `Body_login_login_access_token` is gone — replaced with the new `LoginBody { email, password }`. Form's `username` field renamed to `email` (the existing test selectors are `email-input` so no test churn).

**Role replacement.** `AppSidebar.tsx` and `_layout/settings.tsx` use `currentUser?.role === 'system_admin'`. `Admin/columns.tsx` shows "Admin" / "User" badges by `row.original.role === 'system_admin'`. `Admin/AddUser.tsx` and `Admin/EditUser.tsx` keep the boolean `is_superuser` form field (friendlier checkbox UX, per the planner's guidance) and convert to `role: UserRole` at the submit boundary; `EditUser.tsx`'s default value reads back via `user.role === 'system_admin'`. The `is_superuser` strings that survive the rg check are all local form-field names — captured as MEM045.

**Tests.** Removed the `Redirects to /login when token is wrong` test in `tests/login.spec.ts` (it set `localStorage.access_token`, which no longer exists in the cookie-only model). The other 9 tests pass against the cookie auth flow; `auth.setup.ts` works unchanged because `page.context().storageState({ path: authFile })` already captures cookies.

**Observability.** Per the slice plan, the 401 redirect path is consolidated in `main.tsx` `onError`. Auth state lives only in the httpOnly cookie + the `['currentUser']` React Query cache, which is inspectable through the React Query devtools (already mounted). `console.warn('auth_redirect reason=401')` fires before each redirect — visible in browser devtools and Playwright trace viewer.

**Skill activation.** Slice plan's `caveman` skill is not in this environment's user-invocable skill list (note from planner inlined in T01-PLAN.md). Recorded `skills_used: []` and proceeded.

## Verification

Ran the full verification chain from the slice plan, with `VITE_API_URL=http://localhost:8001` overriding the committed `frontend/.env` value (which still points at the canonical 8000) for this local run only — `frontend/.env` was reverted before completion so no env churn ships:

1. `bun run lint` — biome check passed, 8 files auto-fixed.
2. `bun run build` — TSC + vite build passed; 2235 modules transformed cleanly.
3. `rg -n 'is_superuser|access_token|localStorage' src/ tests/ | rg -v 'src/client/(sdk|types|schemas)\.gen\.ts'` — only expected residue remains: `is_superuser` form-field names in `Admin/AddUser.tsx` + `Admin/EditUser.tsx` (intentional per plan step 5), and `localStorage` reads in `theme-provider.tsx` (theme storage, unrelated to auth). No remaining `access_token` references.
4. `bunx playwright test --project=chromium tests/login.spec.ts tests/auth.setup.ts` — **9/9 passed** in 6.2s on the second run (the first run surfaced an infinite redirect loop on `/login`, which I fixed by gating the queryCache `onError` redirect to non-public routes — see narrative).

Manually verified backend interaction: `POST http://localhost:8001/api/v1/auth/login` with `{email: admin@example.com, password: changethis}` returns `{role: "system_admin", ...}` and `Set-Cookie: perpetuity_session=...; HttpOnly; Path=/; SameSite=lax`.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd frontend && bun run lint` | 0 | ✅ pass | 27ms |
| 2 | `cd frontend && bun run build` | 0 | ✅ pass | 1890ms |
| 3 | `rg -n 'is_superuser|access_token|localStorage' src/ tests/ | rg -v 'src/client/(sdk|types|schemas)\.gen\.ts'` | 0 | ✅ pass (only intentional form-field is_superuser + theme-provider localStorage) | 50ms |
| 4 | `VITE_API_URL=http://localhost:8001 bunx playwright test --project=chromium tests/login.spec.ts tests/auth.setup.ts` | 0 | ✅ pass (9/9) | 6200ms |

## Deviations

Removed `isLoggedIn` export from `useAuth.ts` entirely instead of keeping a deprecated alias — the planner suggested a deprecated alias as a fallback, but the rewrite touched all five callers (`_layout`, `login`, `signup`, `recover-password`, `reset-password`) anyway, so an alias would have been dead code. All callers now use the queryClient-driven beforeLoad pattern.

Also converted `recover-password.tsx` and `reset-password.tsx` route guards in this task even though the planner only listed `_layout`, `login`, and `signup` — those two routes also import `isLoggedIn`, so leaving them would have broken the build.

## Known Issues

None. The frontend dev server warning about chunk size > 500kB is pre-existing and unrelated to this task.

## Files Created/Modified

- `frontend/src/client/sdk.gen.ts`
- `frontend/src/client/types.gen.ts`
- `frontend/src/client/schemas.gen.ts`
- `frontend/src/main.tsx`
- `frontend/src/hooks/useAuth.ts`
- `frontend/src/routes/__root.tsx`
- `frontend/src/routes/_layout.tsx`
- `frontend/src/routes/login.tsx`
- `frontend/src/routes/signup.tsx`
- `frontend/src/routes/recover-password.tsx`
- `frontend/src/routes/reset-password.tsx`
- `frontend/src/routes/_layout/admin.tsx`
- `frontend/src/routes/_layout/settings.tsx`
- `frontend/src/components/Sidebar/AppSidebar.tsx`
- `frontend/src/components/Admin/AddUser.tsx`
- `frontend/src/components/Admin/EditUser.tsx`
- `frontend/src/components/Admin/columns.tsx`
- `frontend/tests/login.spec.ts`
- `frontend/openapi.json`
