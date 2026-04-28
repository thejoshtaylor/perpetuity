---
id: S03
parent: M003-umluob
milestone: M003-umluob
provides:
  - ["citation-by-test verification that M003/S03's three success criteria (idle reap + volume persistence, two-phase liveness check, container lifecycle) are met by code already on main", "human-action note re-filing the M003-umluob ≡ M002-jy6pde duplication so the next human owner can reconcile (close M003 as delivered or replan toward R009–R012)", "verification-by-citation slice pattern, now established across two M003 slices in a row, that subsequent verification slices (S04/S05/S06 are likely candidates) can follow"]
requires:
  - slice: M002/S04
    provides: reaper module (reaper.py, 359 lines), lifespan teardown ordering (MEM170/MEM190), tmux/container session model, AttachMap (D018 second phase), reaper integration tests
  - slice: M002/S05
    provides: bundled e2e (test_m002_s05_full_acceptance) proving volume persistence across reap and successful re-provision against the same (user, team) pair — step 9 is the S03 demo
affects:
  - ["No code or runtime affected — verification + documentation only. Slice does not unblock S04/S05/S06 on its own; the M003-umluob ≡ M002-jy6pde duplication still requires a human reconciliation before subsequent slices' scope can be trusted."]
key_files:
  - [".gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md", "orchestrator/orchestrator/reaper.py", "orchestrator/orchestrator/main.py", "orchestrator/orchestrator/attach_map.py", "orchestrator/orchestrator/volume_store.py", "orchestrator/tests/integration/test_reaper.py", "backend/tests/integration/test_m002_s05_full_acceptance_e2e.py"]
key_decisions:
  - ["Treated S03 as verification-only over already-shipped M002/S04 + M002/S05 code (mirroring M003/S01/T01) — no orchestrator source, compose, Dockerfile, or test code modified.", "Cited the bundled M002/S05 e2e step 9 as the demo-level proof of all three S03 criteria end-to-end, since the slice plan demo IS that e2e's step 8–9.", "Re-filed the M003-umluob ≡ M002-jy6pde duplication hand-off as a top-level human-action note in T02-VERIFICATION.md (now in two verification reports plus four memories MEM200/MEM201/MEM202/MEM205) so it stays unmissable until a human owner reconciles M003.", "Recorded zero accepted divergences for this slice — the nano_cpus=1.0 vCPU divergence noted in S01/T01 is a provisioning concern, not a reaper concern, and does not affect S03 criteria."]
patterns_established:
  - ["Verification-by-citation slice pattern (now used for two M003 slices in a row): T0X-VERIFICATION.md with one `## Criterion:` section per success criterion, ≥1 verbatim PASSED line per criterion, file-and-line citations into the source modules, plus a top-level `## Human action required:` block when the slice surfaces a duplication or hand-off the planner can't autonomously resolve.", "Slice-plan grep gate as the mechanical stopping condition for verification slices: `[criterion-section-count >= N] AND [duplication-note grep] AND [PASSED-count >= M] AND [cited tests exit 0]` — keeps the gate enforceable without a human in the loop while still parking real decisions for a human owner."]
observability_surfaces:
  - ["INFO log keys exercised by cited tests: reaper_started, reaper_tick scanned=N killed=N reaped_containers=N, reaper_killed_session session_id=<sid> reason=idle_no_attach, reaper_reaped_container container_id=<12> user_id=<uuid> team_id=<uuid> reason=last_session_killed, attach_registered session_id=<sid> (used by two-phase test as synchronization signal)", "WARNING log keys preserved (non-noisy, no traceback): reaper_tick_failed reason=<ExceptionClassName>, reaper_kill_failed, reaper_tmux_ls_failed, reaper_container_remove_failed, reaper_skipped_bad_record, reaper_stop_timeout", "Inspection surfaces: docker compose logs orchestrator, redis-cli KEYS 'session:*', docker ps --filter label=user_id=… --filter label=team_id=…, Postgres workspace_volume row, system_settings idle_timeout_seconds key", "Failure visibility for this slice: the verification report itself is the durable failure-state — a missing PASSED line or missing Criterion section is the on-disk evidence that the milestone is not actually delivered"]
drill_down_paths:
  []
duration: ""
verification_result: passed
completed_at: 2026-04-25T14:50:01.893Z
blocker_discovered: false
---

