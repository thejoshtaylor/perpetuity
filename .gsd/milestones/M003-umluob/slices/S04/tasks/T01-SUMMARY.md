---
id: T01
parent: S04
milestone: M003-umluob
key_files:
  - .gsd/milestones/M003-umluob/slices/S04/tasks/T01-VERIFICATION.md
key_decisions:
  - Treat the orchestrator-internal test_ws_bridge::test_disconnect_reconnect_preserves_scrollback failure as a pre-existing test seeding bug (committed at bfc9cc6 before workspace_volume FK was wired at a4de0d1) and record it as a Verification gap rather than modify the test or stop the slice — the literal S04 demo is fully proven by the backend test_m002_s05_full_acceptance e2e which uses signup-driven user/team creation and PASSED on this run.
duration: 
verification_result: mixed
completed_at: 2026-04-25T15:05:13.118Z
blocker_discovered: false
---

# T01: Verified M003/S04 demo (tmux + Redis + reattach across restart) by citation against shipped M002/S04+S05 code; produced T01-VERIFICATION.md with all four sub-criteria PASS via the literal-S04-demo backend e2e and re-filed the M003-umluob ≡ M002-jy6pde duplication hand-off (third in a row).

**Verified M003/S04 demo (tmux + Redis + reattach across restart) by citation against shipped M002/S04+S05 code; produced T01-VERIFICATION.md with all four sub-criteria PASS via the literal-S04-demo backend e2e and re-filed the M003-umluob ≡ M002-jy6pde duplication hand-off (third in a row).**

## What Happened

Followed the verification-by-citation pattern locked by M003/S01/T01 (MEM200/MEM201) and M003/S03/T02 (MEM205). Verified all cited source line ranges still hold at HEAD `b1afe70`: `start_tmux_session` L374-409, `capture_scrollback` L430-465, `kill_tmux_session` L468-488, `resize_tmux_session` L491-526, `list_tmux_sessions` L412-427 in `orchestrator/orchestrator/sessions.py`; `RedisSessionRegistry` L51-265 in `orchestrator/orchestrator/redis_client.py`; `_lifespan` L146-252 (registry binding L196-200, teardown order L240-252) in `orchestrator/orchestrator/main.py`; `create_session` L88-138, `get_session_by_id` L155-174, `delete_session` L177-226, `get_scrollback` L229-264, `resize_session` L267-303 in `orchestrator/orchestrator/routes_sessions.py`; `session_stream` L97-458 in `orchestrator/orchestrator/routes_ws.py`. Ran the cited tests live against the compose stack: `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance` PASSED in 30.12s (the literal S04 demo — covers all four sub-criteria in one bundled run); `backend/tests/integration/test_m002_s04_e2e.py::test_m002_s04_full_demo` PASSED in 19.83s (multi-tmux + scrollback proxy); `orchestrator/tests/integration/test_reaper.py::test_reaper_keeps_container_with_surviving_session` PASSED (R008 sibling-skip). Wrote `.gsd/milestones/M003-umluob/slices/S04/tasks/T01-VERIFICATION.md` with one `## Criterion:` section per sub-criterion (tmux pty ownership / Redis source-of-truth across restart / scrollback ≥100KB restored on reattach / sibling-skip with same shell PID), verbatim PASSED lines from each live run, file-and-line citations, and the literal `M003-umluob duplicates M002-jy6pde` hand-off block. Recorded MEM206 (architecture: third-in-a-row verification slice) and MEM207 (gotcha: pre-existing test_ws_bridge._seed_session seeding bug).

Two non-blocking gaps surfaced and recorded honestly in the report rather than papered over: (1) `orchestrator/tests/integration/test_ws_bridge.py::test_disconnect_reconnect_preserves_scrollback` 503s on HEAD with `workspace_volume_store_unavailable: ForeignKeyViolationError` because its `_seed_session` helper was committed at `bfc9cc6` BEFORE the workspace_volume FK was wired at `a4de0d1` and never updated to seed user/team rows the way sibling tests in the same package do — pre-existing test seeding bug, NOT an S04-functionality regression, fully outside this verification's scope; (2) `test_reaper_skips_attached_session` flaked with `losetup: failed to set up loop device` because linuxkit's loop device pool is exhausted (44/64 leaked across many test runs today) — environmental, also not an S04 regression. Documented both as a `## Verification gap:` section per the slice plan's failure-handling rule.

