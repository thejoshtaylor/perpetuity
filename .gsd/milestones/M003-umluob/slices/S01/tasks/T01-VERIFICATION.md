# T01 Verification Report — M003-umluob / S01

**Slice:** S01 — Orchestrator skeleton + container provisioning
**Milestone:** M003-umluob
**Task:** T01 — Verify M003/S01 success criteria against existing codebase and file follow-up note
**Date:** 2026-04-25
**Verdict:** ✅ ALL CRITERIA PASS (verification slice; no new orchestrator code in scope)

This report proves M003/S01's success criteria by citation against tests already in `main`. M003-umluob inherits its implementation unchanged from M002/S01 (PROJECT.md: `M002-jy6pde — COMPLETE`; commits e54a3d4, fb0a2ec, 3a52462, etc.). The slice's stopping condition is this artifact, not new code.

## Human action required: M003-umluob duplicates M002-jy6pde

The seven success criteria for M003/S01 are **byte-for-byte the same set** that M002/S01 already shipped and that ship-tests still cover. Auto-mode cannot decide whether M003 should be:
- (a) closed as already-delivered (recommended path; M003 then pivots to its true scope), or
- (b) re-planned with `gsd_replan_slice` so that M003-umluob owns *new* work — most plausibly the Projects-and-GitHub scope (R009–R012 per PROJECT.md) that the rest of M003 pre-supposes.

A human owner must reconcile this before subsequent M003 slices proceed; the planner's auto-mode assumption is recorded in `T01-PLAN.md` (the verification-slice interpretation).

## Known accepted divergences

- **`nano_cpus = 1_000_000_000` (1.0 vCPU) shipped, vs. spec's `2_000_000_000` (2.0 vCPU).** Pre-existing accepted divergence carried over from M002 per PROJECT.md and MEM follow-ups; not failing this verification. Tracked separately for the human owner.

## Verification environment

- Host Docker daemon up; `perpetuity-db-1` (postgres:18 on host port 55432, MEM021), `perpetuity-redis-1`, and `perpetuity-orchestrator-1` running.
- Required images present locally: `orchestrator:latest`, `perpetuity/workspace:latest`, `perpetuity/workspace:test`.
- Tests executed from working directory `/Users/josh/code/perpetuity` with env loaded from `.env` (POSTGRES_PASSWORD/POSTGRES_USER/POSTGRES_DB/REDIS_PASSWORD).
- Orchestrator suite via `orchestrator/.venv/bin/pytest`; backend e2e via `backend` `uv run pytest`.

---

## Criterion: Orchestrator service exists; sole `/var/run/docker.sock` mount; privileged

**Source-of-truth files:** `docker-compose.yml`

**Static evidence (grep `docker-compose.yml`):**
- Service block declared at line 71: `  orchestrator:`
- The ONLY `docker.sock` mount in the file is line 102, scoped under the `orchestrator:` block:
  `      - /var/run/docker.sock:/var/run/docker.sock`
- Other services discovered (`db:` L3, `adminer:` L22, `redis:` L32, `prestart:` L142, `backend:` L175, `frontend:` L221) — none reference `docker.sock`.
- `privileged: true` is present at line 122 inside the orchestrator block (D014/MEM136; strictly stronger than CAP_SYS_ADMIN, accepted).

**Verdict:**
- PASS: docker-compose.yml line 102 is the only `/var/run/docker.sock` mount; it is scoped to the `orchestrator:` service.
- PASS: docker-compose.yml line 122 sets `privileged: true` on the orchestrator service.

---

## Criterion: Image hard-fails on boot if `perpetuity/workspace:latest` missing

**Source-of-truth files:** `orchestrator/orchestrator/main.py` (`_ensure_workspace_image`, lifespan)

**Tests covering criterion:**
- `orchestrator/tests/integration/test_image_pull.py::test_image_pull_ok_for_existing_image`
- `orchestrator/tests/integration/test_image_pull.py::test_image_pull_failed_exits_nonzero`

