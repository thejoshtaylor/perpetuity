# T01 Verification Report — M003-umluob / S04

**Slice:** S04 — Tmux session model + Redis registry + reattach across orchestrator restart
**Milestone:** M003-umluob
**Task:** T01 — Verify M003/S04 demo by citation against shipped M002 code
**Date:** 2026-04-25
**Verdict:** ✅ ALL FOUR SUB-CRITERIA PASS via the literal-S04-demo backend e2e (`test_m002_s05_full_acceptance`) + the multi-tmux backend e2e (`test_m002_s04_full_demo`) + a corroborating orchestrator reaper test. ⚠️ One pre-existing test seeding gap surfaced in `orchestrator/tests/integration/test_ws_bridge.py` is recorded below as a Verification gap; it is **not** an S04-functionality regression — the test post-dates the workspace_volume FK wiring and was never updated to seed user/team rows.

This report proves M003/S04's demo by citation against tests already in `main`. The tmux session model (`orchestrator/orchestrator/sessions.py`), Redis registry (`orchestrator/orchestrator/redis_client.py`), lifespan rebuilds (`orchestrator/orchestrator/main.py`), HTTP routes (`orchestrator/orchestrator/routes_sessions.py`), and the WS-style interface (`orchestrator/orchestrator/routes_ws.py`) all shipped under M002/S04 + M002/S05. The slice's stopping condition is this artifact, not new code.

## Human action required: M003-umluob duplicates M002-jy6pde

The single demo bullet for M003/S04 (POST creates tmux session → WS-style interface pipes 'echo hello' → restart orchestrator → reconnect same session_id → scrollback contains 'hello' → 'echo world' on same shell) is **byte-for-byte the same demo** that M002/S04 + M002/S05 already shipped and that the bundled e2e `test_m002_s05_full_acceptance` still covers end-to-end against the live compose stack. Auto-mode cannot decide whether M003 should be:

- (a) closed as already-delivered (recommended path; M003 then pivots to its true scope), or
- (b) re-planned with `gsd_replan_slice` so that M003-umluob owns *new* work — most plausibly the Projects-and-GitHub scope (R009–R012 per PROJECT.md) that the rest of M003 pre-supposes.

A human owner must reconcile this before subsequent M003 slices proceed. Same hand-off was filed by M003/S01/T01 (`.gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md`) and M003/S03/T02 (`.gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md`); recorded in MEM200/MEM201/MEM202/MEM205. Three slices in a row landing the same hand-off is a strong tell.

## Known accepted divergences

None for this slice. The tmux/Redis/reattach demo is fully spec-aligned. (The `nano_cpus = 1_000_000_000` divergence noted in S01/T01 is a container-provisioning concern, not a tmux/reattach concern; do not record it here per MEM203.)

## Verification environment

- Host Docker daemon up; `perpetuity-db-1` (postgres:18 on host port 55432, MEM021), `perpetuity-redis-1`, and `perpetuity-orchestrator-1` running and healthy.
- Required images present locally: `orchestrator:latest`, `perpetuity/workspace:latest`, `perpetuity/workspace:test`.
- Tests executed from working directory `/Users/josh/code/perpetuity` with env loaded from `.env` (`POSTGRES_PASSWORD`/`POSTGRES_USER`/`POSTGRES_DB`/`REDIS_PASSWORD=changethis` per MEM111).
- Orchestrator suite via `orchestrator/.venv/bin/pytest`; backend e2e via `backend` `uv run pytest` (resolves to project `.venv/bin/python`, MEM041).
- Working tree clean at HEAD `b1afe70` (`git status --porcelain` empty before this report was written); no source/compose/Dockerfile/test-code modified during this verification.

---

## Criterion: Tmux owns the pty inside the workspace container (D012/MEM092) — orchestrator restart kills the docker exec stream but tmux keeps the shell + scrollback alive

**Source-of-truth files:**
- `orchestrator/orchestrator/sessions.py`:
  - `start_tmux_session` L374–L409 — `tmux new-session -d` (detached) so docker exec returns immediately and tmux owns the pty. Emits `session_created session_id=… container_id=…` INFO log.
  - `list_tmux_sessions` L412–L427 — `tmux ls -F #{session_name}` returns surviving session names; treats `no server running` as `[]`.
  - `capture_scrollback` L430–L465 — `tmux capture-pane -p -S - -E -` capped to `settings.scrollback_max_bytes` (default 100 KiB per D017). Returns `""` on `can't find session` for the orphaned-state guard.
  - `kill_tmux_session` L468–L488 — `tmux kill-session -t <sid>`; container is **not** stopped (R008 — sibling tmux sessions stay alive).
  - `resize_tmux_session` L491–L526 — `tmux refresh-client -C cols,rows` for cooperative multi-attach resize.
