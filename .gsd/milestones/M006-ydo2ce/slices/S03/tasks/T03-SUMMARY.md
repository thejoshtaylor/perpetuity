---
id: T03
parent: S03
milestone: M006-ydo2ce
key_files:
  - (none)
key_decisions:
  - (none)
duration: 
verification_result: passed
completed_at: 2026-05-12T21:51:53.446Z
blocker_discovered: false
---

# T03: Implemented refresh-on-expiry path in get_user_access_token with 4 failure branches, retry logic, and 5 new passing unit tests

**Implemented refresh-on-expiry path in get_user_access_token with 4 failure branches, retry logic, and 5 new passing unit tests**

## What Happened

Extended get_user_access_token in backend/app/core/github_user_tokens.py to implement the full refresh path via a new _refresh_user_token helper. The helper: (1) logs github_user_token_refresh_attempted on entry; (2) decrypts the stored refresh token — re-raises GitHubUserTokenDecryptError (no DELETE) if ciphertext is corrupt; (3) reads client_id/secret via T01's read_github_app_oauth_credentials; (4) POSTs to _GITHUB_TOKEN_URL with grant_type=refresh_token; (5) retries once on httpx.HTTPError before raising UserTokenUnavailable(reason=refresh_transient) without deleting the row; (6) on bad_refresh_token or other error fields in the GitHub response body, DELETEs the row, commits, and raises with the appropriate reason; (7) on unparseable response body, DELETEs row and raises reason=refresh_unexpected_response; (8) on success, encrypts both new tokens, updates expiry timestamps, calls session.commit(), logs github_user_token_refreshed with new_token_prefix=ghu_ (4-char prefix only — no plaintext, no ciphertext), and returns the new plaintext. Added _ORCH_TIMEOUT constant (httpx.Timeout(10.0, connect=3.0)) and logging import. Five new tests added to test_github_user_tokens_refresh.py covering: refresh success (row updated, no delete), bad_refresh_token (delete + raise), unparseable body (delete + raise), transient network error (2 attempts, no delete, raise), and corrupt refresh ciphertext (GitHubUserTokenDecryptError, no delete). All 10 tests pass.

## Verification

cd backend && uv run pytest tests/unit/test_github_user_tokens_refresh.py -v — 10 passed in 0.13s. Verified: (1) three structured log keys present (github_user_token_refresh_attempted, github_user_token_refreshed, github_user_token_refresh_failed); (2) no log line contains literal 'access_token' or 'refresh_token' as a value — only new_token_prefix=ghu_ (4 chars) is emitted on success path; (3) refresh_transient does NOT delete the row; (4) bad_refresh_token/refresh_rejected/refresh_unexpected_response DO delete the row; (5) corrupt ciphertext raises GitHubUserTokenDecryptError without deleting.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && uv run pytest tests/unit/test_github_user_tokens_refresh.py -v` | 0 | PASS — 10/10 tests passed | 130ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

None.
