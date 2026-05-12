# S03: S03: get_user_access_token refresh-on-read helper — UAT

**Milestone:** M006-ydo2ce
**Written:** 2026-05-12T21:56:36.629Z

# User Acceptance Tests: S03 get_user_access_token

## Preconditions

1. **Database Setup**: github_user_oauth_tokens table exists with columns: user_id (UUID PK), access_token_encrypted (BYTEA), refresh_token_encrypted (BYTEA), access_token_expires_at (TIMESTAMPTZ), refresh_token_expires_at (TIMESTAMPTZ), updated_at (TIMESTAMPTZ)

2. **Encryption**: SYSTEM_SETTINGS_ENCRYPTION_KEY environment variable set to valid Fernet key (44-char base64)

3. **GitHub App Config**: system_settings table contains:
   - Row with key="github_app_client_id", value="<plain_client_id_string>"
   - Row with key="github_app_client_secret", value_encrypted="<fernet_encrypted_secret>"

4. **Async Context**: Function called within async event loop (pytest-asyncio or asyncio.run)

5. **Test User**: UUID user_id exists in database users table

## UAT Scenario 1: Happy Path — Token Is Fresh (No Network Call)

**Given**:
- github_user_oauth_tokens row exists for user_id
- access_token_encrypted contains encrypted token "ghu_FreshAccessToken1234567890"
- access_token_expires_at = now() + 10 minutes
- No network connectivity (simulated by patching httpx.AsyncClient.post to raise AssertionError)

**When**:
- `await get_user_access_token(session, user_id)` is called

**Then**:
- ✓ Function returns plaintext "ghu_FreshAccessToken1234567890"
- ✓ No HTTP call is made to GitHub
- ✓ No session.delete() call
- ✓ No session.commit() call
- ✓ Log line "github_user_token_refresh_attempted" is NOT present
- ✓ Execution completes within <100ms (decryption only, no I/O)

---

## UAT Scenario 2: Row-Missing Path — No Database Row

**Given**:
- No github_user_oauth_tokens row for user_id
- session.get(GitHubUserOAuthToken, user_id) returns None

**When**:
- `await get_user_access_token(session, user_id)` is called

**Then**:
- ✓ Raises UserTokenUnavailable exception
- ✓ exc.user_id == user_id
- ✓ exc.reason == "row_missing"
- ✓ str(exc) contains both user_id and "row_missing"
- ✓ No HTTP call is made to GitHub
- ✓ No session.delete() call
- ✓ No session.commit() call

---

## UAT Scenario 3: Refresh Success — Expired Token + Valid GitHub Response

**Given**:
- github_user_oauth_tokens row exists for user_id
- access_token_encrypted contains encrypted token "ghu_OldExpiredToken"
- access_token_expires_at = now() - 1 hour (expired)
- refresh_token_encrypted contains encrypted token "ghr_ValidRefreshToken"
- refresh_token_expires_at = now() + 5 days (valid)
- GitHub OAuth credentials configured (client_id, client_secret)
- GitHub API mocked to return 200 with body:
  ```json
  {
    "access_token": "ghu_NewAccessToken1234567890",
    "refresh_token": "ghr_NewRefreshToken1234567890",
    "token_type": "bearer",
    "expires_in": 28800,
    "refresh_token_expires_in": 15897600
  }
  ```

**When**:
- `await get_user_access_token(session, user_id)` is called

**Then**:
- ✓ Exactly one HTTP POST is made to https://github.com/login/oauth/access_token
- ✓ POST payload includes grant_type="refresh_token" and refresh_token="ghr_ValidRefreshToken"
- ✓ POST headers include Accept: application/json
- ✓ POST uses httpx.Timeout(10.0, connect=3.0)
- ✓ Function returns plaintext "ghu_NewAccessToken1234567890"
- ✓ row.access_token_encrypted is updated to encrypted new token
- ✓ row.refresh_token_encrypted is updated to encrypted new token
- ✓ row.access_token_expires_at = now() + 28800 seconds
- ✓ row.refresh_token_expires_at = now() + 15897600 seconds
- ✓ row.updated_at is updated to now()
- ✓ session.commit() is called exactly once
- ✓ session.delete() is NOT called
- ✓ Log line "github_user_token_refreshed" contains user_id and new_token_prefix="ghu_"
- ✓ Log line does NOT contain plaintext tokens or ciphertext bytes

---

## UAT Scenario 4: Bad Refresh Token — GitHub Rejects Token