- `orchestrator/orchestrator/routes_ws.py` L97–L173 — `session_stream` looks up the Redis record, captures scrollback for the attach frame, then opens `tmux attach-session -t <sid>` as a docker exec stream (L186–L197). Emits `session_attached session_id=… container_id=…` INFO log at L169–L173. Comment block L176–L180 explicitly states tmux ownership of the pty: a fresh `bash` fallback would be wrong because the tmux session was created in T03.

**Tests covering criterion:**
- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance` — step 4–6 capture the shell PID via `echo $$`, restart the ephemeral orchestrator, reconnect, and assert the same PID is still on the shell (proving tmux retained the bash process across the orchestrator-process death). This is the literal S04 demo.

**Run command:** `uv run pytest -m e2e tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance -v` (from `backend/`, env loaded from project `.env`)

**Verbatim runner output:**
```
tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance PASSED [100%]
======================== 1 passed, 3 warnings in 30.12s ========================
```

**Verdict:**
- PASSED: `test_m002_s05_full_acceptance` — proves tmux ownership of pty across orchestrator-process death (PID equality before/after restart is the load-bearing assertion at step 6).

---

## Criterion: Redis registry is the durable source-of-truth across orchestrator restart (D013, no in-memory fallback)

**Source-of-truth files:**
- `orchestrator/orchestrator/redis_client.py` `RedisSessionRegistry` L51–L265:
  - `set_session` L83–L105 — `SET session:<sid>` + `SADD user_sessions:<uid>:<tid>` in a single transactional pipeline; stamps `last_activity` on every write.
  - `get_session` L107–L118 — `GET session:<sid>`; `None` if missing.
  - `scan_session_keys` L176–L230 — `SCAN MATCH session:*` (cursor-based, non-blocking) for the reaper; tolerates raced deletes.
  - `list_sessions` L232–L265 — resolves `user_sessions:<uid>:<tid>` index → `MGET session:<sid>` and scrubs stale ids best-effort.
- `orchestrator/orchestrator/main.py` `_lifespan` L146–L252:
  - L196–L200 binds a fresh `RedisSessionRegistry()` to `app.state.registry` on every boot — no in-memory shim, no migration step. The next request reads from Redis directly.
  - L240–L252 lifespan teardown order: `stop_reaper` FIRST, then `registry.close()`, then `close_pool(pg_pool)`, then `docker.close()` (MEM170/MEM190).
- `orchestrator/orchestrator/routes_sessions.py`:
  - `create_session` L88–L138 — calls `registry.set_session` (no in-memory cache, every read goes to Redis).
  - `get_session_by_id` L155–L174 — pure `registry.get_session` lookup; 404 on `None`.
  - `delete_session` L177–L226 — `registry.get_session` → `kill_tmux_session` → `registry.delete_session`.
  - `get_scrollback` L229–L264 — `registry.get_session` lookup before pulling the live tmux capture-pane.
  - `resize_session` L267–L303 — same `registry.get_session` lookup; 404 if Redis says no such session.

**Tests covering criterion:**
- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance` — step 5 (`docker restart <ephemeral_orchestrator>`) hard-kills the orchestrator process; step 6 reconnects WITH THE SAME `session_id` UUID. The only way the orchestrator can route the new WS attach to the right `(container_id, tmux_session)` pair is by re-reading the Redis row on its very first request after boot — there is no in-memory durability surface left after the process died.
- `backend/tests/integration/test_m002_s04_e2e.py::test_m002_s04_full_demo` — exercises the multi-tmux Redis index (`user_sessions:*` SADD/SMEMBERS) across two sessions in one container.

**Run command (M002/S04 multi-tmux):** `uv run pytest -m e2e tests/integration/test_m002_s04_e2e.py::test_m002_s04_full_demo -v` (from `backend/`)

**Verbatim runner output:**
```
tests/integration/test_m002_s04_e2e.py::test_m002_s04_full_demo PASSED   [100%]
======================== 1 passed, 3 warnings in 19.83s ========================
```

**Verdict:**
- PASSED: `test_m002_s04_full_demo` — proves the Redis user_sessions index correctly drives multi-session list/scrollback proxy through the backend.
- PASSED: `test_m002_s05_full_acceptance` — proves the Redis row survives an orchestrator process restart and is the source-of-truth the rebooted orchestrator reads from on first request (already cited above).

---

## Criterion: Scrollback capped to `scrollback_max_bytes` (≥100 KiB per D017) is restored to the new attach after orchestrator restart

