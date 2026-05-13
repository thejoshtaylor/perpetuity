# S08: Fix S04→S06 409 response shape mismatch + align Playwright mocks

**Goal:** CreateGitHubRepoDialog.tsx correctly parses the nested `body.detail.code` field from the backend 409 response instead of comparing `body.detail` as a flat string. All Playwright test mocks use the actual backend shape `{"detail": {"code": "github_user_token_required", "installation_id": N, "reason": "..."}}`. All 7+ Playwright tests pass with the corrected mocks. Backend integration tests confirm no regression.
**Demo:** After this: CreateGitHubRepoDialog.tsx correctly parses the nested `body.detail.code` field from the backend 409 response. Playwright test mocks use the actual backend shape `{"detail": {"code": "github_user_token_required", "installation_id": N, "reason": "..."}}`. All 7+ Playwright tests pass with the corrected mocks. Backend integration tests confirm no regression.

## Must-Haves

- 1. Frontend 409 parsing reads `body.detail.code === "github_user_token_required"` and extracts `body.detail.installation_id` / `body.detail.reason` (nested object path).
- 2. All Playwright mock responses in CreateGitHubRepoDialog.spec.ts use the nested `{"detail": {"code": ..., "installation_id": ..., "reason": ...}}` shape matching the real backend HTTPException at github.py:1237-1244.
- 3. All 7 existing Playwright tests in CreateGitHubRepoDialog.spec.ts pass (npx playwright test CreateGitHubRepoDialog --no-deps).
- 4. All 12 backend tests in test_github_create_repository.py pass with zero changes (regression-clean).
- 5. TypeScript compilation has zero errors (npx tsc --noEmit).

## Proof Level

- This slice proves: contract — tests exercise the corrected parsing against mocks shaped like the real backend response

## Integration Closure

Upstream: S04 backend HTTPException at github.py:1237-1244 produces `{"detail": {"code": "github_user_token_required", "installation_id": N, "reason": "..."}}`. Downstream: S06 frontend CreateGitHubRepoDialog.tsx consumes this shape. This slice aligns the consumer to the producer. After this slice, the S04→S06 boundary contract is closed — no further wiring remains for the milestone.

## Verification

- console.warn arguments updated to log the nested detail object fields; no new observability surfaces added.

## Tasks

- [x] **T01: Fix 409 response parsing in CreateGitHubRepoDialog.tsx to read nested detail object** `est:15m`
  ## Why
  The backend HTTPException at `backend/app/api/routes/github.py:1237-1244` sends `{"detail": {"code": "github_user_token_required", "installation_id": N, "reason": "..."}}` but the frontend at `CreateGitHubRepoDialog.tsx:193-204` compares `body.detail === "github_user_token_required"` (flat string equality). Since `body.detail` is an object, this always fails and the reinstall CTA never renders against the real backend.
  - Files: `frontend/src/components/Teams/Projects/CreateGitHubRepoDialog.tsx`
  - Verify: cd frontend && npx tsc --noEmit

- [x] **T02: Align Playwright mock responses to nested 409 shape and verify all 7 tests pass** `est:20m`
  ## Why
  All Playwright mocks in CreateGitHubRepoDialog.spec.ts currently use the flat shape `{"detail": "github_user_token_required", "installation_id": N, "reason": "..."}` which doesn't match the real backend. After T01 fixes the parsing, these mocks must be updated to the nested shape so the tests exercise the real contract.
  - Files: `frontend/tests/components/CreateGitHubRepoDialog.spec.ts`
  - Verify: cd frontend && npx playwright test CreateGitHubRepoDialog --no-deps && cd ../backend && uv run pytest tests/api/routes/test_github_create_repository.py -v

## Files Likely Touched

- frontend/src/components/Teams/Projects/CreateGitHubRepoDialog.tsx
- frontend/tests/components/CreateGitHubRepoDialog.spec.ts
