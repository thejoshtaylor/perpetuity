---
id: T03
parent: S01
milestone: M006-ydo2ce
key_files:
  - (none)
key_decisions:
  - (none)
duration: 
verification_result: passed
completed_at: 2026-05-12T21:14:49.321Z
blocker_discovered: false
---

# T03: Created app/core/github_user_tokens.py with encrypt_user_token/decrypt_user_token/GitHubUserTokenDecryptError and 6-test unit suite — all 6 passing

**Created app/core/github_user_tokens.py with encrypt_user_token/decrypt_user_token/GitHubUserTokenDecryptError and 6-test unit suite — all 6 passing**

## What Happened

Created backend/app/core/github_user_tokens.py exporting exactly three names: encrypt_user_token, decrypt_user_token, GitHubUserTokenDecryptError. The module wraps encrypt_setting/decrypt_setting from app.core.encryption with no Fernet constructor call. SystemSettingDecryptError is caught in decrypt_user_token and re-raised as GitHubUserTokenDecryptError, keeping the two exception classes fully distinct so future ERROR logs can pinpoint which table's ciphertext failed. GitHubUserTokenDecryptError accepts optional user_id: uuid.UUID | None and includes it in the message when present. Created backend/tests/unit/test_github_user_tokens_crypto.py with 6 test cases: round-trip correctness, encrypted bytes don't contain plaintext, bad ciphertext raises GitHubUserTokenDecryptError, error class is distinct from SystemSettingDecryptError, optional user_id works, and test_model_registered confirms GitHubUserOAuthToken is in SQLModel metadata.

## Verification

cd backend && uv run pytest tests/unit/test_github_user_tokens_crypto.py -v → 6 passed in 0.10s

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && uv run pytest tests/unit/test_github_user_tokens_crypto.py -v` | 0 | pass | 100ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

None.
