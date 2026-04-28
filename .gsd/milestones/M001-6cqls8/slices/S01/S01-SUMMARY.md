---
id: S01
parent: M001-6cqls8
milestone: M001-6cqls8
provides:
  - ["httpOnly cookie session auth (POST /api/v1/auth/signup, /login, /logout)", "cookie-based get_current_user and get_current_user_ws dependencies (app/api/deps.py)", "create_session_token / decode_session_token helpers (app/core/security.py)", "set_session_cookie / clear_session_cookie helpers (app/core/cookies.py)", "SESSION_COOKIE_NAME setting (default perpetuity_session)", "UserRole enum (user, system_admin) on User model; is_superuser column removed", "TeamRole enum (member, admin) + TeamMember join table + minimal Team stub table", "Alembic migration s01_auth_and_roles (fully reversible)", "WS /api/v1/ws/ping cookie-authenticated echo endpoint", "Cookie-based test fixtures (login_cookie_headers, superuser_cookies, normal_user_cookies)"]
requires:
  []
affects:
  - ["backend/app/models.py", "backend/app/api/deps.py", "backend/app/api/main.py", "backend/app/api/routes/auth.py", "backend/app/api/routes/ws.py", "backend/app/api/routes/login.py", "backend/app/api/routes/users.py", "backend/app/api/routes/items.py", "backend/app/core/security.py", "backend/app/core/cookies.py", "backend/app/core/config.py", "backend/app/core/db.py", "backend/app/crud.py", "backend/app/alembic/versions/s01_auth_and_roles.py", "backend/tests/conftest.py", "backend/tests/utils/user.py", "backend/tests/utils/utils.py", "backend/tests/api/routes/test_auth.py", "backend/tests/api/routes/test_ws_auth.py", "backend/tests/migrations/__init__.py", "backend/tests/migrations/test_s01_migration.py", "backend/tests/api/routes/test_login.py", "backend/tests/api/routes/test_users.py", "backend/tests/api/routes/test_items.py", "backend/tests/crud/test_user.py"]
key_files:
  - ["backend/app/api/routes/auth.py", "backend/app/api/routes/ws.py", "backend/app/api/deps.py", "backend/app/core/security.py", "backend/app/core/cookies.py", "backend/app/models.py", "backend/app/alembic/versions/s01_auth_and_roles.py", "backend/tests/api/routes/test_auth.py", "backend/tests/api/routes/test_ws_auth.py", "backend/tests/migrations/test_s01_migration.py"]
key_decisions:
  - ["httpOnly cookie sessions over localStorage Bearer: XSS-resistant and WS-upgrade-compatible (browsers can't set Authorization on WS upgrades, but cookies flow automatically). Self-contained HS256 JWT — no session table yet.", "UserRole/TeamRole enums replace is_superuser bool. Authorization checks use role==UserRole.system_admin. TeamRole lives on the TeamMember join table so a user can be admin of team A and member of team B.", "Read cookie via request.cookies.get(settings.SESSION_COOKIE_NAME) instead of FastAPI's Cookie(alias=...) dependency — Cookie fixes the name at import time; dict-lookup honors env overrides uniformly across HTTP and WS (MEM018).", "All four user-existence-adjacent auth failures collapse to uniform 401 'Not authenticated' to prevent account enumeration. Inactive-user stays 400 because the user successfully authenticated — diagnostically useful to ops (MEM019).", "clear_session_cookie mirrors every attribute (key, path, httponly, samesite, secure) of set_session_cookie — attribute drift causes browsers to silently keep the cookie on logout.", "Migration uses add-nullable → backfill → NOT-NULL for the new role column (safe pattern for populated tables) and logs per-branch rowcounts for ops visibility.", "WS auth dep opens its own short-lived Session(engine) rather than Depends(get_db) — FastAPI doesn't resolve Depends for helpers invoked imperatively from a WS endpoint (MEM022).", "WS auth close is called BEFORE accept — Starlette converts pre-accept close into a handshake rejection with the supplied code/reason, which is what the 1008 contract requires.", "SQLModel enums land in Postgres with lowercase typname (userrole, teamrole). Migration tests must query pg_type with lowercase (MEM020).", "Migration test added autouse fixture to commit/close the session-scoped autouse db session and engine.dispose() before alembic runs — otherwise DROP COLUMN waits forever on an AccessShareLock (MEM016)."]
