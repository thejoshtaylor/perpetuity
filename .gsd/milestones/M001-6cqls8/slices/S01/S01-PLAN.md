# S01: Auth migration + role system

**Goal:** Migrate auth from localStorage JWT bearer to httpOnly cookie sessions, replace is_superuser bool with UserRole enum, and introduce TeamRole enum + TeamMember join table (with a minimal Team stub) so that signup/login/logout work via cookies, GET /users/me returns the role field, WS connections authenticate via cookie, and the migration runs cleanly on a fresh Postgres database.
**Demo:** Signup, login, logout all work via httpOnly cookies; GET /users/me returns role; WS connection authenticated via cookie passes integration test; migration runs cleanly on fresh DB

## Must-Haves

- **Demo:** After this slice, on a fresh Postgres:
- `POST /api/v1/auth/signup` creates a user with role=user and sets a session cookie
- `POST /api/v1/auth/login` with correct credentials sets an httpOnly, SameSite=Lax session cookie
- `POST /api/v1/auth/logout` clears the cookie
- `GET /api/v1/users/me` with the session cookie returns `{id, email, role, ...}`
- `WS /api/v1/ws/ping` succeeds when the client passes the session cookie, rejects otherwise (1008)
- `alembic upgrade head` runs cleanly on a fresh DB; `alembic downgrade -1` from head also succeeds.
- **Must-haves:**
- Every Active owned requirement (R001, R002) maps to at least one task.
- No test mocks the database — all integration tests run against real Postgres (per D001/D002 rationale and project constraint).
- Existing `is_superuser=True` rows migrate to `role=system_admin`; `is_superuser=False` to `role=user`.
- `TeamRole` enum (`member`, `admin`) and a `TeamMember` join table exist, with a minimal `Team` stub table so FKs resolve; S02 will extend `Team` with real columns.
- Cookie flags: `HttpOnly=True`, `SameSite=Lax`, `Secure` gated on `ENVIRONMENT != "local"`.
- Session token remains a signed JWT for now (same `SECRET_KEY`, HS256) so the DB need not carry a session table yet.
- **Verification (test files — all tracked in git):**
- `backend/tests/api/routes/test_auth.py` — cookie signup/login/logout + /users/me role field + negative cases (bad password, inactive user, missing cookie, expired cookie)
- `backend/tests/api/routes/test_ws_auth.py` — WS ping success with cookie, 1008 close without cookie
- `backend/tests/migrations/test_s01_migration.py` — upgrade head on fresh DB + downgrade -1 round trip, assert enum types exist and `is_superuser` column is gone, assert existing superuser rows mapped to `role=system_admin`
- Run: `cd backend && uv run pytest tests/api/routes/test_auth.py tests/api/routes/test_ws_auth.py tests/migrations/test_s01_migration.py -v`
- All three files must pass against the real Postgres provided by `docker-compose.yml` / the project's test DB fixture.

## Proof Level

- This slice proves: - This slice proves: contract + integration (real Postgres, real HTTP client, real WS client)
- Real runtime required: yes (Postgres via docker-compose or pytest-postgresql)
- Human/UAT required: no (API-level slice; S04 delivers the UAT flow)

## Integration Closure

- Upstream surfaces consumed: `app/core/config.py` (SECRET_KEY, ENVIRONMENT, FRONTEND_HOST), `app/core/db.py` (engine), `app/models.py` (User), `app/api/deps.py` (SessionDep, get_current_user), existing `app/core/security.py` password helpers
- New wiring introduced in this slice: new `app/api/routes/auth.py` router mounted in `app/api/main.py`; new `app/api/routes/ws.py` minimal WS ping endpoint mounted in `app/api/main.py`; new cookie-based `get_current_user` replaces the OAuth2 bearer version (old `/login/access-token` endpoint removed — nothing in the codebase outside of tests references it and tests are rewritten in T05)
- What remains before the milestone is truly usable end-to-end: S02 (real Team model + personal team on signup), S03 (invite/join/role management), S04 (frontend login + dashboard + mobile), S05 (system admin panel). This slice alone has no UI — curl/httpx/pytest only.

## Verification

- Runtime signals: structured log on login success/failure (email hash, not email), on signup, on logout, on WS auth reject (with reason: missing_cookie | invalid_jwt | user_inactive | user_not_found)
- Inspection surfaces: `GET /api/v1/users/me` returns current session identity; backend logs via existing uvicorn/FastAPI logging; alembic version table (`alembic_version`) reveals migration state
- Failure visibility: WS reject uses close code 1008 with a `reason` string; login returns 400 with generic "Incorrect email or password"; cookie decode errors log the failure class (InvalidTokenError vs ValidationError) without leaking the token value
- Redaction constraints: never log the session JWT, the raw password, or the decoded cookie payload; hash or redact emails in logs (use `email.split('@')[0][:3] + '***'` helper or equivalent)

## Tasks