**Source-of-truth files:**
- `orchestrator/orchestrator/sessions.py` `capture_scrollback` L430–L465 — `tmux capture-pane -t <sid> -p -S - -E -`, byte-cap-truncated by `_exec_collect(..., max_bytes=settings.scrollback_max_bytes)` per D017.
- `orchestrator/orchestrator/routes_ws.py` L146–L173 — on every fresh WS attach (which is also what happens after an orchestrator restart, since the prior exec stream died), the orchestrator first calls `capture_scrollback` and ships it as the `attach` frame's `scrollback` (base64) field. The post-restart client gets the same buffer the pre-restart client wrote into.
- `orchestrator/orchestrator/routes_sessions.py` `get_scrollback` L229–L264 — also exposes the same capture as `POST /v1/sessions/{sid}/scrollback` for the backend's `GET /api/v1/sessions/{sid}/scrollback` proxy (M002/S05 hand-off).

**Tests covering criterion:**
- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance` — step 6 asserts `'hello' in scrollback` after restart. The `hello` string was written into tmux via `echo hello\n` before the restart, so the assertion passes only if the post-restart `capture-pane` returned the pre-restart buffer. (Already cited above; PASS captured under criterion 1.)

**Verdict:**
- PASSED: `test_m002_s05_full_acceptance` — proves scrollback survives across orchestrator restart and is delivered to the new attach (the explicit `step 6: prior 'hello' missing from scrollback after orch restart` failure-message assertion is the load-bearing check).

---

## Criterion: Sibling-skip on the multi-tmux container (R008) — same shell across the orchestrator-restart boundary, AND the container is preserved when other tmux sessions remain

**Source-of-truth files:**
- `orchestrator/orchestrator/sessions.py` `kill_tmux_session` L468–L488 — kills only the named tmux session; container is intentionally left running.
- `orchestrator/orchestrator/main.py` `_lifespan` L235 — starts the idle reaper `app.state.reaper_task = start_reaper(app)`. The reaper is the only path that ever stops/removes a container (D013).
- `orchestrator/orchestrator/routes_ws.py`:
  - `attach_registered session_id=… count=…` INFO log at L236–L240 (refcount-on-attach, MEM181).
  - `attach_unregistered session_id=… count=…` INFO log at L453–L458 (refcount-on-detach, finally block).
- `orchestrator/orchestrator/routes_sessions.py` `delete_session` L177–L226 — explicit comment "Container is intentionally not stopped — sibling tmux sessions on the same container stay alive (R008)".

**Tests covering criterion:**
- `orchestrator/tests/integration/test_reaper.py::test_reaper_keeps_container_with_surviving_session` — idles one of two tmux sessions sharing a container, runs a tick, asserts the idled tmux session is killed but the container survives because of the sibling-skip path.
- `backend/tests/integration/test_m002_s04_e2e.py::test_m002_s04_full_demo` — drives two WS sessions into the same container and verifies multi-tmux + scrollback proxy.
- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance` — step 6 asserts `pid_after == pid_before` (same shell, same bash PID across the orchestrator-process restart).

**Run command:** `.venv/bin/pytest tests/integration/test_reaper.py::test_reaper_keeps_container_with_surviving_session -v` (from `orchestrator/`)

**Verbatim runner output:**
```
tests/integration/test_reaper.py::test_reaper_keeps_container_with_surviving_session PASSED [100%]
============================== 1 passed in 6.51s ===============================
```

**Verdict:**
- PASSED: `test_reaper_keeps_container_with_surviving_session` — proves R008 sibling-skip on the orchestrator side.
- PASSED: `test_m002_s04_full_demo` — already cited above; proves multi-tmux behavior end-to-end through the backend.
- PASSED: `test_m002_s05_full_acceptance` — already cited above; proves the same shell PID survives orchestrator restart (the strongest tmux-ownership assertion in the suite).

---

## Aggregate runner output (the literal-S04-demo backend e2e in isolation)

```
============================= test session starts ==============================
platform darwin -- Python 3.12.13, pytest-7.4.4, pluggy-1.6.0 -- /Users/josh/code/perpetuity/.venv/bin/python
cachedir: .pytest_cache
rootdir: /Users/josh/code/perpetuity/backend
configfile: pyproject.toml
plugins: anyio-4.12.1
collecting ... collected 1 item

tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance PASSED

======================== 1 passed, 3 warnings in 30.12s ========================
```

---

## Verification gap: orchestrator/tests/integration/test_ws_bridge.py::test_disconnect_reconnect_preserves_scrollback

**Status:** ❌ FAILED on HEAD with `503 workspace_volume_store_unavailable: ForeignKeyViolationError` from `POST /v1/sessions`.

