---
estimated_steps: 1
estimated_files: 2
skills_used: []
---

# T02: `get_user_access_token` + `UserTokenUnavailable` + happy-path/skew logic

The core unit-of-work. Locking the contract before adding the network paths means refresh-path failures are easy to differentiate from logic bugs. Add class UserTokenUnavailable(Exception) with user_id and reason attributes. Add async get_user_access_token(session, user_id) -> str with: row missing -> UserTokenUnavailable(reason=row_missing); row exists AND now() < access_token_expires_at - 60s -> decrypt and return the access token (no GitHub call). Module-level constants _ACCESS_TOKEN_SKEW_SECONDS = 60 and _GITHUB_TOKEN_URL.

## Inputs

- `S01's GitHubUserOAuthToken model + encrypt_user_token / decrypt_user_token / GitHubUserTokenDecryptError`

## Expected Output

- `UserTokenUnavailable exception class with user_id and reason attributes`
- `get_user_access_token with row-fetch, skew-check, happy-path branches`
- `Module-level constants _ACCESS_TOKEN_SKEW_SECONDS=60 and _GITHUB_TOKEN_URL`
- `Test asserts no GitHub HTTP call is made in either happy or row_missing branch`

## Verification

cd backend && uv run pytest tests/unit/test_github_user_tokens_refresh.py -v -k "happy or row_missing"