patterns_established:
  - ["Cookie-first auth for both HTTP and WS via a shared SESSION_COOKIE_NAME setting read via request.cookies/websocket.cookies — the pattern to follow for any new protected route in M001+.", "Uniform 401 'Not authenticated' for any user-existence-adjacent failure; 400 only when the user authenticated successfully but cannot proceed (inactive).", "Email redaction via local _redact_email (first 3 chars of local + full domain); escalate to a shared util only when a second caller appears.", "Migration-test isolation: autouse fixture releases the session-scoped db session + disposes the engine pool before alembic DDL, and disposes again in teardown. Required for any future DDL test.", "Cookie-based test fixtures (login_cookie_headers, superuser_cookies, normal_user_cookies) always call client.cookies.clear() before logging in to avoid httpx CookieConflict from stale jar state (MEM017).", "Role checks use enum equality (role == UserRole.system_admin) — never string comparison or bool coercion."]
observability_surfaces:
  - ["INFO log on auth events: signup_ok, signup_duplicate, login_ok, login_failed, login_inactive, logout_ok (all with redacted email)", "INFO log on WS auth reject with reason=missing_cookie|invalid_token|user_not_found|user_inactive; DEBUG log on WS auth accept", "DEBUG log on cookie decode failure recording the pyjwt exception class (ExpiredSignatureError vs InvalidTokenError) — never the token payload", "WS failure visibility: close code 1008 + reason string — the only inspection surface available before the handshake completes", "alembic_version table reveals migration state for ops (pre-S01 revision vs s01_auth_and_roles)"]
drill_down_paths:
  - ["`.gsd/milestones/M001-6cqls8/slices/S01/tasks/T01-SUMMARY.md` — migration + enums", "`.gsd/milestones/M001-6cqls8/slices/S01/tasks/T02-SUMMARY.md` — cookie/session helpers", "`.gsd/milestones/M001-6cqls8/slices/S01/tasks/T03-SUMMARY.md` — /auth router + get_current_user rewrite", "`.gsd/milestones/M001-6cqls8/slices/S01/tasks/T04-SUMMARY.md` — WS cookie auth + /ws/ping", "`.gsd/milestones/M001-6cqls8/slices/S01/tasks/T05-SUMMARY.md` — integration tests + fixture migration", "MEM016 (gotcha) — migration test session-lock fix; MEM017 (gotcha) — CookieConflict on httpx jars; MEM018 (convention) — request.cookies.get over Cookie(alias); MEM019 (convention) — no user enumeration in auth failures; MEM020 (convention) — lowercase pg enum typname; MEM022 (pattern) — WS Depends/close-before-accept; MEM023 (architecture) — httpOnly cookie rationale; MEM024 (architecture) — role enum model."]
duration: ""
verification_result: passed
completed_at: 2026-04-24T22:59:58.273Z
blocker_discovered: false
---

# S01: Auth migration + role system

**Migrated auth from localStorage JWT Bearer tokens to httpOnly cookie sessions and replaced `is_superuser` with a `UserRole` enum — signup/login/logout work via cookies, `GET /users/me` returns `role`, WS `/ws/ping` authenticates via cookie, and the S01 migration round-trips cleanly on fresh Postgres.**

## What Happened


S01 converted the template's OAuth2 Bearer-token-in-localStorage scheme into httpOnly cookie sessions and re-modelled authorization as role enums, so every later slice in M001 can gate features on role without ever handing a token to JavaScript.

**What was built (by task):**

