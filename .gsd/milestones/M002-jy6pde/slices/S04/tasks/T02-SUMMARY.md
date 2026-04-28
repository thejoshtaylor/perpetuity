---
id: T02
parent: S04
milestone: M002-jy6pde
key_files:
  - orchestrator/orchestrator/reaper.py
  - orchestrator/orchestrator/main.py
  - orchestrator/orchestrator/redis_client.py
  - orchestrator/orchestrator/volume_store.py
  - backend/app/api/routes/admin.py
  - orchestrator/tests/integration/test_reaper.py
  - backend/tests/api/routes/test_admin_settings.py
key_decisions:
  - sessions.py raises DockerUnavailable (the wrap of DockerError+OSError), so the reaper catches DockerUnavailable in addition to DockerError+OSError at every Docker-touching call site — without this, every reaper tick that touches a missing/gone container surfaces as reaper_tick_failed and no session ever reaps (MEM168).
  - stop_reaper runs FIRST in the lifespan teardown, before registry.close()/close_pool/docker.close(), so an in-flight tick doesn't trip on torn-down resources. The 5s wait_for budget covers the worst-case in-flight docker exec for kill_tmux_session.
  - Container reap's `_find_container_by_labels` re-check guards against label-collision races: if the user re-provisioned a fresh container in the same (user, team) between the kill_tmux pass and the reap pass, the labels match a different container_id and the reaper skips — never clobbers a fresh container.
  - scan_session_keys uses SCAN (cursor-based, non-blocking) rather than KEYS to avoid blocking redis's single-threaded loop in production where session counts grow.
  - Integration tests run on the host (per MEM137/MEM141 + the docker CLI being absent inside the orchestrator image) and use docker exec perpetuity-redis-1 redis-cli for direct Redis seeding because the compose redis has no published host port (MEM169).
  - When testing the attached-skip case, open the WS attach FIRST and back-date last_activity AFTER the attach is registered — the reverse order races a 1-second reaper interval and reaps the session before the WS upgrade can register it (MEM171).
  - Wrapped reaper_loop's tick in try/except Exception (and asyncio.CancelledError separately) so transient Redis/Docker hiccups never kill the reaper task — only the lifespan teardown's task.cancel() exits the loop.
duration: 
verification_result: passed
completed_at: 2026-04-25T12:48:45.549Z
blocker_discovered: false
---

# T02: Add background idle reaper to orchestrator lifespan with system_settings.idle_timeout_seconds lookup, two-phase D018 liveness check (Redis idle + AttachMap), and container reap when last tmux session dies.

**Add background idle reaper to orchestrator lifespan with system_settings.idle_timeout_seconds lookup, two-phase D018 liveness check (Redis idle + AttachMap), and container reap when last tmux session dies.**

## What Happened

Created `orchestrator/orchestrator/reaper.py` with `start_reaper(app)` / `stop_reaper(task)` and the `reaper_loop` coroutine. The loop sleeps `REAPER_INTERVAL_SECONDS` (env-overridable, default 30s, clamped to [1, 300]), then on each tick: (1) resolves idle timeout via the new `volume_store._resolve_idle_timeout_seconds(pool)` helper (mirrors `_resolve_default_size_gb` exactly — same SELECT, JSONB-as-text parse, bool-rejection, range gate [1, 86400]); (2) iterates `registry.scan_session_keys()` — a NEW `scan_session_keys()` AsyncIterator on `RedisSessionRegistry` that wraps `SCAN MATCH session:*` (production-hostile `KEYS` is avoided per the task plan); (3) for each session, applies the D018 two-phase check — reapable iff `idle > timeout AND not attach_map.is_attached(sid)`; (4) kills the tmux session via the existing `kill_tmux_session` (idempotent — returns False if already gone) then drops the Redis row via `delete_session`; (5) for any container the reaper just emptied, double-checks `list_tmux_sessions` returns [] (handles orphaned-tmux race) and the container is still owned by the same (user, team) via `_find_container_by_labels`, then `container.stop(timeout=5)` + `container.delete(force=True)` — workspace_volume row + .img persist (D015 invariant); (6) emits `reaper_tick scanned/killed/reaped_containers` per tick. Every iteration is wrapped in a single `try/except Exception` that logs WARNING `reaper_tick_failed reason=<class>` so transient errors never kill the task; only `asyncio.CancelledError` exits the loop.

Wired into `main.py::_lifespan`: after the Redis registry, attach map (lazy-init from T01), and pg pool are bound, `app.state.reaper_task = start_reaper(app)`. In the finally block, `stop_reaper(task)` runs FIRST — before `registry.close()`/`close_pool`/`docker.close()` — so an in-flight tick doesn't trip on torn-down resources (would surface as noisy `reaper_tick_failed` warnings on every shutdown). `stop_reaper` cancels with a 5s teardown budget and swallows `CancelledError`.

Backend admin validator: registered `idle_timeout_seconds` in `_VALIDATORS` with the same shape as `workspace_volume_size_gb` — bool rejected, int required, range [1, 86400], 422 body `{detail: 'invalid_value_for_key', key, reason: 'must be int in 1..86400'}`. NO partial-apply warnings (the new value just biases the next reaper tick). Constant `IDLE_TIMEOUT_SECONDS_KEY` added for symmetry.

