---
id: T05
parent: S01
milestone: M001-6cqls8
key_files:
  - backend/tests/conftest.py
  - backend/tests/utils/user.py
  - backend/tests/utils/utils.py
  - backend/tests/api/routes/test_auth.py
  - backend/tests/api/routes/test_ws_auth.py
  - backend/tests/migrations/__init__.py
  - backend/tests/migrations/test_s01_migration.py
  - backend/tests/api/routes/test_login.py
  - backend/tests/api/routes/test_users.py
  - backend/tests/api/routes/test_items.py
  - backend/tests/crud/test_user.py
key_decisions:
  - Added a module-level autouse `_release_autouse_db_session` fixture to commit, expire, close, and dispose the engine pool before each migration test — the session-scoped autouse `db` fixture's open SQLAlchemy transaction would otherwise hold an AccessShareLock that blocks alembic's DROP COLUMN indefinitely (captured as MEM014).
  - Helpers (`login_cookie_headers`, `get_superuser_cookies`) call `client.cookies.clear()` BEFORE logging in. Without this, a stale session cookie left on the TestClient jar from a prior test collides with the fresh login response and httpx raises CookieConflict — caught only when sequencing the WS happy-path test after the cookie-clearing logout test.
  - Hand-forged expired and ghost-user JWTs (rather than monkeypatching `ACCESS_TOKEN_EXPIRE_MINUTES`) so the expiry/non-existence assertions don't perturb the global settings object that other tests in the same module read.
  - Removed `test_register_user`/`test_register_user_already_exists_error` from test_users.py — they targeted `/users/signup`, which was deleted in T03; the equivalent coverage now lives in test_auth.py against `/auth/signup`.
duration: 
verification_result: passed
completed_at: 2026-04-24T22:45:44.267Z
blocker_discovered: false
---

# T05: Add cookie-auth integration tests for /auth, WS, and the S01 migration; migrate the existing suite off Bearer tokens to cookie fixtures

**Add cookie-auth integration tests for /auth, WS, and the S01 migration; migrate the existing suite off Bearer tokens to cookie fixtures**

## What Happened

Wrote three new test files exercising the S01 stack against the real Postgres test DB with no mocks:

- `tests/api/routes/test_auth.py` (13 tests): signup sets cookie + returns role, signup duplicate → 400, login sets cookie, login wrong password → 400, login unknown email → 400 (uniform message), /users/me without cookie → 401, /users/me with cookie returns role + no is_superuser, /users/me with tampered cookie → 401, /users/me with hand-forged expired JWT → 401, /users/me with valid signature for deleted user → 401 (no enumeration), logout clears cookie + subsequent /users/me → 401, logout idempotent without cookie → 200, and a redaction structural test asserting the raw email never appears in failed-login log lines.
- `tests/api/routes/test_ws_auth.py` (6 tests): all three documented WS reject reasons (`missing_cookie`, `invalid_token` for both garbage and expired JWT, `user_not_found`, `user_inactive`) plus the happy-path pong-with-role echo. Each negative case asserts both close code 1008 AND the reason string from the inspection contract.
- `tests/migrations/test_s01_migration.py` (2 tests): runs alembic downgrade → seed pre-S01 rows via raw SQL → upgrade head → assert role mapping + enum types (lowercase per MEM012) + is_superuser dropped + team/team_member tables created. Second test seeds post-S01 rows then downgrades and asserts is_superuser is restored with correct booleans, role column gone, enum types dropped, team tables dropped.

Replaced the old `user_authentication_headers`/`superuser_token_headers` helpers with `login_cookie_headers` and `superuser_cookies`/`normal_user_cookies` fixtures that return `httpx.Cookies` and call `client.cookies.clear()` before login (necessary to avoid `httpx.CookieConflict`). Rewrote `tests/api/routes/test_login.py` to keep only password-recovery + reset-password coverage plus the bcrypt → argon2 hash-upgrade tests in cookie form (the `/login/access-token` endpoint is gone). Migrated `test_users.py` and `test_items.py` to the new cookie fixtures and replaced `is_superuser` boolean assertions with `role` checks. Updated `tests/crud/test_user.py` (which is_superuser still referenced) to use UserRole instead.

Hit one non-trivial blocker: the migration tests hung indefinitely on alembic's DROP COLUMN. Root cause: the session-scoped autouse `db` fixture in `tests/conftest.py` keeps a SQLAlchemy Session open for the whole pytest session; SQLAlchemy implicitly holds an AccessShareLock on the `user` table, and alembic's exclusive lock waits forever. Added a module-level autouse `_release_autouse_db_session` fixture that commits + expires + closes the session and calls `engine.dispose()` before alembic runs, plus a second dispose after restoring head. Captured this gotcha as MEM014.

Final result: full backend suite is 76 passed, 0 failed in 4.2s.

## Verification

Ran the slice-level command verbatim from the task plan: `cd backend && uv run pytest tests/api/routes/test_auth.py tests/api/routes/test_ws_auth.py tests/migrations/test_s01_migration.py -v && uv run pytest tests/ -v`. The targeted run reported 21 passed; the full suite reported 76 passed (0 failed, 112 warnings). Spot-checked behavior: WS rejects carry close code 1008 with the documented reason string; redaction test confirms the raw email never appears in `app.api.routes.auth` log records; migration up + down round-trip correctly maps `is_superuser ↔ role` for the seeded fixtures; downgrade test verifies enum types are dropped from `pg_type`; fixture refactor confirmed by the rest of test_users.py and test_items.py passing without re-introducing the old Bearer headers.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `uv run pytest tests/api/routes/test_auth.py tests/api/routes/test_ws_auth.py tests/migrations/test_s01_migration.py -v -p no:cacheprovider` | 0 | ✅ pass | 770ms |
| 2 | `uv run pytest tests/ -v -p no:cacheprovider` | 0 | ✅ pass | 4200ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `backend/tests/conftest.py`
- `backend/tests/utils/user.py`
- `backend/tests/utils/utils.py`
- `backend/tests/api/routes/test_auth.py`
- `backend/tests/api/routes/test_ws_auth.py`
- `backend/tests/migrations/__init__.py`
- `backend/tests/migrations/test_s01_migration.py`
- `backend/tests/api/routes/test_login.py`
- `backend/tests/api/routes/test_users.py`
- `backend/tests/api/routes/test_items.py`
- `backend/tests/crud/test_user.py`
