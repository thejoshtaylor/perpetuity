---
id: T05
parent: S01
milestone: M005-oaptsz
key_files:
  - frontend/playwright.config.ts
  - frontend/tests/m005-oaptsz-sw-bypass.spec.ts
  - frontend/tests/m005-oaptsz-mobile-audit.spec.ts
  - frontend/src/components/ui/button.tsx
  - frontend/src/components/ui/loading-button.tsx
  - frontend/src/components/ui/input.tsx
  - frontend/src/components/ui/password-input.tsx
  - frontend/src/components/ui/tabs.tsx
  - frontend/src/components/ui/sonner.tsx
  - frontend/src/components/ui/sidebar.tsx
  - frontend/src/routes/__root.tsx
  - frontend/src/routes/login.tsx
  - frontend/src/components/Common/Footer.tsx
  - frontend/src/components/Admin/AdminTeamsColumns.tsx
key_decisions:
  - Adopted the design-system-primitive-floor pattern (MEM333) — `min-h-11` / `min-w-11` on Button/LoadingButton/Input/PasswordInput/Tabs/SidebarTrigger raises the bounding box to satisfy the touch-target gate while preserving the visible h-9/h-10 styling for desktop. This is cheaper than auditing every call site and applies the rule once at the design-system level.
  - Inline anchor links inside paragraphs/tables (login: Forgot/Sign-up, admin-teams: View members, footer: social icons) get a transparent inline-flex 44x44 shell — visually enlarging them would break surrounding text rhythm.
  - TanStack Router/React Query devtools floating buttons are now opt-in via `?devtools=1` (MEM335) — the audit harness against the dev server should never see them, and devs can still toggle them on.
  - The SW-bypass spec uses `context.route` (not `page.route`) at the browser-context level so the route mock fires for SW-mediated fetches; `serviceWorkers: 'allow'` is required on the project (MEM334).
  - Production preview (`bun run build && bun run preview --port 4173 --strictPort`) is the only environment where the SW registers — vite-plugin-pwa devOptions.enabled is false (MEM334). The webServer config is an array of two so dev (5173) and preview (4173) coexist.
duration: 
verification_result: passed
completed_at: 2026-04-28T09:20:21.709Z
blocker_discovered: false
---

# T05: feat(pwa): mobile-audit fix pass + SW NetworkOnly /api/* slice contract gate (touch-targets, no horizontal scroll, SW bypass proven)

**feat(pwa): mobile-audit fix pass + SW NetworkOnly /api/* slice contract gate (touch-targets, no horizontal scroll, SW bypass proven)**

## What Happened

T05 closes the M005-oaptsz/S01 slice contract gate by (a) flipping the audit harness from "records failing baselines via test.fixme" to "passes everywhere," and (b) shipping the SW NetworkOnly bypass spec that proves /api/* requests are never silently cached.

Resume state: a prior session committed all the source-level fixes under "GSD-Unit: M005-oaptsz/S01/T05" (commit 89d127f) but never invoked gsd_complete_task, so no SUMMARY.md / DB row existed. This session verified the implementation in-tree, ran the four verification gates, and recorded the result.

Implementation pattern (per MEM333): the cleanest way to satisfy the >=44x44 CSS-px touch-target gate without rewriting visual layouts was to add `min-h-11` (and `min-w-11` where width was the issue) to the shared design-system primitives — Button (default/sm/lg/icon/icon-sm/icon-lg), LoadingButton (mirroring Button), Input, PasswordInput, TabsList, TabsTrigger, and the SidebarTrigger override. The visible `h-9 / h-10 / size-9` styling stays for desktop while the bounding box grows to 44px on mobile measurement. Inline anchor links inside paragraphs/tables — "Forgot password?" and "Sign up" on /login, the team-row "View members" link in admin-teams columns, and the social-icon links in Footer — got a transparent inline-flex 44x44 padding shell instead, because enlarging them visually would break surrounding text rhythm. The Sonner toast close button was overridden via toastOptions.classNames to !h-11 !w-11.

Devtools gating (MEM335): TanStack Router devtools and React Query devtools render floating ~40x40 / 150x30 buttons that fail the touch-target gate even in dev mode. They're now gated on `?devtools=1` inside `src/routes/__root.tsx` so default dev runs (and the Playwright audit, which hits the dev server) never see them while devs can still toggle them on with the query string.

SW-bypass slice contract gate: `frontend/tests/m005-oaptsz-sw-bypass.spec.ts` waits for `navigator.serviceWorker.controller !== null` (the canonical signal that the SW is intercepting future fetches), installs a `context.route('**/api/v1/utils/health-check/', ...)` mock at the BROWSER CONTEXT level (per MEM334 — page.route does not fire for SW-mediated fetches), fetches once with body 'first' (asserts 'first'), flips a closure variable so the same mock returns 'second' on the next call, fetches again, and asserts 'second'. A CacheFirst SW would have populated its cache from the first fetch and returned 'first' on the second call — that assertion would fail and surface the silent-cache regression that M005-sqm8et's run-status polling cannot tolerate.

