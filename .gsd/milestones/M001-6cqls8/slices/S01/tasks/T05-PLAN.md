---
estimated_steps: 12
estimated_files: 9
skills_used: []
---

# T05: Write integration tests for auth flow, WS cookie auth, and the S01 migration

Create three test files that run against the real Postgres test DB (no mocks). Update `backend/tests/conftest.py` + `backend/tests/utils/user.py` helpers to issue cookies instead of Bearer tokens (add `login_cookie_headers(client, email, password) -> httpx.Cookies` helper; deprecate / replace `user_authentication_headers`; update `superuser_token_headers` / `normal_user_token_headers` fixtures to return a `TestClient`-with-cookies or an `httpx.Cookies` object — whichever is simpler for pytest reuse). Then:

1. `backend/tests/api/routes/test_auth.py` — signup sets cookie; signup duplicate email → 400; login sets cookie; login wrong password → 400; /users/me with cookie returns role; /users/me without cookie → 401; /users/me with tampered cookie → 401; /users/me with expired cookie → 401 (monkeypatch `ACCESS_TOKEN_EXPIRE_MINUTES=-1` or use `freezegun`); logout clears cookie; logout idempotent without cookie → 200.
2. `backend/tests/api/routes/test_ws_auth.py` — WS connect with valid cookie → pong with role; WS connect without cookie → close 1008 missing_cookie; WS connect with garbage cookie → close 1008 invalid_token. Use `TestClient(app).websocket_connect(...)`.
3. `backend/tests/migrations/test_s01_migration.py` — uses a scoped alembic runner: downgrade to the pre-S01 revision, seed one `is_superuser=True` and one `is_superuser=False` user via raw SQL, upgrade head, assert both users exist with correct `role`, assert `TeamRole` and `UserRole` enum types exist (`SELECT typname FROM pg_type`), assert `is_superuser` column no longer exists, assert `team` and `team_member` tables exist. Then downgrade -1 and assert reversal.

Remove or update `backend/tests/api/routes/test_login.py` — keep only the password-recovery tests; move the token tests into `test_auth.py` in cookie form. Wipe the `test_users.py` / `test_items.py` reliance on bearer tokens by routing through the new cookie fixtures.

Failure Modes:
| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| Postgres test DB | pytest fails with clear message; no cleanup state leaked between tests (use `db` fixture's existing cleanup) | N/A | N/A |
| Alembic runner | Tests skip with reason if alembic env can't bootstrap; never silent-pass | N/A | N/A |

Load Profile: test suite only — sequential, one DB.

Negative Tests: every auth endpoint has at least one negative case; migration has both up and down paths; WS has all three reject reasons exercised.

## Inputs

- ``backend/tests/conftest.py``
- ``backend/tests/utils/user.py``
- ``backend/tests/api/routes/test_login.py``
- ``backend/tests/api/routes/test_users.py``
- ``backend/tests/api/routes/test_items.py``
- ``backend/app/api/routes/auth.py``
- ``backend/app/api/routes/ws.py``
- ``backend/app/alembic/versions/s01_auth_and_roles.py``
- ``backend/app/alembic/env.py``

## Expected Output

- ``backend/tests/api/routes/test_auth.py``
- ``backend/tests/api/routes/test_ws_auth.py``
- ``backend/tests/migrations/__init__.py``
- ``backend/tests/migrations/test_s01_migration.py``
- ``backend/tests/conftest.py``
- ``backend/tests/utils/user.py``
- ``backend/tests/api/routes/test_login.py``
- ``backend/tests/api/routes/test_users.py``
- ``backend/tests/api/routes/test_items.py``

## Verification

cd backend && uv run pytest tests/api/routes/test_auth.py tests/api/routes/test_ws_auth.py tests/migrations/test_s01_migration.py -v && uv run pytest tests/ -v

## Observability Impact

Test failures should surface close code + reason for WS tests and response JSON + status for HTTP tests. No new runtime signals — this task consumes the ones T02–T04 added.
