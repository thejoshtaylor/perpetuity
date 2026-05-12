# S03: `get_user_access_token` refresh-on-read helper

**Goal:** Backend callers can request a valid OAuth access token for a Perpetuity user by user_id, and the helper returns a token that is either the stored unexpired one or a freshly refreshed one. When the refresh token itself is expired or revoked by GitHub, the helper deletes the row and raises a sentinel UserTokenUnavailable so callers can map it to a 409 telling the user to reinstall.
**Demo:** Three unit tests against a respx-mocked token endpoint: (1) row exists, access token unexpired → returns the stored plaintext directly (no GitHub call). (2) row exists, access token expired but refresh token valid → POSTs to github.com/login/oauth/access_token with grant_type=refresh_token; helper updates the row and returns the new plaintext. (3) row exists, refresh token expired → GitHub returns 400 bad_refresh_token; the helper deletes the row and raises UserTokenUnavailable. (4) row does not exist → raises UserTokenUnavailable without making any HTTP call.

## Must-Haves

- get_user_access_token returns valid token within 60s skew threshold; refresh-success path updates row atomically and returns new plaintext; four refresh-failure reasons (bad_refresh_token, refresh_rejected, refresh_unexpected_response, refresh_transient) map cleanly to UserTokenUnavailable reasons; refresh-transient does NOT delete the row; decrypt-failure raises GitHubUserTokenDecryptError without DELETE; no log line contains token plaintext or ciphertext.

## Proof Level

- This slice proves: Contract — every documented branch of the refresh helper has a unit test driving it via respx. No real runtime or UAT.

## Integration Closure

Upstream surfaces consumed: S01's GitHubUserOAuthToken SQLModel + encrypt_user_token / decrypt_user_token + GitHubUserTokenDecryptError; existing GitHub App OAuth client_id/client_secret in system_settings; the GitHub token-exchange endpoint at github.com/login/oauth/access_token. New wiring: get_user_access_token, UserTokenUnavailable, _read_github_app_oauth_credentials. No new route; S04 wires this helper into the create-repository route.

## Verification

- Three new structured log keys (github_user_token_refresh_attempted, github_user_token_refreshed, github_user_token_refresh_failed); each carries user_id and a reason. No log line contains token plaintext, ciphertext bytes, or full refresh-response body. The 4-char new_token_prefix=ghu_ line is the only token-derived value emitted and only on the success path.

## Tasks

- [ ] **T01: Extract `_read_github_app_oauth_credentials` into `app/core/github_app_oauth.py`** `est:45m`
  The refresh helper must read client_id + client_secret from system_settings — the exact pattern that already exists at backend/app/api/routes/github.py:307-342. Duplicating it would force the core helper to import from a route module (wrong layering) OR copy-paste the pattern (drift). Extract once. Create module with read_github_app_oauth_credentials(session) -> tuple[str, str] that fetches both rows, decrypts the secret via decrypt_setting, raises HTTPException(503, detail=github_app_not_configured) on missing or HTTPException(503, detail=github_app_credential_error) on decrypt failure. Refactor _resolve_installation_id_from_oauth_code to call through this helper.
  - Files: `backend/app/core/github_app_oauth.py`, `backend/app/api/routes/github.py`, `backend/tests/unit/test_github_app_oauth_credentials.py`
  - Verify: cd backend && uv run pytest tests/unit/test_github_app_oauth_credentials.py tests/api/routes/test_github_oauth_resolve.py -v

- [ ] **T02: `get_user_access_token` + `UserTokenUnavailable` + happy-path/skew logic** `est:1h`
  The core unit-of-work. Locking the contract before adding the network paths means refresh-path failures are easy to differentiate from logic bugs. Add class UserTokenUnavailable(Exception) with user_id and reason attributes. Add async get_user_access_token(session, user_id) -> str with: row missing -> UserTokenUnavailable(reason=row_missing); row exists AND now() < access_token_expires_at - 60s -> decrypt and return the access token (no GitHub call). Module-level constants _ACCESS_TOKEN_SKEW_SECONDS = 60 and _GITHUB_TOKEN_URL.
  - Files: `backend/app/core/github_user_tokens.py`, `backend/tests/unit/test_github_user_tokens_refresh.py`
  - Verify: cd backend && uv run pytest tests/unit/test_github_user_tokens_refresh.py -v -k "happy or row_missing"

- [ ] **T03: Refresh-on-expiry path + success update + failure-mode branches** `est:2h`
  This is the slice's substance. Each documented reason in must-have (5) needs its own respx-driven test. Extend get_user_access_token to handle expired-access branch: log github_user_token_refresh_attempted, read client_id/secret via T01's helper, decrypt the refresh token (re-raise GitHubUserTokenDecryptError if it fails), POST to _GITHUB_TOKEN_URL with timeout _ORCH_TIMEOUT, parse defensively. On success: update row, commit, log github_user_token_refreshed, return new plaintext. On four failure branches: DELETE the row, commit, log github_user_token_refresh_failed reason=..., raise UserTokenUnavailable. On network-class exception: no DELETE, retry once with no backoff, then raise UserTokenUnavailable(reason=refresh_transient).
  - Files: `backend/app/core/github_user_tokens.py`, `backend/tests/unit/test_github_user_tokens_refresh.py`
  - Verify: cd backend && uv run pytest tests/unit/test_github_user_tokens_refresh.py -v

## Files Likely Touched

- backend/app/core/github_app_oauth.py
- backend/app/api/routes/github.py
- backend/tests/unit/test_github_app_oauth_credentials.py
- backend/app/core/github_user_tokens.py
- backend/tests/unit/test_github_user_tokens_refresh.py
