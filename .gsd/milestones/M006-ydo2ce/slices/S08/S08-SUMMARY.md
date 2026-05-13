---
id: S08
parent: M006-ydo2ce
milestone: M006-ydo2ce
provides:
  - (none)
requires:
  []
affects:
  []
key_files:
  - (none)
key_decisions:
  - (none)
patterns_established:
  - (none)
observability_surfaces:
  - none
drill_down_paths:
  []
duration: ""
verification_result: passed
completed_at: 2026-05-13T18:17:51.974Z
blocker_discovered: false
---

# S08: Fix S04→S06 409 response shape mismatch + align Playwright mocks

**CreateGitHubRepoDialog.tsx now correctly parses nested 409 response detail object; all Playwright and backend tests pass without regression.**

## What Happened

S08 fixed a contract mismatch between backend (S04) and frontend (S06). The backend HTTPException at github.py:1237-1244 sends `{"detail": {"code": "github_user_token_required", "installation_id": N, "reason": "..."}}` (nested object), but the frontend was comparing `body.detail === "github_user_token_required"` (flat string equality), causing the reinstall CTA to never render.\n\nT01 fixed the parsing in CreateGitHubRepoDialog.tsx by changing three read sites:\n- `body.detail?.code === "github_user_token_required"` (was flat string equality)\n- `body.detail?.installation_id` (was `body.installation_id`)\n- `body.detail?.reason` (was `body.reason`)\nOptional chaining safely handles unexpected response shapes. The 502 and 503 checks were left unchanged—those backend paths raise with flat string detail, per github.py:1245+.\n\nT02 updated all three 409 Playwright mock bodies from the old flat shape to the nested shape matching the real backend. Tests (a) and (b) got `{"detail": {"code": "github_user_token_required", "installation_id": FAKE_INSTALLATION_ID, "reason": "row_missing"}}`. Test (e) got `{"detail": {"code": "github_user_token_required"}}` to exercise the optional-fields branch via optional chaining. The 502 and 503 mocks use flat string detail and were left unchanged—they match their backend contract.\n\nVerification:\n- Frontend build: clean (no errors from CreateGitHubRepoDialog.tsx)\n- Playwright: 30 passed, 5 skipped (browser-config skips), 0 failed\n- Backend regression: 9/9 tests passed; no files modified\n- TypeScript: zero errors in changed file (pre-existing errors in unrelated files: vapid.test.ts, notification specs)\n\nThe S04→S06 boundary is now closed—the frontend consumer and backend producer agree on the 409 response shape.

## Verification

1. T01: cd /Users/josh/code/perpetuity/frontend && npx tsc --noEmit (no matches for CreateGitHubRepoDialog → PASS)\n2. T02: npx playwright test CreateGitHubRepoDialog --no-deps (30 passed, 5 skipped, 0 failed → PASS)\n3. Backend regression: cd backend && uv run pytest tests/api/routes/test_github_create_repository.py -v (9/9 passed → PASS)\n4. Frontend build: npm run build --prefix frontend (clean → PASS)

## Requirements Advanced

None.

## Requirements Validated

None.

## New Requirements Surfaced

None.

## Requirements Invalidated or Re-scoped

None.

## Operational Readiness

None.

## Deviations

None.

## Known Limitations

None.

## Follow-ups

None.

## Files Created/Modified

None.