# S03: Idle reaper + container lifecycle (verification-only over M002/S04 + M002/S05)

**Verified all three S03 success criteria — idle reap with volume persistence, two-phase liveness check, and container lifecycle — by citation against tests already shipped under M002/S04 + M002/S05; produced T02-VERIFICATION.md with 10 PASSED lines and re-filed the M003-umluob ≡ M002-jy6pde duplication hand-off.**

## What Happened

## What this slice delivered

S03's three success criteria — (1) idle session reaped with workspace volume persisting; (2) two-phase liveness check (Redis `last_activity` past `idle_timeout_seconds` AND no live `AttachMap` entry are BOTH required to reap); (3) container lifecycle (reaper kills tmux + removes container; sibling sessions block reap) — are byte-for-byte the same scope that **M002/S04 + M002/S05 already shipped to `main`**. This slice is therefore verification-only, mirroring the M003/S01/T01 pattern exactly: zero orchestrator source, compose, Dockerfile, or test code modified.

## What was already on main (cited, not changed)

- `orchestrator/orchestrator/reaper.py` (359 lines) — full reaper loop with `_reap_one_tick`, `start_reaper`, `stop_reaper`. Two-phase check at L131–L136; container reap path at L187–L274.
- `orchestrator/orchestrator/main.py` L246–L252 — lifespan teardown order (MEM170/MEM190): `stop_reaper` runs FIRST, then `registry.close` → `close_pool` → `docker.close`.
- `orchestrator/orchestrator/sessions.py` — `kill_tmux_session`, `list_tmux_sessions`, `_find_container_by_labels`, `DockerUnavailable` wrapper (MEM168/MEM176).
- `orchestrator/orchestrator/attach_map.py` — `get_attach_map`, `is_attached` (D018 second phase).
- `orchestrator/orchestrator/volume_store.py` L332 — `_resolve_idle_timeout_seconds` (system_settings read per tick).
- `orchestrator/orchestrator/redis_client.py` — `scan_session_keys`, `delete_session`.
- `orchestrator/tests/integration/test_reaper.py` — 12 tests, ~828 lines.
- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py` steps 8–9 (lines ~786–880) — bundled e2e proving volume persistence across reap and successful re-provision against the same (user, team) pair.

## What this slice produced

- `.gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md` (165 lines): one `## Criterion:` section per S03 success criterion (3 sections), 10 verbatim PASSED lines from live test runs, file-and-line citations into reaper.py / sessions.py / attach_map.py / volume_store.py / routes_ws.py / main.py / the e2e source, and a top-level `## Human action required: M003-umluob duplicates M002-jy6pde` block re-stating the same reconciliation hand-off filed by S01/T01 and recorded in MEM200/MEM201/MEM202.
- `.gsd/milestones/M003-umluob/slices/S03/tasks/T02-SUMMARY.md` and `T02-VERIFY.json`.

## Test runs (this slice)

- Orchestrator reaper integration tests (4/4 cited): PASSED in 19.69s against the live compose stack — `test_reaper_kills_idle_session_with_no_attach`, `test_reaper_skips_attached_session`, `test_reaper_reaps_container_when_last_session_killed`, `test_reaper_keeps_container_with_surviving_session`.
- Backend M002/S05 acceptance e2e (1/1 bundled demo proof): PASSED in 30.46s — exercises orchestrator restart, idle reap with 3s window, post-reap container-gone assertion, and `workspace_volume` UUID re-select after re-provision.

## Why this is verification-only

Auto-mode cannot autonomously decide to re-scope or close M003-umluob. The slice plan stopping condition is "verification artifact + grep-able invariants on disk + cited tests pass" — all met. A human owner must reconcile the duplication: close M003 as already-delivered (recommended) or `gsd_replan_slice` toward its real Projects/GitHub scope per R009–R012.

## What downstream slices should know

- **S04, S05, S06 are likely also verification-only** by the same logic (S04 = tmux session model + Redis registry + reattach was shipped under M002/S04 too; S05 = WS bridge was shipped under M002/S04 routes_ws.py + M002/S04 e2e; S06 = final integrated acceptance = literally `test_m002_s05_full_acceptance`). Each slice should make that call independently against its own success criteria, but the prior probability is high.
- The verification-by-citation pattern (T02-VERIFICATION.md with `## Criterion:` sections + verbatim PASSED lines + duplication hand-off) is now established for two slices in a row (S01/T01 and S03/T02). Subsequent verification slices should follow it.
- The M003-umluob ≡ M002-jy6pde duplication hand-off is now filed in **two** verification reports plus three memories (MEM200/MEM201/MEM202, plus MEM205 captured this slice). It is unmissable on disk and in memory.

