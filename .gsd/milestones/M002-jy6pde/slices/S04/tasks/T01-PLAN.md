---
estimated_steps: 5
estimated_files: 4
skills_used: []
---

# T01: Add orchestrator-side live-attach map + instrument WS bridge to register/unregister on every attach/detach

Add `orchestrator/orchestrator/attach_map.py` — a process-local async-safe counter `session_id -> int` (refcount, not bool, because the same session_id can in principle have two simultaneous WS attaches sharing one tmux session via cooperative attach; the reaper must see >0 if any are live). Public surface: `register(session_id) -> int` (returns new count), `unregister(session_id) -> int` (returns new count, decrements floor-zero), `is_attached(session_id) -> bool` (count > 0), `live_session_ids() -> set[str]` (snapshot of all keys with count > 0 — used by the reaper). Internally backed by an `asyncio.Lock` plus a `dict[str, int]`. No Redis touch — this is INTENTIONALLY in-process and will not survive an orchestrator restart, which is correct: an orchestrator restart drops every WS attach because the exec stream dies (D012 says tmux survives, but the attach does not) so a restart-time empty map is the right truth.

Instrument `orchestrator/orchestrator/routes_ws.py::session_stream`: after the exec stream `__aenter__` succeeds (i.e. AFTER the attach frame is sent and we've actually entered the bidirectional pump phase), call `register(session_id)` and emit INFO `attach_registered session_id=<sid> count=<n>`. In the existing `finally` cleanup (the same scope where the exec stream is closed), call `unregister(session_id)` and emit INFO `attach_unregistered session_id=<sid> count=<n>` regardless of which pump finished first. Place register/unregister inside try/finally so a mid-stream exception path still decrements. Do NOT register before exec start — a failed exec start should not leave a stale entry the reaper treats as live.

Expose a module-level singleton `_ATTACH_MAP` plus a `get_attach_map()` accessor (mirrors the redis_client pattern). The reaper in T02 imports `get_attach_map` and calls `is_attached`. Tests import the same accessor and inject a fresh map per-test via a `set_attach_map` setter — mirrors `set_registry`/`set_pool`.

Unit test `orchestrator/tests/unit/test_attach_map.py`: register/unregister increments+decrements, floor-zero on extra unregister, concurrent register/unregister under asyncio.gather preserves invariant, `live_session_ids` returns only keys with count>0, `is_attached` is False for never-registered. Integration smoke `orchestrator/tests/integration/test_ws_attach_map.py`: attach a WS to a real session → `is_attached(sid) is True` while connected → close WS → poll up to 1s → `is_attached(sid) is False`. Also assert: an exec-start failure path does NOT leave the map registering the session (provoke by deleting the tmux session via direct docker exec right before the WS upgrade — the attach pumps will fail; teardown must be clean).

Do NOT change the WS frame protocol (MEM097 lock). Do NOT touch the Redis registry shape — this map is orthogonal.

## Inputs

- ``orchestrator/orchestrator/routes_ws.py``
- ``orchestrator/orchestrator/redis_client.py``
- ``orchestrator/orchestrator/main.py``
- ``orchestrator/tests/integration/test_ws_bridge.py``

## Expected Output

- ``orchestrator/orchestrator/attach_map.py``
- ``orchestrator/orchestrator/routes_ws.py``
- ``orchestrator/tests/unit/test_attach_map.py``
- ``orchestrator/tests/integration/test_ws_attach_map.py``

## Verification

docker compose build orchestrator && docker compose up -d --force-recreate orchestrator && cd orchestrator && uv run pytest tests/unit/test_attach_map.py -v && docker cp tests perpetuity-orchestrator-1:/app/tests && docker compose exec orchestrator /app/.venv/bin/pytest tests/integration/test_ws_attach_map.py tests/integration/test_ws_bridge.py -v

## Observability Impact

Adds INFO `attach_registered session_id=<uuid> count=<n>` after every successful WS attach (post-exec-start, post-attach-frame) and INFO `attach_unregistered session_id=<uuid> count=<n>` in the WS teardown finally — emitted regardless of which side closed first. Both UUID-only (MEM134). The map itself is process-local — it cannot leak any data to Redis or Postgres. Orchestrator-restart drops the map and that is intentional: every live WS attach also dies on restart, so empty-map and zero-attaches are the same truth at boot.
