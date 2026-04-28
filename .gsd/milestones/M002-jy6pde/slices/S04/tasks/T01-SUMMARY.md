---
id: T01
parent: S04
milestone: M002-jy6pde
key_files:
  - orchestrator/orchestrator/attach_map.py
  - orchestrator/orchestrator/routes_ws.py
  - orchestrator/tests/unit/test_attach_map.py
  - orchestrator/tests/integration/test_ws_attach_map.py
key_decisions:
  - AttachMap singleton uses lazy-init get_attach_map() rather than lifespan-bound set/clear pair — the map has no external resource so lifespan binding adds no value; lazy init also makes routes_ws.py importable in unit suites where the lifespan never runs, no main.py change required.
  - Refcount (int) instead of bool because cooperative tmux attach lets two simultaneous WS clients share one tmux session — the reaper must treat any positive count as live.
  - unregister drops the key on floor-zero (rather than leaving a 0-valued entry) so live_session_ids stays cheap after many connect/disconnect cycles.
  - Wrapped the entire pump-and-teardown section of session_stream in try/finally so any mid-stream raise (cancellation, unexpected exception) still decrements; register call sits AFTER stream.__aenter__() succeeds so failed exec starts never bump the refcount.
  - Reframed the integration test for the failure path: the plan's 'kill tmux session to provoke __aenter__ failure' framing is incorrect (the docker exec spawns even when the inner command fails) — the test now asserts the stronger contract that register/unregister stay balanced even when the pumps observe immediate exec EOF, which is what the plan actually needed.
duration: 
verification_result: passed
completed_at: 2026-04-25T12:26:44.062Z
blocker_discovered: false
---

# T01: Add orchestrator-side AttachMap (process-local refcount + asyncio.Lock) and instrument the WS bridge to register after exec __aenter__ and unregister in finally — emits attach_registered/attach_unregistered INFO logs, foundation for the S04 reaper's two-phase liveness check.

**Add orchestrator-side AttachMap (process-local refcount + asyncio.Lock) and instrument the WS bridge to register after exec __aenter__ and unregister in finally — emits attach_registered/attach_unregistered INFO logs, foundation for the S04 reaper's two-phase liveness check.**

## What Happened

Created `orchestrator/orchestrator/attach_map.py` with an `AttachMap` class — a `dict[str, int]` guarded by a single `asyncio.Lock`. Public surface matches the task plan: `register(session_id) -> int` (new count after increment), `unregister(session_id) -> int` (new count after decrement, floor zero), `is_attached(session_id) -> bool` (count > 0), `live_session_ids() -> set[str]` (snapshot copy of keys with count > 0). Refcount instead of bool is intentional — cooperative `tmux attach-session` allows two simultaneous WS clients to share one tmux session, and the reaper must see "live" if any client is attached. `unregister` floors at zero AND drops the key entirely so the live_session_ids snapshot stays small after long connect/disconnect cycles.

Module-level singleton uses a lazy-init `get_attach_map()` + `set_attach_map(None)` setter, mirroring `set_registry`/`set_pool` shape from redis_client and volume_store. Chose lazy-init over lifespan-bound because there is no external resource to open or close — the map is pure in-process state. This means importers of routes_ws.py get a valid map even in unit suites where the lifespan never runs (no main.py change required).

Instrumented `routes_ws.py::session_stream` per the plan's discipline: register is called AFTER `await stream.__aenter__()` succeeds (so a failed exec start in the preceding except branches never bumps the refcount), and unregister lives in a finally block wrapping the entire pump-and-teardown section so any mid-stream exception path still decrements. Both calls emit INFO log lines with the new count, matching the slice observability taxonomy from MEM134 (UUIDs only, no PII).

Wrote 11 unit tests in `tests/unit/test_attach_map.py` covering: register/unregister increment+decrement, floor-at-zero on extra unregister (including never-registered ids), is_attached false for never-registered, live_session_ids returns only positive counts and a snapshot copy that cannot mutate internal state, two concurrency tests under asyncio.gather (200x register-then-200x unregister, plus 150x interleaved register/sleep(0)/unregister pairs proving the lock holds under contention), multi-session-id isolation, and the singleton getter/setter round-trip.

