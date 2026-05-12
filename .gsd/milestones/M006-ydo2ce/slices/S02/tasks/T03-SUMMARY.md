---
id: T03
parent: S02
milestone: M006-ydo2ce
key_files:
  - backend/app/api/routes/github.py
  - backend/tests/api/routes/test_github_install_callback.py
key_decisions:
  - Used raw SQL INSERT...ON CONFLICT for github_user_oauth_tokens upsert because the SQLModel ORM model is out of sync with the real DB schema (nullable vs NOT NULL, missing expiry columns).
  - session.commit() moved after both upserts to guarantee atomic commit of installation row + token row.
  - GET handler stores resolved_oauth as ResolvedOAuthInstall | None; None is the default so POST and Setup URL GET flows skip token persistence without any code change to those paths.
duration: 
verification_result: passed
completed_at: 2026-05-12T21:33:22.354Z
blocker_discovered: false
---

# T03: Added _fetch_github_user_id helper and token persistence in _process_install_callback; 11 new tests all pass.

**Added _fetch_github_user_id helper and token persistence in _process_install_callback; 11 new tests all pass.**

## What Happened

Read github.py, the s17 migration, and the GitHubUserOAuthToken model thoroughly before touching anything. Discovered the ORM model has nullable/extra columns that differ from the actual DB schema (NOT NULL, with access_token_expires_at/refresh_token_expires_at); used raw SQL for the upsert to match the real schema.

Added encrypt_user_token import from app.core.github_user_tokens.

Added _fetch_github_user_id(access_token: str) -> int colocated with _resolve_installation_id_from_oauth_code. The helper calls GitHub GET /user with the Bearer token and raises HTTPException 502 detail='github_user_lookup_failed' on: transport errors (httpx.HTTPError), non-200 status, malformed JSON, and missing/wrong-type id field.

Updated _process_install_callback signature to (session, installation_id, state, oauth_tuple: ResolvedOAuthInstall | None = None). After the github_app_installations INSERT...ON CONFLICT, when oauth_tuple is not None the handler: extracts user_id from the validated state payload, calls _fetch_github_user_id, computes access_token_expires_at and refresh_token_expires_at via timedelta, encrypts both tokens via encrypt_user_token, then executes INSERT...ON CONFLICT (user_id) DO UPDATE on github_user_oauth_tokens. The single session.commit() at the end commits both rows atomically.

Updated the GET install-callback handler to store the full resolved_oauth dataclass (initialized to None) and pass it as oauth_tuple to _process_install_callback. The POST handler path already passes no oauth_tuple (defaults to None) so it naturally skips token persistence.

Created tests/api/routes/test_github_install_callback.py with 11 tests: 6 unit tests for _fetch_github_user_id (happy path, non-200, malformed JSON, missing id field, wrong-type id, transport error) and 5 integration tests (OAuth flow persists token row with correct ciphertext/expiry, POST skips token row, Setup URL GET skips token row, upsert overwrites on reinstall, user-lookup failure redirects with error and leaves no token row).

## Verification

cd backend && uv run pytest tests/api/routes/test_github_install_callback.py -v → 11/11 passed. Pre-existing failures in test_github_install.py (oauth tests missing encryption key fixture) confirmed pre-existed by stash/restore test.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && uv run pytest tests/api/routes/test_github_install_callback.py -v` | 0 | 11/11 passed | 530ms |

## Deviations

None from the task plan. Assumption documented: used raw SQL rather than ORM because GitHubUserOAuthToken model columns don't match the actual DB schema.

## Known Issues

Pre-existing: 6 oauth tests in test_github_install.py fail because that file lacks the _patch_encryption_key autouse fixture (unrelated to T03). Pre-existing: test_github_create_repository.py has an ImportError for create_test_user (unrelated to T03).

## Files Created/Modified

- `backend/app/api/routes/github.py`
- `backend/tests/api/routes/test_github_install_callback.py`
