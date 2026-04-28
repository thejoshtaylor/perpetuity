# T02 Verification Report — M003-umluob / S03

**Slice:** S03 — Idle reaper + container lifecycle
**Milestone:** M003-umluob
**Task:** T02 — Verify M003/S03 reaper + container-lifecycle success criteria against existing tests and file follow-up note
**Date:** 2026-04-25
**Verdict:** ✅ ALL CRITERIA PASS (verification slice; no new orchestrator code in scope)

This report proves M003/S03's success criteria by citation against tests already in `main`. The reaper module (`orchestrator/orchestrator/reaper.py`, 359 lines) and its lifespan wiring (`orchestrator/orchestrator/main.py`, MEM170/MEM190 ordering) shipped under M002/S04; the bundled e2e proving volume persistence across reap and successful re-provision shipped under M002/S05. The slice's stopping condition is this artifact, not new code.

## Human action required: M003-umluob duplicates M002-jy6pde

The three success criteria for M003/S03 are **byte-for-byte the same set** that M002/S04 + M002/S05 already shipped and that ship-tests still cover. Auto-mode cannot decide whether M003 should be:
- (a) closed as already-delivered (recommended path; M003 then pivots to its true scope), or
- (b) re-planned with `gsd_replan_slice` so that M003-umluob owns *new* work — most plausibly the Projects-and-GitHub scope (R009–R012 per PROJECT.md) that the rest of M003 pre-supposes.

A human owner must reconcile this before subsequent M003 slices proceed. Same hand-off was filed by M003/S01/T01 (`.gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md`) and recorded in MEM200/MEM201/MEM202.

## Known accepted divergences

None for this slice. The reaper code, two-phase check, and container-lifecycle path are all spec-aligned. (The `nano_cpus = 1_000_000_000` divergence noted in S01/T01 is a container-provisioning concern, not a reaper concern; it does not affect S03 verification.)

## Verification environment

- Host Docker daemon up; `perpetuity-db-1` (postgres:18 on host port 55432, MEM021), `perpetuity-redis-1`, and `perpetuity-orchestrator-1` running and healthy.
- Required images present locally: `orchestrator:latest`, `perpetuity/workspace:latest`, `perpetuity/workspace:test`.
- Tests executed from working directory `/Users/josh/code/perpetuity` with env loaded from `.env` (`POSTGRES_PASSWORD`/`POSTGRES_USER`/`POSTGRES_DB`/`REDIS_PASSWORD=changethis` per MEM111).
- Orchestrator suite via `orchestrator/.venv/bin/pytest`; backend e2e via `backend` `uv run pytest` (resolves to project `.venv/bin/python`).
- Working tree clean at HEAD `b1afe70` (`git status --porcelain` empty); no source files modified during this verification.

---

## Criterion: Idle session is reaped (Redis last_activity past idle_timeout, no live attach) — workspace volume persists

**Source-of-truth files:**
- `orchestrator/orchestrator/reaper.py` (`_reap_one_tick` candidate-eligibility loop L100–L185; `kill_tmux_session` invocation L141; `registry.delete_session` L165; `reaper_killed_session` log key L179–L182)
- `orchestrator/orchestrator/volume_store.py` (`_resolve_idle_timeout_seconds(pool)` reads `system_settings.idle_timeout_seconds` per tick at L332)
- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py` (step 9 dials `idle_timeout_seconds` to 3 s, waits, asserts `workspace_volume` row survives the reap at L862–L880)

**Tests covering criterion:**
- `orchestrator/tests/integration/test_reaper.py::test_reaper_kills_idle_session_with_no_attach`
- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance` (step 9 — reaper kills sessions, then a `SELECT id FROM workspace_volume` returns a UUID, proving the volume row outlives the container)

**Run command (orchestrator):** `.venv/bin/pytest tests/integration/test_reaper.py::test_reaper_kills_idle_session_with_no_attach -v` (from `orchestrator/`, env loaded from project `.env`)

**Verbatim runner output (orchestrator):**
```
tests/integration/test_reaper.py::test_reaper_kills_idle_session_with_no_attach PASSED [ 25%]
```

**Run command (backend e2e):** `uv run pytest tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance -v` (from `backend/`, env loaded from project `.env`)

**Verbatim runner output (backend e2e):**
```
tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance PASSED [100%]
======================== 1 passed, 3 warnings in 30.49s ========================
```

**Verdict:**
- PASS: test_reaper_kills_idle_session_with_no_attach
- PASS: test_m002_s05_full_acceptance (step 9 proves volume persistence across reaper-driven reap)

---

## Criterion: D018 two-phase liveness check — idle Redis last_activity AND no live AttachMap entry are BOTH required to reap

**Source-of-truth files:**
- `orchestrator/orchestrator/reaper.py` L131–L136: the two-phase check itself —
  ```python
  if await attach_map.is_attached(session_id):
      # Two-phase check (D018): a stale Redis last_activity does NOT
      # justify killing a tmux session that has a live WS attach.
      if container_id:
          surviving_by_container[str(container_id)] += 1
      continue
  ```
- `orchestrator/orchestrator/attach_map.py` (`AttachMap.is_attached` L71–L73; `register`/`unregister` L51–L69 — refcount-based, MEM181)
- `orchestrator/orchestrator/routes_ws.py` (`attach_registered session_id=<sid> count=<n>` log key at L237 — synchronization signal used by the test, per MEM171/MEM178)

