# S05: Orchestrator prefers user token for personal installs

**Goal:** The orchestrator's routes_github.py create-repository branch reads X-GitHub-User-Token from the incoming request; when the install is personal AND the header is present, uses the header value as the bearer token for POST /user/repos; when personal AND header absent, returns 422 user_token_required_for_personal_install (defense-in-depth); for org installs, ignores the header (logging at WARN if it was present) and uses the installation token against POST /orgs/{org}/repos exactly as today. THIS is the slice that proves the milestone's core claim: GitHub's POST /user/repos accepts the forwarded user token.
**Demo:** Three orchestrator integration tests against a respx-mocked GitHub: (1) personal install + X-GitHub-User-Token: ghu_test header → orchestrator calls POST api.github.com/user/repos with Authorization: token ghu_test; GitHub mock returns 201. (2) personal install + no header → orchestrator returns 422 with detail == "user_token_required_for_personal_install". (3) org install (with or without header) → orchestrator calls POST api.github.com/orgs/<login>/repos with Authorization: token <install_token>; no header forwarding regardless of X-GitHub-User-Token presence.

## Must-Haves

- Personal install + user_token header -> POST /user/repos with Authorization: token <user_token>; personal install + no header -> 422 user_token_required_for_personal_install with no GitHub call; org install ignores header and uses install token against /orgs/{org}/repos (byte-identical to M005-sqm8et); install-token mint skipped on personal-install + user-token branch (saves rate-limited mint calls); new INFO log carries token_class=user_token user_token_prefix=ghu_; existing M005-sqm8et org-install tests pass.

## Proof Level

- This slice proves: Operational — a real (mocked-real, but the call shape is identical to real-GitHub) POST /user/repos succeeds with the forwarded user token. Until this passes, the milestone has zero proof. Orchestrator integration harness; respx-mocked GitHub. No UAT (that's S07).

## Integration Closure

Upstream surfaces consumed: S04's X-GitHub-User-Token header forwarding; existing lookup_installation, get_installation_token; existing X-Orchestrator-Key auth middleware. New wiring: the user_token header read; the install-token-mint reordering; the personal-install bearer-token swap; the 422 defense-in-depth path.

## Verification

- Existing github_repository_created INFO log retained for org installs (unchanged). New github_repository_created ... token_class=user_token INFO log for personal-install success. New WARN log github_create_repository_failed ... reason=user_token_required_for_personal_install on the 422 path. New WARN log github_create_repository_unexpected_user_token_on_org for the backend-bug case. No token value or ciphertext in orchestrator logs.

## Tasks

- [x] **T01: Reorder install-token mint to after `lookup_installation` + add header read** `est:45m`
  Minting the install token before knowing the install type wastes a GitHub mint call on personal installs that won't use it; doing so also makes the 422 path noisy in logs. Reorder once before adding branching logic so the diff is easier to review. Read user_token = (request.headers.get(X-GitHub-User-Token) or '').strip() or None immediately after the JSON body parse (:243-253). Move the get_installation_token block at :256-275 and the resulting token variable definition at :277-286 to AFTER the lookup_installation block at :311-330. Keep all existing exception mapping intact.
  - Files: `orchestrator/orchestrator/routes_github.py`
  - Verify: cd orchestrator && uv run pytest tests/integration/test_create_repository.py -v

- [x] **T02: Branch on `account_type` + user-token-prefer logic + 422 defense-in-depth** `est:1.5h`
  The slice's substance — every documented branch combination of (account_type, user_token) reaches the right HTTP call. Implement must-have (3) decision matrix. For personal-install + user-token-present branch: build create_url = https://api.github.com/user/repos and auth_header = token <user_token>. For personal-install + no-token branch: return 422 with documented detail BEFORE install-token mint code path is reached. For org installs: log WARN if user_token is not None and continue with install-token path. Success-log line for user-token branch includes token_class=user_token user_token_prefix=<first 4 chars>.
  - Files: `orchestrator/orchestrator/routes_github.py`
  - Verify: cd orchestrator && uv run python -c "from orchestrator.routes_github import create_repository_route; print('ok')"

- [ ] **T03: Integration tests `test_create_repository_user_token.py` + M005-sqm8et 502->422 update** `est:2.5h`
  This is where the milestone's core proof lands — must-have (6) test_personal_install_with_user_token_uses_user_token_for_user_repos is the assertion that POST /user/repos accepts the forwarded token. Until this passes, every other slice in M006-ydo2ce is plumbing for a claim we have not yet proven. Implement all five tests in must-have (6) using respx against api.github.com so the test asserts the exact URL hit by the orchestrator. For the installation-token-mint mock, intercept api.github.com/app/installations/<id>/access_tokens and assert call count of zero in the personal-install-with-user-token test. Update existing M005-sqm8et test that asserts 502 on personal-install create-repository to assert 422 with the new detail.
  - Files: `orchestrator/tests/integration/test_create_repository_user_token.py`, `orchestrator/tests/integration/test_create_repository.py`
  - Verify: cd orchestrator && uv run pytest tests/integration/test_create_repository_user_token.py tests/integration/test_create_repository.py -v

## Files Likely Touched

- orchestrator/orchestrator/routes_github.py
- orchestrator/tests/integration/test_create_repository_user_token.py
- orchestrator/tests/integration/test_create_repository.py
