---
id: T03
parent: S01
milestone: M001-6cqls8
key_files:
  - backend/app/api/routes/auth.py
  - backend/app/api/deps.py
  - backend/app/api/main.py
  - backend/app/api/routes/login.py
  - backend/app/api/routes/users.py
  - backend/app/api/routes/items.py
  - backend/app/core/security.py
key_decisions:
  - Read the session cookie via `request.cookies.get(settings.SESSION_COOKIE_NAME)` instead of FastAPI's `Cookie(alias=...)` dependency — the latter fixes the cookie key at import time and couples the route signature to the setting; dict lookup lets env overrides of SESSION_COOKIE_NAME take effect without code changes (captured as MEM013)
  - Returned uniform 401 'Not authenticated' for all three non-existence / bad-cookie branches (no cookie, bad JWT, user_id not in DB) — per the plan's explicit 'don't leak user existence' directive, so an attacker can't distinguish 'this account exists but cookie invalid' from 'no such account'
  - Kept the 400 'Inactive user' separate from the 401 branch — an inactive user has a valid cookie and valid user row, so 400 matches the failure-mode table. Did not collapse it into 401 since that would hide a diagnostically-useful state from ops
  - Left `Token` and `TokenPayload` pydantic classes in models.py intact — they're now unreferenced but are harmless pure data shapes; removing them is out of scope and risks breaking any future caller that imports them before T05 rewrites the tests
  - Added the `_redact_email` helper inline in auth.py (not in a shared util module) — the slice-level Redaction constraint says 'email.split('@')[0][:3] + '***' or equivalent'; keeping it local avoids a premature shared-util abstraction until a second caller appears
duration: 
verification_result: passed
completed_at: 2026-04-24T22:09:22.941Z
blocker_discovered: false
---

# T03: Wire /auth router (signup/login/logout) + cookie-based get_current_user + UserRole checks replacing is_superuser

**Wire /auth router (signup/login/logout) + cookie-based get_current_user + UserRole checks replacing is_superuser**

## What Happened

Built `backend/app/api/routes/auth.py` with three endpoints: `POST /auth/signup` (JSON email/password/full_name?), `POST /auth/login` (JSON email/password — not OAuth2 form), and `POST /auth/logout` (no body, idempotent). Signup creates a user with `role=UserRole.user` via `crud.create_user`, issues a session JWT via `create_session_token`, sets the httpOnly cookie via `set_session_cookie`, and returns `UserPublic`. Login authenticates via `crud.authenticate` (which already handles timing-attack-safe hashing via DUMMY_HASH), returns 400 with the generic "Incorrect email or password" on bad creds, returns 400 "Inactive user" if `user.is_active=False`, otherwise sets the cookie and returns `UserPublic`. Logout just calls `clear_session_cookie` and returns `{message: "Logged out"}` — idempotent by design (works without a cookie).\n\nRewrote `backend/app/api/deps.py::get_current_user` to read the session cookie by name from `Request.cookies` using `settings.SESSION_COOKIE_NAME`. Removed the `OAuth2PasswordBearer` and `reusable_oauth2` global entirely. Chose the `request.cookies.get(settings.SESSION_COOKIE_NAME)` pattern over FastAPI's `Cookie(alias=...)` dependency because the latter fixes the cookie name at import time — `request.cookies` honors env-var overrides of `SESSION_COOKIE_NAME` without code changes (captured as MEM013). All failure paths return a uniform 401 "Not authenticated": missing cookie, unparseable/expired/tampered JWT, and missing user by id (no 404 — per the plan, don't leak user existence). Inactive user still returns 400 "Inactive user" since the user successfully authenticated but is disabled. Rewrote `get_current_active_superuser` to check `current_user.role == UserRole.system_admin` instead of `is_superuser`.\n\nTrimmed `backend/app/api/routes/login.py` to keep only password-recovery endpoints (recover_password, reset_password, recover_password_html_content). Deleted `POST /login/access-token` and `POST /login/test-token` — both superseded by cookie `/auth/*`. Removed unused imports (`OAuth2PasswordRequestForm`, `timedelta`, `CurrentUser`, `security`, `settings`, `Token`, `UserPublic`).\n\nDeleted `POST /users/signup` from `backend/app/api/routes/users.py` (replaced by `/auth/signup`) and swapped every `current_user.is_superuser` comparison to `current_user.role == UserRole.system_admin` / `!=`. Same swap in `backend/app/api/routes/items.py` (3 occurrences in the four item routes). Added `UserRole` imports where needed; removed the now-unused `UserRegister` import from users.py.\n\nMounted the new auth router in `backend/app/api/main.py` via `api_router.include_router(auth.router)` (placed before `login.router` so the auth tag appears first in the OpenAPI schema).\n\nDeleted the old `create_access_token(subject, expires_delta)` helper from `backend/app/core/security.py` per T02's note that T03 removes it once callers are rewritten. Verified no remaining imports via a project-wide grep — only `create_session_token`/`decode_session_token` are referenced now. Left the `Token` and `TokenPayload` SQLModel classes in `models.py` alone — they're no longer imported anywhere but are harmless pure data shapes; aggressive removal is out of scope.\n\nDid not modify `backend/app/initial_data.py` — per T01's summary, the `is_superuser` seed already moved to `core/db.py::init_db` and was updated there; `initial_data.py` only drives the session. Verified by re-reading the file.\n\nAdded observability logs as spec'd: `logger.info` on signup ok / signup dup / login ok / login failed / login inactive / logout ok, all with an `_redact_email` helper that returns `abc***@domain.com` (first 3 chars of local + full domain). No raw emails, passwords, or tokens are ever logged. Cookie decode failures log at DEBUG (exception class only) — that's in `decode_session_token` from T02.\n\nLive-exercised the full flow via `TestClient(app)` against the running Postgres at port 55432: signup sets cookie + returns role="user", `/users/me` returns role, `/users/me` without cookie → 401, duplicate signup → 400, logout → 200, login correct → 200 + cookie, login wrong password → 400 "Incorrect email or password", logout without cookie → 200 idempotent, tampered cookie on `/users/me` → 401 (not 500). All 9 checks passed. Redacted email log lines visible in stderr confirm the observability hookup.