- **T01** — Alembic migration `s01_auth_and_roles` creates Postgres enum types `userrole` and `teamrole` (lowercase, per SQLModel defaults), adds `role` to `user` via add-nullable → backfill → NOT-NULL (the safe pattern for a required column on a populated table), data-migrates `is_superuser=True → system_admin` / `False → user` with per-branch rowcount logging, drops `is_superuser`, and creates a minimal `team` stub (id + created_at) plus `team_member` (user_id, team_id, role, created_at, unique (user_id, team_id)). Fully reversible: downgrade restores `is_superuser` with the correct booleans and drops the new tables/enums. Updated `app/models.py`, `app/crud.py`, `app/initial_data.py`/`app/core/db.py::init_db` to use `UserRole` instead of the removed bool.

- **T02** — Pure cookie/session infrastructure in `app/core/security.py` (`create_session_token(user_id) -> str` HS256 JWT with `SECRET_KEY`, and `decode_session_token(token) -> uuid.UUID | None` that catches `jwt.ExpiredSignatureError`, `jwt.InvalidTokenError`, plus `ValueError`/`ValidationError` from `sub → UUID` coercion, logging only the exception class via `logger.debug` — never the token payload, per the slice redaction constraint) and `app/core/cookies.py` (`set_session_cookie` / `clear_session_cookie` applying `httponly=True`, `samesite="lax"`, `secure=(settings.ENVIRONMENT != "local")`, `max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60`, `path="/"`, `key=settings.SESSION_COOKIE_NAME`). Added `SESSION_COOKIE_NAME` (default `perpetuity_session`) to `app/core/config.py`.

- **T03** — `app/api/routes/auth.py` with `POST /auth/signup`, `POST /auth/login` (JSON body — NOT OAuth2 form), and `POST /auth/logout` (idempotent, no body). Signup creates a user with `role=UserRole.user` via `crud.create_user`, sets the cookie, returns `UserPublic` (which now includes `role`). Login uses `crud.authenticate` (timing-attack-safe, already argon2), returns generic 400 "Incorrect email or password" on bad creds, 400 "Inactive user" if `is_active=False`. Logout calls `clear_session_cookie` and returns `{message: "Logged out"}` whether or not a cookie was present. Rewrote `app/api/deps.py::get_current_user` to read the cookie via `request.cookies.get(settings.SESSION_COOKIE_NAME)` (NOT `Cookie(alias=...)` — see Patterns below) and removed the `OAuth2PasswordBearer` and `reusable_oauth2` globals entirely. All four "user-existence-adjacent" failure branches return uniform 401 "Not authenticated" so an attacker can't enumerate accounts. `get_current_active_superuser` now checks `role == UserRole.system_admin`. Deleted `POST /login/access-token`, `POST /login/test-token`, `POST /users/signup`, and the old `create_access_token` helper. Mounted the new router in `app/api/main.py` and swapped 7 `is_superuser` call sites in `users.py` / `items.py`. Added a local `_redact_email("alice@example.com") → "ali***@example.com"` helper for login observability — kept inline until a second caller appears.

- **T04** — `get_current_user_ws(websocket)` in `app/api/deps.py` mirrors the HTTP dependency: reads `websocket.cookies.get(settings.SESSION_COOKIE_NAME)`, decodes, loads the user, and on any failure calls `await websocket.close(code=1008, reason=<reason>)` and raises `WebSocketDisconnect`. The four documented reasons — `missing_cookie`, `invalid_token`, `user_not_found`, `user_inactive` — are the sole WS failure inspection surface (no HTTP 401 counterpart possible pre-handshake). Opens a dedicated short-lived `Session(engine)` because FastAPI does not resolve `Depends(get_db)` for WS-parameter helpers called imperatively. `app/api/routes/ws.py` exposes `WS /ws/ping` that calls the auth dep BEFORE `websocket.accept()` (Starlette converts a pre-accept close into a handshake rejection with the supplied code/reason), then echoes `{"pong": str(user.id), "role": user.role.value}`.

