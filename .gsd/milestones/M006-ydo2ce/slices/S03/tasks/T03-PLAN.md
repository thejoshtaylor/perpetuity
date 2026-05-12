---
estimated_steps: 1
estimated_files: 2
skills_used: []
---

# T03: Refresh-on-expiry path + success update + failure-mode branches

This is the slice's substance. Each documented reason in must-have (5) needs its own respx-driven test. Extend get_user_access_token to handle expired-access branch: log github_user_token_refresh_attempted, read client_id/secret via T01's helper, decrypt the refresh token (re-raise GitHubUserTokenDecryptError if it fails), POST to _GITHUB_TOKEN_URL with timeout _ORCH_TIMEOUT, parse defensively. On success: update row, commit, log github_user_token_refreshed, return new plaintext. On four failure branches: DELETE the row, commit, log github_user_token_refresh_failed reason=..., raise UserTokenUnavailable. On network-class exception: no DELETE, retry once with no backoff, then raise UserTokenUnavailable(reason=refresh_transient).

## Inputs

- `T01's read_github_app_oauth_credentials`
- `T02's get_user_access_token skeleton + UserTokenUnavailable`

## Expected Output

- `Refresh-success path updates row atomically (encrypt new tokens, new expires_at, commit, return plaintext)`
- `Four failure branches (bad_refresh_token, refresh_rejected, refresh_unexpected_response, refresh_transient) with documented reason strings`
- `refresh_transient does NOT DELETE the row; bad_refresh_token / refresh_rejected / refresh_unexpected_response DO DELETE`
- `Decrypt-failure on stored refresh token raises GitHubUserTokenDecryptError without DELETE`
- `No log line contains \baccess_token\b or \brefresh_token\b literal substring`

## Verification

cd backend && uv run pytest tests/unit/test_github_user_tokens_refresh.py -v