- [x] **T01: Introduce UserRole/TeamRole enums, TeamMember + minimal Team tables, and data-migrate is_superuser → role** `est:2h`
  Replace `is_superuser` with a `UserRole` enum on `User`, introduce `TeamRole` enum, and add `TeamMember` + a minimal `Team` stub table so FKs resolve in this slice and S02 can extend `Team` with real columns. Single Alembic migration that: (1) creates both enum types, (2) adds `role` column to `user` with a data migration (`is_superuser=True → system_admin`, else `user`), (3) drops `is_superuser`, (4) creates `team` stub (`id UUID PK`, `created_at`), (5) creates `team_member` (`user_id`, `team_id`, `role`, `created_at`, composite PK or unique constraint on `(user_id, team_id)`). Must be fully reversible (downgrade restores `is_superuser` bool by mapping `system_admin → True`, others → False; drops new tables/enums).

Failure Modes:
| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| Postgres migration | Fail loudly and roll back transaction | N/A (local DDL) | N/A |
| Existing superuser rows | Map to `system_admin`; log count of migrated rows | N/A | N/A (rows are already validated by SQLModel) |

Load Profile: negligible — one-shot migration against a small user table; single transaction.

Negative Tests:
- Migration against a DB with zero users, one user, and multiple users (mix of superuser/non)
- Downgrade from head back to previous revision and re-upgrade (idempotent round trip)
- Enum value not in allowed set rejected by SQLModel/Postgres
  - Files: `backend/app/models.py`, `backend/app/alembic/versions/s01_auth_and_roles.py`, `backend/app/initial_data.py`, `backend/app/crud.py`
  - Verify: cd backend && uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head && uv run python -c "from app.models import User, UserRole, TeamRole, TeamMember, Team; assert UserRole.system_admin.value == 'system_admin'; assert TeamRole.admin.value == 'admin'"

- [x] **T02: Add httpOnly cookie session layer (token helpers, cookie set/clear utilities, settings)** `est:45m`
  Build the cookie session infrastructure that T03 will wire into endpoints. Extend `app/core/security.py` with `create_session_token(user_id) -> str` (HS256 JWT, `SECRET_KEY`, `exp=ACCESS_TOKEN_EXPIRE_MINUTES`) and `decode_session_token(token) -> uuid.UUID | None`. Add `app/core/cookies.py` with `set_session_cookie(response, token)` and `clear_session_cookie(response)` helpers that apply: `httponly=True`, `samesite='lax'`, `secure=(settings.ENVIRONMENT != 'local')`, `max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60`, `path='/'`, name=`settings.SESSION_COOKIE_NAME` (default `perpetuity_session`). Add `SESSION_COOKIE_NAME` to `app/core/config.py`. No FastAPI wiring yet — pure helpers with unit tests inline in the file or a small `tests/core/test_cookies.py` (optional — integration tests in T05 cover it end-to-end). Do NOT delete the old `create_access_token` helper yet — T03 does that once callers are rewritten.

Failure Modes:
| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| JWT decode (`jwt.decode`) | Return `None` from `decode_session_token`; caller treats as unauthenticated | N/A (local op) | Return `None` |
| SECRET_KEY missing | Raise at import — fail fast | N/A | N/A |

Load Profile: trivial — pure in-memory JWT encode/decode per request; HS256 is ~microseconds.

Negative Tests (covered in T05 integration suite): tampered JWT returns None; expired JWT returns None; wrong-algorithm JWT returns None.
  - Files: `backend/app/core/security.py`, `backend/app/core/cookies.py`, `backend/app/core/config.py`
  - Verify: cd backend && uv run python -c "from app.core.security import create_session_token, decode_session_token; import uuid; u=uuid.uuid4(); t=create_session_token(u); assert decode_session_token(t)==u; assert decode_session_token('garbage') is None" && uv run python -c "from app.core.cookies import set_session_cookie, clear_session_cookie; from fastapi import Response; r=Response(); set_session_cookie(r,'tok'); assert 'perpetuity_session' in r.headers.get('set-cookie','')"

- [x] **T03: Wire /auth router (signup/login/logout) + cookie-based get_current_user + /users/me role field** `est:2h`
  Create `app/api/routes/auth.py` with three endpoints: `POST /auth/signup` (body: email, password, full_name?), `POST /auth/login` (body: email, password — JSON, not OAuth2 form), `POST /auth/logout` (no body). Signup creates a user with `role=UserRole.user`, sets the session cookie, returns `UserPublic`. Login validates, sets cookie, returns `UserPublic`. Logout clears cookie, returns `{message: 'Logged out'}`. Mount the new router in `app/api/main.py`. Rewrite `app/api/deps.py::get_current_user` to read `settings.SESSION_COOKIE_NAME` from `Request.cookies` via `Cookie(None)` dependency — remove the `OAuth2PasswordBearer` usage and the `reusable_oauth2` global. Update `UserPublic` to include `role: UserRole`. Delete the old `POST /login/access-token` endpoint and the `/users/signup` endpoint (replaced by `/auth/*`); update `app/api/routes/login.py` to keep only password-recovery endpoints, and remove signup from `users.py`. Update `get_current_active_superuser` to check `current_user.role == UserRole.system_admin` instead of `is_superuser`. Update `initial_data.py` and any tooling that referenced `is_superuser` on the User model.

