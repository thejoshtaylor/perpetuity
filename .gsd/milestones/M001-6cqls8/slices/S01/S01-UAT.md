# S01: Auth migration + role system — UAT

**Milestone:** M001-6cqls8
**Written:** 2026-04-24T22:59:58.273Z


# S01 UAT: Auth migration + role system

**Proof level:** contract + integration (real Postgres, real HTTP client, real WS client). No human UI in this slice — S04 delivers the UX-level UAT for login/dashboard.

## Preconditions

1. Clean Postgres instance reachable on the port named by `.env` (`POSTGRES_PORT=55432` in this repo).
2. `cd backend && uv run alembic upgrade head` has been run (or is run automatically by the test DB bootstrap fixture).
3. `backend/.env` has `ENVIRONMENT=local` so the `Secure` cookie flag is off and httpx cookie jars work in tests.

## Test Cases

### TC-01: Signup happy path sets httpOnly cookie

**Steps:**
1. `POST /api/v1/auth/signup` with JSON `{"email": "tc01@example.com", "password": "CorrectHorseBatteryStaple!"}`.
2. Inspect the response status, body, and `Set-Cookie` header.
3. Using the returned cookie jar, call `GET /api/v1/users/me`.

**Expected:**
- Step 1 → 200, body is `UserPublic` with `email`, `id`, `role = "user"`, `is_active = true`.
- `Set-Cookie` header contains `perpetuity_session=<jwt>`; includes `HttpOnly`, `SameSite=Lax`, `Path=/`, `Max-Age=<ACCESS_TOKEN_EXPIRE_MINUTES*60>`; does NOT include `Secure` (because `ENVIRONMENT=local`).
- Step 3 → 200, body `role == "user"`, `id` matches step 1.

### TC-02: Signup duplicate email → 400

**Steps:** `POST /auth/signup` twice with the same email.
**Expected:** Second response → 400. Body's `detail` does not reveal that the email existed (matches the generic duplicate response already in `crud.create_user`).

### TC-03: Login happy path

**Steps:**
1. Signup once (TC-01). Logout (TC-09) to clear the jar.
2. `POST /api/v1/auth/login` with JSON `{"email": <same>, "password": <same>}`.
3. With the returned cookie, call `GET /api/v1/users/me`.

**Expected:**
- Step 2 → 200, body is `UserPublic`, `Set-Cookie` refreshes `perpetuity_session`.
- Step 3 → 200, body `role == "user"`.

### TC-04: Login wrong password → 400 uniform

**Steps:** Signup a user. Attempt `POST /auth/login` with wrong password.
**Expected:** 400 with body `{"detail": "Incorrect email or password"}`. No `Set-Cookie` header. No information about whether the email exists.

### TC-05: Login unknown email → same uniform 400

**Steps:** `POST /auth/login` with email that was never created.
**Expected:** 400 with body `{"detail": "Incorrect email or password"}` — identical to TC-04.

### TC-06: /users/me without cookie → 401

**Steps:** `GET /api/v1/users/me` with no cookie jar.
**Expected:** 401 with body `{"detail": "Not authenticated"}`.

### TC-07: /users/me with tampered cookie → 401 (not 500)

**Steps:** Manually set the `perpetuity_session` cookie to `not.a.valid.jwt` and call `GET /users/me`.
**Expected:** 401 with body `{"detail": "Not authenticated"}`. Stack trace / 500 is a failure.

### TC-08: /users/me with expired cookie → 401

**Steps:** Hand-forge a JWT with `exp` in the past (the test uses `pyjwt` directly) and call `GET /users/me`.
**Expected:** 401 with body `{"detail": "Not authenticated"}`. Server debug log records `ExpiredSignatureError` class name — NOT the token.

### TC-09: Logout clears the cookie

**Steps:** Login, then `POST /api/v1/auth/logout`, then `GET /users/me` with the same jar.
**Expected:**
- Logout → 200 with body `{"message": "Logged out"}`; `Set-Cookie` header sets `perpetuity_session` with `Max-Age=0` (or equivalent expiry in the past), same attributes as the set path (matching attributes are required or browsers silently keep the cookie).
- Final `GET /users/me` → 401.

### TC-10: Logout without cookie is idempotent

**Steps:** With an empty jar, call `POST /auth/logout`.
**Expected:** 200 with `{"message": "Logged out"}`. Set-Cookie still emitted (harmless, matches the attribute-matching rule from TC-09).

