---
id: T01
parent: S01
milestone: M006-ydo2ce
key_files:
  - backend/app/alembic/versions/s17_github_user_oauth_tokens.py
  - backend/tests/migrations/test_s17_github_user_oauth_tokens_migration.py
key_decisions:
  - Used role='user' in _make_user helper instead of is_superuser=FALSE — the s01 migration replaced is_superuser with the UserRole enum column
  - Started a standalone postgres:16 docker container on port 55432 (matching .env POSTGRES_PORT) since the compose stack DB could not bind port 5432 (already taken by unrelated container)
  - access_token_expires_at and refresh_token_expires_at are NOT NULL with no server_default — the application layer always supplies these from the token-exchange response
duration: 
verification_result: passed
completed_at: 2026-05-12T21:13:08.545Z
blocker_discovered: false
---

# T01: Created s17_github_user_oauth_tokens Alembic migration (10-column table, PK on user_id, FK CASCADE to user) and 8-test migration test suite — all 8 tests pass.

**Created s17_github_user_oauth_tokens Alembic migration (10-column table, PK on user_id, FK CASCADE to user) and 8-test migration test suite — all 8 tests pass.**

## What Happened

Read s09_team_secrets.py as the migration template, confirmed down_revision from s16_workflow_run_rejected_status.py, and read test_s09_team_secrets_migration.py for the fixture pattern (MEM016 autouse _release_autouse_db_session + _restore_head_after). Created s17_github_user_oauth_tokens.py with 10 columns: user_id (UUID, PK, FK→user.id CASCADE), installation_id (BigInteger), github_user_id (BigInteger), access_token_encrypted (LargeBinary), refresh_token_encrypted (LargeBinary), access_token_expires_at (DateTime timezone=True), refresh_token_expires_at (DateTime timezone=True), scope (String 255), created_at and updated_at (both DateTime timezone=True, server_default NOW()). Created test_s17_github_user_oauth_tokens_migration.py with autouse fixtures copied verbatim from the s09 template and 8 test functions covering: column shape/types/nullability, PK on user_id, FK CASCADE delete action, duplicate user_id PK violation, cascade-on-user-delete, server defaults for created_at/updated_at, downgrade drops table, and downgrade+re-upgrade roundtrip schema identity. First test run failed with 3 test errors because _make_user inserted is_superuser=FALSE but the s01 migration replaced that column with a role enum. Fixed by changing the INSERT to use role='user' instead. After fix all 8 tests pass in 0.50s. The test DB was started via docker run postgres:16 on port 55432 (matching POSTGRES_PORT in .env) since the compose stack was not running. alembic upgrade head applied all 17 migrations including s17 cleanly.

## Verification

cd backend && uv run pytest tests/migrations/test_s17_github_user_oauth_tokens_migration.py -v — 8 passed, 0 failed, 0 errors in 0.50s.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd /Users/josh/code/perpetuity/backend && uv run alembic upgrade head` | 0 | All 17 migrations applied including s17_github_user_oauth_tokens | 4200ms |
| 2 | `cd /Users/josh/code/perpetuity/backend && uv run pytest tests/migrations/test_s17_github_user_oauth_tokens_migration.py -v` | 0 | 8 passed, 0 failed | 500ms |

## Deviations

_make_user helper uses role='user' column (not is_superuser=FALSE) to match the actual schema introduced by s01 migration. This deviation from the s09 test pattern (which uses a team table) is expected since the s17 test targets the user table directly.

## Known Issues

None.

## Files Created/Modified

- `backend/app/alembic/versions/s17_github_user_oauth_tokens.py`
- `backend/tests/migrations/test_s17_github_user_oauth_tokens_migration.py`