Failure Modes:
| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| Password verify | 400 Incorrect email or password (generic) | N/A | N/A |
| Cookie decode | 401 Not authenticated | N/A | 401 Not authenticated |
| User.is_active=False | 400 Inactive user | N/A | N/A |
| User lookup by id | 401 Not authenticated (not 404 — don't leak existence) | N/A | N/A |

Load Profile:
- Shared resources: DB session pool; password hasher (Argon2 CPU)
- Per-operation cost: 1 DB query for login, 1 insert for signup, 0 for logout; Argon2 verify ~50–100ms
- 10x breakpoint: Argon2 CPU (mitigation is out of scope for S01 — documented only)

Negative Tests (in T05):
- Missing cookie → 401
- Tampered cookie → 401 (not 500)
- Expired cookie → 401
- Login with wrong password → 400 generic message (no user-existence leak)
- Signup with duplicate email → 400
- Logout without cookie → 200 idempotent (clears cookie anyway)
  - Files: `backend/app/api/routes/auth.py`, `backend/app/api/main.py`, `backend/app/api/deps.py`, `backend/app/models.py`, `backend/app/api/routes/login.py`, `backend/app/api/routes/users.py`, `backend/app/initial_data.py`
  - Verify: cd backend && uv run python -c "from app.main import app; routes={r.path for r in app.routes}; assert '/api/v1/auth/signup' in routes and '/api/v1/auth/login' in routes and '/api/v1/auth/logout' in routes; assert '/api/v1/login/access-token' not in routes" && uv run python -c "from app.models import UserPublic; assert 'role' in UserPublic.model_fields"

- [x] **T04: Add WS cookie auth dependency and minimal /ws/ping endpoint** `est:45m`
  Add `get_current_user_ws(websocket: WebSocket) -> User` helper in `app/api/deps.py` that reads the session cookie from the WebSocket's `cookies` dict, decodes it, loads the user, and on any failure calls `await websocket.close(code=1008, reason='<reason>')` and raises `WebSocketDisconnect`. Reasons must be one of: `missing_cookie`, `invalid_token`, `user_not_found`, `user_inactive`. Add `app/api/routes/ws.py` exposing `WS /ws/ping` that accepts the upgrade, awaits one message, and echoes `{'pong': str(user.id), 'role': user.role.value}`. Mount the router in `app/api/main.py`. The endpoint exists solely to satisfy R001's WS clause and the roadmap success criterion; it will be replaced / supplemented by real WS endpoints in M002+.

Failure Modes:
| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| Missing cookie on upgrade | close(1008, 'missing_cookie') | N/A | N/A |
| Invalid/expired JWT | close(1008, 'invalid_token') | N/A | close(1008, 'invalid_token') |
| User.is_active=False | close(1008, 'user_inactive') | N/A | N/A |
| User row missing | close(1008, 'user_not_found') | N/A | N/A |

Load Profile: trivial — 1 DB query per connection at accept time; no per-message DB traffic.

Negative Tests (in T05):
- WS connect without cookie → close 1008 reason missing_cookie
- WS connect with tampered cookie → close 1008 reason invalid_token
- WS connect with valid cookie → echoes pong with user id and role
  - Files: `backend/app/api/routes/ws.py`, `backend/app/api/deps.py`, `backend/app/api/main.py`
  - Verify: cd backend && uv run python -c "from app.main import app; paths={getattr(r,'path',None) for r in app.routes}; assert '/api/v1/ws/ping' in paths" && uv run python -c "from app.api.deps import get_current_user_ws; assert callable(get_current_user_ws)"

- [x] **T05: Write integration tests for auth flow, WS cookie auth, and the S01 migration** `est:2h`
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
  - Files: `backend/tests/api/routes/test_auth.py`, `backend/tests/api/routes/test_ws_auth.py`, `backend/tests/migrations/__init__.py`, `backend/tests/migrations/test_s01_migration.py`, `backend/tests/conftest.py`, `backend/tests/utils/user.py`, `backend/tests/api/routes/test_login.py`, `backend/tests/api/routes/test_users.py`, `backend/tests/api/routes/test_items.py`
  - Verify: cd backend && uv run pytest tests/api/routes/test_auth.py tests/api/routes/test_ws_auth.py tests/migrations/test_s01_migration.py -v && uv run pytest tests/ -v

## Files Likely Touched

- backend/app/models.py
- backend/app/alembic/versions/s01_auth_and_roles.py
- backend/app/initial_data.py
- backend/app/crud.py
- backend/app/core/security.py
- backend/app/core/cookies.py
- backend/app/core/config.py
- backend/app/api/routes/auth.py
- backend/app/api/main.py
- backend/app/api/deps.py
- backend/app/api/routes/login.py
- backend/app/api/routes/users.py
- backend/app/api/routes/ws.py
- backend/tests/api/routes/test_auth.py
- backend/tests/api/routes/test_ws_auth.py
- backend/tests/migrations/__init__.py
- backend/tests/migrations/test_s01_migration.py
- backend/tests/conftest.py
- backend/tests/utils/user.py
- backend/tests/api/routes/test_login.py
- backend/tests/api/routes/test_users.py
- backend/tests/api/routes/test_items.py