Playwright wiring (MEM334): the `m005-oaptsz-sw` project has its own `baseURL: http://localhost:4173` because vite-plugin-pwa devOptions.enabled defaults to false (the SW does NOT register on the dev server). The webServer config is an array of two: `bun run dev` (port 5173, every other project) and `bun run build && bun run preview --port 4173 --strictPort` (port 4173, this project only). The project sets `serviceWorkers: 'allow'` and uses a fresh-context storageState so SW registration starts from zero per test. All other projects (chromium, mobile-chrome, mobile-chrome-no-auth, iphone-13-mobile-safari, desktop-firefox) extend `testIgnore` to include `m005-oaptsz-sw-bypass.spec.ts`.

The audit spec itself was simplified (T04 originally used `test.fixme()` keyed on per-route flags to record failing baselines; T05 removed all the fixme infrastructure since every route now passes). Visual-diff baselines for both mobile-chrome and iphone-13-mobile-safari are committed in the spec's snapshots directory.

## Verification

All four verification gates from T05-PLAN passed in this session:

1. `bunx playwright test --project=m005-oaptsz-sw m005-oaptsz-sw-bypass.spec.ts` — 1 passed (8.5s). The slice contract gate proves the SW NetworkOnly /api/* contract is intact: two consecutive fetches against the same mocked URL observed two different bodies, which is impossible under any caching strategy.

2. `bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts` — 15 passed (11.8s). All 7 routes (login, home, items, teams, admin, admin-teams, settings) pass both the no-horizontal-scroll + touch-targets >=44px assertion and the visual-diff baseline at 1% tolerance.

3. `bunx playwright test --project=iphone-13-mobile-safari m005-oaptsz-mobile-audit.spec.ts` — 15 passed (17.1s). Same 7 routes, same two assertions, on a WebKit engine at iPhone 13 (390x844) viewport.

4. `! grep -q 'test.fixme' frontend/tests/m005-oaptsz-mobile-audit.spec.ts` — gate pass. Zero `test.fixme` references remain in the audit spec, which means every route's expected-fail annotation has been removed and the suite is enforcing rather than recording.

Slice runtime signals (from S01-PLAN's Verification section) are emitted by T01's sw.ts and T03's main.tsx registerSW callback — those were verified in their respective tasks and are not re-proven here, but they continue to be the authoritative signal that the SW is alive and serving the bypass contract.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `bunx playwright test --project=m005-oaptsz-sw m005-oaptsz-sw-bypass.spec.ts` | 0 | ✅ pass | 8500ms |
| 2 | `bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts` | 0 | ✅ pass | 11800ms |
| 3 | `bunx playwright test --project=iphone-13-mobile-safari m005-oaptsz-mobile-audit.spec.ts` | 0 | ✅ pass | 17100ms |
| 4 | `! grep -q 'test.fixme' tests/m005-oaptsz-mobile-audit.spec.ts` | 0 | ✅ pass | 50ms |

## Deviations

No deviations from the T05-PLAN. The plan said to remove `test.fixme()` annotations as fixes land; the prior session's commit removed them all in one pass, and this session confirmed all routes now pass. The plan also said to update `frontend/tests/login.spec.ts` if it asserts on the legacy "Full Stack FastAPI Project" title — login.spec.ts does not assert on that title, so no change was needed.

Resume state: the prior `chore: auto-commit after execute-task` (89d127f) did not have a corresponding SUMMARY.md or DB row, indicating the prior session was interrupted between commit and gsd_complete_task. This session re-ran the full verification gate (not just the fixme grep) before recording completion — the four gates ran cleanly so no replan was needed.

## Known Issues

None blocking. Two small forward-looking notes for future agents:

- The chunk-size-warning from the production build (`Some chunks are larger than 500 kB after minification`) is unaddressed. T05 does not own bundle splitting; it's a candidate for a future M005-* slice if mobile cold-start latency becomes a concern.
- The Footer text still says "Full Stack FastAPI Template - {currentYear}". This is cosmetic; T02 changed the document title to "Perpetuity" but the footer copy is independent. Leave for a downstream branding pass — touching it here would be scope creep.

## Files Created/Modified

- `frontend/playwright.config.ts`
- `frontend/tests/m005-oaptsz-sw-bypass.spec.ts`
- `frontend/tests/m005-oaptsz-mobile-audit.spec.ts`
- `frontend/src/components/ui/button.tsx`
- `frontend/src/components/ui/loading-button.tsx`
- `frontend/src/components/ui/input.tsx`
- `frontend/src/components/ui/password-input.tsx`
- `frontend/src/components/ui/tabs.tsx`
- `frontend/src/components/ui/sonner.tsx`
- `frontend/src/components/ui/sidebar.tsx`
- `frontend/src/routes/__root.tsx`
- `frontend/src/routes/login.tsx`
- `frontend/src/components/Common/Footer.tsx`
- `frontend/src/components/Admin/AdminTeamsColumns.tsx`
