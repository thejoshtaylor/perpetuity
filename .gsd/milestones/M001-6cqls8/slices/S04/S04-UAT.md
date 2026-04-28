# S04: Frontend: auth + team dashboard — UAT

**Milestone:** M001-6cqls8
**Written:** 2026-04-25T01:06:14.793Z

# S04 UAT — Frontend: auth + team dashboard

**Scope:** End-to-end browser flows for the M001 user-facing collaboration loop. All flows must pass at 375px (mobile-chrome) AND desktop (chromium).

**Preconditions:**
- Backend running on `:8001` (or `:8000` if Docker doesn't conflict — MEM046).
- Postgres up with migrations applied.
- Frontend `bun run dev` (Vite on `:5173`).
- For automated runs: `VITE_API_URL=http://localhost:8001 bunx playwright test --project=chromium --project=mobile-chrome --project=mobile-chrome-no-auth tests/teams.spec.ts`.
- Two browser sessions / contexts available (one per user) for the invite flow.
- Test users: random emails per run (use `tests/utils/random.ts`).

---

## UAT-1: Signup auto-creates personal team and lands on Teams Dashboard

**Project:** `mobile-chrome-no-auth` + `chromium`

1. Open the app at the root URL while logged out.
   - **Expected:** Redirected to `/login` (since `/` → `/teams` → bounces unauthed users to `/login?next=/teams`).
2. Click "Sign Up", fill in random email, password, full name; submit.
   - **Expected:** Form submits without inline errors. Cookie set: `Set-Cookie: perpetuity_session=...; HttpOnly`.
3. After submission, observe the resulting URL and page.
   - **Expected:** URL is `/teams` (or `/`). At least one team card visible. Card shows the user's full name (or default personal-team name), an `admin` role badge, and a `Personal` chip.
   - **Expected text:** `Welcome back, nice to see you again!` visible in the page header.

**Edge cases:**
- Email already taken → backend 400, error toast surfaces `body.detail`, form stays on /signup.
- Password fails policy → inline zod error, no submission.

---

## UAT-2: Authenticated user creates a new team and sees it in the list

**Project:** `chromium` + `mobile-chrome` (authenticated suite)

1. Logged-in user lands on `/teams`.
2. Click `[data-testid=create-team-button]` (in dashboard header or empty-state).
   - **Expected:** Modal opens with a single name field, focused.
3. Type "Engineering"; click `[data-testid=create-team-submit]`.
   - **Expected:** Toast "Team created" appears. Modal closes. Dashboard now shows a new card with text `Engineering` and an `admin` role badge.

**Edge cases:**
- Empty name → zod inline error, button disabled.
- Name >255 chars → zod inline error.
- Backend 409 (slug conflict) → toast `body.detail` ("Team name conflicts ..."), modal stays open.

---

## UAT-3: Admin generates and copies an invite link

**Project:** `chromium` + `mobile-chrome` (authenticated suite)

1. From `/teams`, click the "Engineering" card → routes to `/teams/{teamId}`.
2. Verify header shows team name + `admin` badge (no Personal chip — non-personal team).
3. Click `[data-testid=invite-button]`.
   - **Expected:** Backend POST to `/api/v1/teams/{id}/invite` returns 200. A panel appears containing a read-only Input field with the invite URL, a Copy button, and "expires in 7 days" text.
   - **Expected URL shape:** `${baseURL}/invite/<code>`.
4. Click `[data-testid=copy-invite-url]`.
   - **Expected:** Toast "Copied" appears. Button shows visible "Copied" state. The URL is now in the clipboard.

**Edge cases:**
- Non-HTTPS preview origin → fallback to `document.execCommand('copy')`, toast still appears (MEM056).
- Stale cache shows InviteButton to a demoted member → first click 403s, button self-hides defensively.

---

## UAT-4: Second user accepts invite via /invite/{code}

**Project:** `mobile-chrome-no-auth` + `chromium`

1. User A (signed in) generates an invite for the "Engineering" team and copies the URL (per UAT-3).
2. In a fresh browser context (no auth cookie), navigate to the copied invite URL.
   - **Expected:** Redirected to `/login?next=/invite/<code>` because the route's `beforeLoad` failed `ensureQueryData(['currentUser'])`.
3. Click "Sign Up" from login page; sign up as user B.
   - **Expected:** After signup, sanitizeNextPath resolves `?next=/invite/<code>` and navigates back to `/invite/<code>`.
4. Wait for the join mutation to complete.
   - **Expected:** Toast `Joined Engineering`. Browser navigates to `/teams/{teamId}`. The "Engineering" team appears in user B's `/teams` list with a `member` role badge AND user B's auto-created personal team is also visible.

**Edge cases:**
- Already-member (409) → toast "You are already a member" + 2s redirect to `/teams` (backend 409 detail body has no team id — known limitation).
- Code expired or already used (410) → "This invite has expired or already been used" copy.
- Bogus code (404) → "Invite not found" card with link back to /teams (in test, asserted via toast text — see MEM049).

---

## UAT-5: Admin promotes then demotes a member

**Project:** `chromium` + `mobile-chrome` (authenticated suite)

1. As user A on `/teams/{teamId}` (where user B is a member from UAT-4), locate user B's `[data-testid=member-row]`.
2. Open `[data-testid=member-actions]` dropdown.
   - **Expected:** Menu shows "Promote to admin" + "Remove from team" (no "Demote to member" because B is already a member — no-op direction is hidden).
3. Click "Promote to admin".
   - **Expected:** Toast "Role updated". User B's row badge changes from `member` to `admin`.
4. Reopen the dropdown via keyboard (focus + Enter — mouse re-click has a Radix close/reopen race in headless Chromium).
   - **Expected:** Menu now shows "Demote to member" + "Remove from team".
5. Click "Demote to member".
   - **Expected:** Toast "Role updated". Badge returns to `member`.

**Edge cases:**
- 403 on the mutation (caller is no longer admin) → toast "Only team admins can change roles".

---

## UAT-6: Cannot demote the last admin (defense-in-depth)

**Project:** `chromium` + `mobile-chrome` (authenticated suite)

1. As user A on a team where they are the sole admin, open their own `[data-testid=member-row]` dropdown.
   - **Expected:** No "Demote to member" / "Remove from team" actions on the caller's own row (UI hides controls when `isSelf`). Verify the menu items are NOT in the DOM.
2. Bypass the UI and fire a direct API PATCH:
   ```
   await fetch(`${API_URL}/api/v1/teams/${tid}/members/${uid}/role`,
     { method: 'PATCH', credentials: 'include',
       headers: {'Content-Type': 'application/json'},
       body: JSON.stringify({ role: 'member' }) })
   ```
   - **Expected:** HTTP 400 with detail "Cannot demote the last admin". The UI's defensive removal is therefore not masking a real backend bug.

---

## UAT-7: Admin removes a member with type-to-confirm

**Project:** `chromium` + `mobile-chrome` (authenticated suite)

1. As user A on `/teams/{teamId}`, open user B's `[data-testid=member-actions]` dropdown.
2. Click "Remove from team".
   - **Expected:** Confirm dialog opens with focused input.
3. Type either user B's email OR the literal phrase `remove` into the input.
   - **Expected:** `[data-testid=remove-member-confirm]` button enables.
4. Click confirm.
   - **Expected:** Toast "Member removed". User B's row disappears from the list.

**Edge cases:**
- Stale row (B was already removed by another admin) → 404 → toast "Member already removed" + automatic refetch.
- Personal team — Members section shows no Remove control because `is_personal === true` omits row-level controls.

---

## UAT-8: Expired or unknown invite shows a clear error

**Project:** `chromium` + `mobile-chrome` (authenticated and unauthenticated)

1. Navigate to `/invite/totally-bogus-code`.
   - **Expected (authenticated):** Toast "Invite not found" appears (asserting on the toast rather than the testid'd error card per MEM049).
   - **Expected (unauthenticated):** First bounces to `/login?next=/invite/totally-bogus-code`. After signup/login, lands back on /invite, then surfaces the same "Invite not found" toast.

---

## UAT-9: Logout clears session cookie and bounces to /login

**Project:** `chromium` + `mobile-chrome` (authenticated suite)

1. As an authenticated user, click the user-menu trigger.
   - **Mobile assertion:** trigger reachable on a 375px viewport (`tests/teams.spec.ts:270`).
2. Click "Log out".
   - **Expected:** `AuthService.logout()` is called server-side; `Set-Cookie: perpetuity_session=; Max-Age=0` clears the session.
3. Observe the resulting URL.
   - **Expected:** Browser is at `/login`. `queryClient.removeQueries()` has cleared all caches.
4. Attempt to navigate to `/teams` directly.
   - **Expected:** `_layout` `beforeLoad`'s `ensureQueryData(['currentUser'])` fails with 401, throws `redirect({to: '/login', search: {next: '/teams'}})`. URL becomes `/login?next=/teams`.

---

## UAT-10: 375px mobile viewport — no horizontal scroll on /teams (R022 hook)

**Project:** `chromium` + `mobile-chrome`

1. As an authenticated user, set viewport to `375 x 812`.
2. Navigate to `/teams`.
3. Run: `await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)`.
   - **Expected:** `true`. No horizontal scroll. R022 (mobile usability) is mechanically validated.

**Edge cases:**
- If this fails, T02–T04 must be revisited — the responsive layout is broken.
- The same check should be sanity-extended to `/teams/{teamId}` and the invite/members panels in any future mobile audit.

---

## Pass Criteria Summary

All 10 UAT scenarios pass under both `chromium` and `mobile-chrome` projects (where applicable; signup-flow scenarios run under `mobile-chrome-no-auth`). The Playwright command from `tests/teams.spec.ts` reports **23 passed, 8 skipped, 0 failed** — the 8 skipped are intentional opt-outs of the no-auth project for authenticated tests.

Backend invariants verified externally:
- `Set-Cookie: perpetuity_session=...; HttpOnly; SameSite=lax` issued on signup/login.
- POST /api/v1/teams creates the team + admin membership atomically.
- POST /api/v1/teams/{id}/invite returns `{code, url, expires_at}` with a 7-day TTL.
- POST /api/v1/teams/join/{code} accepts → 200; expired → 410; consumed → 410; already-member → 409; bogus → 404.
- PATCH /api/v1/teams/{id}/members/{uid}/role: 200 on valid, 400 last-admin, 403 non-admin.
- DELETE /api/v1/teams/{id}/members/{uid}: 200 on valid, 400 last-admin/personal-team, 403 non-admin.
- GET /api/v1/teams/{id}/members: 200 with `{data, count}`, 403 non-member, 404 unknown team.
