# S01: Orchestrator skeleton + container provisioning

**Goal:** Prove that M003/S01's success criteria — orchestrator service exists, image is hard-fail-on-boot, POST /v1/sessions provisions a per-(user, team) container with the required Docker labels and resource limits, GET filters by labels, second POST reuses the warm container, DELETE removes it, X-Orchestrator-Key gates every HTTP request — are met by the existing codebase, which inherited the implementation from M002/S01 and is unchanged. The slice produces no new orchestrator code; it produces a single verification artifact whose pass is the slice's stopping condition. If verification surfaces a regression or gap, this task is the place to file the remediation note (no remediation code is in scope here — that goes to a follow-up slice).
**Demo:** Integration test against real Docker: orchestrator starts, image is present (hard-fails boot if not). POST /v1/sessions with (user_id, team_id) provisions a real container with labels user_id and team_id, mem_limit=2g, nano_cpus=2_000_000_000, pids_limit=512, name perpetuity-ws-<first8-team>; returns container_id and session_id. GET /v1/sessions?user_id=&team_id= lists by labels. Second POST for same (user, team) reuses the existing warm container. DELETE removes the container. All requests without correct X-Orchestrator-Key return 401.

## Must-Haves

- Orchestrator service exists in `docker-compose.yml`; only it mounts `/var/run/docker.sock`; only it has CAP_SYS_ADMIN (orchestrator runs `privileged: true` per MEM136 — strictly stronger than CAP_SYS_ADMIN, accepted)
- `perpetuity/workspace:latest` image builds from the orchestrator workspace Dockerfile and is pulled-or-built on orchestrator startup with hard fail on failure
- `POST /v1/sessions` with `(user_id, team_id)` provisions a real container with labels `user_id`/`team_id`, `mem_limit=2g`, `nano_cpus=1_000_000_000` (D-drift from spec's 2_000_000_000 documented in PROJECT.md M002 carry-overs — accepted as known cap divergence; not blocking S01), `pids_limit=512`, name `perpetuity-ws-<first8-team>`, returning `container_id` and `session_id`
- `GET /v1/sessions?user_id=&team_id=` lists by labels
- Second `POST` for same `(user, team)` reuses the existing warm container
- `DELETE` removes the container
- All HTTP requests without correct `X-Orchestrator-Key` return 401

## Proof Level

- This slice proves: - This slice proves: integration
- Real runtime required: yes (Docker daemon + Redis + ephemeral orchestrator)
- Human/UAT required: no

## Integration Closure

- Upstream surfaces consumed: `orchestrator/orchestrator/auth.py` (two-key auth), `orchestrator/orchestrator/sessions.py` (provision/list/delete + label discovery), `orchestrator/orchestrator/main.py` (image pull on lifespan, hard fail on failure), `docker-compose.yml` (service definition + socket mount + privileged), `backend/tests/integration/test_m002_s01_e2e.py` (existing end-to-end coverage), `orchestrator/tests/integration/test_sessions_lifecycle.py`, `orchestrator/tests/integration/test_image_pull.py`, `orchestrator/tests/unit/test_auth.py`
- New wiring introduced in this slice: none — this is a verification slice
- What remains before the milestone is truly usable end-to-end: S02 (volumes + system_settings) → S03 (idle reaper) → S04 (tmux model + Redis + reattach) → S05 (cookie-authed WS bridge) → S06 (final integrated acceptance). Per PROJECT.md the entire M002 milestone shipped these; the human owner should reconcile whether M003-umluob is a duplicate of M002-jy6pde and either close M003 as already-delivered or pivot M003 to its intended Projects-and-GitHub scope (R009-R012) — captured as the slice's only follow-up note.

## Verification

- Runtime signals: existing M002 INFO keys (`orchestrator_starting`, `orchestrator_ready`, `image_pull_ok`, `container_provisioned`, `session_created`) and ERROR keys (`image_pull_failed`, `orchestrator_ws_unauthorized`) — no new signals introduced
- Inspection surfaces: orchestrator `/healthz`; `docker ps --filter label=perpetuity.managed=true`; `docker compose logs orchestrator`; `GET /v1/sessions?user_id=&team_id=` for label-scoped lookup
- Failure visibility: verification report records which test ids passed and which (if any) failed, with the failing assertion captured verbatim
- Redaction constraints: do not log shared-secret API keys; do not echo workspace volume paths beyond the ones already produced by orchestrator logs (MEM134 discipline preserved)

## Tasks

- [x] **T01: Verify M003/S01 success criteria against existing codebase and file follow-up note** `est:1h`
  M003/S01's success criteria are byte-for-byte the same set that M002/S01 already shipped (PROJECT.md: 'M002-jy6pde — COMPLETE'; commits e54a3d4, fb0a2ec, 3a52462, etc.). Rather than re-implement work that is already in `main`, this task runs the existing test suite that covers every S01 criterion and produces a single verification report. The task ALSO files a documented note inside the slice's task summary calling out the M003-umluob/M002-jy6pde duplication so a human can reconcile it — auto-mode cannot make that call.

Scope is verification + documentation only. Do not edit orchestrator source, docker-compose.yml, or the workspace Dockerfile. If a test fails, capture the failing assertion verbatim in the report and stop — do NOT attempt remediation in this task; remediation belongs to a follow-up slice.

AUTO-MODE ASSUMPTION (documented for the human): the planner is interpreting M003/S01 as a verification slice over already-shipped code. If the human disagrees and wants new work here, the slice should be re-planned with `gsd_replan_slice` after re-scoping M003 in the roadmap.

Slice mapping for the success criteria → existing tests (proof-by-citation):
  • Service exists / sole socket mount / privileged: assertions in `docker-compose.yml` checked by a small grep block in the report.
  • Image hard-fail on boot: `orchestrator/tests/integration/test_image_pull.py::test_image_pull_failed_exits_nonzero` and `::test_image_pull_ok_for_existing_image`.
  • Provisioning with labels + limits + name: `orchestrator/tests/integration/test_sessions_lifecycle.py::test_create_session_provisions_container_and_tmux` and the `backend/tests/integration/test_m002_s01_e2e.py::test_m002_s01_full_e2e` capstone.
  • GET lists by labels: `test_sessions_lifecycle.py::test_list_sessions_filters_by_user_team`.
  • Second POST reuses warm container: `test_sessions_lifecycle.py::test_second_session_reuses_container_multi_tmux`.
  • DELETE removes the container: covered by the M002/S01 e2e (`test_m002_s01_full_e2e`) and `test_delete_kills_one_session_keeps_others`.
  • X-Orchestrator-Key 401: `orchestrator/tests/unit/test_auth.py::test_http_*_key_*` and `test_sessions_lifecycle.py::test_missing_api_key_returns_401`.

Known cap divergence (from PROJECT.md): spec says `nano_cpus=2_000_000_000`; implementation ships `1_000_000_000` (1.0 vCPU). This is a pre-existing accepted divergence per MEM follow-ups — record it in the verification report under 'Known accepted divergences' and do NOT fail verification on it.

Output: `.gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md` with one section per success criterion, the test ids that prove it, the verbatim PASS/FAIL line from the test runner, and the human-action note about M003 duplication.
  - Files: `docker-compose.yml`, `orchestrator/orchestrator/main.py`, `orchestrator/orchestrator/sessions.py`, `orchestrator/orchestrator/auth.py`, `orchestrator/tests/integration/test_sessions_lifecycle.py`, `orchestrator/tests/integration/test_image_pull.py`, `orchestrator/tests/unit/test_auth.py`, `backend/tests/integration/test_m002_s01_e2e.py`, `.gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md`
  - Verify: test -f .gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md && grep -q '^## Criterion: ' .gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md && grep -q 'M003-umluob duplicates M002-jy6pde' .gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md && grep -cE '^- (PASS|FAIL): ' .gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md | awk '{ if ($1+0 >= 7) exit 0; else exit 1 }'

## Files Likely Touched

- docker-compose.yml
- orchestrator/orchestrator/main.py
- orchestrator/orchestrator/sessions.py
- orchestrator/orchestrator/auth.py
- orchestrator/tests/integration/test_sessions_lifecycle.py
- orchestrator/tests/integration/test_image_pull.py
- orchestrator/tests/unit/test_auth.py
- backend/tests/integration/test_m002_s01_e2e.py
- .gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md
