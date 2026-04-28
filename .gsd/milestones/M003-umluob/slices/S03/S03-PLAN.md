# S03: Idle reaper + container lifecycle

**Goal:** Verify that all three M003/S03 success criteria — idle reap with volume persistence, container lifecycle (kill+remove via reaper), and the D018 two-phase liveness check (idle Redis last_activity AND no live AttachMap entry required) — are already met by code and tests already in `main` (originally shipped under M002/S04 + M002/S05). Produce a single citation-by-test verification artifact and re-file the human-action note about the M003-umluob ≡ M002-jy6pde duplication so a human owner can reconcile before subsequent M003 slices proceed.
**Demo:** Integration test: provision a container, write a marker file inside the workspace volume, set last_activity in Redis to a time exceeding the idle timeout, run the reaper tick — the container is stopped, the volume persists (the .img file still exists on host). Re-provision the same (user, team) — a new container starts, the marker file is still readable at /workspaces/<user_id>/<team_id>/marker. Two-phase check test: even with stale last_activity, an active WS attachment in the in-memory map prevents reaping.

## Must-Haves

- T02-VERIFICATION.md exists under .gsd/milestones/M003-umluob/slices/S03/tasks/, contains one `## Criterion:` section per S03 success criterion (≥3), and contains the M003/M002 duplication human-action note (same shape as S01's T01-VERIFICATION.md).
- ≥3 verbatim PASS lines from live test runs are pasted into the report (the three core orchestrator reaper integration tests covering: idle-no-attach reap, two-phase attach skip, container reap on last-session kill), plus the bundled M002/S05 e2e PASS line that proves volume persistence across reap and re-provision.
- All cited tests pass on the current `main` checkout from a clean working directory: `orchestrator/.venv/bin/pytest tests/integration/test_reaper.py::test_reaper_kills_idle_session_with_no_attach tests/integration/test_reaper.py::test_reaper_skips_attached_session tests/integration/test_reaper.py::test_reaper_reaps_container_when_last_session_killed tests/integration/test_reaper.py::test_reaper_keeps_container_with_surviving_session` exits 0; `backend && uv run pytest tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance` exits 0.
- No orchestrator source, docker-compose.yml, workspace Dockerfile, or test code is modified — verification + documentation only.

## Proof Level

- This slice proves: - This slice proves: contract (verification-only — no new runtime composition; the runtime path is exercised by M002-shipped tests this slice cites)
- Real runtime required: yes (the cited tests run against the real compose stack — Docker daemon, Postgres, Redis)
- Human/UAT required: no (mechanical: tests pass + grep-able invariants in the report)

## Integration Closure

- Upstream surfaces consumed: orchestrator/orchestrator/reaper.py (reaper loop, `_reap_one_tick`, `start_reaper`, `stop_reaper`); orchestrator/orchestrator/main.py lifespan teardown ordering (MEM170/MEM190 — `stop_reaper` runs FIRST before registry/pool/docker close); orchestrator/orchestrator/sessions.py (`kill_tmux_session`, `list_tmux_sessions`, `_find_container_by_labels`, `DockerUnavailable` wrapper per MEM168/MEM176); orchestrator/orchestrator/attach_map.py (`get_attach_map`, `is_attached` — D018 second phase); orchestrator/orchestrator/volume_store.py (`_resolve_idle_timeout_seconds`, system_settings read per tick); orchestrator/orchestrator/redis_client.py (`scan_session_keys`, `delete_session`); the cited orchestrator integration tests; the bundled M002/S05 e2e proving volume persistence across reap and re-provision.
- New wiring introduced in this slice: none (zero new code; verification artifact only).
- What remains before the milestone is truly usable end-to-end: a human owner must reconcile the M003-umluob ≡ M002-jy6pde duplication (close M003 as already-delivered or `gsd_replan_slice` toward its real Projects/GitHub scope per R009–R012). S04, S05, S06 are also expected to be verification-only by the same logic but that is the next slice's call, not this one's.

## Verification

- Runtime signals: none added by this slice. Existing reaper INFO keys exercised by the cited tests are `reaper_started`, `reaper_tick scanned=N killed=N reaped_containers=N`, `reaper_killed_session session_id=<sid> reason=idle_no_attach`, `reaper_reaped_container container_id=<12> user_id=<uuid> team_id=<uuid> reason=last_session_killed`. WARNING keys preserved: `reaper_tick_failed reason=<class>`, `reaper_kill_failed`, `reaper_tmux_ls_failed`, `reaper_container_remove_failed`, `reaper_skipped_bad_record`, `reaper_stop_timeout`, `attach_registered session_id=<sid>` (used by the two-phase test as a synchronization signal).
- Inspection surfaces: orchestrator container logs (`docker compose logs orchestrator`), Redis session-key scan (`docker exec perpetuity-redis-1 redis-cli -a $REDIS_PASSWORD --no-auth-warning KEYS 'session:*'`), Docker label-scoped `docker ps --filter label=user_id=… --filter label=team_id=…`, Postgres `workspace_volume` row, and the system_settings `idle_timeout_seconds` key.
- Failure visibility: every reaper-tick error is keyed `reaper_tick_failed reason=<ExceptionClassName>` (no traceback noise); the verification report itself is the durable failure-state for this slice — a missing PASS line or missing criterion section is the on-disk evidence that the milestone is not actually delivered.
- Redaction constraints: report cites UUIDs (user_id, team_id, session_id, container_id-truncated-to-12) and structured log keys only — no emails, team slugs, or secrets per MEM134.

## Tasks

- [x] **T02: Verify M003/S03 reaper + container-lifecycle success criteria against existing tests and file follow-up note** `est:1h`
  S03's three success criteria — (1) idle session is reaped with workspace volume persisting; (2) two-phase liveness check (Redis last_activity past idle_timeout AND no live AttachMap attach are BOTH required to reap); (3) container lifecycle (reaper kills tmux + removes container, sibling sessions block reap) — are byte-for-byte the same scope M002/S04 + M002/S05 already shipped to `main`. The reaper module (orchestrator/orchestrator/reaper.py, 360 lines) is fully implemented. Integration tests in orchestrator/tests/integration/test_reaper.py cover all three criteria (12 tests, ~828 lines). The bundled M002/S05 e2e test proves the end-to-end demo path including volume-row persistence across reap and successful re-provision against the same (user, team) pair (backend/tests/integration/test_m002_s05_full_acceptance_e2e.py, steps 8–9, lines ~786–880).

This task — analogous to M003/S01/T01 — runs the cited tests from a clean working tree against the real compose stack and produces `.gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md` containing one `## Criterion:` section per S03 success criterion, verbatim PASS lines from each cited test, file-and-line citations into reaper.py / sessions.py / attach_map.py / the e2e test source, and a human-action note re-stating the M003-umluob ≡ M002-jy6pde duplication so it stays visible until a human resolves it. No orchestrator source, docker-compose.yml, workspace Dockerfile, or test code is modified — strict verification + documentation scope, mirroring the S01/T01 pattern exactly.

Rationale: the planner cannot autonomously decide to re-scope M003-umluob or close it as already-delivered. Filing the verification report keeps the slice's stopping condition mechanically checkable while preserving an explicit hand-off note for the human owner.
  - Files: `.gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md`, `.gsd/milestones/M003-umluob/slices/S03/tasks/T02-SUMMARY.md`, `orchestrator/orchestrator/reaper.py`, `orchestrator/orchestrator/main.py`, `orchestrator/orchestrator/sessions.py`, `orchestrator/orchestrator/attach_map.py`, `orchestrator/orchestrator/volume_store.py`, `orchestrator/tests/integration/test_reaper.py`, `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py`
  - Verify: test -f .gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md && [ "$(grep -c '^## Criterion:' .gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md)" -ge 3 ] && grep -q 'M003-umluob duplicates M002-jy6pde' .gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md && [ "$(grep -c 'PASSED' .gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md)" -ge 4 ] && (cd orchestrator && .venv/bin/pytest tests/integration/test_reaper.py::test_reaper_kills_idle_session_with_no_attach tests/integration/test_reaper.py::test_reaper_skips_attached_session tests/integration/test_reaper.py::test_reaper_reaps_container_when_last_session_killed tests/integration/test_reaper.py::test_reaper_keeps_container_with_surviving_session -v) && (cd backend && uv run pytest tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance -v)

## Files Likely Touched

- .gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md
- .gsd/milestones/M003-umluob/slices/S03/tasks/T02-SUMMARY.md
- orchestrator/orchestrator/reaper.py
- orchestrator/orchestrator/main.py
- orchestrator/orchestrator/sessions.py
- orchestrator/orchestrator/attach_map.py
- orchestrator/orchestrator/volume_store.py
- orchestrator/tests/integration/test_reaper.py
- backend/tests/integration/test_m002_s05_full_acceptance_e2e.py
