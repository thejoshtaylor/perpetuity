# S04: Backend forwards `X-GitHub-User-Token` to orchestrator for personal installs

**Goal:** POST /api/v1/teams/{team_id}/github/installations/{installation_id}/create-repository looks up the install's account_type, resolves the current user's OAuth access token via S03's get_user_access_token whenever the install is personal, and forwards it to the orchestrator as an X-GitHub-User-Token header. The 'team member who is NOT the installing user' and the 'row missing' cases both surface as a clean 409 github_user_token_required so the frontend can render the reinstall CTA.
**Demo:** Three backend-route integration tests against the test client: (1) personal install + token row present for current_user.id → orchestrator receives a httpx call with X-GitHub-User-Token: <plaintext> header; backend returns 201. (2) personal install + no token row → backend returns 409 {"detail": "github_user_token_required", "installation_id": <int>} without calling the orchestrator. (3) org install → backend calls the orchestrator without the new header (M005-sqm8et regression-clean).

## Must-Haves

- Route branches on account_type; personal install resolves user token via S03; X-GitHub-User-Token header forwarded only for personal installs; UserTokenUnavailable maps to 409 github_user_token_required with installation_id and reason in body; refresh_transient maps to 502; GitHubUserTokenDecryptError maps to 503 with ERROR log; defense-in-depth assertion catches bug-forwarded user token for org install; no token plaintext, ciphertext, or Authorization header in logs.

## Proof Level

- This slice proves: Integration — the backend route correctly resolves the install type, calls the right helper, forwards the right header, and surfaces all five exception paths with the documented HTTP responses. Postgres + the test client + a mocked orchestrator; no real GitHub call. No UAT.

## Integration Closure

Upstream surfaces consumed: S03's get_user_access_token, UserTokenUnavailable, GitHubUserTokenDecryptError; existing _orch_create_repository; existing GitHubAppInstallation table for account_type lookup. New wiring: the account_type branch in the route; the user_token parameter on _orch_create_repository; the new exception-to-HTTP mapping table.

## Verification

- Existing github_repository_created INFO log unchanged. New WARN log github_user_token_required user_id=<uuid> installation_id=<int> reason=<reason> on the 409 path. New WARN log github_token_refresh_transient. New ERROR log github_user_token_decrypt_failed. Every exception branch carries user_id AND installation_id AND a stable reason/detail string. No token plaintext, no ciphertext, no Authorization header verbatim in logs.

## Tasks

- [x] **T01: Extend `_orch_create_repository` to accept and forward optional `user_token`** `est:45m`
  Lock the helper's signature change before changing the route, so the route change is the only place exception mapping matters. Add user_token: str | None = None to the function signature. Build headers = {X-Orchestrator-Key: settings.ORCHESTRATOR_API_KEY} then if user_token is not None: headers[X-GitHub-User-Token] = user_token. No other behavior change. Add unit test that asserts: (a) calling with user_token=None produces a request without the new header, (b) calling with user_token=ghu_test produces a request with X-GitHub-User-Token: ghu_test, (c) X-Orchestrator-Key header is always present.
  - Files: `backend/app/api/routes/github.py`, `backend/tests/api/routes/test_github_orch_create_repository.py`
  - Verify: cd backend && uv run pytest tests/api/routes/test_github_orch_create_repository.py -v

- [x] **T02: Wire `account_type` branch + `get_user_access_token` + exception mapping into the route** `est:1.5h`
  This is the slice's user-visible substance. Each exception class has a distinct HTTP status, and each has to be surfaced separately so S06's CTA logic can branch on detail accurately. After the installation_not_found check at :992-993, before body validation at :996, add new section. If installation.account_type != Organization: try user_token = await get_user_access_token(session, current_user.id); catch UserTokenUnavailable and GitHubUserTokenDecryptError and map them to documented HTTP responses. Else: user_token = None. Pass user_token=user_token to _orch_create_repository call at :1019. Add defense-in-depth assertion from must-have (10) immediately before that call.
  - Files: `backend/app/api/routes/github.py`
  - Verify: cd backend && uv run python -c "from app.api.routes.github import create_github_repository; print('ok')"

- [x] **T03: Route integration tests covering all five branches + org-install regression** `est:2h`
  The contract surface this slice ships is a 1-of-5 HTTP response decision tree; every leaf needs an explicit test, including the no-side-effects assertions (orchestrator NOT called on the 409/502/503 branches). Implement the six test cases from must-have (7): test_personal_install_forwards_user_token, test_personal_install_missing_token_returns_409, test_org_install_no_user_token_header, test_personal_install_refresh_transient_returns_502, test_personal_install_decrypt_failure_returns_503, test_personal_install_bad_refresh_token_includes_reason. Use respx for orchestrator mock; assert call count of zero when route is expected to short-circuit. Use caplog to assert no captured log message contains the literal mocked token plaintext.
  - Files: `backend/tests/api/routes/test_github_create_repository.py`
  - Verify: cd backend && uv run pytest tests/api/routes/test_github_create_repository.py -v

## Files Likely Touched

- backend/app/api/routes/github.py
- backend/tests/api/routes/test_github_orch_create_repository.py
- backend/tests/api/routes/test_github_create_repository.py
