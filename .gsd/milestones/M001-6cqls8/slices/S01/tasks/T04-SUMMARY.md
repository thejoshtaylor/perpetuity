---
id: T04
parent: S01
milestone: M001-6cqls8
key_files:
  - backend/app/api/deps.py
  - backend/app/api/routes/ws.py
  - backend/app/api/main.py
key_decisions:
  - Opened a short-lived Session(engine) inside get_current_user_ws rather than using FastAPI's Depends(get_db) — FastAPI does not resolve Depends for helpers invoked imperatively from a WebSocket endpoint, and the plan's load profile (1 DB query per connection) makes a dedicated session trivial.
  - Called await websocket.close() BEFORE websocket.accept() on auth failure — Starlette turns a pre-accept close into a handshake rejection with the given code/reason, which is what the plan's close(1008, reason=...) contract demands for each failure mode.
duration: 
verification_result: passed
completed_at: 2026-04-24T22:11:47.575Z
blocker_discovered: false
---

# T04: Add get_current_user_ws cookie-auth dep and mount /api/v1/ws/ping echoing user id + role

**Add get_current_user_ws cookie-auth dep and mount /api/v1/ws/ping echoing user id + role**

## What Happened

Added `get_current_user_ws(websocket)` to `backend/app/api/deps.py`. It reads the session cookie via `websocket.cookies.get(settings.SESSION_COOKIE_NAME)` — matching the MEM013 convention already used by `get_current_user` so SESSION_COOKIE_NAME env overrides take effect uniformly across HTTP and WS. On each failure branch it calls `await websocket.close(code=1008, reason=<reason>)` and raises `WebSocketDisconnect`, using one of the four documented reasons: `missing_cookie`, `invalid_token`, `user_not_found`, `user_inactive`. Opened its own short-lived `Session(engine)` rather than taking a FastAPI `Depends(get_db)` session — FastAPI does not resolve `Depends` for WebSocket-parameter helpers invoked imperatively, and the plan explicitly says this is a single lookup per connection.

Created `backend/app/api/routes/ws.py` with `WS /ws/ping`. The handler calls `get_current_user_ws` FIRST (so rejects happen before the socket is accepted), then calls `websocket.accept()`, awaits one client message, and echoes `{'pong': str(user.id), 'role': user.role.value}`. `WebSocketDisconnect` from the auth helper is swallowed quietly so the close frame the helper already wrote is the one the client sees. Mounted the router in `backend/app/api/main.py` with no API_V1 prefix change (the existing `settings.API_V1_STR` prefix on `api_router` yields the target path `/api/v1/ws/ping`).

Observability implemented per the Observability Impact section: WS rejects log at INFO with `ws_auth_reject reason=<reason>`; accepts log at DEBUG with `ws_auth_ok user_id=<id>`. No token bytes, no cookie payloads are logged. The close-code/reason pair is the primary inspection surface for failures, per the slice-level failure-visibility constraint.

Negative tests (no-cookie, tampered-cookie, valid-cookie) are explicitly owned by T05 per the task plan, so I did not add integration tests here.

## Verification

Ran the task plan's verification block. (1) `cd backend && uv run python -c "from app.main import app; paths={getattr(r,'path',None) for r in app.routes}; assert '/api/v1/ws/ping' in paths"` exited 0 — the route is registered at the expected prefixed path. (2) `cd backend && uv run python -c "from app.api.deps import get_current_user_ws; assert callable(get_current_user_ws)"` exited 0 — the dependency helper is importable and callable. Both checks pass. Negative/positive WS scenarios (missing cookie, tampered cookie, valid cookie) are explicitly T05's scope per the task plan and are not re-verified here.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && uv run python -c "from app.main import app; paths={getattr(r,'path',None) for r in app.routes}; assert '/api/v1/ws/ping' in paths"` | 0 | pass | 2000ms |
| 2 | `cd backend && uv run python -c "from app.api.deps import get_current_user_ws; assert callable(get_current_user_ws)"` | 0 | pass | 1500ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `backend/app/api/deps.py`
- `backend/app/api/routes/ws.py`
- `backend/app/api/main.py`
