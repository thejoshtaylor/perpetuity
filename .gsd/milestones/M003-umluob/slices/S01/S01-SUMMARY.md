---
id: S01
parent: M003-umluob
milestone: M003-umluob
provides:
  - ["Verification artifact T01-VERIFICATION.md proving the seven M003/S01 success criteria are met by code already in main.", "Documented duplication note (M003-umluob ≡ M002-jy6pde) for the next human or reassess-roadmap pass to act on.", "Documented accepted divergence record (nano_cpus=1_000_000_000)."]
requires:
  []
affects:
  []
key_files:
  - [".gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md", ".gsd/milestones/M003-umluob/slices/S01/tasks/T01-SUMMARY.md", "docker-compose.yml", "orchestrator/orchestrator/sessions.py", "orchestrator/orchestrator/main.py", "orchestrator/orchestrator/auth.py", "orchestrator/orchestrator/config.py"]
key_decisions:
  - ["Treated M003/S01 as verification slice over already-shipped M002 code, per planner's auto-mode assumption — produced citation-by-test report rather than re-implementing.", "Recorded nano_cpus=1_000_000_000 vs 2_000_000_000 as a known accepted divergence carried over from M002 (per PROJECT.md); did NOT fail verification on it.", "Filed M003-umluob/M002-jy6pde duplication as a human-action note (in T01-VERIFICATION.md and in memory MEM202) so a human owner reconciles before subsequent M003 slices proceed.", "Did not modify any orchestrator source, docker-compose.yml, or workspace Dockerfile — strict verification + documentation scope."]
patterns_established:
  - ["Verification slice pattern: when a milestone's success criteria duplicate a prior milestone's already-shipped scope, run the existing test suite and produce a single citation-by-test report rather than re-implementing. The report is the deliverable; the slice plan's `Verify:` command is the stopping condition.", "Human-action note pattern: when auto-mode encounters a decision it cannot make (milestone scope reconciliation, dependency graph rewrites), file the note inline in the slice's verification artifact AND in memory (gotcha category) so reassess-roadmap and future agents see it.", "Static + live evidence pattern: combine `grep`-based static citations against source-of-truth files with verbatim PASS lines from live test runs. Cite the file paths and line numbers in the report so future readers can independently verify."]
observability_surfaces:
  - ["No new observability surfaces introduced. Existing M002 INFO keys preserved: orchestrator_starting, orchestrator_ready, image_pull_ok, container_provisioned, container_reused, session_created. Existing ERROR keys preserved: image_pull_failed, orchestrator_ws_unauthorized."]
drill_down_paths:
  - [".gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md", ".gsd/milestones/M003-umluob/slices/S01/tasks/T01-SUMMARY.md", ".gsd/milestones/M003-umluob/M003-umluob-ROADMAP.md"]
duration: ""
verification_result: passed
completed_at: 2026-04-25T14:26:54.434Z
blocker_discovered: false
---

# S01: Orchestrator skeleton + container provisioning (verification slice)

**Verified all seven M003/S01 success criteria are already met by M002-shipped orchestrator code; produced citation-by-test report and filed human-action note about M003-umluob/M002-jy6pde duplication.**

## What Happened

M003-umluob/S01's success criteria are byte-for-byte identical to M002-jy6pde/S01 (which is `COMPLETE` per PROJECT.md). The planner correctly interpreted this slice as verification-only over already-shipped code, not re-implementation. T01 executed that interpretation: it ran the existing test suite that covers every criterion, captured verbatim PASS lines into `T01-VERIFICATION.md` keyed per-criterion, and filed a human-action note about the milestone duplication.

No orchestrator source, `docker-compose.yml`, or workspace Dockerfile was modified — scope was strictly verification + documentation. The verification ran static checks against source-of-truth files (`docker-compose.yml`, `orchestrator/orchestrator/sessions.py`, `orchestrator/orchestrator/main.py`, `orchestrator/orchestrator/auth.py`, `orchestrator/orchestrator/config.py`) and live test runs from the project root with `.env` loaded:

- `orchestrator/.venv/bin/pytest tests/unit/test_auth.py` — 10 passed in 0.22s
- `orchestrator/.venv/bin/pytest tests/integration/test_image_pull.py::{test_image_pull_ok_for_existing_image,test_image_pull_failed_exits_nonzero}` — 2 passed in 2.73s
- `orchestrator/.venv/bin/pytest tests/integration/test_sessions_lifecycle.py::{test_create_session_provisions_container_and_tmux,test_second_session_reuses_container_multi_tmux,test_delete_kills_one_session_keeps_others,test_list_sessions_filters_by_user_team,test_missing_api_key_returns_401}` — 5 passed in 9.57s
- `backend && uv run pytest tests/integration/test_m002_s01_e2e.py::test_m002_s01_full_e2e` — 1 passed in 20.62s