**Given**:
- github_user_oauth_tokens row exists for user_id
- access_token_encrypted contains encrypted token "ghu_OldExpiredToken"
- access_token_expires_at = now() - 1 hour (expired)
- refresh_token_encrypted contains encrypted token "ghr_BadRefreshToken"
- GitHub API mocked to return 200 with body:
  ```json
  {
    "error": "bad_refresh_token",
    "error_description": "The refresh_token passed is incorrect or expired."
  }
  ```

**When**:
- `await get_user_access_token(session, user_id)` is called

**Then**:
- ✓ Raises UserTokenUnavailable exception
- ✓ exc.user_id == user_id
- ✓ exc.reason == "bad_refresh_token"
- ✓ session.delete(row) is called exactly once
- ✓ session.commit() is called exactly once after delete
- ✓ Log line "github_user_token_refresh_failed" contains reason="bad_refresh_token"

---

## UAT Scenario 5: Unexpected Response — Non-JSON Body

**Given**:
- github_user_oauth_tokens row exists with expired access token
- GitHub API mocked to return 200 with non-JSON body (e.g., HTML error page or plain text)
- response.json() raises ValueError

**When**:
- `await get_user_access_token(session, user_id)` is called

**Then**:
- ✓ Raises UserTokenUnavailable exception
- ✓ exc.reason == "refresh_unexpected_response"
- ✓ session.delete(row) is called exactly once
- ✓ session.commit() is called exactly once

---

## UAT Scenario 6: Missing Required Fields in Success Response

**Given**:
- github_user_oauth_tokens row exists with expired access token
- GitHub API mocked to return 200 with incomplete body (missing access_token or refresh_token):
  ```json
  {
    "token_type": "bearer",
    "expires_in": 28800
  }
  ```

**When**:
- `await get_user_access_token(session, user_id)` is called

**Then**:
- ✓ Raises UserTokenUnavailable exception
- ✓ exc.reason == "refresh_unexpected_response"
- ✓ session.delete(row) is called exactly once

---

## UAT Scenario 7: Generic GitHub Error Response

**Given**:
- github_user_oauth_tokens row exists with expired access token
- GitHub API mocked to return 200 with body containing unknown error:
  ```json
  {
    "error": "unsupported_grant_type",
    "error_description": "..."
  }
  ```

**When**:
- `await get_user_access_token(session, user_id)` is called

**Then**:
- ✓ Raises UserTokenUnavailable exception
- ✓ exc.reason == "refresh_rejected"
- ✓ session.delete(row) is called exactly once

---

## UAT Scenario 8: Network Error — First Attempt Fails, Retries Once

**Given**:
- github_user_oauth_tokens row exists with expired access token
- httpx.AsyncClient.post mocked to raise httpx.ConnectError (network failure) on first call
- httpx.AsyncClient.post mocked to raise httpx.ConnectError again on second call (retry)

**When**:
- `await get_user_access_token(session, user_id)` is called

**Then**:
- ✓ Raises UserTokenUnavailable exception
- ✓ exc.reason == "refresh_transient"
- ✓ httpx.AsyncClient.post is called exactly 2 times (initial + 1 retry)
- ✓ session.delete() is NOT called (row is preserved for manual remediation)
- ✓ session.commit() is NOT called
- ✓ Log line "github_user_token_refresh_failed" contains reason="refresh_transient"

---

## UAT Scenario 9: Corrupt Refresh Token Ciphertext

**Given**:
- github_user_oauth_tokens row exists with expired access token
- refresh_token_encrypted = b"corrupt_not_fernet_token_bytes" (invalid Fernet ciphertext)
- Attempting to decrypt raises cryptography.fernet.InvalidToken

**When**:
- `await get_user_access_token(session, user_id)` is called

**Then**:
- ✓ Raises GitHubUserTokenDecryptError exception (distinct from UserTokenUnavailable)
- ✓ exc.user_id == user_id
- ✓ session.delete() is NOT called (row is preserved; may be key rotation issue)
- ✓ session.commit() is NOT called

---

## UAT Scenario 10: Refresh Token Expiry Dates Handled Correctly

**Given**:
- Refresh response includes expires_in and refresh_token_expires_in as numeric seconds (int or float)

**When**:
- `await get_user_access_token(session, user_id)` is called with expired access token

**Then**:
- ✓ row.access_token_expires_at = now() + timedelta(seconds=int(expires_in))
- ✓ row.refresh_token_expires_at = now() + timedelta(seconds=int(refresh_token_expires_in))
- ✓ If expires_in is missing or not numeric, set access_token_expires_at = None
- ✓ If refresh_token_expires_in is missing or not numeric, set refresh_token_expires_at = None
- ✓ Row is still committed and new access token is returned

