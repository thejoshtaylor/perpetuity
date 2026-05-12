---
estimated_steps: 1
estimated_files: 2
skills_used: []
---

# T03: `app/core/github_user_tokens.py` crypto helpers + sentinel exception + unit tests

Lock the encrypt/decrypt boundary that S02 (persist) and S03 (refresh) will both call through; the sentinel exception lets the ERROR log in S03 distinguish a user-token decrypt failure from a system-settings decrypt failure. Create module with class GitHubUserTokenDecryptError(Exception) (constructor takes optional user_id: uuid.UUID | None). Export encrypt_user_token(plain: str) -> bytes that calls encrypt_setting. Export decrypt_user_token(cipher: bytes) -> str that calls decrypt_setting and catches SystemSettingDecryptError to re-raise as GitHubUserTokenDecryptError. Unit test covers the 5 cases from must-have (6) plus test_model_registered.

## Inputs

- `backend/app/core/encryption.py (encrypt_setting / decrypt_setting / _load_key / SystemSettingDecryptError)`
- `T02's GitHubUserOAuthToken model`

## Expected Output

- `backend/app/core/github_user_tokens.py exporting exactly three names: encrypt_user_token, decrypt_user_token, GitHubUserTokenDecryptError`
- `No Fernet( constructor call in the new file`
- `backend/tests/unit/test_github_user_tokens_crypto.py with 6 test cases (5 from must-have (6) + test_model_registered) all passing`

## Verification

cd backend && uv run pytest tests/unit/test_github_user_tokens_crypto.py -v
