---
estimated_steps: 6
estimated_files: 12
skills_used:
  - make-interfaces-feel-better
  - accessibility
  - test
  - verify-before-complete
  - lint
---

# T05: Mobile-audit fix pass + SW NetworkOnly /api/* integration test (slice contract gate)

Run `bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts` to surface the audit defect list. For each failure, fix the root cause in the relevant `src/components/**` or `src/routes/_layout/*.tsx` file: insufficient padding → tailwind `min-h-[44px] min-w-[44px]` on the offending button/link; horizontal overflow at 360px → adjust container max-w / overflow-x-auto on tables / wrap long text with `break-words`; layout shift → fix flex/grid configuration. Touch targets that genuinely cannot be resized (e.g. inline icons inside dense table rows) should be wrapped in a transparent 44×44 padding shell rather than visually enlarged. Remove each `test.fixme()` annotation as the corresponding assertion passes. Then ship the slice contract gate: write `frontend/tests/m005-oaptsz-sw-bypass.spec.ts` that (a) waits for the SW registration to be `active` (`await page.waitForFunction(() => navigator.serviceWorker.controller !== null)`), (b) intercepts a deterministic backend route — use `page.route('**/api/v1/utils/health-check/', route => route.fulfill({status: 200, body: 'first'}))` — and fetches it via `page.evaluate(() => fetch('/api/v1/utils/health-check/').then(r=>r.text()))`, (c) reroutes the same URL to a different fixture body 'second' and re-fetches, (d) asserts the second fetch returns 'second' (proving NetworkOnly bypass works — a CacheFirst SW would return 'first' on the second call). Bind this spec to a dedicated project `m005-oaptsz-sw` in playwright.config.ts so it can run in isolation without the full audit suite. Update the project to use `testMatch: /m005-oaptsz-sw-bypass\.spec\.ts/` and add the spec to the existing projects' `testIgnore` lists. Remove or update the page title 'Full Stack FastAPI Project' references in `frontend/tests/login.spec.ts` if any test asserts on it (T02 changes it to 'Perpetuity').

## Inputs

- ``frontend/tests/m005-oaptsz-mobile-audit.spec.ts` — T04's audit harness; surfaces defect list`
- ``frontend/playwright.config.ts` — extended in T04; add m005-oaptsz-sw project here`
- ``frontend/src/sw.ts` — T01's SW; this task validates its NetworkOnly behavior`
- ``frontend/src/components/Common/InstallBanner.tsx` — T03; touch-target audit may flag the install button`
- ``frontend/src/components/Sidebar/AppSidebar.tsx` — referenced; mobile drawer touch targets are a likely defect site`
- ``frontend/src/routes/_layout.tsx` — referenced; sticky header touch targets are a likely defect site`
- ``backend/app/api/routes/utils.py` — referenced for the health-check endpoint path used in the SW bypass test`

## Expected Output

- ``frontend/tests/m005-oaptsz-sw-bypass.spec.ts` — new spec proving SW NetworkOnly /api/* bypass via fixture mutation`
- ``frontend/playwright.config.ts` — adds m005-oaptsz-sw project + extends testIgnore on other projects`
- ``frontend/tests/m005-oaptsz-mobile-audit.spec.ts` — all test.fixme annotations removed; suite passes on mobile-chrome + iphone-13-mobile-safari`
- ``frontend/src/components/**/*.tsx` and `frontend/src/routes/_layout/*.tsx` — touch-target + horizontal-scroll fixes for every audit-surfaced defect`

## Verification

cd frontend && bunx playwright test --project=m005-oaptsz-sw m005-oaptsz-sw-bypass.spec.ts && bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts && bunx playwright test --project=iphone-13-mobile-safari m005-oaptsz-mobile-audit.spec.ts && ! grep -q 'test.fixme' frontend/tests/m005-oaptsz-mobile-audit.spec.ts

## Observability Impact

Audit-suite passing state is now the change-detection signal — `bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts` is the canonical mobile-regression check for downstream slices. SW-bypass spec failure means /api/* is being silently cached — a P0 regression alarm for any future SW edit.