- **T05** — Three new test files against the real Postgres test DB (no mocks, per D001/D002):
  - `tests/api/routes/test_auth.py` — 13 cases covering cookie issuance, duplicate email, wrong password (generic 400), unknown email (same uniform 400), missing/tampered/expired cookie (401), valid JWT for deleted user (401, no enumeration), logout idempotency, and a structural redaction test asserting raw emails never appear in the `app.api.routes.auth` log records.
  - `tests/api/routes/test_ws_auth.py` — 6 cases exercising all four reject reasons plus the happy path, each asserting both close code 1008 AND the reason string.
  - `tests/migrations/test_s01_migration.py` — runs alembic downgrade → raw-SQL seed of pre-S01 rows → upgrade → assert role mapping + enum presence + `is_superuser` removed + team tables created; then the reverse for downgrade.
  - Retired `user_authentication_headers` / `superuser_token_headers` fixtures; new `login_cookie_headers`, `superuser_cookies`, `normal_user_cookies` helpers return `httpx.Cookies` and call `client.cookies.clear()` before login to avoid `CookieConflict` from stale jar state. Migrated `test_login.py`, `test_users.py`, `test_items.py`, `tests/crud/test_user.py` off the old Bearer-token flow.

**Migration-test lock hazard discovered and fixed:** The session-scoped autouse `db` fixture in `tests/conftest.py` kept an open SQLAlchemy Session for the whole pytest session, holding an AccessShareLock on the `user` table, which blocked alembic's `DROP COLUMN` indefinitely. Added a module-level autouse `_release_autouse_db_session` in the migration test file that commits / expires / closes the session and calls `engine.dispose()` before alembic runs, plus a second dispose in the teardown of `_restore_head_after`. Captured as MEM016 for future migration suites.

**Post-T05 hardening (this slice):** Tightened the migration test's fixture ordering so `_release_autouse_db_session` is a sibling autouse fixture and `_restore_head_after` depends on it; added a `finally: engine.dispose()` to guarantee no test module inherits a broken pool. The uncommitted diff on `test_s01_migration.py` is this hardening — verification confirms the full suite still passes (76/76) so the fix is a pure robustness improvement.

**Observability:** INFO logs on signup / signup-dup / login-ok / login-failed / login-inactive / logout / ws-auth-reject (with reason) / ws-auth-ok; DEBUG on cookie decode failure class. Emails are redacted (`abc***@example.com`) via `_redact_email`; the raw JWT, raw password, and decoded payload are never logged. Close-code 1008 + reason string is the primary inspection surface for WS rejects.

**Integration closure:** The old Bearer-token path is gone from the application tree (only a handful of `tests/utils/utils.py` helpers still shimmed through to keep test migration a single PR). S02 inherits `TeamMember` / `TeamRole`, the `team` stub, and the cookie-based `get_current_user` dependency without any further wiring needed.


## Verification


**Slice-level verification (from the plan):**

1. `cd backend && uv run pytest tests/api/routes/test_auth.py tests/api/routes/test_ws_auth.py tests/migrations/test_s01_migration.py -v` → **21 passed in 0.82s** (exit 0). Covers every must-have case from the plan: cookie signup/login/logout, /users/me role field, missing/tampered/expired cookie negatives, WS 1008 rejects for all four documented reasons, WS happy-path echoing id + role, migration upgrade+downgrade round trip with pre-seeded is_superuser rows mapping to role.

2. `cd backend && uv run pytest tests/` (full suite) → **76 passed in 3.81s** (exit 0). Confirms the cookie fixture refactor didn't regress `test_users.py` / `test_items.py` / `test_login.py` / `tests/crud/test_user.py`.

3. Route smoke check: `python -c "from app.main import app; routes={r.path for r in app.routes}; assert '/api/v1/auth/signup' in routes and '/api/v1/auth/login' in routes and '/api/v1/auth/logout' in routes; assert '/api/v1/login/access-token' not in routes; assert '/api/v1/ws/ping' in {getattr(r,'path',None) for r in app.routes}"` → exit 0. Confirms new routes mounted, old `/login/access-token` fully removed, WS endpoint registered.