Wrote 3 integration tests in `tests/integration/test_ws_attach_map.py` against a fresh ephemeral orchestrator (mirrors the test_sessions_lifecycle.py boot recipe — privileged, DATABASE_URL, vols dir, rshared workspace mount, plus the user_team fixture seeding Postgres so workspace_volume FK holds). Verifies via docker logs grep on the new structured log lines: (1) WS connect emits `attach_registered session_id=<sid> count=1`, (2) WS close emits `attach_unregistered session_id=<sid> count=0` within the 1s polling window the task plan called out, (3) when the inner tmux session is killed before the WS upgrade the pumps fail with exec_eof but register/unregister are still balanced (count goes 1→0 cleanly via the finally block, no refcount leak).

The structural placement of register inside try/finally and after `__aenter__` is verified by the source itself — the wire test confirms the finally block fires on the failure path. Captured MEM165 documenting that `__aenter__` failures only fire on docker-level errors (daemon unreachable, container gone), not on inner-command-not-found cases like a missing tmux session, so the original plan's "provoke an exec-start failure by deleting the tmux session" framing was slightly off — the actual observable failure shape is exec_eof with non-zero exit_code, not docker_exec_start_failed.

The frame protocol from MEM097 is unchanged. The Redis registry shape is untouched. No main.py change needed (lazy init).

## Verification

Ran the slice's verification flow: `docker compose build orchestrator && docker compose up -d --force-recreate orchestrator` (clean build, healthy startup, image_present=True), `uv run pytest tests/unit/test_attach_map.py -v` → 11/11 pass in 0.02s on host venv (SKIP_IMAGE_PULL_ON_BOOT=1, SKIP_PG_POOL_ON_BOOT=1), and `uv run pytest tests/integration/test_ws_attach_map.py -v` from host with .env loaded → 3/3 pass in ~7s against the live compose stack.

Pre-existing failures in test_ws_bridge.py (8/9 tests fail at POST /v1/sessions with 503 ForeignKeyViolationError) are NOT a T01 regression — confirmed by git stash + re-run on the unmodified main branch: same FK error before my changes. That suite predates the S02 workspace_volume FK and has been broken since S02 landed; tracked in MEM167. The one passing test there (test_unknown_session_id_closes_1008) — which exercises the WS path and does not seed a session — confirms the WS bridge is intact under T01's try/finally wrapping.

Observability surfaces verified in live logs: `attach_registered session_id=<uuid> count=1` and `attach_unregistered session_id=<uuid> count=0` lines appear with UUID-only identifiers (MEM134 compliance). No log lines emit emails, full names, or team slugs.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `docker compose build orchestrator` | 0 | ✅ pass | 7000ms |
| 2 | `docker compose up -d --force-recreate orchestrator` | 0 | ✅ pass | 5000ms |
| 3 | `SKIP_IMAGE_PULL_ON_BOOT=1 SKIP_PG_POOL_ON_BOOT=1 uv run pytest tests/unit/test_attach_map.py -v` | 0 | ✅ pass (11/11) | 20ms |
| 4 | `uv run pytest tests/integration/test_ws_attach_map.py -v` | 0 | ✅ pass (3/3) | 6560ms |

## Deviations

Plan suggested provoking a `docker_exec_start_failed` log via tmux session deletion to prove the map stays empty on failure — but `aiodocker.Container.exec(...).start(detach=False).__aenter__()` succeeds even when the inner `tmux attach-session` will exit non-zero, because docker's HTTP upgrade dance completes before the inner command runs. Documented in MEM165. Reframed the test to assert the equivalent (and stronger) contract: register/unregister stay balanced (count goes 1→0 cleanly) even when pumps observe immediate exec EOF, proving the finally block fires on the failure path.

Did not add anything to main.py's lifespan because the singleton lazy-inits on first access. The plan's listed Inputs included main.py but no main.py change was needed.

## Known Issues

test_ws_bridge.py (S01/T04) is broken on main — 8/9 tests fail at POST /v1/sessions with 503 ForeignKeyViolationError. Pre-existing since S02 added the workspace_volume FK; that test file predates the FK constraint. Out of scope for T01 (verified by git stash + retest before adding T01 changes). Tracked in MEM167; recommended fix is to adopt the user_team fixture from test_sessions_lifecycle.py.

## Files Created/Modified

- `orchestrator/orchestrator/attach_map.py`
- `orchestrator/orchestrator/routes_ws.py`
- `orchestrator/tests/unit/test_attach_map.py`
- `orchestrator/tests/integration/test_ws_attach_map.py`