7 of 7 success criteria PASS by citation. 0 regressions. 1 known accepted divergence recorded (`nano_cpus=1_000_000_000` vs spec's `2_000_000_000` — pre-existing M002 carry-over per PROJECT.md). 1 human-action note filed.

The slice's stopping condition (its plan's verification command) was met: `T01-VERIFICATION.md` exists, contains `## Criterion:` sections, contains the duplication note, and has ≥7 PASS lines (16 actual). The slice produced no new code; the verification artifact is the deliverable.

Important downstream note for the reassess-roadmap agent and future researchers: M003-umluob's remaining slices (S02–S06) describe the same terminal-infrastructure scope M002 already shipped (loopback volumes, idle reaper, tmux + Redis, WS bridge, final acceptance). Per the in-task memory MEM202 and the in-report human-action note, a human owner must reconcile M003 vs M002 before those slices run — either close M003 as already-delivered, or `gsd_replan_slice` it toward its true scope (most plausibly R009–R012 Projects/GitHub work).

## Verification

All seven M003/S01 success criteria PASS by proof-by-citation:

1. **Orchestrator service exists; sole `/var/run/docker.sock` mount; privileged** — `docker-compose.yml` line 71 (orchestrator service), line 102 (sole socket mount, scoped to orchestrator only), line 122 (`privileged: true`). PASS by static check.
2. **Image hard-fails on boot if `perpetuity/workspace:latest` missing** — `test_image_pull_ok_for_existing_image` PASS, `test_image_pull_failed_exits_nonzero` PASS.
3. **`POST /v1/sessions` provisions per-(user,team) container with labels + limits + name policy** — `test_create_session_provisions_container_and_tmux` PASS (asserts created==True, labels user_id/team_id/perpetuity.managed=true, HostConfig.Memory==2 GiB, PidsLimit==512, NanoCpus==1_000_000_000), `test_m002_s01_full_e2e` PASS.
4. **`GET /v1/sessions?user_id=&team_id=` lists by labels** — `test_list_sessions_filters_by_user_team` PASS.
5. **Second `POST` reuses warm container** — `test_second_session_reuses_container_multi_tmux` PASS.
6. **`DELETE` removes the container** — `test_delete_kills_one_session_keeps_others` PASS, `test_m002_s01_full_e2e` PASS.
7. **All HTTP requests without correct `X-Orchestrator-Key` return 401 (with two-key rotation support)** — 5 unit tests + `test_missing_api_key_returns_401` integration: PASS.

Slice plan verification command: `test -f T01-VERIFICATION.md && grep ^## Criterion: && grep 'M003-umluob duplicates M002-jy6pde' && PASS-line count >= 7` — exit 0 with PASS_COUNT=16 (≥7 required).

Known accepted divergence (not failing): `nano_cpus=1_000_000_000` shipped vs `2_000_000_000` spec — pre-existing M002 carry-over.

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

"None from the inlined task plan. Test runs were performed via the local `orchestrator/.venv` and backend `uv run pytest` (per MEM041 — backend tests must run from `backend/`), with env loaded from project `.env` (MEM021 for `POSTGRES_PORT=55432` and `POSTGRES_*`/`REDIS_PASSWORD`)."

## Known Limitations

"This slice produced zero new code. If a human owner concludes M003 should drive new work, the slice should be re-planned via `gsd_replan_slice` and re-executed — the verification artifact does not satisfy a re-scoped definition of M003/S01.\n\nThe `nano_cpus` cap divergence is recorded but not fixed. Containers ship with 1.0 vCPU rather than the spec's 2.0 vCPU; this is an accepted M002 carry-over, not a regression introduced here."

## Follow-ups

"**Human-action required before subsequent M003 slices run:** M003-umluob's remaining slices (S02 volumes, S03 reaper, S04 tmux+Redis, S05 WS bridge, S06 final acceptance) describe the same terminal-infrastructure scope that M002-jy6pde already shipped. A human owner must reconcile by either (a) closing M003 as already-delivered (recommended; M003 then pivots to its real scope) or (b) calling `gsd_replan_slice` to re-scope M003 toward its true Projects/GitHub work (R009–R012 per PROJECT.md). Auto-mode cannot make this call.\n\n**Pre-existing accepted divergence (not blocking):** `nano_cpus=1_000_000_000` (1.0 vCPU) shipped vs spec's `2_000_000_000` (2.0 vCPU). Carried over from M002 per PROJECT.md and MEM follow-ups. Track separately if/when the human owner wants to bump CPU allocation."

## Files Created/Modified

- `.gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md` — New verification report — proof-by-citation for all 7 success criteria, known-divergence record, and human-action note about M003/M002 duplication.
- `.gsd/milestones/M003-umluob/slices/S01/tasks/T01-SUMMARY.md` — Task summary with verification evidence table and key decisions.
