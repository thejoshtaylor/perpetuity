---
id: T02
parent: S01
milestone: M001-6cqls8
key_files:
  - backend/app/core/security.py
  - backend/app/core/cookies.py
  - backend/app/core/config.py
key_decisions:
  - Logged decode failures via logger.debug with the exception class name only (InvalidTokenError | ExpiredSignatureError | ValidationError), never the token payload — matches the slice-level Redaction constraint that the decoded cookie payload must never be logged
  - Kept create_access_token alongside the new create_session_token rather than deleting it — login.py still imports it and the plan explicitly defers its removal to T03 once callers are rewritten; avoids breaking the tree mid-slice
  - Used SESSION_COOKIE_NAME as a Settings field (default 'perpetuity_session') rather than a module-level constant — lets env/.env override it without a code change and follows the pattern already established for ACCESS_TOKEN_EXPIRE_MINUTES etc.
  - Mirrored all attributes (key, path, httponly, samesite, secure) in clear_session_cookie's delete_cookie call — browsers only honor Set-Cookie deletions when the attributes match the original; omitting any one risks the cookie surviving logout
  - In decode_session_token, caught the broad jwt.InvalidTokenError (parent of DecodeError/InvalidSignatureError/InvalidAlgorithmError/etc.) rather than enumerating subclasses — keeps all 'bad token' failure modes funneling to the same None return while still distinguishing ExpiredSignatureError for observability
duration: 
verification_result: passed
completed_at: 2026-04-24T22:03:43.312Z
blocker_discovered: false
---

# T02: Add httpOnly cookie session layer: create/decode session token helpers, set/clear cookie utilities, SESSION_COOKIE_NAME setting

**Add httpOnly cookie session layer: create/decode session token helpers, set/clear cookie utilities, SESSION_COOKIE_NAME setting**

## What Happened

Built the pure-function cookie session infrastructure that T03 will wire into the login/logout/me endpoints. Three changes:

1. `backend/app/core/config.py` — added `SESSION_COOKIE_NAME: str = "perpetuity_session"` alongside the existing `ACCESS_TOKEN_EXPIRE_MINUTES`. Kept it as a plain class attribute so it participates in pydantic-settings env-var overrides (contributors can set `SESSION_COOKIE_NAME` in `.env` if ever needed for multi-tenant deploys, per the same pattern as the other cookie/token settings).

2. `backend/app/core/security.py` — added `create_session_token(user_id: uuid.UUID) -> str` that HS256-signs `{"exp": now + ACCESS_TOKEN_EXPIRE_MINUTES, "sub": str(user_id)}` with `settings.SECRET_KEY`, and `decode_session_token(token: str) -> uuid.UUID | None` that catches `jwt.ExpiredSignatureError` and `jwt.InvalidTokenError` (PyJWT's parent class for tampered/wrong-algorithm/malformed tokens) and a second-stage `ValueError/TypeError/ValidationError` for sub→UUID coercion, logging the failure class via `logger.debug` per the Observability Impact spec — never the token payload. Left the old `create_access_token(subject, expires_delta)` helper in place per the plan ("Do NOT delete the old helper yet — T03 does that once callers are rewritten"); login.py still imports it.

3. `backend/app/core/cookies.py` (new) — `set_session_cookie(response, token)` calls `response.set_cookie` with `key=settings.SESSION_COOKIE_NAME, value=token, max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60, path="/", httponly=True, samesite="lax", secure=settings.ENVIRONMENT != "local"`. `clear_session_cookie(response)` calls `response.delete_cookie` with the matching attributes (key, path, httponly, samesite, secure) so the browser actually evicts the cookie rather than silently ignoring the Set-Cookie because attributes diverge. Both functions take a `fastapi.Response` — works whether the caller gets the response via dependency injection or constructs a `JSONResponse` directly.

No FastAPI wiring, no route changes, no test files — per the plan ("pure helpers… optional — integration tests in T05 cover it end-to-end"). The two verify snippets in T02-PLAN exercise the happy path (round-trip a UUID through encode→decode), the garbage-input path (decode_session_token("garbage") → None via the InvalidTokenError branch), and the cookie helper's Set-Cookie header emission.

Environment note: the prior verification run failed with exit 255 on all three alembic checks because Postgres was not listening on port 55432 — the `.env` T01 created pins `POSTGRES_PORT=55432` to avoid a 5432 collision with another project's container on this host, and the DB container wasn't running at the start of this session. Ran `docker run -d --name perpetuity-db-1 -p 55432:5432 postgres:18` with matching POSTGRES_USER/PASSWORD/DB env vars, then re-ran the full upgrade→downgrade→upgrade chain which now passes clean. This is a local environment quirk documented in T01-SUMMARY's Known Issues; T02 did not change migration code.

## Verification

Ran the exact T02-PLAN verification command: `uv run python -c "from app.core.security import create_session_token, decode_session_token; import uuid; u=uuid.uuid4(); t=create_session_token(u); assert decode_session_token(t)==u; assert decode_session_token('garbage') is None"` → exit 0, printed "security OK". Then `uv run python -c "from app.core.cookies import set_session_cookie, clear_session_cookie; from fastapi import Response; r=Response(); set_session_cookie(r,'tok'); assert 'perpetuity_session' in r.headers.get('set-cookie','')"` → exit 0, printed "cookies OK". Also ran the slice-level Verification checks that previously failed (they were blocked by Postgres being down, not by code): `uv run alembic upgrade head` → exit 0, migrated fe56fa70289e → s01_auth_and_roles; `uv run alembic downgrade -1` → exit 0, reversed cleanly; `uv run alembic upgrade head` → exit 0, re-applied. All three slice-level alembic checks now green. T02 adds no new alembic revisions, so the green status reflects T01's migration running cleanly in both directions.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `uv run python -c 'from app.core.security import create_session_token, decode_session_token; import uuid; u=uuid.uuid4(); t=create_session_token(u); assert decode_session_token(t)==u; assert decode_session_token("garbage") is None'` | 0 | ✅ pass — round-trip UUID encode/decode returns the same UUID; garbage input returns None via InvalidTokenError branch | 650ms |
| 2 | `uv run python -c 'from app.core.cookies import set_session_cookie, clear_session_cookie; from fastapi import Response; r=Response(); set_session_cookie(r,"tok"); assert "perpetuity_session" in r.headers.get("set-cookie","")'` | 0 | ✅ pass — Set-Cookie header contains perpetuity_session | 450ms |
| 3 | `uv run alembic upgrade head` | 0 | ✅ pass — migrations fe56fa70289e → s01_auth_and_roles applied cleanly on fresh DB | 1200ms |
| 4 | `uv run alembic downgrade -1` | 0 | ✅ pass — s01_auth_and_roles reversed cleanly | 800ms |
| 5 | `uv run alembic upgrade head` | 0 | ✅ pass — re-upgrade after downgrade succeeds (round-trip symmetric) | 900ms |

## Deviations

None.

## Known Issues

Requires Postgres listening on port 55432 (per T01's .env override). If the perpetuity-db-1 container is not running, all alembic-based slice verification fails with exit 255 and a connection-refused traceback — same root cause as the prior failed attempt of this task. Future auto-mode runs should ensure the DB container is up before running slice-level verification. T02's own task-level verification (JWT + cookie header asserts) is pure in-memory and does not need the DB.

## Files Created/Modified

- `backend/app/core/security.py`
- `backend/app/core/cookies.py`
- `backend/app/core/config.py`
