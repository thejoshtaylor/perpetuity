# S08: Fix S04→S06 409 response shape mismatch + align Playwright mocks — UAT

**Milestone:** M006-ydo2ce
**Written:** 2026-05-13T18:17:51.975Z

# S08 UAT: 409 response parsing alignment

**Preconditions:**
- Frontend build succeeds (no TS errors)
- Backend and Playwright test suites pass
- Mock shapes in CreateGitHubRepoDialog.spec.ts use nested `{"detail": {"code": "...", "installation_id": ..., "reason": "..."}}` structure

**Test Steps:**

1. **Unit contract: Playwright mocks match backend response shape**
   - Verify `CreateGitHubRepoDialog.spec.ts` line ~291: 409 mock has nested `{"detail": {"code": "github_user_token_required", "installation_id": FAKE_INSTALLATION_ID, "reason": "row_missing"}}`
   - Verify `CreateGitHubRepoDialog.spec.ts` line ~337: 409 mock has nested `{"detail": {...}}` structure
   - Verify `CreateGitHubRepoDialog.spec.ts` line ~507: 409 mock with only `{"detail": {"code": "..."}}` (no reason/installation_id) exercises optional chaining
   - Expected outcome: All three mocks use nested structure; component reads `body.detail?.code` and extracts `body.detail?.installation_id`

2. **Frontend parsing: nested detail object is correctly extracted**
   - Verify CreateGitHubRepoDialog.tsx lines ~194-204: condition is `body.detail?.code === "github_user_token_required"`
   - Verify `body.detail?.installation_id` and `body.detail?.reason` are read from the nested object
   - Verify optional chaining (?.) allows safe access if detail shape is unexpected
   - Expected outcome: Reinstall CTA renders when 409 is returned with nested detail

3. **502/503 branches unchanged: flat string detail still works**
   - Verify CreateGitHubRepoDialog.tsx lines ~154, ~175: 502 and 503 checks still use `body.detail === "github_token_refresh_transient"` and `body.detail === "github_user_token_decrypt_failed"`
   - Verify backend github.py:1245+ raises HTTPException with flat string detail for those codes
   - Expected outcome: 502 and 503 flows still work without modification

4. **All tests pass: Playwright 30/30 (5 skipped), backend 9/9**
   - Run `npx playwright test CreateGitHubRepoDialog --no-deps` → 30 passed, 5 skipped, 0 failed
   - Run `cd backend && uv run pytest tests/api/routes/test_github_create_repository.py -v` → 9/9 passed
   - Run `npm run build --prefix frontend` → succeeds
   - Expected outcome: No regressions; all tests pass

**Edge Cases:**

- 409 with `detail = {"code": "github_user_token_required"}` (no installation_id/reason): optional chaining returns undefined; CTA still renders (test (e))
- 409 with unexpected detail shape (e.g., flat string): optional chaining returns undefined; CTA does not render (safe fallback)
- 502/503 with flat string detail: unchanged code paths work as before

**UAT Type:** Contract alignment (producer–consumer boundary closure)

**Not Proven By This UAT:**
- End-to-end GitHub OAuth with a real reinstall (proven by S07)
- Actual 409 response from real backend orchestrator (proven by S04 backend integration tests)
- User experience of the reinstall CTA in live app (proven by S06/S07 human QA)
