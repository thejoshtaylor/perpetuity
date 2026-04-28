---
estimated_steps: 20
estimated_files: 3
skills_used: []
---

# T05: Mobile-Chrome Playwright project + end-to-end teams.spec covering full slice demo

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

## Inputs

- `frontend/playwright.config.ts`
- `frontend/tests/auth.setup.ts`
- `frontend/tests/login.spec.ts`
- `frontend/tests/config.ts`
- `frontend/tests/utils/random.ts`
- `frontend/src/routes/_layout/teams.tsx`
- `frontend/src/routes/_layout/teams.$teamId.tsx`
- `frontend/src/routes/invite.$code.tsx`
- `frontend/src/components/Teams/CreateTeamDialog.tsx`
- `frontend/src/components/Teams/InviteButton.tsx`
- `frontend/src/components/Teams/MembersList.tsx`
- `frontend/src/components/Teams/RemoveMemberConfirm.tsx`

## Expected Output

- `frontend/playwright.config.ts`
- `frontend/tests/teams.spec.ts`
- `frontend/tests/utils/teams.ts`

## Verification

cd frontend && bun run lint && bunx playwright test --project=chromium --project=mobile-chrome --project=mobile-chrome-no-auth tests/teams.spec.ts; test $? -eq 0

## Observability Impact

Playwright traces on first-retry already enabled. Adds two mobile project runs to the test report so failures attribute to viewport vs. logic. No app-runtime observability changes.