**Run command:** `.venv/bin/pytest tests/integration/test_image_pull.py::test_image_pull_ok_for_existing_image tests/integration/test_image_pull.py::test_image_pull_failed_exits_nonzero -v` (from `orchestrator/`, env loaded from project `.env`)

**Verbatim runner output:**
```
tests/integration/test_image_pull.py::test_image_pull_ok_for_existing_image PASSED [ 50%]
tests/integration/test_image_pull.py::test_image_pull_failed_exits_nonzero PASSED [100%]
============================== 2 passed in 2.73s ===============================
```

**Verdict:**
- PASS: test_image_pull_ok_for_existing_image
- PASS: test_image_pull_failed_exits_nonzero

---

## Criterion: `POST /v1/sessions` provisions a per-(user, team) container with required labels, resource limits, and name policy

**Source-of-truth files:** `orchestrator/orchestrator/sessions.py` (`_container_name` L69, `_container_config` L107, `provision_session` L239)

**Static-code spot checks (sessions.py):**
- Container name policy `perpetuity-ws-<first8-team>` at L69–79.
- Labels include `user_id`, `team_id`, `perpetuity.managed=true` at L129–131.
- HostConfig sets `Memory`, `PidsLimit`, `NanoCpus` from settings at L134–136.
- Defaults in `orchestrator/orchestrator/config.py`: `container_mem_limit="2g"` (L36), `container_pids_limit=512` (L37), `container_nano_cpus=1_000_000_000` (L39). The `1.0 vCPU` value is the documented divergence (see "Known accepted divergences" above); 2g and 512 are spec-compliant.

**Tests covering criterion:**
- `orchestrator/tests/integration/test_sessions_lifecycle.py::test_create_session_provisions_container_and_tmux` (asserts: created==True, container labels `user_id`/`team_id`/`perpetuity.managed=true`, tmux session present, HostConfig.Memory==2 GiB, HostConfig.PidsLimit==512, HostConfig.NanoCpus==1_000_000_000)
- `backend/tests/integration/test_m002_s01_e2e.py::test_m002_s01_full_e2e` (capstone)

**Run output (excerpt):**
```
tests/integration/test_sessions_lifecycle.py::test_create_session_provisions_container_and_tmux PASSED [ 20%]
```
```
tests/integration/test_m002_s01_e2e.py::test_m002_s01_full_e2e PASSED    [100%]
======================== 1 passed, 3 warnings in 20.62s ========================
```

**Verdict:**
- PASS: test_create_session_provisions_container_and_tmux
- PASS: test_m002_s01_full_e2e (capstone)

---

## Criterion: `GET /v1/sessions?user_id=&team_id=` lists by labels

**Source-of-truth files:** `orchestrator/orchestrator/sessions.py` (`_find_container_by_labels` L172, label filter list L188–190)

**Tests covering criterion:**
- `orchestrator/tests/integration/test_sessions_lifecycle.py::test_list_sessions_filters_by_user_team`

**Verbatim runner output:**
```
tests/integration/test_sessions_lifecycle.py::test_list_sessions_filters_by_user_team PASSED [ 80%]
```

**Verdict:**
- PASS: test_list_sessions_filters_by_user_team

---

## Criterion: Second `POST` for same `(user, team)` reuses the warm container

**Source-of-truth files:** `orchestrator/orchestrator/sessions.py` (`provision_session` reuse path at L282–290 — `existing = await _find_container_by_labels(...)`; `container_reused` log; returns existing.id without re-creating)

**Tests covering criterion:**
- `orchestrator/tests/integration/test_sessions_lifecycle.py::test_second_session_reuses_container_multi_tmux`

**Verbatim runner output:**
```
tests/integration/test_sessions_lifecycle.py::test_second_session_reuses_container_multi_tmux PASSED [ 40%]
```

**Verdict:**
- PASS: test_second_session_reuses_container_multi_tmux

---

## Criterion: `DELETE` removes the container