### TC-11: WS /ws/ping happy path

**Steps:** Login (TC-03). Using the cookie, open a WebSocket to `ws://<host>/api/v1/ws/ping`. Send any text frame.
**Expected:** Server echoes a JSON frame `{"pong": "<uuid>", "role": "user"}`. Connection is open (not closed) after the echo.

### TC-12: WS /ws/ping without cookie → 1008 missing_cookie

**Steps:** Open WS to `/api/v1/ws/ping` with no cookie.
**Expected:** WS handshake is rejected with close code 1008 and reason `"missing_cookie"`.

### TC-13: WS with tampered cookie → 1008 invalid_token

**Steps:** Open WS with cookie `perpetuity_session=garbage.garbage.garbage`.
**Expected:** Close code 1008, reason `"invalid_token"`.

### TC-14: WS with expired cookie → 1008 invalid_token

**Steps:** Hand-forge an expired JWT, connect.
**Expected:** Close code 1008, reason `"invalid_token"` (expiry is subsumed under `InvalidTokenError` in our decode).

### TC-15: WS with valid JWT for deleted user → 1008 user_not_found

**Steps:** Login, then delete the user row via raw SQL, then open the WS with the still-valid cookie.
**Expected:** Close code 1008, reason `"user_not_found"`.

### TC-16: WS with inactive user → 1008 user_inactive

**Steps:** Login, then flip the user's `is_active` to `false` via SQL, then open the WS.
**Expected:** Close code 1008, reason `"user_inactive"`.

### TC-17: Migration forward run on fresh Postgres

**Steps:**
1. Drop / re-create the test database (handled by test fixture).
2. `uv run alembic upgrade head`.
3. Query `SELECT typname FROM pg_type WHERE typname IN ('userrole','teamrole')` and `SELECT column_name FROM information_schema.columns WHERE table_name='user'`.

**Expected:**
- Upgrade exits 0.
- Both enum types present (lowercase — do not look for `UserRole`).
- No `is_superuser` column on `user`; `role` column present (`not null`, default `'user'`).
- Tables `team` and `team_member` exist.

### TC-18: Migration data-migrates existing rows

**Steps:**
1. Downgrade to the pre-S01 revision.
2. Raw-SQL insert: one user with `is_superuser=TRUE`, one with `is_superuser=FALSE`.
3. `alembic upgrade head`.
4. `SELECT email, role FROM "user" ORDER BY is_superuser`. (Wait — `is_superuser` is gone after upgrade. Use the email PKs from step 2.)

**Expected:** The formerly-superuser row has `role='system_admin'`; the other has `role='user'`.

### TC-19: Migration downgrade round-trip

**Steps:** From head, `alembic downgrade -1`, then `alembic upgrade head`.

**Expected:** Both commands exit 0. Schema and enum presence match TC-17 after the final upgrade. Any rows that existed with `role='system_admin'` before downgrade come back as `is_superuser=TRUE` post-downgrade (and vice versa).

### TC-20: Log redaction — raw email never logged on failed login

**Steps:** Attempt `POST /auth/login` with an unknown email like `secret@example.com`. Capture the `app.api.routes.auth` log records.

**Expected:** No log record's `message` field contains `"secret@example.com"`. A redacted form (`sec***@example.com`) is allowed.

## Negative / Edge Cases

- **Cookie attribute drift on logout:** If future work changes `set_session_cookie` attributes without matching updates to `clear_session_cookie`, TC-09 will start failing because browsers silently retain the cookie. Keep the attribute pair in lockstep.
- **SECRET_KEY rotation:** Every existing session becomes a TC-07 (tampered) result. Expected behaviour; surface in release notes when rotation happens.
- **Clock skew:** Expired-cookie tests use frozen `exp`. Production clocks within a minute of each other are fine; large skew (>5 min) on a backend may cause false-positive `ExpiredSignatureError`. No mitigation in this slice.
- **DB lock on migration tests:** If anyone reuses the migration-test pattern for a different migration, they must copy the `_release_autouse_db_session` fixture pattern from `test_s01_migration.py` — the session-scoped autouse `db` fixture holds an AccessShareLock that will hang DDL indefinitely (MEM016).

## Run all UAT in one command

`cd backend && uv run pytest tests/api/routes/test_auth.py tests/api/routes/test_ws_auth.py tests/migrations/test_s01_migration.py -v`

All 21 tests must pass before considering this slice UAT-complete. They do, as of 2026-04-24.