**Root cause:** Pre-existing test seeding bug, **not** an S04 functionality regression. The test was committed in `bfc9cc6 feat: Wire orchestrator WS bridge` BEFORE the workspace_volume FK was wired in `a4de0d1 feat: Wire orchestrator volume manager into provision_container`. The test's `_seed_session` helper at `orchestrator/tests/integration/test_ws_bridge.py:207–218` calls `POST /v1/sessions` with random UUIDs for `user_id`/`team_id` and never inserts matching rows in `user`/`team`. Sibling tests in the same package (`test_reaper.py:114–128 _create_pg_user_team`, `test_ws_attach_map.py:130`, `test_sessions_lifecycle.py:406`) DO seed via `INSERT INTO "user"` + `INSERT INTO team` against `perpetuity-db-1`. `test_ws_bridge.py` was simply never updated.

**Failing pytest output (verbatim):**
```
tests/integration/test_ws_bridge.py::test_disconnect_reconnect_preserves_scrollback FAILED [100%]
=================================== FAILURES ===================================
________________ test_disconnect_reconnect_preserves_scrollback ________________
…
            r = c.post(
                "/v1/sessions",
                json={"session_id": sid, "user_id": user, "team_id": team},
            )
>           assert r.status_code == 200, r.text
E           AssertionError: {"detail":"workspace_volume_store_unavailable","reason":"create_volume_failed:ForeignKeyViolationError"}
E           assert 503 == 200
E            +  where 503 = <Response [503 Service Unavailable]>.status_code

tests/integration/test_ws_bridge.py:217: AssertionError
=========================== short test summary info ============================
FAILED tests/integration/test_ws_bridge.py::test_disconnect_reconnect_preserves_scrollback
============================== 1 failed in 1.08s ===============================
```

**Why this does not invalidate the slice:** the same within-process tmux durability is proven by `test_m002_s05_full_acceptance` (the literal S04 demo) which uses signup-driven user/team creation and PASSED on this run. The failure here is in test scaffolding, not in the orchestrator's tmux/Redis/reattach behavior. The fix is a local update to `_seed_session` to call the same `_create_pg_user_team` helper the sibling tests in the package already use — strictly out of scope for this verification-only task per the slice plan ("NO modification of orchestrator source, compose files, Dockerfiles, or test code"). A human owner reconciling M003-umluob ≡ M002-jy6pde should also file a follow-up to fix this seeding gap.

**Secondary environmental flake (not blocking):** `tests/integration/test_reaper.py::test_reaper_skips_attached_session` failed on this host with `losetup: failed to set up loop device: No such file or directory` due to linuxkit's loop device pool being exhausted (44 of 64 in use, leaked across many test runs today). Same image, same code as the sibling-skip test which PASSED; the divergence is purely environmental. The S03/T02 verification on this same HEAD captured this test PASSED earlier today. Not an S04 regression.

---

## Aggregate result

- 4 of 4 sub-criteria PASS by citation against tests in `main`. The literal-S04-demo backend e2e (`test_m002_s05_full_acceptance`) is the load-bearing proof; it covers all four sub-criteria in one bundled run. Two corroborating tests (`test_m002_s04_full_demo` for multi-tmux + scrollback proxy; `test_reaper_keeps_container_with_surviving_session` for the sibling-skip path) pass independently against the live compose stack.
- 0 S04-functionality regressions surfaced.
- 1 verification gap recorded against `test_ws_bridge.py::test_disconnect_reconnect_preserves_scrollback` — pre-existing test seeding bug, not an S04 regression.
- 1 environmental flake noted against `test_reaper_skips_attached_session` — linuxkit loop device pool exhausted, not an S04 regression.
- 0 known accepted divergences for this slice.
- 1 human-action note re-filed (M003-umluob duplicates M002-jy6pde — same hand-off as S01/T01 and S03/T02; third in a row).

No remediation work in scope for this slice. Future agent reconciling M003 vs M002 should:

1. Read this file and its S01 + S03 siblings:
   - `cat .gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md`
   - `cat .gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md`
   - `cat .gsd/milestones/M003-umluob/slices/S04/tasks/T01-VERIFICATION.md`
2. Decide between closing M003 as already-delivered or re-scoping it via `gsd_replan_slice` after re-planning M003 in the roadmap (likely toward R009–R012 Projects-and-GitHub scope).
3. Optionally file a side follow-up to fix the `test_ws_bridge.py::_seed_session` user/team seeding gap so the within-process WS reconnect proof can be re-enabled. This is independent of the M003 reconciliation.
4. Subsequent M003 slices (S05, S06) are expected to follow the same verification-only pattern unless and until the reconciliation flips M003 to net-new scope.
