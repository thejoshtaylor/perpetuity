# S06: Frontend reinstall CTA on 409 + admin runbook

**Goal:** CreateGitHubRepoDialog.tsx recognizes a 409 response with detail: "github_user_token_required" and renders an inline error with a "Reinstall the Perpetuity App on GitHub to grant repo creation access" button that opens the team's install URL in a new tab. The docs/runbooks/m006-github-oauth-setup.md runbook documents the one-time GitHub App admin task.
**Demo:** Playwright test mocks the backend to return 409 {"detail":"github_user_token_required","installation_id":12345,"reason":"row_missing"} on the create-repository POST. The dialog renders an inline error with the documented copy AND a button labeled "Reinstall on GitHub". Clicking the button calls GET /api/v1/teams/{teamId}/github/install-url (mocked to return install_url), then opens that URL via window.open(url, "_blank", "noopener,noreferrer"). The component test asserts the inline error is visible, the button is visible, and clicking the button fetches the install URL and calls window.open with the right url + noopener,noreferrer flags.

## Must-Haves

- Dialog distinguishes 409 github_user_token_required from generic errors via typed GitHubUserTokenRequiredError; renders inline error + reinstall CTA with data-testid=create-repo-reinstall-cta; submit button hidden in tokenRequired state; clicking CTA fetches /install-url and calls window.open with noopener,noreferrer; 502 transient shows retry message without CTA; 503 decrypt-failed shows config-error copy without CTA; runbook exists at docs/runbooks/m006-github-oauth-setup.md with at least 30 lines; m004 runbook cross-references it.

## Proof Level

- This slice proves: Integration — the dialog correctly handles three documented backend error responses (409, 502 transient, 503 decrypt-failed) plus the existing happy path; the reinstall button actually opens the install URL. Component tests with mocked fetch are sufficient; S07 covers real-runtime against real GitHub. No UAT.

## Integration Closure

Upstream surfaces consumed: S04's 409 / 502 / 503 response shapes; the existing /install-url endpoint; the existing Dialog, Button, Form UI primitives. New wiring: the GitHubUserTokenRequiredError class; the tokenRequired component state; the reinstall button mutation. No new route, no new API surface.

## Verification

- A single new console.warn line in the dialog when a 409 is received: console.warn(github_user_token_required, { installationId, reason }). The dialog's data-testid=create-repo-reinstall-cta is the operator's selector for screenshots. Each of the three documented error paths (409, 502 transient, 503 decrypt-failed) maps to a distinct visible copy block. No token plaintext or ciphertext is ever in the frontend.

## Tasks

- [x] **T01: `GitHubUserTokenRequiredError` + mutation error parsing + `tokenRequired` state** `est:1h`
  Separating the 409 path from the existing generic error path is the foundation for all the UX work; do it once with a typed error class so the JSX branch is a clean if (tokenRequired) ... else if (submitError) .... Define class GitHubUserTokenRequiredError extends Error with installationId and reason. In mutationFn, branch on (status===409 && body.detail===github_user_token_required) and throw typed error; on (status===502 && body.detail===github_token_refresh_transient) throw new Error('GitHub had a temporary problem. Try again in a moment.'); on (status===503 && body.detail===github_user_token_decrypt_failed) throw new Error('A configuration error prevented repo creation. The operator has been notified.'). Add tokenRequired state. In onError, branch on instanceof.
  - Files: `frontend/src/components/Teams/Projects/CreateGitHubRepoDialog.tsx`
  - Verify: cd frontend && npx tsc --noEmit && npm test -- CreateGitHubRepoDialog

- [x] **T02: Reinstall CTA JSX + install-url fetch + `window.open`** `est:1h`
  This is the user-visible substance of the slice. Above the existing submitError JSX (:228-236), add conditional block {tokenRequired && (<ReinstallCta ...>)}. Define ReinstallCta as a colocated component. CTA renders the copy block + button with data-testid=create-repo-reinstall-cta. Button's onClick uses a React Query mutation that fetches /api/v1/teams/${teamId}/github/install-url and on success calls window.open(data.install_url, _blank, noopener,noreferrer). On fetch failure, sets a local installUrlError state shown below the button. In parent dialog's footer JSX, replace existing LoadingButton type=submit with conditional: {!tokenRequired ? <LoadingButton type=submit ...> : null}. Cancel button visible in both branches.
  - Files: `frontend/src/components/Teams/Projects/CreateGitHubRepoDialog.tsx`
  - Verify: cd frontend && npx tsc --noEmit

- [x] **T03: Component tests for all four error branches + 409-no-reason case** `est:1.5h`
  Must-have (8)'s five test cases; each maps to one user-visible state that S06 must guarantee. The window.open spy and fetch mock combination is the linchpin proving the install URL actually flows through. Use existing component test harness (Vitest + React Testing Library OR Playwright component testing). Mock fetch per test case to return documented response shape. For window.open test, use vi.spyOn(window, 'open').mockImplementation. For install-url fetch in test (b), mock a second fetch response keyed on URL pattern. Each test case from must-have (8) gets its own it(...) block.
  - Files: `frontend/tests/components/CreateGitHubRepoDialog.test.tsx`
  - Verify: cd frontend && npm test -- CreateGitHubRepoDialog

- [x] **T04: Write runbook `docs/runbooks/m006-github-oauth-setup.md` + cross-reference from m004** `est:45m`
  The milestone is not deployable without an operator changing the GitHub App config; the runbook is how that knowledge persists. Write the runbook with sections Why / What to change / How to verify / Rollback / When this changes per must-have (9). Reference specific GitHub App settings page navigation. Include verification SQL query verbatim. Cross-reference m004 runbook. Add one-liner to m004.
  - Files: `docs/runbooks/m006-github-oauth-setup.md`, `docs/runbooks/m004-secrets-rotation.md`
  - Verify: test -f docs/runbooks/m006-github-oauth-setup.md && [ $(wc -l < docs/runbooks/m006-github-oauth-setup.md) -ge 30 ] && grep -q m006-github-oauth-setup docs/runbooks/m004-secrets-rotation.md

## Files Likely Touched

- frontend/src/components/Teams/Projects/CreateGitHubRepoDialog.tsx
- frontend/tests/components/CreateGitHubRepoDialog.test.tsx
- docs/runbooks/m006-github-oauth-setup.md
- docs/runbooks/m004-secrets-rotation.md
