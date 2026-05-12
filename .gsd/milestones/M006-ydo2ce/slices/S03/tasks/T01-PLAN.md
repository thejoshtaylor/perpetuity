---
estimated_steps: 1
estimated_files: 3
skills_used: []
---

# T01: Extract `_read_github_app_oauth_credentials` into `app/core/github_app_oauth.py`

The refresh helper must read client_id + client_secret from system_settings — the exact pattern that already exists at backend/app/api/routes/github.py:307-342. Duplicating it would force the core helper to import from a route module (wrong layering) OR copy-paste the pattern (drift). Extract once. Create module with read_github_app_oauth_credentials(session) -> tuple[str, str] that fetches both rows, decrypts the secret via decrypt_setting, raises HTTPException(503, detail=github_app_not_configured) on missing or HTTPException(503, detail=github_app_credential_error) on decrypt failure. Refactor _resolve_installation_id_from_oauth_code to call through this helper.

## Inputs

- `backend/app/api/routes/github.py:307-342 (existing client_id/client_secret read pattern)`
- `backend/app/core/encryption.py (decrypt_setting)`

## Expected Output

- `backend/app/core/github_app_oauth.py exporting read_github_app_oauth_credentials`
- `routes/github.py no longer contains the literal client_id_row = session.get(SystemSetting, GITHUB_APP_CLIENT_ID_KEY) line in the OAuth-exchange path`
- `Unit test covers happy path, missing client_id row, decrypt failure`

## Verification

cd backend && uv run pytest tests/unit/test_github_app_oauth_credentials.py tests/api/routes/test_github_oauth_resolve.py -v