## Verification

All slice-plan must-haves verified against the live working tree on HEAD `b1afe70`:

1. **T02-VERIFICATION.md exists with required structure** (slice-plan grep gate):
   - File: `.gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md` (165 lines)
   - `grep -c '^## Criterion:'` → **3** (≥3 required) ✅
   - `grep -q 'M003-umluob duplicates M002-jy6pde'` → **present** ✅
   - `grep -c 'PASSED'` → **10** (≥4 required) ✅

2. **Cited tests pass against the real compose stack** (no mocked Docker):
   - `orchestrator && .venv/bin/pytest tests/integration/test_reaper.py::{test_reaper_kills_idle_session_with_no_attach, test_reaper_skips_attached_session, test_reaper_reaps_container_when_last_session_killed, test_reaper_keeps_container_with_surviving_session} -v` → **4 passed in 19.69s**, exit 0 ✅
   - `backend && uv run pytest tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance -v` → **1 passed in 30.46s**, exit 0 ✅

3. **No source/compose/Dockerfile/test code modified** — strict verification + documentation scope. `git status --porcelain` shows only `.gsd/` artifacts changed (T02-* files plus this slice's summary/UAT, written by the engine).

4. **Coverage of all three S03 success criteria** (one Criterion section each in T02-VERIFICATION.md):
   - Criterion 1 (idle reap + volume persistence): proven by `test_reaper_kills_idle_session_with_no_attach` + `test_m002_s05_full_acceptance` step 9 (workspace_volume row survives reap; UUID re-selectable on re-provision).
   - Criterion 2 (two-phase liveness check): proven by `test_reaper_skips_attached_session` (stale `last_activity` + live AttachMap entry → reaper skips).
   - Criterion 3 (container lifecycle): proven by `test_reaper_reaps_container_when_last_session_killed` (last session killed → container removed) + `test_reaper_keeps_container_with_surviving_session` (sibling session blocks reap).

5. **Slice plan demo path proven end-to-end**: the bundled M002/S05 e2e step 9 IS the S03 demo (provision → write marker → idle → reap → container gone → workspace_volume row survives → re-provision succeeds against same user_id/team_id).

## Requirements Advanced

None.

## Requirements Validated

None.

## New Requirements Surfaced

None.

## Requirements Invalidated or Re-scoped

None.

## Operational Readiness

None.

## Deviations

"None. Slice plan called for verification + documentation only; that is exactly what was produced. Working tree was clean at HEAD b1afe70 before and after T02 execution."

## Known Limitations

"This slice does NOT decide whether M003-umluob should be closed as already-delivered or replanned toward its true Projects/GitHub scope (R009–R012). Auto-mode cannot make that call. The duplication hand-off is now filed in T01-VERIFICATION.md (S01) AND T02-VERIFICATION.md (S03) AND four memories (MEM200/MEM201/MEM202/MEM205). M003/S04, S05, S06 are likely also verification-only by the same logic but each slice should make that call independently against its own success criteria."

## Follow-ups

"HUMAN ACTION REQUIRED: Reconcile M003-umluob ≡ M002-jy6pde duplication before S04/S05/S06 proceed. Two paths: (a) close M003 as already-delivered (recommended; M003 then pivots to its true scope), or (b) gsd_replan_slice so M003-umluob owns *new* work — most plausibly the Projects/GitHub scope (R009–R012) that the rest of M003 pre-supposes."

## Files Created/Modified

- `.gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md` — New: 165-line citation-by-test verification artifact with 3 Criterion sections, 10 verbatim PASSED lines, file:line citations into reaper.py/sessions.py/attach_map.py/volume_store.py/routes_ws.py/main.py/the e2e source, and the M003≡M002 duplication hand-off.
- `.gsd/milestones/M003-umluob/slices/S03/tasks/T02-SUMMARY.md` — Task summary written by the executor for T02 — verification-result=passed, blocker_discovered=false, no source/test files modified.
- `.gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFY.json` — Machine-readable verification record for T02.