## Verification

Ran the T03-PLAN verify chain (exit 0): `uv run python -c "from app.main import app; routes={r.path for r in app.routes}; assert '/api/v1/auth/signup' in routes and '/api/v1/auth/login' in routes and '/api/v1/auth/logout' in routes; assert '/api/v1/login/access-token' not in routes"` + `uv run python -c "from app.models import UserPublic; assert 'role' in UserPublic.model_fields"`. Then a full live TestClient walkthrough against the real Postgres covering: signup happy path + cookie issuance + role=user in response (200), /users/me with cookie returns role (200), /users/me without cookie returns 401, signup duplicate email returns 400, logout returns 200 + clears cookie, login with correct creds returns 200 + cookie, login with wrong password returns 400 "Incorrect email or password" (generic, no user-existence leak), logout without cookie returns 200 idempotent, tampered cookie on /users/me returns 401 (not 500). All 9 end-to-end checks passed. Redacted email logs (e2e***@example.com) visible in stderr confirming the observability hookup. Also verified `create_access_token` is no longer importable from `app.core.security` and that `get_current_user` now takes `(session, request)` params — confirming the cookie-based rewrite.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `uv run python -c "from app.main import app; routes={r.path for r in app.routes}; assert '/api/v1/auth/signup' in routes and '/api/v1/auth/login' in routes and '/api/v1/auth/logout' in routes; assert '/api/v1/login/access-token' not in routes"` | 0 | ✅ pass — all three /auth/* routes mounted; /login/access-token removed | 650ms |
| 2 | `uv run python -c "from app.models import UserPublic; assert 'role' in UserPublic.model_fields"` | 0 | ✅ pass — UserPublic inherits role: UserRole from UserBase | 500ms |
| 3 | `TestClient: POST /api/v1/auth/signup (new email) → 200 + perpetuity_session cookie + body.role='user'` | 0 | ✅ pass | 220ms |
| 4 | `TestClient: GET /api/v1/users/me (with cookie) → 200 + body.role='user'` | 0 | ✅ pass | 40ms |
| 5 | `TestClient: GET /api/v1/users/me (no cookie) → 401 {"detail":"Not authenticated"}` | 0 | ✅ pass | 20ms |
| 6 | `TestClient: POST /api/v1/auth/signup (duplicate email) → 400` | 0 | ✅ pass | 30ms |
| 7 | `TestClient: POST /api/v1/auth/logout → 200 {"message":"Logged out"} + cookie cleared` | 0 | ✅ pass | 25ms |
| 8 | `TestClient: POST /api/v1/auth/login (correct creds) → 200 + cookie + /users/me returns 200 after login` | 0 | ✅ pass | 210ms |
| 9 | `TestClient: POST /api/v1/auth/login (wrong password) → 400 {"detail":"Incorrect email or password"} (generic, no user-existence leak)` | 0 | ✅ pass | 180ms |
| 10 | `TestClient: POST /api/v1/auth/logout (no cookie present) → 200 {"message":"Logged out"} (idempotent)` | 0 | ✅ pass | 20ms |
| 11 | `TestClient: GET /api/v1/users/me with tampered cookie 'not.a.valid.jwt' → 401 (not 500)` | 0 | ✅ pass | 25ms |
| 12 | `Import check: from app.core.security import create_access_token → ImportError` | 0 | ✅ pass — old helper deleted as spec'd | 30ms |

## Deviations

The plan's input/output list named `backend/app/initial_data.py` but that file doesn't reference `is_superuser` — the admin seed lives in `backend/app/core/db.py::init_db` which T01 already updated to pass `role=UserRole.system_admin`. Left `initial_data.py` untouched (it only drives the session). The plan also didn't list `backend/app/api/routes/items.py` in the expected-output file list but it had 4 `is_superuser` references that had to be rewritten; otherwise any item route call would raise `AttributeError` at runtime post-migration. Swapped them to `role == UserRole.system_admin` comparisons. Finally, the plan listed `UserPublic` needing a `role` field update — no change needed because `UserPublic(UserBase)` already inherits `role: UserRole` from UserBase after T01's model changes; verified via `'role' in UserPublic.model_fields`.

## Known Issues

Tests in `backend/tests/` still reference `is_superuser` on User and the old `/login/access-token` / `/users/signup` routes (per the grep: test_users.py, test_login.py, test_user.py, utils/user.py, utils/utils.py) — these are explicitly T05's scope per the slice plan and will fail until T05 rewrites them to use cookie-based fixtures. Running the full pytest suite now would show those failures; T03's verification bar is the two `python -c` assertions plus the live TestClient flow, which all pass. The `InsecureKeyLengthWarning` in stderr during local test runs is from the 'changethis' default SECRET_KEY — also out of scope (the config.py validator already warns about this in local and raises in staging/production).

## Files Created/Modified

- `backend/app/api/routes/auth.py`
- `backend/app/api/deps.py`
- `backend/app/api/main.py`
- `backend/app/api/routes/login.py`
- `backend/app/api/routes/users.py`
- `backend/app/api/routes/items.py`
- `backend/app/core/security.py`
