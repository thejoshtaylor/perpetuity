---
estimated_steps: 22
estimated_files: 7
skills_used: []
---

# T02: Add background idle reaper to orchestrator lifespan + system_settings.idle_timeout_seconds lookup + container reap on last-tmux-killed

Add `orchestrator/orchestrator/reaper.py` exposing `start_reaper(app) -> asyncio.Task` and `stop_reaper(task: asyncio.Task) -> None`. The reaper is launched from `_lifespan` in `main.py` after the Redis registry, attach map, and pg pool are bound; cancelled on lifespan teardown before those resources close.

Reaper loop (run forever until cancelled):
  1. Sleep `reaper_interval_seconds` (env-overridable via `REAPER_INTERVAL_SECONDS` for tests; default 30s; clamp to [1, 300]).
  2. Resolve idle timeout: call new helper `volume_store._resolve_idle_timeout_seconds(pool)` which mirrors `_resolve_default_size_gb` exactly (SELECT value FROM system_settings WHERE key='idle_timeout_seconds'; validate `isinstance(v, int) and 1 <= v <= 86400`; on miss/invalid/error log WARNING `system_settings_lookup_failed key=idle_timeout_seconds reason=<class>` and fall back to `settings.idle_timeout_seconds`). Emit INFO `idle_timeout_seconds_resolved source=<system_settings|fallback> value=<n>` once per tick.
  3. Build the work list: scan Redis via `redis_client.scan_session_keys()` — a NEW thin wrapper added to `redis_client.py` that does `SCAN MATCH session:*` and yields `(session_id, record)` tuples. Do NOT use `KEYS` (production-hostile blocking scan). For each `(sid, record)`, compute `idle = now - record['last_activity']`. The session is reapable iff `idle > idle_timeout_seconds AND not attach_map.is_attached(sid)`. Two-phase check is the entire correctness story (D018) — never skip the attach-map half.
  4. For each reapable session: call `kill_tmux_session(docker, container_id, sid)` (idempotent — already returns False if missing). Then `delete_session(sid)` against Redis to drop the record + index. Emit INFO `reaper_killed_session session_id=<uuid> reason=idle_no_attach`.
  5. After all kills for this tick, group surviving Redis sessions by `container_id`. For each container_id that the reaper killed at least one session on this tick AND that has zero remaining Redis sessions referencing it, double-check via `list_tmux_sessions(docker, container_id)` (already in S01) — if tmux ls reports zero sessions, stop+remove the container with a label-scoped lookup `_find_container_by_labels(docker, user_id, team_id)` (S01 helper). Container removal: `await container.stop(timeout=5)` then `await container.delete(force=True)`. Emit INFO `reaper_reaped_container container_id=<short> user_id=<uuid> team_id=<uuid> reason=last_session_killed`. The workspace_volume row + the underlying loopback .img are NOT touched — the next POST /api/v1/sessions will refind the row (D015 invariant) and re-mount the .img via the existing `ensure_volume_for` (idempotent on already-mounted by S02 contract).
  6. Emit INFO `reaper_tick scanned=<n> killed=<n> reaped_containers=<n>` once per tick.

Failure handling: every iteration is wrapped in `try/except Exception as exc` that logs WARNING `reaper_tick_failed reason=<class>` and continues — a transient Redis/Docker error must NOT kill the reaper task. asyncio.CancelledError propagates (lifespan teardown is the only legitimate cancel).

Admin validator: register `idle_timeout_seconds` in `backend/app/api/routes/admin.py::_VALIDATORS` with the same shape as `_validate_workspace_volume_size_gb` (reject bool, require int in [1, 86400]; error body `{detail: 'invalid_value_for_key', key, reason: 'must be int in 1..86400'}`). NO partial-apply warnings for this key — the new value just biases the next reaper tick. Add `IDLE_TIMEOUT_SECONDS_KEY = 'idle_timeout_seconds'` constant in admin.py for symmetry with `WORKSPACE_VOLUME_SIZE_GB_KEY`.

Wire into lifespan: in `main.py::_lifespan`, after `set_registry(registry)` and `set_pool(pg_pool)`, store the attach map singleton via the T01 setter and `task = asyncio.create_task(reaper_loop(app))` (or call `start_reaper(app)`). Save the task handle as `app.state.reaper_task`. In the finally block, before `set_registry(None)`, call `stop_reaper(app.state.reaper_task)` which `task.cancel()`s and `await`s the task with a 5s timeout, swallowing CancelledError.

