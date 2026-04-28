---
id: T01
parent: S01
milestone: M003-umluob
key_files:
  - .gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md
key_decisions:
  - Treated M003/S01 as verification slice over already-shipped M002 code, per planner's documented auto-mode assumption — produced report rather than re-implementing.
  - Recorded the nano_cpus 1_000_000_000 vs 2_000_000_000 cap as a known accepted divergence (per PROJECT.md and task plan) rather than failing verification on it.
  - Filed M003-umluob/M002-jy6pde duplication as a human-action note inside T01-VERIFICATION.md so a human owner can reconcile (close as delivered, or re-plan toward R009–R012 Projects-and-GitHub scope).
duration: 
verification_result: passed
completed_at: 2026-04-25T14:24:23.971Z
blocker_discovered: false
---

# T01: Verify M003/S01 success criteria against existing M002 implementation and file M003/M002 duplication note

**Verify M003/S01 success criteria against existing M002 implementation and file M003/M002 duplication note**

## What Happened

Executed T01 as a verification-only slice (no orchestrator code touched). Confirmed by static citation and live test runs that all seven M003/S01 success criteria are already satisfied by code shipped under M002-jy6pde and unchanged in main.

Static checks against the source-of-truth files:
- `docker-compose.yml` — orchestrator service at line 71 is the SOLE service mounting `/var/run/docker.sock` (line 102) and runs `privileged: true` (line 122; D014/MEM136).
- `orchestrator/orchestrator/sessions.py` — `_container_name` enforces `perpetuity-ws-<first8-team>` (L69), `_container_config` sets labels `user_id`/`team_id`/`perpetuity.managed=true` (L129–131) and `Memory`/`PidsLimit`/`NanoCpus` from settings (L134–136); `_find_container_by_labels` filters by `user_id=`, `team_id=`, `perpetuity.managed=true` (L188–190); `provision_session` reuse path returns the existing container without re-creating (L282–290).
- `orchestrator/orchestrator/main.py` — `_ensure_workspace_image` raises `ImagePullFailed` on inspect/pull failure; lifespan exits non-zero on hard fail.
- `orchestrator/orchestrator/auth.py` — `_collect_keys` aggregates primary + previous keys, `verify_http_key` raises HTTP 401 on miss (L125), lifespan refuses to boot without primary key (L161–164).
- `orchestrator/orchestrator/config.py` — defaults `container_mem_limit="2g"` (L36), `container_pids_limit=512` (L37), `container_nano_cpus=1_000_000_000` (L39). The 1.0 vCPU value is the documented divergence carried over from M002 per PROJECT.md (spec asks for 2_000_000_000 / 2.0 vCPU); per the task plan this is a known accepted divergence and does NOT fail verification.

Test runs (env loaded from project `.env`):
- `orchestrator/.venv/bin/pytest tests/unit/test_auth.py` — 10 passed in 0.22s.
- `orchestrator/.venv/bin/pytest tests/integration/test_image_pull.py::{test_image_pull_ok_for_existing_image, test_image_pull_failed_exits_nonzero}` — 2 passed in 2.73s.
- `orchestrator/.venv/bin/pytest tests/integration/test_sessions_lifecycle.py::{test_create_session_provisions_container_and_tmux, test_second_session_reuses_container_multi_tmux, test_delete_kills_one_session_keeps_others, test_list_sessions_filters_by_user_team, test_missing_api_key_returns_401}` — 5 passed in 9.57s.
- `backend && uv run pytest tests/integration/test_m002_s01_e2e.py::test_m002_s01_full_e2e` — 1 passed in 20.62s.

Total: 18 distinct test executions, 0 failures. Verbatim PASS lines captured in `T01-VERIFICATION.md` keyed by success criterion.

Human-action note (filed in T01-VERIFICATION.md and as a memory): M003-umluob's S01 success criteria are byte-for-byte M002-jy6pde/S01 — auto-mode cannot decide whether M003 should be (a) closed as already-delivered or (b) re-planned via `gsd_replan_slice` to its true scope (most plausibly the R009–R012 Projects-and-GitHub work referenced elsewhere in PROJECT.md). The slice's auto-mode interpretation is the verification-slice reading; a human owner must reconcile before subsequent M003 slices proceed.

No orchestrator source, docker-compose.yml, or workspace Dockerfile was modified, per task scope.

## Verification

Slice-plan verification command (`test -f T01-VERIFICATION.md && grep ^## Criterion: && grep 'M003-umluob duplicates M002-jy6pde' && PASS-line count >= 7`) ran from /Users/josh/code/perpetuity and exited 0 with PASS_COUNT=16 (≥7 required). Each cited test was executed live and passed; runner output is reproduced verbatim in T01-VERIFICATION.md keyed to the seven success criteria.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `orchestrator/.venv/bin/pytest tests/unit/test_auth.py` | 0 | ✅ pass | 220ms |
| 2 | `orchestrator/.venv/bin/pytest tests/integration/test_image_pull.py::test_image_pull_ok_for_existing_image tests/integration/test_image_pull.py::test_image_pull_failed_exits_nonzero` | 0 | ✅ pass | 2730ms |
| 3 | `orchestrator/.venv/bin/pytest tests/integration/test_sessions_lifecycle.py::test_create_session_provisions_container_and_tmux tests/integration/test_sessions_lifecycle.py::test_second_session_reuses_container_multi_tmux tests/integration/test_sessions_lifecycle.py::test_delete_kills_one_session_keeps_others tests/integration/test_sessions_lifecycle.py::test_list_sessions_filters_by_user_team tests/integration/test_sessions_lifecycle.py::test_missing_api_key_returns_401` | 0 | ✅ pass | 9570ms |
| 4 | `backend && uv run pytest tests/integration/test_m002_s01_e2e.py::test_m002_s01_full_e2e` | 0 | ✅ pass | 20620ms |
| 5 | `test -f T01-VERIFICATION.md && grep '^## Criterion: ' && grep 'M003-umluob duplicates M002-jy6pde' && PASS-line count >= 7` | 0 | ✅ pass | 30ms |

## Deviations

None from the inlined task plan. Test runs were performed via the local orchestrator/.venv and backend `uv run pytest` (per MEM041 — backend tests must run from backend/), with env loaded from project .env (per MEM021 for POSTGRES_PORT=55432 and POSTGRES_*/REDIS_PASSWORD).

## Known Issues

M003-umluob/S01's success criteria duplicate M002-jy6pde/S01 byte-for-byte. A human owner must reconcile (close M003 as delivered, or re-scope via gsd_replan_slice) before subsequent M003 slices proceed.

## Files Created/Modified

- `.gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md`
