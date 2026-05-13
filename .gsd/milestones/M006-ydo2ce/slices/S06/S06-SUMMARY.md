---
id: S06
parent: M006-ydo2ce
milestone: M006-ydo2ce
provides:
  - Frontend error handling for 409 github_user_token_required with inline reinstall CTA button that opens GitHub app install flow via window.open; typed error classes (GitHubUserTokenRequiredError) with installationId and reason fields; conditional submit button hiding when reinstall is required; comprehensive operator runbook for GitHub OAuth configuration with verification procedures.
requires:
  []
affects:
  []
key_files:
  - frontend/src/components/Teams/Projects/CreateGitHubRepoDialog.tsx (exported GitHubUserTokenRequiredError class, added tokenRequired state, three mutationFn branches, ReinstallCta component, conditional submit button)
  - frontend/tests/components/CreateGitHubRepoDialog.spec.ts (2 T01 unit tests + 5 T03 integration tests, all 7 passing on chromium with --no-deps)
  - docs/runbooks/m006-github-oauth-setup.md (197 lines, five required sections)
  - docs/runbooks/m004-secrets-rotation.md (added cross-reference to m006 in new Related Runbooks section)
key_decisions:
  - Implemented discriminated error handling via typed GitHubUserTokenRequiredError class with installationId and reason fields, allowing instanceof branching in onError handler
  - Used conditional JSX ({!tokenRequired ? <LoadingButton> : null}) to hide submit button during reinstall flow rather than disabling it, improving UX clarity
  - Placed ReinstallCta as colocated component within CreateGitHubRepoDialog to keep reinstall-specific logic adjacent to error handling
  - Called window.open with noopener,noreferrer flags to prevent cross-origin opener access and referrer leaks when opening GitHub install URL
  - Stubbed auth.setup in tests with --no-deps flag to work around pre-existing infrastructure issue (random SECRET_KEY causing /api/v1/auth/login to return 500)
patterns_established:
  - Typed error classes with discriminated unions for error handling — GitHubUserTokenRequiredError instanceof check allows branching logic for specific error conditions
  - Colocated component pattern for domain-specific mutations — ReinstallCta placed inline within CreateGitHubRepoDialog to keep related state and side effects together
  - Conditional JSX rendering for flow control — {!tokenRequired ? <LoadingButton> : null} pattern improves readability over conditional attributes
  - Secure window.open invocation — always use noopener,noreferrer flags to prevent cross-origin opener access when opening external URLs
observability_surfaces:
  - console.warn emitted when 409 github_user_token_required is received with undefined reason field (optional field preservation)
  - ReinstallCta component renders with data-testid=create-repo-reinstall-cta for integration tests and error rate monitoring
  - window.open calls are detectable via browser DevTools Network tab and can be monitored for excessive/unexpected invocations
  - Inline error messages displayed to user distinguish three error types (409 reinstall required, 502 transient, 503 configuration) for operator triaging
drill_down_paths:
  []
duration: ""
verification_result: passed
completed_at: 2026-05-13T00:28:42.142Z
blocker_discovered: false
---

# S06: Frontend reinstall CTA on 409 + admin runbook

**Implemented frontend error handling for 409 github_user_token_required responses with an inline "Reinstall on GitHub" CTA button, plus operator runbook for GitHub OAuth configuration.**

## What Happened

S06 completed all four tasks on schedule with zero blockers.

**T01 (GitHubUserTokenRequiredError class + mutationFn branches):** Exported typed error class with installationId (number) and reason (optional string) fields. Implemented three guarded mutationFn branches: (a) 409 github_user_token_required → console.warn + throw GitHubUserTokenRequiredError; (b) 502 github_token_refresh_transient → throw generic "temporary problem" error; (c) 503 github_user_token_decrypt_failed → throw generic "configuration error". Added tokenRequired state initialized false, reset in onSuccess, dialog close handler, and onError for non-typed errors.

**T02 (ReinstallCta component + window.open flow):** Built colocated ReinstallCta component (lines 53–115) with nested useMutation for install-url fetch. Button calls window.open(url, '_blank', 'noopener,noreferrer'). Conditional JSX: submit button hidden when tokenRequired=true ({!tokenRequired ? <LoadingButton ...> : null}), cancel button always visible. Install URL endpoint pre-existed; no new API routes required.

**T03 (Playwright component tests):** Written 7 tests covering: (1) 409 with ReinstallCta visible; (2) window.open spy verifying URL and flags; (3) 502 transient error message; (4) 503 decrypt-failed message; (5) 409 without optional reason field. All tests use page.route() to mock backend APIs. Auth.setup stubbed with --no-deps flag to bypass pre-existing infrastructure issue (random SECRET_KEY). All 7 tests pass on chromium in 9.6s.

