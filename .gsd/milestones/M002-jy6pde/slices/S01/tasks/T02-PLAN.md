---
estimated_steps: 1
estimated_files: 8
skills_used: []
---

# T02: Orchestrator core: shared-secret auth (two-key), Redis client, image-pull-on-boot

Wire the orchestrator's foundational machinery that every later orchestrator task depends on. Implement: (1) shared-secret middleware in `orchestrator/orchestrator/auth.py` that reads `X-Orchestrator-Key` header on HTTP requests and the `Sec-WebSocket-Protocol` header (or query string `?key=`) for WS — tries `ORCHESTRATOR_API_KEY` first, then `ORCHESTRATOR_API_KEY_PREVIOUS` if set; on mismatch returns 401 for HTTP and closes 1008 reason=`unauthorized` for WS (per D016). Decision (auto-mode): use a query param `?key=` for WS rather than a sub-protocol because backend→orchestrator hop is server-to-server and easier to wire with httpx_ws/websockets clients. (2) Redis client wrapper in `orchestrator/orchestrator/redis_client.py` using `redis.asyncio` — exposes `set_session(session_id, data)`, `get_session(session_id)`, `update_last_activity(session_id)`, `delete_session(session_id)`, `list_sessions(user_id, team_id)`. Redis unreachable → `RedisUnavailable` exception that the FastAPI exception handler maps to 503 (D013: no in-memory fallback). (3) Image-pull-on-boot in `orchestrator/orchestrator/main.py` startup hook: connects to Docker via `aiodocker`, pulls `perpetuity/workspace:latest` (image tag from env `WORKSPACE_IMAGE`, defaults to `perpetuity/workspace:latest`); on failure emits ERROR `image_pull_failed` and exits with code 1 — boot blocker per D018/CONTEXT error-handling section. (4) Add `/v1/health` route now reports `{status:'ok', image_present: true}` after the pull succeeds — health flips to false if Docker becomes unreachable. ASSUMPTION (auto-mode): WS auth header strategy is `?key=<value>` query string. Documented in `orchestrator/orchestrator/auth.py` docstring so a follow-up can switch to subprotocol if a security audit prefers it. ASSUMPTION: `ORCHESTRATOR_API_KEY` is required at boot; missing → exit 1.

## Inputs

- ``orchestrator/orchestrator/main.py``
- ``orchestrator/orchestrator/config.py``
- ``.gsd/DECISIONS.md` (D013, D016, D018)`

## Expected Output

- ``orchestrator/orchestrator/auth.py``
- ``orchestrator/orchestrator/redis_client.py``
- ``orchestrator/orchestrator/main.py` (modified — adds startup pull + health update)`
- ``orchestrator/orchestrator/config.py` (modified — adds key/redis settings)`
- ``orchestrator/orchestrator/errors.py``
- ``orchestrator/tests/unit/test_auth.py``
- ``orchestrator/tests/integration/test_image_pull.py``
- ``orchestrator/tests/integration/test_redis_client.py``

## Verification

Unit: `orchestrator/tests/unit/test_auth.py` covers (a) HTTP request with correct `X-Orchestrator-Key` → 200; (b) wrong key → 401; (c) `ORCHESTRATOR_API_KEY_PREVIOUS` set, request with previous key → 200 (two-key acceptance); (d) WS with `?key=current` → accept; (e) WS with `?key=wrong` → close(1008, reason='unauthorized'). Integration: `test_redis_client.py` against the real redis container — set/get/update/delete/list round-trip; with redis killed mid-test, asserts `RedisUnavailable` raised. Integration: `test_image_pull.py` boots a fresh orchestrator container with `WORKSPACE_IMAGE=perpetuity/workspace:test` — asserts log line `image_pull_ok`; then with `WORKSPACE_IMAGE=does-not-exist:nope` asserts orchestrator exits 1 and log line `image_pull_failed`. Run all orchestrator tests with `cd orchestrator && uv run pytest`.

## Observability Impact

INFO `image_pull_ok image=perpetuity/workspace:test`. ERROR `image_pull_failed image=... reason=...` on boot failure (then exit 1). WARNING `redis_unreachable` on connect failure (after which 503 is returned). ERROR `orchestrator_ws_unauthorized key_prefix=<first4chars>...` — never log full key. All exception paths include a request_id (UUID) for correlation.
