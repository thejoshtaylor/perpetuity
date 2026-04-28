---
estimated_steps: 13
estimated_files: 9
skills_used: []
---

# T01: Verify M003/S01 success criteria against existing codebase and file follow-up note

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

## Inputs

- ``docker-compose.yml` — confirms orchestrator is the sole service mounting /var/run/docker.sock and runs privileged`
- ``orchestrator/orchestrator/main.py` — confirms image pull on lifespan with hard fail`
- ``orchestrator/orchestrator/sessions.py` — confirms label-scoped provisioning, name policy, resource limits, warm-container reuse`
- ``orchestrator/orchestrator/auth.py` — confirms X-Orchestrator-Key gate`
- ``orchestrator/tests/integration/test_sessions_lifecycle.py` — provisioning / list / reuse / DELETE / 401 evidence`
- ``orchestrator/tests/integration/test_image_pull.py` — image hard-fail-on-boot evidence`
- ``orchestrator/tests/unit/test_auth.py` — two-key X-Orchestrator-Key evidence`
- ``backend/tests/integration/test_m002_s01_e2e.py` — end-to-end capstone evidence`

## Expected Output

- ``.gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md` — verification report keyed by success criterion with test-id → PASS/FAIL evidence and the M003/M002 duplication follow-up note`

## Verification

test -f .gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md && grep -q '^## Criterion: ' .gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md && grep -q 'M003-umluob duplicates M002-jy6pde' .gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md && grep -cE '^- (PASS|FAIL): ' .gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md | awk '{ if ($1+0 >= 7) exit 0; else exit 1 }'

## Observability Impact

No runtime code touched. Verification report itself is the new inspection surface — a future agent reconciling M003 vs M002 can read this file to see which criteria are already satisfied and which (if any) regressed.

Signals added/changed: none.
How a future agent inspects this: `cat .gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md`.
Failure state exposed: any criterion whose row says `FAIL: <assertion>` is a regression that this task surfaces but does not fix.