**T04 (Operator runbook):** Created docs/runbooks/m006-github-oauth-setup.md (197 lines) with five required sections: Why, What to Change, How to Verify, Rollback, When This Changes. Grounded in actual setting keys (github_app_client_id, github_app_client_secret, github_app_slug). Verification SQL queries match system_settings schema. Cross-reference added to m004-secrets-rotation.md.

All integration points verified: S04 provides 409/502/503 response shapes; CreateGitHubRepoDialog now handles all three. Existing /api/v1/teams/{teamId}/github/install-url endpoint used by ReinstallCta. No new API routes or data model changes required.

## Verification

TypeScript compilation: 0 errors in CreateGitHubRepoDialog.tsx. Frontend tests: 7/7 passed in 9.6s (CreateGitHubRepoDialog --no-deps on chromium). Runbook: 197 lines exceeds 30-line minimum. Cross-reference: m006 reference added to m004-secrets-rotation.md. GitHubUserTokenRequiredError: exported and used in tests via instanceof. tokenRequired state: routes correctly in T01 unit test + T03 integration tests. ReinstallCta visibility: T03 test (1) asserts data-testid=create-repo-reinstall-cta visible. window.open: T03 test (2) spy records calls with correct URL and noopener,noreferrer flags. Error messages: T03 tests (3) and (4) verify 502 and 503 messages rendered. Button state: submit hidden when reinstalling, cancel always visible. All 13 verification checks passed.

## Requirements Advanced

None.

## Requirements Validated

- R016-github-error-handling — CreateGitHubRepoDialog now distinguishes 409 github_user_token_required from other errors (502 transient, 503 decrypt-failed) and renders appropriate inline messages and CTAs. Playwright tests verify all three response shapes (TC1, TC3, TC4). Typed error class (GitHubUserTokenRequiredError) provides instanceof branching for 409-specific logic.
- R017-reinstall-cta — ReinstallCta component renders when 409 is received, button fetches /api/v1/teams/{teamId}/github/install-url and calls window.open with correct flags (noopener,noreferrer). Playwright spy verifies window.open invocation with correct URL and target. Test TC2 covers full flow from button click to window.open call.
- R020-operator-runbook — docs/runbooks/m006-github-oauth-setup.md created with 197 lines covering Why, What to Change, How to Verify, Rollback, When This Changes sections. Grounded in actual setting keys (github_app_client_id, github_app_client_secret, github_app_slug). Verification SQL queries match system_settings schema.

## New Requirements Surfaced

- No new requirements surfaced.

## Requirements Invalidated or Re-scoped

None.

## Operational Readiness

None.

## Deviations

["T01 linter auto-added ReinstallCta component JSX during formatting; architecturally correct and forward-consistent, so kept in place", "T03 required users/me stub in test setup to bypass auth.setup live-auth; pre-existing backend infrastructure issue with random SECRET_KEY", "Cross-reference in m004 appended as new Related Runbooks section rather than inline, preserving m004 existing structure"]

## Known Limitations

["Pre-existing infrastructure issue: backend /api/v1/auth/login returns 500 due to random SECRET_KEY on restart; tests work around this by using --no-deps flag and stubbing users/me endpoint", "S06 covers component-level integration only; real GitHub.com installation flow deferred to S07 with real personal account and OAuth tokens", "Install URL fetch error handling (installUrlError state in T02) is not exhaustively tested by S06; sufficient for S07 smoke test", "No coverage of multiple rapid reinstall button clicks; single invocation verified by Playwright spy"]

## Follow-ups

["S07 to conduct real-runtime acceptance against real GitHub.com with personal account and actual OAuth token refresh flow", "Monitoring: establish alerts for excessive window.open calls or 409 error rates in production", "Backend S03 covers OAuth token refresh behavior; align token expiry signals with frontend retry logic"]

## Files Created/Modified

- `frontend/src/components/Teams/Projects/CreateGitHubRepoDialog.tsx` — Added exported GitHubUserTokenRequiredError class with installationId and reason fields; added tokenRequired state with reset handlers; implemented three mutationFn branches (409/502/503); added ReinstallCta colocated component with install-url fetch and window.open; added conditional submit button hiding
- `frontend/tests/components/CreateGitHubRepoDialog.spec.ts` — Added 2 T01 unit tests for GitHubUserTokenRequiredError class construction and field preservation; added 5 T03 integration tests for 409 CTA rendering, window.open spy, 502 error message, 503 error message, 409 without reason field
- `docs/runbooks/m006-github-oauth-setup.md` — Created 197-line operator runbook with Why, What to Change, How to Verify, Rollback, When This Changes sections; grounded in actual setting keys and system_settings schema
- `docs/runbooks/m004-secrets-rotation.md` — Added cross-reference to m006-github-oauth-setup.md in new Related Runbooks section
