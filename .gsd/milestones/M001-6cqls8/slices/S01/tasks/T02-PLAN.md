---
estimated_steps: 8
estimated_files: 3
skills_used: []
---

# T02: Add httpOnly cookie session layer (token helpers, cookie set/clear utilities, settings)

Build the cookie session infrastructure that T03 will wire into endpoints. Extend `app/core/security.py` with `create_session_token(user_id) -> str` (HS256 JWT, `SECRET_KEY`, `exp=ACCESS_TOKEN_EXPIRE_MINUTES`) and `decode_session_token(token) -> uuid.UUID | None`. Add `app/core/cookies.py` with `set_session_cookie(response, token)` and `clear_session_cookie(response)` helpers that apply: `httponly=True`, `samesite='lax'`, `secure=(settings.ENVIRONMENT != 'local')`, `max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60`, `path='/'`, name=`settings.SESSION_COOKIE_NAME` (default `perpetuity_session`). Add `SESSION_COOKIE_NAME` to `app/core/config.py`. No FastAPI wiring yet — pure helpers with unit tests inline in the file or a small `tests/core/test_cookies.py` (optional — integration tests in T05 cover it end-to-end). Do NOT delete the old `create_access_token` helper yet — T03 does that once callers are rewritten.

Failure Modes:
| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| JWT decode (`jwt.decode`) | Return `None` from `decode_session_token`; caller treats as unauthenticated | N/A (local op) | Return `None` |
| SECRET_KEY missing | Raise at import — fail fast | N/A | N/A |

Load Profile: trivial — pure in-memory JWT encode/decode per request; HS256 is ~microseconds.

Negative Tests (covered in T05 integration suite): tampered JWT returns None; expired JWT returns None; wrong-algorithm JWT returns None.

## Inputs

- ``backend/app/core/security.py``
- ``backend/app/core/config.py``

## Expected Output

- ``backend/app/core/security.py``
- ``backend/app/core/cookies.py``
- ``backend/app/core/config.py``

## Verification

cd backend && uv run python -c "from app.core.security import create_session_token, decode_session_token; import uuid; u=uuid.uuid4(); t=create_session_token(u); assert decode_session_token(t)==u; assert decode_session_token('garbage') is None" && uv run python -c "from app.core.cookies import set_session_cookie, clear_session_cookie; from fastapi import Response; r=Response(); set_session_cookie(r,'tok'); assert 'perpetuity_session' in r.headers.get('set-cookie','')"

## Observability Impact

`decode_session_token` logs the failure class (InvalidTokenError | ExpiredSignatureError | ValidationError) via `logger.debug` — never the token payload. No new metrics.