**Tests covering criterion:**
- `orchestrator/tests/integration/test_reaper.py::test_reaper_skips_attached_session` — opens a real WS attach FIRST (per MEM171/MEM178 to avoid the 1-second-tick race), back-dates `last_activity` AFTER `attach_registered`, then runs a tick and asserts the session is NOT killed.

**Run command:** `.venv/bin/pytest tests/integration/test_reaper.py::test_reaper_skips_attached_session -v` (from `orchestrator/`)

**Verbatim runner output:**
```
tests/integration/test_reaper.py::test_reaper_skips_attached_session PASSED [ 50%]
```

**Verdict:**
- PASS: test_reaper_skips_attached_session — proves the AttachMap half of D018 blocks a reap that the Redis half would otherwise authorize.

---

## Criterion: Container lifecycle — reaper kills tmux + removes container on last-session kill; sibling sessions block container reap

**Source-of-truth files:**
- `orchestrator/orchestrator/reaper.py` container-reap pass L187–L274:
  - L188 `candidates_for_reap` gate — only containers the reaper *just* emptied are eligible (MEM192: clean DELETE never enters this path)
  - L193–L194 `surviving_by_container` sibling-skip check (R008 — multi-tmux containers stay alive until ALL sessions are gone)
  - L196 `list_tmux_sessions` orphaned-state guard (do not remove the container if tmux still has a survivor)
  - L228 `_find_container_by_labels` re-confirmation before deletion (don't clobber a container that was re-provisioned mid-tick)
  - L242–L244 `container.stop(timeout=…)` then `container.delete(force=True)`
  - L266–L272 `reaper_reaped_container container_id=<12> user_id=<uuid> team_id=<uuid> reason=last_session_killed` INFO log
- `orchestrator/orchestrator/sessions.py` (`kill_tmux_session`, `list_tmux_sessions`, `_find_container_by_labels` — DockerError + OSError wrapped into `DockerUnavailable` per MEM168/MEM176)
- `orchestrator/orchestrator/main.py` lifespan teardown order L246–L252: `stop_reaper` runs FIRST, then `registry.close` → `close_pool` → `docker.close` (MEM170/MEM190)

**Tests covering criterion:**
- `orchestrator/tests/integration/test_reaper.py::test_reaper_reaps_container_when_last_session_killed` — reap path: idle the only session, run a tick, assert container is gone.
- `orchestrator/tests/integration/test_reaper.py::test_reaper_keeps_container_with_surviving_session` — sibling-skip path: idle one of two sessions sharing a container, run a tick, assert the tmux session is killed but the container survives (R008).
- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance` (step 9 again — the post-reap `docker ps -q --filter label=user_id=… --filter label=team_id=…` returns empty, proving the container was actually removed end-to-end against the live compose stack).

**Run command:** `.venv/bin/pytest tests/integration/test_reaper.py::test_reaper_reaps_container_when_last_session_killed tests/integration/test_reaper.py::test_reaper_keeps_container_with_surviving_session -v` (from `orchestrator/`)

**Verbatim runner output:**
```
tests/integration/test_reaper.py::test_reaper_reaps_container_when_last_session_killed PASSED [ 75%]
tests/integration/test_reaper.py::test_reaper_keeps_container_with_surviving_session PASSED [100%]
```

**Verbatim e2e PASS line (already cited above):**
```
tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance PASSED [100%]
```

**Verdict:**
- PASS: test_reaper_reaps_container_when_last_session_killed
- PASS: test_reaper_keeps_container_with_surviving_session
- PASS: test_m002_s05_full_acceptance (step 9 covers container removal end-to-end)

---

## Aggregate runner output (orchestrator suite, four tests in one invocation)

```
============================= test session starts ==============================
platform darwin -- Python 3.12.13, pytest-8.4.2, pluggy-1.6.0 -- /Users/josh/code/perpetuity/orchestrator/.venv/bin/python3
cachedir: .pytest_cache
rootdir: /Users/josh/code/perpetuity/orchestrator
configfile: pyproject.toml
plugins: asyncio-0.26.0, anyio-4.13.0
asyncio: mode=Mode.AUTO, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 4 items

tests/integration/test_reaper.py::test_reaper_kills_idle_session_with_no_attach PASSED [ 25%]
tests/integration/test_reaper.py::test_reaper_skips_attached_session PASSED [ 50%]
tests/integration/test_reaper.py::test_reaper_reaps_container_when_last_session_killed PASSED [ 75%]
tests/integration/test_reaper.py::test_reaper_keeps_container_with_surviving_session PASSED [100%]

============================== 4 passed in 19.67s ==============================
```

---

## Aggregate result

- 3 of 3 success criteria PASS by citation against tests in `main` (4 orchestrator integration tests + 1 backend e2e, total 5 PASS lines).
- 0 regressions surfaced.
- 0 known accepted divergences for this slice.
- 1 human-action note filed (M003-umluob duplicates M002-jy6pde — same hand-off as S01/T01).

No remediation work in scope for this slice. Future agent reconciling M003 vs M002 should:
1. Read this file (`cat .gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md`) and its S01 sibling (`cat .gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md`).
2. Decide between closing M003 as already-delivered or re-scoping it via `gsd_replan_slice` after re-planning M003 in the roadmap (likely toward R009–R012 Projects-and-GitHub scope).
3. Subsequent M003 slices (S04, S05, S06) are expected to follow the same verification-only pattern unless and until the reconciliation flips M003 to net-new scope.
