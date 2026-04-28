# S01: PWA install + service worker + mobile polish pass — UAT

**Milestone:** M005-oaptsz
**Written:** 2026-04-28T09:26:41.138Z

# M005-oaptsz / S01 — UAT Script: PWA install + service worker + mobile polish pass

**Slice goal:** Verify Perpetuity is installable as a PWA on mobile Chrome with a correctly-classified service worker (NetworkOnly for /api/* and /ws/*) and that every existing flow passes a four-project Playwright mobile audit at 360px width.

**Reviewer:** Run from `frontend/` (the playwright config lives there — running from the repo root will produce `Project(s) "X" not found. Available projects: ""`).

## Preconditions

- Repo at HEAD of `main` after S01 closes; M005-oaptsz/S01 commits applied (T01–T05).
- `bun install` ran successfully; `node_modules` includes `vite-plugin-pwa` and `workbox-window`.
- `frontend/.env` (or repo-root `.env`) defines `VITE_API_URL` (typically `http://localhost:8000` or staging URL).
- For the SW-bypass test, the production preview server (port 4173) must be reachable — Playwright's `webServer` config will start it via `bun run build && bun run preview --port 4173 --strictPort` automatically; takes ~30–60s on a cold cache.
- For real-device steps (UAT-3), a Pixel-class Android with mobile Chrome ≥114 and an iPhone 13/14 with iOS 16.4+.

## Test cases

### UAT-1: Service worker NetworkOnly /api/* contract (slice contract gate)

**Goal:** Prove the SW does not silently cache /api/* responses.

1. From `frontend/`, run `bunx playwright test --project=m005-oaptsz-sw m005-oaptsz-sw-bypass.spec.ts`.
2. **Expected:** 1 test passes within ~10s. The test waits for `navigator.serviceWorker.controller !== null`, installs a `context.route('**/api/v1/utils/health-check/', ...)` mock at the BrowserContext level, fetches once (asserts body 'first'), flips the mock to return 'second' on the next call, fetches again, asserts 'second'.
3. **Failure mode (regression):** if the second fetch returns 'first', the SW has cached the first response — the `NetworkOnly` route is misconfigured. Investigate `frontend/src/sw.ts` for changes to the `/api/*` route or the precache manifest, and check the SW lifecycle console.info logs in the trace.

### UAT-2: Mobile audit on Pixel 5 (mobile-chrome)

**Goal:** Verify every existing flow renders cleanly on a Pixel-class Android viewport.

1. From `frontend/`, run `bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts`.
2. **Expected:** 15 tests pass within ~12s. Routes covered: login, home, items, teams, admin, admin-teams, settings. Each gets two tests — no-horizontal-scroll + touch-targets ≥44×44, plus a visual-diff baseline at 1% tolerance.
3. **Edge case — touch-target failure:** if a new component lands without `min-h-11` / `min-w-11`, the test failure message lists the offending element's tag/role/text/dimensions. Fix at the design-system primitive level (Button, Input, etc.) per MEM337, not at the call site.
4. **Edge case — visual-diff drift:** if a baseline updates (e.g. icon swap), regenerate via `bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts --update-snapshots` after manual review.

### UAT-3: Mobile audit on iPhone 13 (iphone-13-mobile-safari)

**Goal:** Verify the same flows on a WebKit engine at iPhone 13 viewport (390×844).

1. From `frontend/`, run `bunx playwright test --project=iphone-13-mobile-safari m005-oaptsz-mobile-audit.spec.ts`.
2. **Expected:** 15 tests pass within ~17s. Same 7 routes, same two assertions, separate visual-diff baselines committed in `tests/m005-oaptsz-mobile-audit.spec.ts-snapshots/`.
3. **Edge case — WebKit-only regressions:** if a route passes mobile-chrome but fails iphone-13-mobile-safari, suspect a `:focus-visible` styling difference or a Safari-only flexbox quirk. Reproduce in Safari via `webkit/Test/iPhone 13` device emulation in DevTools.

### UAT-4: Audit harness has no test.fixme annotations remaining

**Goal:** Confirm the audit suite is enforcing (not just recording) every assertion.

1. From the repo root, run `! grep -q 'test.fixme' frontend/tests/m005-oaptsz-mobile-audit.spec.ts`.
2. **Expected:** exit 0. Zero `test.fixme(...)` references remain; the spec is unconditionally executing every audit assertion. T04 originally used fixme to record failing baselines; T05 removed them all.

### UAT-5: Build emits dist/sw.js with the route classifier

**Goal:** Verify the production build produces a service worker that contains the route classification logic.

1. From `frontend/`, run `bun run build`.
2. **Expected:** build succeeds (the chunk-size warning above 500 kB is acceptable per T05 — bundle splitting is a future M005 candidate).
3. Run `test -f dist/sw.js && grep -q 'NetworkOnly' dist/sw.js && grep -q 'precache' dist/sw.js`.
4. **Expected:** exit 0. The minified SW contains `NetworkOnly` (via the `STRATEGY_NETWORK_ONLY` string sentinel — class names are minified, but the string survives) and `precache` from the Workbox precache manifest registration.

### UAT-6: Manifest + icons + index.html metadata wired

**Goal:** Confirm Lighthouse PWA install criteria are present.

1. From `frontend/`, run `node -e "const m=JSON.parse(require('fs').readFileSync('public/manifest.webmanifest','utf8'));if(!m.name||!m.start_url||!m.icons||m.icons.length<2)throw new Error('manifest invalid')"`.
2. **Expected:** exit 0. Manifest parses and has name, start_url, and ≥2 icons.
3. Run `test -f public/pwa-192.png && test -f public/pwa-512.png && test -f public/pwa-512-maskable.png && test -f public/apple-touch-icon-180.png`.
4. **Expected:** exit 0. All four PWA icons present.
5. Run `grep -q 'manifest.webmanifest' index.html && grep -q 'apple-touch-icon' index.html && grep -q 'theme-color' index.html`.
6. **Expected:** exit 0. `index.html` links manifest, apple-touch-icon, and theme-color meta.

### UAT-7: InstallBanner + OfflineBanner mounted in layout

**Goal:** Confirm the install/offline UX surface exists in the rendered layout.

1. Run `grep -q 'beforeinstallprompt' frontend/src/components/Common/InstallBanner.tsx`.
2. **Expected:** exit 0. The Android beforeinstallprompt deferred-prompt pattern is wired.
3. Run `grep -q 'navigator.onLine' frontend/src/components/Common/OfflineBanner.tsx`.
4. **Expected:** exit 0. The `online`/`offline` event source is wired.
5. Run `grep -q 'InstallBanner' frontend/src/routes/_layout.tsx && grep -q 'OfflineBanner' frontend/src/routes/_layout.tsx`.
6. **Expected:** exit 0. Both banners mount inside the authenticated layout.

### UAT-8: Real-device install on Android Pixel-class device (deferred to S05 milestone-level acceptance)

**Status:** S05 owns the real-device acceptance scenario. S01 closes on automated mobile Chrome + iPhone 13 emulation per the slice plan's Proof Level. The script below documents what S05 will exercise:

1. Open `https://<staging-host>` on a Pixel-class Android in Chrome ≥114.
2. **Expected:** within 30s, an install banner appears (either browser-native or the in-app InstallBanner). Tapping Install runs the deferred prompt and adds the app to the home screen.
3. Launch from the home-screen icon.
4. **Expected:** the app opens in standalone mode (no Chrome chrome). Complete the full demo flow (login → dashboard → terminal → projects → run history) without horizontal scroll. All touch targets feel comfortable to tap (no precision required).
5. Open Chrome DevTools (remote-debug via USB) → Application → Service Workers.
6. **Expected:** the SW for the staging origin is `activated and is running`. The Application → Manifest tab validates with no errors.

## Edge cases to monitor

- **Stale SW after deploy:** if a user has a stale SW from a prior visit, the `pwa-update-available` CustomEvent should fire and InstallBanner should render the inline Refresh action. Tapping Refresh posts `{type:'SKIP_WAITING'}` to the waiting worker and reloads. To reproduce: deploy a new build, force-reload the page, and look for the Refresh banner.
- **Mobile network flapping:** OfflineBanner only clears on a 2xx response from `/api/v1/utils/health-check/`. The `online` event alone is not trusted because mobile networks fire it before reachability is real.
- **iOS Safari < 16.4:** has no Web Push; iOS users see the share/A2HS toast once via `pwa.ios_toast_shown` localStorage flag and do not see the install banner. Push UX degrades gracefully (no crash, no spam toast on every visit).
- **Devtools floating buttons:** TanStack Router and React Query devtools are now opt-in via `?devtools=1`. The audit harness against the dev server will not see them; devs can still toggle via the query string.
- **Verification CWD:** all Playwright commands MUST run from `frontend/`. From the repo root, Playwright cannot find the config and reports `Project(s) "X" not found. Available projects: ""` (MEM336).
