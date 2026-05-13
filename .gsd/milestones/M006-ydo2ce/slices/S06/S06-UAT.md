# S06: Frontend reinstall CTA on 409 + admin runbook — UAT

**Milestone:** M006-ydo2ce
**Written:** 2026-05-13T00:28:42.143Z

# S06 User Acceptance Testing (UAT)

## Scope

S06 proves **integration-level correctness** of the frontend error handling and reinstall CTA flow. All verification is conducted via component tests with mocked backend responses. S07 covers real-runtime acceptance against real GitHub.com.

## UAT Type

**Component Integration Tests** — Playwright tests with page.route() mocking backend APIs. No live backend or GitHub account required. All tests are deterministic and idempotent.

## Preconditions

- frontend npm packages installed (`npm install`)
- Playwright browsers available (already installed by prior test runs)
- No live backend or GitHub account required (all APIs mocked)
- Run with `--no-deps` flag to bypass auth.setup (pre-existing infrastructure issue with random SECRET_KEY)

## Test Cases

### TC1: 409 Response Triggers Reinstall CTA Block
**Precondition:** User navigates to team-detail, opens create-repository dialog.
**Steps:**
1. Backend mocked to return `POST /api/v1/teams/{teamId}/repositories` → 409 with body `{"detail": "github_user_token_required", "installation_id": 12345, "reason": "row_missing"}`
2. User submits the create-repository form
3. Dialog mutation fetches the API and receives 409
**Expected Outcome:**
- onError executes, throws GitHubUserTokenRequiredError (instanceof check confirms type)
- tokenRequired state set to true
- ReinstallCta component renders with data-testid="create-repo-reinstall-cta" visible
- Submit button hidden (conditional {!tokenRequired ? <LoadingButton> : null})
- Cancel button visible
- Inline error message displayed with copy about reinstalling the app

### TC2: Reinstall Button Opens Install URL via window.open
**Precondition:** TC1 passes; ReinstallCta is rendered.
**Steps:**
1. page.route() intercepts `GET /api/v1/teams/{teamId}/github/install-url` → 200 with `{"install_url": "https://github.com/apps/perpetuity/installations/new"}`
2. page.addInitScript() places a spy on window.open before navigation
3. User clicks the "Reinstall on GitHub" button in ReinstallCta
4. installUrlMutation fires, fetches the URL, then calls window.open
**Expected Outcome:**
- window.open called exactly once with arguments: (url="https://github.com/apps/perpetuity/installations/new", target="_blank", options="noopener,noreferrer")
- data-testid="create-repo-reinstall-button" click handler properly chains fetch → window.open
- No error thrown; spinner visible on button during fetch

### TC3: 502 github_token_refresh_transient Shows Generic Transient Error
**Precondition:** User navigates to team-detail, opens create-repository dialog.
**Steps:**
1. Backend mocked to return `POST /api/v1/teams/{teamId}/repositories` → 502 with body `{"detail": "github_token_refresh_transient"}`
2. User submits the form
3. Dialog mutation receives 502
**Expected Outcome:**
- onError executes, throws generic Error with message "GitHub had a temporary problem. Try again in a moment."
- tokenRequired state remains false (not instanceof GitHubUserTokenRequiredError)
- submitError state set to the message
- submitError JSX block rendered (above ReinstallCta block)
- Submit button visible (because !tokenRequired is true)
- User can retry without reinstalling

### TC4: 503 github_user_token_decrypt_failed Shows Configuration Error
**Precondition:** User navigates to team-detail, opens create-repository dialog.
**Steps:**
1. Backend mocked to return `POST /api/v1/teams/{teamId}/repositories` → 503 with body `{"detail": "github_user_token_decrypt_failed"}`
2. User submits the form
3. Dialog mutation receives 503
**Expected Outcome:**
- onError executes, throws generic Error with message "A configuration error prevented repo creation. The operator has been notified."
- tokenRequired state remains false
- submitError state set to the message
- submitError JSX block rendered
- Submit button visible
- User sees operator-facing message (not a retry CTA)

### TC5: 409 Without Optional reason Field Is Handled Gracefully
**Precondition:** User navigates to team-detail, opens create-repository dialog.
**Steps:**
1. Backend mocked to return `POST /api/v1/teams/{teamId}/repositories` → 409 with body `{"detail": "github_user_token_required", "installation_id": 12345}` (no reason field)
2. User submits the form
**Expected Outcome:**
- GitHubUserTokenRequiredError constructed with installationId=12345, reason=undefined (optional field)
- ReinstallCta rendered (tokenRequired = true)
- console.warn emitted with undefined reason value (not an error)
- Dialog behavior unchanged from TC1 (CTA visible, submit hidden)

## Edge Cases Not Proven by S06

- Real GitHub App installation with a real personal account
- Actual OAuth refresh token expiry behavior (tested in backend S03, not frontend)
- Multiple rapid clicks on the reinstall button (not relevant for frontend; tested by Playwright spy single invocation)
- Network failure during install-url fetch (T02 includes installUrlError state but S06 does not cover the error branch exhaustively; sufficient for smoke test in S07)

## Verification Evidence

All 7 tests (2 from T01, 5 from T03) pass deterministically on chromium:

| Test | Status | Duration |
|------|--------|----------|
| GitHubUserTokenRequiredError class — instanceof | ✓ PASS | <100ms |
| GitHubUserTokenRequiredError — reason field preservation | ✓ PASS | <100ms |
| 409 reinstall CTA visible | ✓ PASS | ~1.5s |
| 409 window.open spy flow | ✓ PASS | ~1.8s |
| 502 transient error message | ✓ PASS | ~1.5s |
| 503 decrypt-failed message | ✓ PASS | ~1.5s |
| 409 without reason field | ✓ PASS | ~1.5s |
| **Total** | **7/7 PASS** | **9.6s** |

Run command: `cd frontend && npm test -- CreateGitHubRepoDialog --project=chromium --no-deps`

## Not Proven by This UAT

- S07 covers real GitHub.com installation flow with a real personal account and real OAuth tokens
- Real token refresh behavior (backend S03 covers this)
- Org-install regression (backend S05 + M005 integration tests cover this)
- Real 409/502/503 response shapes from production backend (integration tested in backend S04/S05)