Slice gate `test -f … && [ "$(grep -c '^## Criterion:' …)" -ge 4 ] && grep -q 'M003-umluob duplicates M002-jy6pde' … && [ "$(grep -c 'PASSED' …)" -ge 4 ] && [ -z "$(git status --porcelain | grep -v '^.. .gsd/' || true)" ]` returned exit 0. Final counts: 4 `## Criterion:` sections, 2 occurrences of the duplication hand-off string, 13 PASSED lines, zero non-`.gsd/` git changes.

## Verification

Ran the slice plan's verification command verbatim and got exit 0 (`GATE PASS exit=0`). All four gate clauses satisfied: T01-VERIFICATION.md exists; contains 4 `## Criterion:` sections (≥4 required); contains the literal `M003-umluob duplicates M002-jy6pde` string; contains 13 lines with `PASSED` token (≥4 required); `git status --porcelain | grep -v '^.. .gsd/'` is empty (only `.gsd/` artifacts changed, no source/compose/Dockerfile/test-code modified). Three live test runs against the compose stack provided the verbatim PASSED lines: backend `test_m002_s05_full_acceptance` (the literal S04 demo, covers all four sub-criteria), backend `test_m002_s04_full_demo` (multi-tmux + scrollback proxy), orchestrator `test_reaper_keeps_container_with_surviving_session` (R008 sibling-skip).

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `uv run pytest -m e2e tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance -v` | 0 | ✅ pass | 30120ms |
| 2 | `uv run pytest -m e2e tests/integration/test_m002_s04_e2e.py::test_m002_s04_full_demo -v` | 0 | ✅ pass | 19830ms |
| 3 | `.venv/bin/pytest tests/integration/test_reaper.py::test_reaper_keeps_container_with_surviving_session -v` | 0 | ✅ pass | 6510ms |
| 4 | `test -f T01-VERIFICATION.md && [ criterion_count -ge 4 ] && grep -q duplication-string && [ PASSED_count -ge 4 ] && no non-.gsd git changes` | 0 | ✅ pass | 50ms |
| 5 | `.venv/bin/pytest tests/integration/test_ws_bridge.py::test_disconnect_reconnect_preserves_scrollback -v` | 1 | ❌ fail (recorded as Verification gap; pre-existing test seeding bug, not an S04 regression — see report) | 1080ms |

## Deviations

None. Stayed strictly within scope: zero source/compose/Dockerfile/test-code changes; only `.gsd/` artifacts written.

## Known Issues

(1) `orchestrator/tests/integration/test_ws_bridge.py::_seed_session` (L207-218) posts random user/team UUIDs to `POST /v1/sessions` and 503s with `workspace_volume_store_unavailable: ForeignKeyViolationError` — pre-existing test bug, fix is to port the `_create_pg_user_team` psql-seed helper from sibling test files (test_reaper.py L114-128, test_ws_attach_map.py L130, test_sessions_lifecycle.py L406). Recorded as MEM207. Out of scope for this verification-only task. (2) Linuxkit loop device pool is exhausted on this host (44/64 in use, leaked from many test runs today), causing `test_reaper_skips_attached_session` to flake on `losetup: failed to set up loop device`. Environmental, not an S04 regression; the same test PASSED in MEM205's S03/T02 verification on the same HEAD earlier today. (3) M003-umluob ≡ M002-jy6pde reconciliation is now blocking THREE M003 slices (S01, S03, S04) and a human owner must decide between closing M003 as already-delivered or re-scoping it toward R009-R012 Projects-and-GitHub.

## Files Created/Modified

- `.gsd/milestones/M003-umluob/slices/S04/tasks/T01-VERIFICATION.md`
