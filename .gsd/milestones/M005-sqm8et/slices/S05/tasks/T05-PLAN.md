---
estimated_steps: 13
estimated_files: 1
skills_used: []
---

# T05: E2e integration test suite (test_m005_s05_run_history_admin_e2e.py)

Write backend/tests/integration/test_m005_s05_run_history_admin_e2e.py covering the full S05 surface against a live compose stack. Follow the exact pattern established in test_m005_s03_workflow_run_engine_e2e.py and test_m005_s04_webhook_dispatch_e2e.py: pytestmark = pytest.mark.e2e, skip if PERPETUITY_E2E_STACK not set, use the shared conftest compose fixtures.

Required test functions (5 minimum):
1. test_run_history_list_with_filters — create 3 runs with different trigger_types + statuses, hit GET /teams/{id}/runs with each filter combination, verify correct subset returned, verify snapshot field present even for a workflow that was deleted after run creation.
2. test_admin_manual_trigger — system admin POSTs to /api/v1/admin/workflows/{id}/trigger with {"trigger_payload": {"note": "manual test"}}, verify 202 + run_id, verify run appears in history with trigger_type='admin_manual', verify non-admin gets 403.
3. test_concurrent_cap_enforcement — set max_concurrent_runs=2 on a workflow, fire 3 simultaneous dispatch requests, verify exactly 2 succeed (202) and 1 returns 429 with {detail: 'workflow_cap_exceeded', cap_type: 'concurrent'}, verify audit row with status='rejected' in run history.
4. test_hourly_cap_enforcement — set max_runs_per_hour=2 on a workflow, fire 3 sequential dispatch requests, verify 3rd returns 429 with cap_type='hourly'.
5. test_orphan_run_recovery — create a WorkflowRun row directly in DB with status='running' and last_heartbeat_at=now()-20min, call recover_orphan_runs() task directly (not via Beat), verify run transitions to status='failed' with error_class='worker_crash', verify step_runs in running/pending also marked failed.
6. test_discriminator_sweep — run all S05 discriminators (workflow_cap_exceeded, recover_orphan_runs_sweep, workflow_run_orphan_recovered, admin_manual_trigger_queued) through a combined log sweep; verify no sk-ant- or sk- prefix leakage.

Why/Files/Do/Verify/Done-when:
- Why: S05 has no value without exercisable proof. Mocked unit tests prove logic; e2e tests prove the wiring.
- Files: backend/tests/integration/test_m005_s05_run_history_admin_e2e.py
- Verify: cd backend && POSTGRES_DB=perpetuity_app uv run pytest -m e2e tests/integration/test_m005_s05_run_history_admin_e2e.py -v (6 skipped without live stack, exit 0)
- Done when: pytest collects 6 test functions, all skip cleanly without live stack, exit 0.

## Inputs

- `backend/tests/integration/test_m005_s03_workflow_run_engine_e2e.py`
- `backend/tests/integration/test_m005_s04_webhook_dispatch_e2e.py`
- `backend/tests/conftest.py`
- `backend/app/api/routes/workflows.py`
- `backend/app/services/workflow_dispatch.py`
- `backend/app/workflows/tasks.py`

## Expected Output

- `backend/tests/integration/test_m005_s05_run_history_admin_e2e.py`

## Verification

cd backend && POSTGRES_DB=perpetuity_app uv run pytest -m e2e tests/integration/test_m005_s05_run_history_admin_e2e.py -v 2>&1 | tail -10