4. Model smoke check: `python -c "from app.models import UserPublic, UserRole, TeamRole, TeamMember, Team; assert 'role' in UserPublic.model_fields; assert UserRole.system_admin.value == 'system_admin'; assert TeamRole.admin.value == 'admin'"` → exit 0. Confirms enum values and UserPublic role field exposure.

5. Migration round-trip: inside the migration test — downgrade to pre-S01, raw-SQL seed (is_superuser TRUE, is_superuser FALSE), upgrade head, assert both users end up with `role=system_admin` and `role=user` respectively, assert `userrole`/`teamrole` enum types present in `pg_type` (lowercase — see MEM020), assert `user.is_superuser` column no longer exists via `information_schema.columns`, assert `team` and `team_member` tables exist. Reverse direction covered by the downgrade test.

**Environment prerequisite confirmed:** Local Postgres on port 55432 (per `.env` `POSTGRES_PORT=55432`). The test suite and alembic both read this; no additional config was needed at run time.

**What is NOT yet verified (out of scope for S01):**

- No frontend UI exercises the cookie flow yet (S04 delivers that).
- No real team membership is created on signup (S02 — personal team bootstrap).
- No system-admin promotion flow UI (S05).


## Requirements Advanced

- R002 — UserRole (user, system_admin) and TeamRole (member, admin) enums exist on User and TeamMember; is_superuser removed everywhere in the API layer (deps.py, users.py, items.py). Full system-admin enforcement UAT still pending S05; TeamRole enforcement still pending S03 invite/membership endpoints.

## Requirements Validated

- R001 — 21/21 slice-level tests pass: cookie signup/login/logout + /users/me role + WS cookie auth for all four reject reasons and happy path. Full suite 76/76. Test files: backend/tests/api/routes/test_auth.py, test_ws_auth.py, tests/migrations/test_s01_migration.py.

## New Requirements Surfaced

None.

## Requirements Invalidated or Re-scoped

None.

## Operational Readiness

None.

## Deviations

Deviations from the plan are minor and documented in task summaries: (1) `initial_data.py` was not modified because the admin seed already moved to `app/core/db.py::init_db` which T01 updated; (2) `app/api/routes/items.py` was not listed in the plan's file inventory but had 4 `is_superuser` references that had to be rewritten — swapped to `role == UserRole.system_admin`; (3) `Token` / `TokenPayload` SQLModel classes in `models.py` are now unreferenced but were left intact as harmless pure data shapes (aggressive removal out of scope); (4) the expired-cookie test hand-forges a JWT with past `exp` rather than monkeypatching `ACCESS_TOKEN_EXPIRE_MINUTES`, so global settings aren't perturbed mid-test; (5) uncommitted hardening to `test_s01_migration.py` tightens the autouse fixture ordering around the engine-pool lock fix (full suite still 76/76).

## Known Limitations

SECRET_KEY in .env is still the default `changethis` — config.py warns in local and raises in staging/production, but any operator running in staging must override it before first boot. SESSION_JWT has no DB-backed revocation list: logout clears only the cookie, so a user who already copied the JWT elsewhere can re-use it until `exp`. If revocation matters for a later milestone, either add a session table or move to short-lived access tokens + a refresh cookie. Email recovery / "forgot password" endpoints from the template still live in `login.py`; they remain functional but weren't exercised in this slice's tests — password-recovery coverage was inherited from the template and reformatted only.

## Follow-ups

["S02 will add real Team columns (name, slug, is_personal) and personal-team bootstrap on signup — `team` stub table and `TeamMember` join already exist.", "S03 will enforce TeamRole on invite/role-management endpoints — the enum and join table are ready.", "S05 will add system-admin panel (GET /admin/teams, promote-system-admin with confirm) — get_current_active_superuser is already rewritten to role == system_admin.", "Consider moving `_redact_email` to a shared util once a second caller (S02 signup flow?) appears.", "Consider a DB-backed session table if revocation-on-logout becomes a requirement; currently logout only clears the client cookie.", "Replace the `changethis` default SECRET_KEY before any non-local deploy — config.py already raises in staging/production, but document the regen step in an ops checklist."]

## Files Created/Modified

None.
