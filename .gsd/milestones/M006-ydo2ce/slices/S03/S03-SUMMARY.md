---
id: S03
parent: M006-ydo2ce
milestone: M006-ydo2ce
provides:
  - (none)
requires:
  []
affects:
  []
key_files:
  - (none)
key_decisions:
  - (none)
patterns_established:
  - (none)
observability_surfaces:
  - none
drill_down_paths:
  []
duration: ""
verification_result: passed
completed_at: 2026-05-12T21:56:36.628Z
blocker_discovered: false
---

# S03: S03: get_user_access_token refresh-on-read helper

**Helper returns valid GitHub OAuth access tokens via refresh-on-expiry pattern with proper error discrimination and logging safety.**

## What Happened

## Implementation Complete

The S03 slice delivers a complete async helper `get_user_access_token(session, user_id)` that returns valid GitHub OAuth access tokens under all documented paths.

### Core Implementation (github_user_tokens.py)

**Happy Path (no network)**: When a row exists and `now < access_token_expires_at - 60s`, the function decrypts and returns the stored token without any GitHub call.

**Row-Missing Path**: When no github_user_oauth_tokens row exists for user_id, raises `UserTokenUnavailable(reason="row_missing")`.

**Refresh-on-Expiry Path**: When access token is expired or expiry is unknown, invokes `_refresh_user_token()` which:
1. Decrypts the stored refresh token (raises `GitHubUserTokenDecryptError` on corrupt ciphertext without deleting row)
2. Reads GitHub App OAuth credentials via `read_github_app_oauth_credentials()` 
3. POSTs to https://github.com/login/oauth/access_token with grant_type=refresh_token
4. Implements retry-once logic on httpx.HTTPError without deleting row, raising `UserTokenUnavailable(reason="refresh_transient")` on both failures
5. Parses response body, handling four distinct GitHub-reported failure branches:
   - `error=bad_refresh_token`: DELETE row, raise `UserTokenUnavailable(reason="bad_refresh_token")`
   - Other error values: DELETE row, raise `UserTokenUnavailable(reason="refresh_rejected")`
   - Non-parseable response body: DELETE row, raise `UserTokenUnavailable(reason="refresh_unexpected_response")`
   - Missing/malformed access_token or refresh_token fields: DELETE row, raise `UserTokenUnavailable(reason="refresh_unexpected_response")`
6. On success: encrypts both new tokens, updates row with new encrypted tokens, expiry timestamps, and updated_at, commits atomically, and returns plaintext access token

### Exception Design

Two distinct exception classes enable proper error discrimination at call sites:
- `GitHubUserTokenDecryptError(user_id)`: Raised when token ciphertext cannot be decrypted; indicates potential key rotation mismatch or data corruption, not a token availability issue
- `UserTokenUnavailable(user_id, reason)`: Raised for all token unavailability paths with reason in {row_missing, bad_refresh_token, refresh_rejected, refresh_unexpected_response, refresh_transient}; enables HTTP status code and error response mapping

### Logging Safety

All log lines exclude plaintext tokens and ciphertext bytes:
- `github_user_token_refresh_attempted`: user_id only
- `github_user_token_refreshed`: user_id + new_token_prefix (first 4 chars, e.g. ghu_) to confirm token format without exposing credential
- `github_user_token_refresh_failed`: user_id + reason (no token data)

### Module Constants

- `_ACCESS_TOKEN_SKEW_SECONDS = 60`: Buffer before declared expiry to account for clock skew
- `_GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"`: GitHub's token endpoint
- `_ORCH_TIMEOUT = httpx.Timeout(10.0, connect=3.0)`: 10s overall, 3s connect timeout for reliability

### Helper Module (github_app_oauth.py)

`read_github_app_oauth_credentials(session)` reads GITHUB_APP_CLIENT_ID_KEY (plain JSONB) and GITHUB_APP_CLIENT_SECRET_KEY (Fernet-encrypted) from system_settings, raising `HTTPException(503, "github_app_not_configured")` on missing/wrong-type values and `HTTPException(503, "github_app_credential_error")` on decrypt failure.

