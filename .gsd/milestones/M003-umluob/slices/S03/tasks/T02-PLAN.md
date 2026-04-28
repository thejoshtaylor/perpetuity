---
estimated_steps: 3
estimated_files: 9
skills_used: []
---

# T02: Verify M003/S03 reaper + container-lifecycle success criteria against existing tests and file follow-up note

S03's three success criteria — (1) idle session is reaped with workspace volume persisting; (2) two-phase liveness check (Redis last_activity past idle_timeout AND no live AttachMap attach are BOTH required to reap); (3) container lifecycle (reaper kills tmux + removes container, sibling sessions block reap) — are byte-for-byte the same scope M002/S04 + M002/S05 already shipped to `main`. The reaper module (orchestrator/orchestrator/reaper.py, 360 lines) is fully implemented. Integration tests in orchestrator/tests/integration/test_reaper.py cover all three criteria (12 tests, ~828 lines). The bundled M002/S05 e2e test proves the end-to-end demo path including volume-row persistence across reap and successful re-provision against the same (user, team) pair (backend/tests/integration/test_m002_s05_full_acceptance_e2e.py, steps 8–9, lines ~786–880).

This task — analogous to M003/S01/T01 — runs the cited tests from a clean working tree against the real compose stack and produces `.gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md` containing one `## Criterion:` section per S03 success criterion, verbatim PASS lines from each cited test, file-and-line citations into reaper.py / sessions.py / attach_map.py / the e2e test source, and a human-action note re-stating the M003-umluob ≡ M002-jy6pde duplication so it stays visible until a human resolves it. No orchestrator source, docker-compose.yml, workspace Dockerfile, or test code is modified — strict verification + documentation scope, mirroring the S01/T01 pattern exactly.

Rationale: the planner cannot autonomously decide to re-scope M003-umluob or close it as already-delivered. Filing the verification report keeps the slice's stopping condition mechanically checkable while preserving an explicit hand-off note for the human owner.

## Inputs

- ``.gsd/milestones/M003-umluob/M003-umluob-ROADMAP.md` — S03 success-criteria definition (idle reap demo, two-phase check, volume persistence, re-provision proof)`
- ``.gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md` — template structure to mirror (criterion-by-criterion citations, accepted-divergences block, human-action note)`
- ``orchestrator/orchestrator/reaper.py` — implementation of the reaper loop, `_reap_one_tick`, two-phase check (lines 131–136), container-reap pass (lines 187–272), `start_reaper`/`stop_reaper``
- ``orchestrator/orchestrator/main.py` — lifespan teardown ordering (`stop_reaper` first per MEM170/MEM190)`
- ``orchestrator/orchestrator/sessions.py` — `kill_tmux_session`, `list_tmux_sessions`, `_find_container_by_labels`, `DockerUnavailable` wrapper`
- ``orchestrator/orchestrator/attach_map.py` — `get_attach_map().is_attached(session_id)` second-phase check`
- ``orchestrator/orchestrator/volume_store.py` — `_resolve_idle_timeout_seconds(pool)` reads system_settings.idle_timeout_seconds per tick`
- ``orchestrator/tests/integration/test_reaper.py` — `test_reaper_kills_idle_session_with_no_attach` (criterion 1), `test_reaper_skips_attached_session` (criterion 2 / D018), `test_reaper_reaps_container_when_last_session_killed` (criterion 3 — kill + remove), `test_reaper_keeps_container_with_surviving_session` (criterion 3 — sibling skip)`
- ``backend/tests/integration/test_m002_s05_full_acceptance_e2e.py` — bundled M002/S05 e2e steps 8–9 prove the S03 roadmap demo end-to-end (idle reap → container removed → workspace_volume row + .img survive → next provision re-finds and re-mounts)`

## Expected Output

- ``.gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md` — citation-by-test verification report. MUST contain one `## Criterion:` section per S03 success criterion (≥3 sections); each criterion section MUST cite the source-of-truth file paths with line numbers and paste the verbatim PASS line(s) from the live test run that covers it. MUST contain a top-level `## Human action required: M003-umluob duplicates M002-jy6pde` block re-stating the same reconciliation hand-off filed by S01/T01. MUST contain `## Verification environment` describing the run (host-based pytest with project `.env` loaded, MEM021 PG port 55432, MEM111 REDIS_PASSWORD=changethis). SHOULD include any accepted-divergences block if discovered during the run (none expected — all cited tests are green on `main`).`
- ``.gsd/milestones/M003-umluob/slices/S03/tasks/T02-SUMMARY.md` — task summary mirroring T01-SUMMARY.md: verification evidence table (one row per criterion, columns: criterion / cited test / verdict), key decisions block (re-affirms verification-only scope; cross-refs MEM192 about clean-DELETE not entering the reap path; cross-refs MEM168/MEM170/MEM171/MEM175/MEM180/MEM182 as the architectural memories the report relies on), and a follow-ups section repeating the human-action hand-off.`

## Verification

test -f .gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md && [ "$(grep -c '^## Criterion:' .gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md)" -ge 3 ] && grep -q 'M003-umluob duplicates M002-jy6pde' .gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md && [ "$(grep -c 'PASSED' .gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md)" -ge 4 ] && (cd orchestrator && .venv/bin/pytest tests/integration/test_reaper.py::test_reaper_kills_idle_session_with_no_attach tests/integration/test_reaper.py::test_reaper_skips_attached_session tests/integration/test_reaper.py::test_reaper_reaps_container_when_last_session_killed tests/integration/test_reaper.py::test_reaper_keeps_container_with_surviving_session -v) && (cd backend && uv run pytest tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance -v)

## Observability Impact

Signals added/changed: none. The verification artifact ITSELF is the durable failure-state surface — a future agent inspects this slice's correctness by reading T02-VERIFICATION.md (presence + section count + PASS-line count + duplication-note presence) and re-running the cited test commands. Cited tests continue to emit `reaper_started`, `reaper_tick`, `reaper_killed_session`, `reaper_reaped_container`, and `attach_registered` INFO keys + `reaper_tick_failed`, `reaper_kill_failed`, `reaper_tmux_ls_failed`, `reaper_container_remove_failed`, `reaper_stop_timeout` WARNING keys, which the report cites as inspection surfaces.
