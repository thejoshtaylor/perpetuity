---
estimated_steps: 4
estimated_files: 3
skills_used:
  - test
  - accessibility
  - web-design-guidelines
  - verify-before-complete
---

# T04: Extend playwright.config.ts to four projects + ship mobile-audit harness with touch-target, horizontal-scroll, and visual-diff assertions

Update `frontend/playwright.config.ts` to add `iphone-13-mobile-safari` (use `devices['iPhone 13']`) and `desktop-firefox` (use `devices['Desktop Firefox']`) projects to the existing chromium + mobile-chrome set. Both inherit `storageState: 'playwright/.auth/user.json'` and `dependencies: ['setup']` and `testIgnore: 'm004-guylpp.spec.ts'`. Create `frontend/tests/m005-oaptsz-mobile-audit.spec.ts`: a parameterized spec that walks every existing authenticated route (`/`, `/items`, `/teams`, `/admin`, `/admin/teams`, `/settings`) plus the login route, and for each: (a) asserts `await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)` returns true (no horizontal scroll within a 1px tolerance for sub-pixel rendering), (b) collects all interactive elements (`button, a, [role=button], input, select, textarea`) and asserts each one's `boundingBox()` has `width >= 44 && height >= 44` OR is `:not(:visible)`, (c) takes a `toHaveScreenshot` baseline with `maxDiffPixelRatio: 0.01` (1% tolerance per CONTEXT). Add a helper `frontend/tests/utils/audit.ts` that exports `assertNoHorizontalScroll(page)` and `assertTouchTargets(page)` so the audit logic stays composable. Bind the mobile-audit spec to the new mobile projects via per-project `testMatch` if needed; default behavior is the spec runs across all four projects since chromium-only desktop touch-target enforcement still catches keyboard-mouse regressions. The spec MUST tolerate a known-failing first run — its job is to surface the defect list T05 fixes. Use `test.fixme()` annotations (NOT `test.skip`) where the audit currently fails so the failing baseline is recorded but the spec doesn't block the suite; T05 removes the fixme annotations as fixes land.

## Inputs

- ``frontend/playwright.config.ts` — current 4-project config (setup, chromium, mobile-chrome, mobile-chrome-no-auth, m004-guylpp); add iphone-13-mobile-safari + desktop-firefox`
- ``frontend/tests/admin.spec.ts` — referenced for the existing test pattern (login → goto → expect)`
- ``frontend/tests/utils/user.ts` — existing logInUser helper`
- ``frontend/tests/config.ts` — existing firstSuperuser credentials`
- ``frontend/src/routes/_layout/*.tsx` — referenced to enumerate the routes the audit walks`

## Expected Output

- ``frontend/playwright.config.ts` — adds iphone-13-mobile-safari and desktop-firefox projects`
- ``frontend/tests/m005-oaptsz-mobile-audit.spec.ts` — new spec walking every authenticated route, asserting no-horizontal-scroll + touch-target ≥44px + visual-diff baseline`
- ``frontend/tests/utils/audit.ts` — exports `assertNoHorizontalScroll` and `assertTouchTargets` helpers`

## Verification

cd frontend && bunx playwright test --list --project=iphone-13-mobile-safari 2>&1 | grep -q 'm005-oaptsz-mobile-audit' && bunx playwright test --list --project=desktop-firefox 2>&1 | grep -q 'm005-oaptsz-mobile-audit' && grep -q 'assertNoHorizontalScroll' frontend/tests/utils/audit.ts && grep -q 'assertTouchTargets' frontend/tests/utils/audit.ts

## Observability Impact

Failing audit assertions emit Playwright trace + screenshot diffs to `playwright-report/`. A future agent debugging a mobile regression can `bunx playwright show-report` to localize which route + element + viewport breaks. Visual-diff baselines under `tests/m005-oaptsz-mobile-audit.spec.ts-snapshots/` become the change-detection signal for future UI-touching slices.