**Source-of-truth files:** `orchestrator/orchestrator/sessions.py` (delete handler), exercised by both the orchestrator-level lifecycle test and the backend e2e capstone.

**Tests covering criterion:**
- `orchestrator/tests/integration/test_sessions_lifecycle.py::test_delete_kills_one_session_keeps_others`
- `backend/tests/integration/test_m002_s01_e2e.py::test_m002_s01_full_e2e` (full lifecycle including container removal)

**Verbatim runner output:**
```
tests/integration/test_sessions_lifecycle.py::test_delete_kills_one_session_keeps_others PASSED [ 60%]
```
```
tests/integration/test_m002_s01_e2e.py::test_m002_s01_full_e2e PASSED    [100%]
```

**Verdict:**
- PASS: test_delete_kills_one_session_keeps_others
- PASS: test_m002_s01_full_e2e (covers DELETE end-to-end)

---

## Criterion: All HTTP requests without correct `X-Orchestrator-Key` return 401 (two-key support during rotation)

**Source-of-truth files:** `orchestrator/orchestrator/auth.py` (`_collect_keys` L57–62 supports primary + previous; `verify_http_key` raises 401 at L125; lifespan refuses to boot without primary key at L161–164)

**Tests covering criterion:**
- `orchestrator/tests/unit/test_auth.py::test_http_correct_key_returns_200`
- `orchestrator/tests/unit/test_auth.py::test_http_wrong_key_returns_401`
- `orchestrator/tests/unit/test_auth.py::test_http_missing_key_returns_401`
- `orchestrator/tests/unit/test_auth.py::test_http_previous_key_accepted_during_rotation`
- `orchestrator/tests/unit/test_auth.py::test_http_health_is_public`
- `orchestrator/tests/integration/test_sessions_lifecycle.py::test_missing_api_key_returns_401`

**Verbatim runner output (unit, all 10 auth tests collected):**
```
tests/unit/test_auth.py::test_http_correct_key_returns_200 PASSED        [ 10%]
tests/unit/test_auth.py::test_http_wrong_key_returns_401 PASSED          [ 20%]
tests/unit/test_auth.py::test_http_missing_key_returns_401 PASSED        [ 30%]
tests/unit/test_auth.py::test_http_previous_key_accepted_during_rotation PASSED [ 40%]
tests/unit/test_auth.py::test_http_health_is_public PASSED               [ 50%]
tests/unit/test_auth.py::test_ws_correct_key_accepts PASSED              [ 60%]
tests/unit/test_auth.py::test_ws_wrong_key_closes_1008 PASSED            [ 70%]
tests/unit/test_auth.py::test_ws_missing_key_closes_1008 PASSED          [ 80%]
tests/unit/test_auth.py::test_ws_previous_key_accepted PASSED            [ 90%]
tests/unit/test_auth.py::test_ws_unauthorized_log_redacts_full_key PASSED [100%]
============================== 10 passed in 0.22s ==============================
```

**Verbatim runner output (integration):**
```
tests/integration/test_sessions_lifecycle.py::test_missing_api_key_returns_401 PASSED [100%]
```

**Verdict:**
- PASS: test_http_correct_key_returns_200
- PASS: test_http_wrong_key_returns_401
- PASS: test_http_missing_key_returns_401
- PASS: test_http_previous_key_accepted_during_rotation
- PASS: test_http_health_is_public
- PASS: test_missing_api_key_returns_401

---

## Aggregate result

- 7 of 7 success criteria PASS by citation against tests in `main`.
- 0 regressions surfaced.
- 1 known accepted divergence (`nano_cpus=1_000_000_000`) recorded; not failing.
- 1 human-action note filed (M003-umluob duplicates M002-jy6pde).

No remediation work in scope for this slice. Future agent reconciling M003 vs M002 should:
1. Read this file (`cat .gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md`).
2. Decide between closing M003 as already-delivered or re-scoping it via `gsd_replan_slice` after re-planning M003 in the roadmap (likely toward R009–R012 Projects-and-GitHub scope).
