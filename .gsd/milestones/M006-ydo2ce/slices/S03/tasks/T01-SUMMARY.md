---
id: T01
parent: S03
milestone: M006-ydo2ce
key_files:
  - /Users/josh/code/perpetuity/backend/app/core/github_app_oauth.py
  - /Users/josh/code/perpetuity/backend/tests/unit/test_github_app_oauth_credentials.py
  - /Users/josh/code/perpetuity/backend/app/api/routes/github.py
key_decisions:
  - Placed the new module in app/core (not app/api/routes) to eliminate the layering violation that would arise from the refresh helper importing from a route module
  - Kept the HTTPException raises inside the core helper (rather than raising a domain exception and translating at the call site) because there is only one consumer and the route layer already uses HTTPException directly
  - Removed GITHUB_APP_CLIENT_ID_KEY and GITHUB_APP_CLIENT_SECRET_KEY from the route imports entirely — they are now only referenced inside the core helper
duration: 
verification_result: passed
completed_at: 2026-05-12T21:46:16.365Z
blocker_discovered: false
---

# T01: Extracted read_github_app_oauth_credentials into backend/app/core/github_app_oauth.py and refactored the route to call through it

**Extracted read_github_app_oauth_credentials into backend/app/core/github_app_oauth.py and refactored the route to call through it**

## What Happened

Read the existing credential-reading block in backend/app/api/routes/github.py (lines 351-386) and backend/app/core/encryption.py. The pattern in the route read client_id from system_settings as a plain string and client_secret as a Fernet-encrypted BYTEA, raising HTTPException 503 on missing rows or decrypt failure. Created backend/app/core/github_app_oauth.py exporting read_github_app_oauth_credentials(session) -> tuple[str, str] that encapsulates the full pattern — client_id check, client_secret presence check, and decrypt_setting call — raising HTTPException(503, detail="github_app_not_configured") on missing/invalid rows and HTTPException(503, detail="github_app_credential_error") on decrypt failure. Refactored _resolve_installation_id_from_oauth_code in the route to call the new helper in a single line, removing the ~35-line inline block. Cleaned up the route imports: removed GITHUB_APP_CLIENT_ID_KEY, GITHUB_APP_CLIENT_SECRET_KEY, SystemSettingDecryptError, and decrypt_setting (none still needed in the route after the extraction), and added the import for read_github_app_oauth_credentials. Created backend/tests/unit/test_github_app_oauth_credentials.py with three tests covering the happy path, missing client_id row, and decrypt failure.

## Verification

Ran cd backend && uv run pytest tests/unit/test_github_app_oauth_credentials.py tests/api/routes/test_github_oauth_resolve.py -v — all 10 tests passed (3 new unit tests + 7 pre-existing oauth_resolve tests).

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd /Users/josh/code/perpetuity/backend && uv run pytest tests/unit/test_github_app_oauth_credentials.py tests/api/routes/test_github_oauth_resolve.py -v` | 0 | 10 passed, 3 warnings | 1200ms |

## Deviations

None. The implementation matches the task plan exactly.

## Known Issues

None.

## Files Created/Modified

- `/Users/josh/code/perpetuity/backend/app/core/github_app_oauth.py`
- `/Users/josh/code/perpetuity/backend/tests/unit/test_github_app_oauth_credentials.py`
- `/Users/josh/code/perpetuity/backend/app/api/routes/github.py`
