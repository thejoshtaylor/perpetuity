---
id: T03
parent: S06
milestone: M006-ydo2ce
key_files:
  - frontend/tests/components/CreateGitHubRepoDialog.spec.ts
key_decisions:
  - Stubbed users/me in each T03 test to bypass the _layout beforeLoad auth guard — this is necessary because the backend /api/v1/auth/login returns 500 (pre-existing infrastructure issue with auto-generated SECRET_KEY on restart)
  - Used page.addInitScript to intercept window.open before navigation so the spy is in place when the reinstall button fires the mutation
  - Reused the single spec file from T01 rather than creating a second file — the run command 'npm test -- CreateGitHubRepoDialog' covers both describe blocks cleanly
  - The --no-deps flag is documented in the verification evidence; the auth.setup.ts failure is a pre-existing issue not introduced by T03
duration: 
verification_result: passed
completed_at: 2026-05-12T23:14:15.972Z
blocker_discovered: false
---

# T03: Added five Playwright component tests covering all four error branches (409 reinstall CTA, 409 window.open flow, 502 transient, 503 decrypt-failed) plus the 409-no-reason optional-field case

**Added five Playwright component tests covering all four error branches (409 reinstall CTA, 409 window.open flow, 502 transient, 503 decrypt-failed) plus the 409-no-reason optional-field case**

## What Happened

Appended a new `CreateGitHubRepoDialog — error branches` describe block to the existing spec file (frontend/tests/components/CreateGitHubRepoDialog.spec.ts), which already held the T01 error-class unit tests. The five T03 tests use Playwright's page.route() to mock all backend API calls, allowing the dialog chain (team-detail → CreateProjectDialog → CreateGitHubRepoDialog) to be exercised without a live backend.

Key implementation decisions:
1. Stubbed `GET /api/v1/users/me` within each test's setup — the `_layout.tsx` beforeLoad guard calls `ensureQueryData(['currentUser'])` and throws redirect on any error; mocking this endpoint bypasses the live-auth requirement and prevents the /login redirect.
2. Stubbed all team-detail sibling calls (members, projects, secrets, workflows, installations, repositories) in a shared `stubTeamDetailSiblings` helper.
3. Navigated through the full component tree (team-detail → New Project button → installation select → Create new repository button) to open the actual CreateGitHubRepoDialog in a real browser context — not a contrived render — so the test exercises the real component wiring.
4. Used `page.addInitScript` to override `window.open` before navigation in test (b), recording calls into `window.__openCalls` for assertion. Playwright's `page.evaluate` then inspects the call record asynchronously with `expect(...).toPass()` to handle React's async mutation flow.
5. The `--no-deps` flag is required when running locally because the auth.setup.ts project cannot refresh the session (backend /api/v1/auth/login returns 500 — pre-existing infrastructure issue with the random SECRET_KEY). The T03 tests are fully self-contained and do not need live auth.

All 7 tests (2 T01 + 5 T03) pass in 9-11s on chromium.

## Verification

Ran: cd frontend && npm test -- CreateGitHubRepoDialog --project=chromium --no-deps. All 7 tests passed (9.6s). The five T03 tests each independently stub the backend and exercise the component in a real Chromium browser context.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd /Users/josh/code/perpetuity/frontend && npm test -- CreateGitHubRepoDialog --project=chromium --no-deps` | 0 | 7 passed (9.6s) | 9600ms |

## Deviations

Added users/me stub to stubTeamDetailSiblings — not mentioned in the task plan, but required because the _layout.tsx beforeLoad guard redirects to /login on any error from readUserMe, and the backend auth endpoint is broken (500). Without this stub the tests cannot reach the team-detail route at all. This is an adaptation to local infrastructure state, not a deviation from the S06 contract.

## Known Issues

The auth.setup.ts setup project fails because the backend /api/v1/auth/login returns 500 (auto-generated SECRET_KEY on each backend restart makes stored JWTs invalid). Running the full chromium suite without --no-deps will show 7 skipped due to setup failure. T03 tests are fully self-contained and pass with --no-deps. This is a pre-existing infrastructure issue unrelated to S06.

## Files Created/Modified

- `frontend/tests/components/CreateGitHubRepoDialog.spec.ts`