---

## UAT Scenario 11: 60-Second Skew Applied at Happy Path Boundary

**Given**:
- github_user_oauth_tokens row exists
- access_token_expires_at = now() + 45 seconds (within 60-second skew window)
- access_token_encrypted contains valid token

**When**:
- `await get_user_access_token(session, user_id)` is called

**Then**:
- ✓ Happy path is NOT taken (45s < 60s skew)
- ✓ Refresh path is executed (because now() >= access_token_expires_at - 60s)
- ✓ POST to GitHub is attempted

---

## UAT Scenario 12: Boundary Condition — Token Expires Exactly at Skew Window

**Given**:
- github_user_oauth_tokens row exists
- access_token_expires_at = now() + 60 seconds (exactly at skew boundary)

**When**:
- `await get_user_access_token(session, user_id)` is called

**Then**:
- ✓ Refresh path is executed (now() >= access_token_expires_at - 60s is True)
- ✓ POST to GitHub is attempted

---

## UAT Scenario 13: Null Expiry Timestamp Triggers Refresh

**Given**:
- github_user_oauth_tokens row exists
- access_token_expires_at = None
- access_token_encrypted contains valid token

**When**:
- `await get_user_access_token(session, user_id)` is called

**Then**:
- ✓ Happy path is NOT taken (requires access_token_expires_at is not None)
- ✓ Refresh path is executed
- ✓ POST to GitHub is attempted

---

## UAT Scenario 14: Null Access Token Encrypted Triggers Refresh

**Given**:
- github_user_oauth_tokens row exists
- access_token_encrypted = None
- access_token_expires_at = now() + 10 minutes (far in future)

**When**:
- `await get_user_access_token(session, user_id)` is called

**Then**:
- ✓ Happy path is NOT taken (requires access_token_encrypted is not None)
- ✓ Refresh path is executed

---

## UAT Scenario 15: Logging Does Not Expose Secrets

**Precondition**: Capture all log lines during test execution

**Given**:
- All test scenarios above execute with logging enabled at INFO and WARNING levels

**When**:
- Tests complete

**Then**:
- ✓ No plaintext access tokens appear in any log line
- ✓ No plaintext refresh tokens appear in any log line
- ✓ No ciphertext bytes appear in any log line
- ✓ Only first 4 characters of new token (e.g., "ghu_") appear on success
- ✓ user_id appears in all logs (INFO and WARNING)
- ✓ reason appears in all failure logs

---

## UAT Scenario 16: Exception Classes Are Distinct and Properly Structured

**When**:
- Code imports GitHubUserTokenDecryptError and UserTokenUnavailable

**Then**:
- ✓ GitHubUserTokenDecryptError is not a subclass of UserTokenUnavailable
- ✓ UserTokenUnavailable is not a subclass of GitHubUserTokenDecryptError
- ✓ Both exceptions inherit from Exception
- ✓ GitHubUserTokenDecryptError.__init__ accepts optional user_id: UUID | None
- ✓ UserTokenUnavailable.__init__ requires user_id: UUID and reason: str
- ✓ Both exceptions expose .user_id attribute on instance
- ✓ UserTokenUnavailable exposes .reason attribute on instance
- ✓ str(exception) includes both user_id and reason (for UserTokenUnavailable)

---

## Not Proven By This UAT

1. **End-to-End Integration with Real GitHub API**: Tests use mocked httpx.AsyncClient.post; actual GitHub token refresh flow not exercised (reserved for integration tests with real or sandbox GitHub credentials)

2. **Database Transaction Isolation**: Tests use AsyncMock session; SQLAlchemy transaction semantics (isolation levels, locks) not validated

3. **Concurrency Under Load**: Multiple simultaneous refresh attempts for same user_id not tested; session locking behavior unknown

4. **Key Rotation Scenarios**: SYSTEM_SETTINGS_ENCRYPTION_KEY rotation mid-flight not covered; assumes key is stable across decrypt-refresh-commit sequence

5. **Clock Skew at Service Boundary**: Tests assume system clock is synchronized; NTP drift or intentional clock manipulation not tested

6. **Upstream Stale Token Bug**: If GitHub issues tokens that expire before declared expires_in seconds, this helper would cache and fail on next use; assumed not to occur

7. **Partial Row Corruption**: Scenarios where only one of (access_token_encrypted, refresh_token_encrypted, timestamps) is NULL/corrupt not explicitly tested (assumed all-or-nothing row state)

