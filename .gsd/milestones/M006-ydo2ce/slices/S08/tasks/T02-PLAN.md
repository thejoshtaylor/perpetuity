---
estimated_steps: 27
estimated_files: 1
skills_used: []
---

# T02: Align Playwright mock responses to nested 409 shape and verify all 7 tests pass

## Why
All Playwright mocks in CreateGitHubRepoDialog.spec.ts currently use the flat shape `{"detail": "github_user_token_required", "installation_id": N, "reason": "..."}` which doesn't match the real backend. After T01 fixes the parsing, these mocks must be updated to the nested shape so the tests exercise the real contract.

## Steps
1. Read `frontend/tests/components/CreateGitHubRepoDialog.spec.ts`.
2. Find every `route.fulfill` that returns a 409 response (at least 3 locations: test (a) ~line 294, test (b) ~line 340, test (e) ~line 510).
3. Change each mock body from:
   ```json
   {"detail": "github_user_token_required", "installation_id": N, "reason": "row_missing"}
   ```
   to:
   ```json
   {"detail": {"code": "github_user_token_required", "installation_id": N, "reason": "row_missing"}}
   ```
4. For test (e) which omits `reason` and `installation_id`, change to:
   ```json
   {"detail": {"code": "github_user_token_required"}}
   ```
5. Verify 502 and 503 mock shapes are untouched (they use flat string detail matching the backend).
6. Run `cd frontend && npx playwright test CreateGitHubRepoDialog --no-deps` — all 7 tests must pass.
7. Run `cd backend && uv run pytest tests/api/routes/test_github_create_repository.py -v` — all 12 tests must pass (regression-clean, no changes to backend).

## Must-Haves
- All 409 mocks use nested `{"detail": {"code": ..., "installation_id": ..., "reason": ...}}` shape
- 502 and 503 mocks unchanged
- All 7 Playwright tests pass
- All 12 backend tests pass (no backend files changed)

## Done when
Playwright: 7/7 passed. Backend: 12/12 passed. Zero mock bodies use the old flat 409 shape.

## Inputs

- ``frontend/tests/components/CreateGitHubRepoDialog.spec.ts` — current Playwright mocks with flat 409 shape`
- ``frontend/src/components/Teams/Projects/CreateGitHubRepoDialog.tsx` — T01 output with corrected parsing logic`

## Expected Output

- ``frontend/tests/components/CreateGitHubRepoDialog.spec.ts` — all 409 mock bodies updated to nested detail shape`

## Verification

cd frontend && npx playwright test CreateGitHubRepoDialog --no-deps && cd ../backend && uv run pytest tests/api/routes/test_github_create_repository.py -v
