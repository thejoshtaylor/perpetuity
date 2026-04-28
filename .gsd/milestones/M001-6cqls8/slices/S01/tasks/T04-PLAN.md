---
estimated_steps: 13
estimated_files: 3
skills_used: []
---

# T04: Add WS cookie auth dependency and minimal /ws/ping endpoint

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

## Inputs

- ``backend/app/api/deps.py``
- ``backend/app/api/main.py``
- ``backend/app/core/cookies.py``
- ``backend/app/core/security.py``
- ``backend/app/models.py``

## Expected Output

- ``backend/app/api/routes/ws.py``
- ``backend/app/api/deps.py``
- ``backend/app/api/main.py``

## Verification

cd backend && uv run python -c "from app.main import app; paths={getattr(r,'path',None) for r in app.routes}; assert '/api/v1/ws/ping' in paths" && uv run python -c "from app.api.deps import get_current_user_ws; assert callable(get_current_user_ws)"

## Observability Impact

WS rejects log at INFO: `ws_auth_reject reason=missing_cookie|invalid_token|user_inactive|user_not_found`. Accepts log at DEBUG: `ws_auth_ok user_id=<redacted>`. Close code 1008 + reason string is the primary inspection surface for WS failures.