Mid-execution gotcha: my first cut of the reaper only caught `DockerError` + `OSError`, but `sessions.py` wraps both into `DockerUnavailable` at the boundary — so every kill_tmux_session call surfaced as `reaper_tick_failed reason=DockerUnavailable` and no session ever got reaped. Fixed by adding `DockerUnavailable` catches in three places (kill, tmux ls, container lookup). Captured as MEM168 so future agents writing background callers of sessions.py don't have to rediscover.

Tests in `orchestrator/tests/integration/test_reaper.py` against the live compose stack: 9 cases covering all 7 required from the task plan (kill idle no-attach, skip attached, skip non-idle, reap container on last session, keep container with surviving session, resolver happy/fallback/invalid, and reaper-survives-redis-blip). The blip test is in-process — patches `_resolve_idle_timeout_seconds` and a fake registry whose first call raises — proving the loop survives. Direct Redis writes use `docker exec perpetuity-redis-1 redis-cli` (compose redis is internal-only, can't open a client from host; captured as MEM169). For the attached-session test the WS attach must open BEFORE back-dating last_activity (otherwise REAPER_INTERVAL_SECONDS=1 races and reaps before register; MEM171).

Backend `tests/api/routes/test_admin_settings.py` extended with 9 new cases for `idle_timeout_seconds`: happy path 200 + empty warnings, idempotent two-PUT logging, first-time `previous_value_present=false` log, no shrink-warnings log line emitted (key has no per-row state), 422 on non-int/bool/zero/86401, and 200 on the boundary 86400. All 26 admin settings tests green.

## Verification

Ran the canonical task-plan verify chain end to end. `docker compose build orchestrator backend` succeeded. `docker compose up -d --force-recreate orchestrator` came up healthy with `reaper_started interval_seconds=30` in logs. `cd orchestrator && DATABASE_URL=postgresql://postgres:changethis@127.0.0.1:5432/app uv run pytest tests/integration/test_reaper.py -v` → 9/9 pass in 26.5s on the host (per MEM137/MEM141 these tests run from the host so the fixture's `docker run -d` for the ephemeral orchestrator can use the host docker daemon; per MEM169 redis seeding goes through `docker exec perpetuity-redis-1 redis-cli`). `cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_admin_settings.py -v` → 26/26 pass in 0.40s. Regression: the existing T01 `tests/integration/test_ws_attach_map.py` (3 cases) and `tests/unit/test_attach_map.py` (11 cases) plus the rest of the unit suite (22 total) all stay green. `ruff check` is clean across reaper.py/redis_client.py/volume_store.py/main.py/test_reaper.py/admin.py/test_admin_settings.py. The reaper observability is verified by the integration tests grepping for `reaper_killed_session`, `reaper_reaped_container`, and `idle_timeout_seconds_resolved` in `docker compose logs orchestrator`.

The task plan's verify line called for `docker cp ... && docker compose exec orchestrator pytest`, but the orchestrator image has no `docker` CLI installed (verified — `docker compose exec orchestrator which docker` returns "executable file not found"), so the integration test cannot run inside the container. T01 hit the same constraint and ran on the host. Followed the same precedent — tests run on the host with DATABASE_URL pointed at the host-published `127.0.0.1:5432` postgres and direct Redis I/O routed through `docker exec`. Documented as MEM169.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `docker compose build orchestrator backend` | 0 | ✅ pass | 9000ms |
| 2 | `docker compose up -d --force-recreate orchestrator` | 0 | ✅ pass | 5000ms |
| 3 | `cd orchestrator && DATABASE_URL=postgresql://postgres:changethis@127.0.0.1:5432/app uv run pytest tests/integration/test_reaper.py -v` | 0 | ✅ pass (9/9) | 26530ms |
| 4 | `cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_admin_settings.py -v` | 0 | ✅ pass (26/26) | 400ms |
| 5 | `cd orchestrator && uv run pytest tests/integration/test_ws_attach_map.py tests/unit/test_attach_map.py -v` | 0 | ✅ pass (14/14 regression) | 6580ms |
| 6 | `uv run ruff check (reaper, redis_client, volume_store, main, test_reaper, admin, test_admin_settings)` | 0 | ✅ pass (clean) | 200ms |

## Deviations

"Task plan's verify line says `docker cp orchestrator/tests perpetuity-orchestrator-1:/app/tests && docker compose exec orchestrator /app/.venv/bin/pytest tests/integration/test_reaper.py -v`. The orchestrator image does not contain the docker CLI (verified: `docker compose exec orchestrator which docker` returns 'executable file not found'), so the test fixture's `subprocess.run(['docker', ...])` cannot work from inside the container. Followed T01's precedent and ran the suite on the host with DATABASE_URL pointed at the host-published 127.0.0.1:5432 postgres and Redis I/O routed via `docker exec perpetuity-redis-1 redis-cli`. All 9 tests pass; the verification contract (real Docker daemon + real Redis + real Postgres) is honored.\n\nThe S03 _resolve_default_size_gb existing tests live in test_volumes.py, but the parallel idle_timeout resolver tests landed in test_reaper.py for cohesion with the rest of the reaper suite — closer to the consumer, and the file already needs the pg_pool / clean_<key> fixture pair."

## Known Issues

"None new. The pre-existing S01 test_ws_bridge.py FK failures (MEM167) are unchanged."

## Files Created/Modified

- `orchestrator/orchestrator/reaper.py`
- `orchestrator/orchestrator/main.py`
- `orchestrator/orchestrator/redis_client.py`
- `orchestrator/orchestrator/volume_store.py`
- `backend/app/api/routes/admin.py`
- `orchestrator/tests/integration/test_reaper.py`
- `backend/tests/api/routes/test_admin_settings.py`