Tests:
  - `orchestrator/tests/integration/test_reaper.py` against real Docker + real Redis (no real backend needed). Test cases (each test creates its own ephemeral orchestrator container with `REAPER_INTERVAL_SECONDS=1`):
    1. `test_reaper_kills_idle_session_with_no_attach`: insert a Redis session record with `last_activity = time.time() - 60`, `idle_timeout_seconds=5`, no attach in the map. Wait 3s. Assert: tmux kill was issued (mock-or-real container — real container preferred), Redis session deleted.
    2. `test_reaper_skips_attached_session`: same as above but register the session in the attach map. Wait 3s. Assert: session still in Redis, tmux session still alive.
    3. `test_reaper_skips_non_idle_session`: `last_activity = now`, no attach. Assert: session untouched after 3s.
    4. `test_reaper_reaps_container_when_last_session_killed`: provision a real container with one tmux session, kill the WS attach, set last_activity in the past. Wait. Assert: container removed via `docker ps -a --filter label=...` returns empty.
    5. `test_reaper_keeps_container_with_surviving_session`: provision container with two tmux sessions; mark one idle and unattached, the other recently-active. Wait. Assert: only the idle one is killed, container still running.
    6. `test_resolve_idle_timeout_seconds_reads_system_settings` + fallback + invalid-value test (mirrors S03's S04/T03 tests). Insert system_settings row idle_timeout_seconds=7 → helper returns 7. Insert value='banana' → fallback + WARNING.
    7. `test_reaper_survives_redis_blip`: stop the redis container mid-tick (no — just patch `scan_session_keys` to raise once), assert WARNING logged and reaper still running on next tick.
  - `backend/tests/api/routes/test_admin_settings.py`: extend with a happy-path PUT idle_timeout_seconds=120 returning 200 + previous_value_present semantics + 422 on bool/string/range — same gate matrix as workspace_volume_size_gb but no warnings field.

Negative tests in the task plan (Q7): zero-session container (reaper finds empty Redis but container exists from a partial-cleanup race) — the reaper SHOULD reap it because `list_tmux_sessions` returns []. Stale attach-map entry (orchestrator restart loses the map but Redis still has the session record marked recent) — the reaper waits the timeout out, then reaps. Multiple tmux sessions for one container, one reaped — container survives. All covered by the test cases above.

## Inputs

- ``orchestrator/orchestrator/main.py``
- ``orchestrator/orchestrator/redis_client.py``
- ``orchestrator/orchestrator/sessions.py``
- ``orchestrator/orchestrator/volume_store.py``
- ``orchestrator/orchestrator/attach_map.py``
- ``backend/app/api/routes/admin.py``

## Expected Output

- ``orchestrator/orchestrator/reaper.py``
- ``orchestrator/orchestrator/main.py``
- ``orchestrator/orchestrator/redis_client.py``
- ``orchestrator/orchestrator/volume_store.py``
- ``backend/app/api/routes/admin.py``
- ``orchestrator/tests/integration/test_reaper.py``
- ``backend/tests/api/routes/test_admin_settings.py``

## Verification

docker compose build orchestrator backend && docker compose up -d --force-recreate orchestrator && docker cp orchestrator/tests perpetuity-orchestrator-1:/app/tests && docker compose exec orchestrator /app/.venv/bin/pytest tests/integration/test_reaper.py -v && cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_admin_settings.py -v

## Observability Impact

Adds INFO `reaper_started interval_seconds=<n>` (lifespan startup), INFO `reaper_tick scanned=<n> killed=<n> reaped_containers=<n>` (per loop iteration), INFO `reaper_killed_session session_id=<uuid> reason=idle_no_attach` (per kill), INFO `reaper_reaped_container container_id=<short> user_id=<uuid> team_id=<uuid> reason=last_session_killed` (per container reap), INFO `idle_timeout_seconds_resolved source=<system_settings|fallback> value=<n>` (per tick), WARNING `reaper_tick_failed reason=<class>` (per swallowed iteration error), WARNING `system_settings_lookup_failed key=idle_timeout_seconds reason=<class>` (mirrors S03 pattern). All identifiers UUID-only. The reaper task itself is the new failure surface: lifespan teardown must cancel+await the task or pytest leaks `Task was destroyed but it is pending` warnings — the test suite for the reaper proves the cancel path is clean.