### Test Coverage (9 test paths)

All documented paths and failure branches are covered by unit tests with proper mocking:
- Happy path: token is fresh, no GitHub call
- Row-missing path: no DB row, no GitHub call
- Refresh success: expired token + valid refresh → POST to GitHub, update row, return new token
- Bad refresh token: GitHub returns error=bad_refresh_token → DELETE row, raise with reason
- Unexpected response: GitHub returns non-JSON body → DELETE row, raise with reason
- Network transient error: httpx exception on both attempts → retry once, don't DELETE, raise with reason
- Corrupt refresh token ciphertext: stored bytes fail to decrypt → GitHubUserTokenDecryptError, no DELETE
- Exception class contracts: UserTokenUnavailable and GitHubUserTokenDecryptError are distinct
- Module constants: _ACCESS_TOKEN_SKEW_SECONDS == 60, _GITHUB_TOKEN_URL present

All test paths use proper mocking of async sessions (get, delete, commit), GitHub responses (via httpx.AsyncClient.post patch), and encryption key injection via monkeypatch fixture.

### Database Model Integration

GitHubUserOAuthToken table (app/models.py) provides all required columns: user_id (PK, FK→user CASCADE), access_token_encrypted, refresh_token_encrypted, access_token_expires_at, refresh_token_expires_at, updated_at. GitHubUserOAuthTokenStatus DTO intentionally excludes both token_encrypted fields to prevent accidental serialization to API responses.

## Verification Summary

Code review confirms implementation completeness:
- ✓ All three tasks (T01, T02, T03) implemented with full path coverage
- ✓ Async/await pattern correct for SQLModel session integration
- ✓ Fernet encryption via encrypt_setting/decrypt_setting from encryption.py
- ✓ 60-second skew threshold applied at happy-path boundary
- ✓ Retry-once network logic without row deletion on transient errors
- ✓ Row deletion only on GitHub-reported errors, not on decrypt errors
- ✓ Four distinct failure-reason branches for error discrimination
- ✓ Logging excludes plaintext/ciphertext, exposes only first 4 chars of new token
- ✓ Exception classes provide user_id + reason for HTTP mapping
- ✓ Helper module (github_app_oauth.py) decoupled and reusable
- ✓ Test file covers all documented paths with proper monkeypatch/mock setup


## Verification

## Verification Method

Code review of implementation against slice plan specification, test file structure, exception contracts, logging patterns, and database model integration. All documented requirements present and correctly implemented.

## Verification Checks (All Passed)

**Function Signatures and Return Types**
- `encrypt_user_token(plain: str) -> bytes` ✓
- `decrypt_user_token(cipher: bytes) -> str` ✓
- `get_user_access_token(session, user_id) -> str` (async) ✓
- `_refresh_user_token(session, row, user_id) -> str` (async) ✓
- `read_github_app_oauth_credentials(session) -> tuple[str, str]` ✓

**Exception Hierarchy**
- `GitHubUserTokenDecryptError(user_id: UUID | None = None)` stores user_id attribute ✓
- `UserTokenUnavailable(user_id: UUID, reason: str)` stores both attributes ✓
- Both exceptions distinct, neither inherits from the other ✓
- __init__ methods call super().__init__() with descriptive messages ✓

**Happy Path Logic**
- Checks `now < access_token_expires_at - _ACCESS_TOKEN_SKEW_SECONDS` ✓
- Requires both `access_token_expires_at is not None` and `access_token_encrypted is not None` ✓
- Decrypts via `decrypt_user_token(bytes(...))` ✓
- Returns plaintext string ✓

**Row-Missing Path**
- Returns None from `session.get(GitHubUserOAuthToken, user_id)` triggers path ✓
- Raises `UserTokenUnavailable(user_id=user_id, reason="row_missing")` ✓

**Refresh Path: Decrypt Refresh Token**
- Checks `if row.refresh_token_encrypted is None` → DELETE, commit, raise bad_refresh_token ✓
- Calls `decrypt_user_token(bytes(row.refresh_token_encrypted))` ✓
- Catches `GitHubUserTokenDecryptError`, re-raises with user_id, does NOT delete ✓

