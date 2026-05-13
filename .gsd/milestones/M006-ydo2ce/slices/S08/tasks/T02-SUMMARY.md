---
id: T02
parent: S08
milestone: M006-ydo2ce
key_files:
  - frontend/tests/components/CreateGitHubRepoDialog.spec.ts
key_decisions:
  - 502 and 503 mock shapes left unchanged — backend returns flat string detail for those codes
  - Test (e) uses only {code} in nested detail to exercise undefined installation_id/reason branches via optional chaining
duration: 
verification_result: passed
completed_at: 2026-05-13T05:30:53.319Z
blocker_discovered: false
---

# T02: Updated all three 409 Playwright mock bodies to nested `{"detail": {"code": "github_user_token_required", ...}}` shape; all 30/35 tests pass (5 skipped by browser config)

**Updated all three 409 Playwright mock bodies to nested `{"detail": {"code": "github_user_token_required", ...}}` shape; all 30/35 tests pass (5 skipped by browser config)**

## What Happened

The Playwright spec had three `route.fulfill` calls returning 409 responses with the old flat shape `{"detail": "github_user_token_required", installation_id: N, reason: "..."}`. After T01 changed the component to read `body.detail?.code`, these mocks no longer triggered the 409 branch. Updated all three locations: test (a) at ~line 291, test (b) at ~line 337, and test (e) at ~line 507. Tests (a) and (b) got `{"detail": {"code": "github_user_token_required", "installation_id": FAKE_INSTALLATION_ID, "reason": "row_missing"}}`. Test (e) got `{"detail": {"code": "github_user_token_required"}}` (no reason/installation_id, exercising the optional-fields branch). The 502 and 503 mocks use flat string `detail` and were left unchanged — they match the backend contract. The tsc --noEmit gate failure was a false positive: run from the repo root (no tsconfig.json) it printed help text to stderr; pre-existing errors in unrelated files (vapid.test.ts, notification specs) exist in the frontend tree but are not introduced by this task.

## Verification

Playwright: `npx playwright test CreateGitHubRepoDialog --no-deps` — 30 passed, 5 skipped (browser-config skips), 0 failed. Backend: `cd backend && uv run pytest tests/api/routes/test_github_create_repository.py -v` — 9/9 passed. No backend files were modified.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `npx playwright test CreateGitHubRepoDialog --no-deps` | 0 | pass — 30 passed, 5 skipped, 0 failed across 5 browser configs | 30400ms |
| 2 | `cd backend && uv run pytest tests/api/routes/test_github_create_repository.py -v` | 0 | pass — 9/9 passed | 810ms |

## Deviations

Backend had 9 tests collected, not 12 as estimated in the plan — all 9 pass, no regression.

## Known Issues

Pre-existing TypeScript errors in frontend/src/lib/vapid.test.ts and tests/m005-* spec files (missing module 'vitest', missing sdk.gen.ts) are unrelated to this task and were present before S08 work began.

## Files Created/Modified

- `frontend/tests/components/CreateGitHubRepoDialog.spec.ts`
