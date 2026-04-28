# S01: Orchestrator skeleton + container provisioning (verification slice) — UAT

**Milestone:** M003-umluob
**Written:** 2026-04-25T14:26:54.435Z

# UAT — M003-umluob/S01: Orchestrator skeleton + container provisioning (verification slice)

**Scope:** This is a verification slice over already-shipped M002 code. UAT here re-runs the proof-by-citation evidence rather than exercising new functionality. The slice produced no user-facing change; success is "verification artifact exists and proofs hold."

**Preconditions:**
- Repo at `/Users/josh/code/perpetuity` on branch `main` with no in-flight changes to `orchestrator/`, `docker-compose.yml`, or `workspace.Dockerfile`.
- Docker daemon up; compose stack running (`db`, `redis`, `orchestrator`).
- Required images present locally: `orchestrator:latest`, `perpetuity/workspace:latest`, `perpetuity/workspace:test`.
- `.env` populated with `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_PORT=55432` (MEM021), `REDIS_PASSWORD`, `ORCHESTRATOR_API_KEY`.
- Orchestrator venv at `orchestrator/.venv/bin/pytest` initialized; backend uses `uv run pytest` from `backend/` (MEM041).

---

## UAT-1: Verification artifact exists and is well-formed

**Steps:**
1. From repo root, run: `test -f .gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md && echo OK`
2. Run: `grep -c '^## Criterion: ' .gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md`
3. Run: `grep -c 'M003-umluob duplicates M002-jy6pde' .gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md`
4. Run: `grep -cE '^- (PASS|FAIL): ' .gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md`

**Expected:**
1. Prints `OK`.
2. ≥ 6 criterion sections (one per success criterion plus aggregate; T01 emits 7 criterion-blocks).
3. ≥ 1 occurrence of the duplication note.
4. ≥ 7 PASS lines, 0 FAIL lines.

---

## UAT-2: Re-run the auth unit tests

**Steps:**
1. From `orchestrator/`: `set -a && . ../.env && set +a && .venv/bin/pytest tests/unit/test_auth.py -v`

**Expected:**
- All 10 auth tests pass: `test_http_correct_key_returns_200`, `test_http_wrong_key_returns_401`, `test_http_missing_key_returns_401`, `test_http_previous_key_accepted_during_rotation`, `test_http_health_is_public`, `test_ws_correct_key_accepts`, `test_ws_wrong_key_closes_1008`, `test_ws_missing_key_closes_1008`, `test_ws_previous_key_accepted`, `test_ws_unauthorized_log_redacts_full_key`.
- Final line: `10 passed`.

---

## UAT-3: Re-run image pull integration tests

**Steps:**
1. From `orchestrator/` with env loaded: `.venv/bin/pytest tests/integration/test_image_pull.py::test_image_pull_ok_for_existing_image tests/integration/test_image_pull.py::test_image_pull_failed_exits_nonzero -v`

**Expected:**
- Both tests PASS. `2 passed`.
- `test_image_pull_failed_exits_nonzero` confirms hard-fail-on-boot behavior when image is missing.

---

## UAT-4: Re-run sessions lifecycle integration tests

**Steps:**
1. From `orchestrator/` with env loaded: `.venv/bin/pytest tests/integration/test_sessions_lifecycle.py::test_create_session_provisions_container_and_tmux tests/integration/test_sessions_lifecycle.py::test_second_session_reuses_container_multi_tmux tests/integration/test_sessions_lifecycle.py::test_delete_kills_one_session_keeps_others tests/integration/test_sessions_lifecycle.py::test_list_sessions_filters_by_user_team tests/integration/test_sessions_lifecycle.py::test_missing_api_key_returns_401 -v`

**Expected:**
- All 5 tests PASS. `5 passed`.
- `test_create_session_provisions_container_and_tmux` asserts: container labels (`user_id`, `team_id`, `perpetuity.managed=true`), `HostConfig.Memory == 2 * 1024**3`, `HostConfig.PidsLimit == 512`, `HostConfig.NanoCpus == 1_000_000_000` (the documented divergence).
- `test_second_session_reuses_container_multi_tmux` asserts the second POST returns the same `container_id` and emits `container_reused`.
- `test_list_sessions_filters_by_user_team` asserts the GET filter respects label scoping.
- `test_delete_kills_one_session_keeps_others` asserts targeted DELETE doesn't affect peers.
- `test_missing_api_key_returns_401` asserts requests with no `X-Orchestrator-Key` return 401.

---

## UAT-5: Re-run the M002/S01 backend e2e capstone

**Steps:**
1. From `backend/`: `uv run pytest tests/integration/test_m002_s01_e2e.py::test_m002_s01_full_e2e -v`

**Expected:**
- `1 passed`. End-to-end: signup → create session via backend → backend calls orchestrator → orchestrator provisions container → DELETE through backend removes container. This is the integrated proof for criteria 3 and 6.

---

## UAT-6 (Edge): Static check — `/var/run/docker.sock` is mounted only by orchestrator

**Steps:**
1. From repo root: `grep -n '/var/run/docker.sock' docker-compose.yml`
2. Inspect surrounding service block for each match.

**Expected:**
- Exactly one match (line ~102) under the `orchestrator:` service. No `docker.sock` references under `db`, `redis`, `backend`, `frontend`, `prestart`, or `adminer`.

---

## UAT-7 (Edge): Static check — `privileged: true` is on orchestrator and only orchestrator

**Steps:**
1. From repo root: `grep -n 'privileged: true' docker-compose.yml`

**Expected:**
- Exactly one match (line ~122), within the `orchestrator:` block.

---

## UAT-8 (Edge): Confirm the known accepted nano_cpus divergence is not regressed in either direction

**Steps:**
1. From repo root: `grep -n 'container_nano_cpus' orchestrator/orchestrator/config.py`

**Expected:**
- `container_nano_cpus: int = 1_000_000_000` is present (the accepted carry-over from M002).
- If this value changes (either to spec's 2_000_000_000 or to anything else), update the divergence record in `T01-VERIFICATION.md` and re-run UAT-4.

---

## Out-of-scope for UAT (recorded for reconciliation)

- M003-umluob/S01 success criteria duplicate M002-jy6pde/S01. A human owner must decide whether M003 is closed as already-delivered or replanned via `gsd_replan_slice` toward Projects/GitHub scope (R009–R012). This is documented in `T01-VERIFICATION.md` and memory MEM202; the auto-mode pipeline will flag it again at reassess-roadmap.

## Pass criteria for the slice

- All UAT-1 through UAT-8 pass.
- No new FAIL lines appear in `T01-VERIFICATION.md`.
- The duplication note and known-divergence note remain present.