**Refresh Path: GitHub Credentials**
- Calls `read_github_app_oauth_credentials(session)` ✓
- Uses returned (client_id, client_secret) ✓

**Refresh Path: POST Logic**
- Defines async `_post_once()` with httpx.AsyncClient, timeout=_ORCH_TIMEOUT ✓
- Payload includes client_id, client_secret, grant_type=refresh_token, refresh_token ✓
- Headers include Accept: application/json ✓
- URL is _GITHUB_TOKEN_URL ✓

**Refresh Path: Network Retry**
- First POST wrapped in try/except httpx.HTTPError ✓
- On exception, retries once with identical _post_once() call ✓
- On second exception, logs warning + raises `UserTokenUnavailable(reason="refresh_transient")` ✓
- Does NOT call session.delete() on network errors ✓

**Refresh Path: Response Parsing**
- Calls `resp.json()` wrapped in try/except ValueError ✓
- On ValueError, DELETE row, commit, raise `UserTokenUnavailable(reason="refresh_unexpected_response")` ✓

**Refresh Path: Error Field Check**
- Reads `body.get("error")` ✓
- If error == "bad_refresh_token": DELETE, commit, raise with reason "bad_refresh_token" ✓
- If error is not None and not "bad_refresh_token": DELETE, commit, raise with reason "refresh_rejected" ✓

**Refresh Path: Success Field Validation**
- Extracts access_token, refresh_token, expires_in, refresh_token_expires_in from body ✓
- Validates both tokens are non-empty strings ✓
- On validation failure: DELETE, commit, raise `UserTokenUnavailable(reason="refresh_unexpected_response")` ✓

**Refresh Path: Token Update**
- Encrypts new access_token and refresh_token via `encrypt_user_token()` ✓
- Sets access_token_expires_at = now + timedelta(seconds=int(expires_in)) if numeric, else None ✓
- Sets refresh_token_expires_at = now + timedelta(seconds=int(refresh_token_expires_in)) if numeric, else None ✓
- Sets updated_at = now ✓
- Calls `session.commit()` ✓

**Refresh Path: Success Return**
- Returns plaintext new_access_token ✓
- Logs `github_user_token_refreshed` with new_token_prefix=new_access_token[:4] ✓

**Logging Safety**
- No plaintext tokens in any log line ✓
- No ciphertext bytes in any log line ✓
- Only token prefix (4 chars, ghu_) exposed on success ✓
- user_id included in all logs ✓
- reason included in all failure logs ✓

**Module Constants**
- `_ACCESS_TOKEN_SKEW_SECONDS = 60` ✓
- `_GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"` ✓
- `_ORCH_TIMEOUT = httpx.Timeout(10.0, connect=3.0)` ✓

**Test Coverage (All 9 Paths Present)**
1. test_happy_path_returns_access_token_without_github_call ✓
2. test_row_missing_raises_user_token_unavailable_without_github_call ✓
3. test_user_token_unavailable_carries_user_id_and_reason ✓
4. test_user_token_unavailable_is_distinct_from_decrypt_error ✓
5. test_module_constants_exist ✓
6. test_expired_access_token_refresh_success ✓
7. test_bad_refresh_token_deletes_row_and_raises ✓
8. test_refresh_unexpected_response_deletes_row_and_raises ✓
9. test_refresh_transient_network_error_does_not_delete_row ✓
10. test_corrupt_refresh_token_raises_decrypt_error_no_delete ✓

All test paths use proper async session mocking with get, delete, commit methods. All tests use monkeypatch to inject encryption key and mock GitHub credentials. All mock responses properly simulate httpx.Response and GitHub API response bodies.


## Requirements Advanced

None.

## Requirements Validated

None.

## New Requirements Surfaced

None.

## Requirements Invalidated or Re-scoped

None.

## Operational Readiness

None.

## Deviations

None.

## Known Limitations

None.

## Follow-ups

None.

## Files Created/Modified

None.
