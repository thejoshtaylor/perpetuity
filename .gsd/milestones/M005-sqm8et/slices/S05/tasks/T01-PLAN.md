---
estimated_steps: 8
estimated_files: 5
skills_used: []
---

# T01: s15 migration + run history list endpoint + admin manual trigger endpoint

Add max_concurrent_runs and max_runs_per_hour columns to the workflows table (s15 Alembic migration), update Workflow SQLModel and Pydantic DTOs, then add two new endpoints: GET /api/v1/teams/{team_id}/runs (paginated, filtered by status/trigger_type/time range, accessible to team members) and POST /api/v1/admin/workflows/{id}/trigger (system-admin only, accepts synthetic trigger_payload JSON, enqueues an admin_manual WorkflowRun via existing workflow_dispatch.dispatch_workflow_run). Register both routes in backend/app/api/main.py.

Snapshot semantics for the history list: WorkflowRun.workflow_snapshot (set at run creation in S03) must be readable even when the parent workflow row is deleted. The list query must LEFT JOIN workflow on workflow_id so deleted workflows don't drop the run from history — or rely on the snapshot field directly.

Why/Files/Do/Verify/Done-when:
- Why: Backend foundation for history UI, admin ops, and cap enforcement (cap fields needed by T02).
- Files: backend/app/alembic/versions/s15_workflow_operational_caps.py, backend/app/models.py, backend/app/api/routes/workflows.py, backend/app/api/routes/workflows_crud.py, backend/app/api/main.py
- Do: Write s15 migration with nullable Integer columns max_concurrent_runs and max_runs_per_hour on workflows table + composite index (workflow_id, status, created_at DESC) on workflow_runs for cap queries. Update Workflow SQLModel + WorkflowPublic/WorkflowCreate/WorkflowUpdate DTOs. Add GET runs list endpoint with query params: status (optional enum), trigger_type (optional enum), after (optional ISO datetime), before (optional ISO datetime), limit (default 50, max 200), offset. Add POST admin trigger endpoint guarded by is_system_admin dependency. Wire both into main.py.
- Verify: uv run pytest backend/tests/unit/test_s15_migration.py -v; uv run pytest backend/tests/unit/test_run_history_endpoint.py -v; uv run pytest backend/tests/unit/test_admin_trigger_endpoint.py -v
- Done when: Migration applies cleanly (alembic upgrade head), list endpoint returns correct shape with all filter combos, admin trigger returns 202 with run_id, non-admin gets 403.

## Inputs

- `backend/app/models.py`
- `backend/app/api/routes/workflows.py`
- `backend/app/api/routes/workflows_crud.py`
- `backend/app/api/main.py`
- `backend/app/alembic/versions/s14_webhook_delivery_id.py`
- `backend/app/services/workflow_dispatch.py`

## Expected Output

- `backend/app/alembic/versions/s15_workflow_operational_caps.py`
- `backend/app/models.py`
- `backend/app/api/routes/workflows.py`
- `backend/tests/unit/test_s15_migration.py`
- `backend/tests/unit/test_run_history_endpoint.py`
- `backend/tests/unit/test_admin_trigger_endpoint.py`

## Verification

cd backend && uv run pytest tests/unit/test_s15_migration.py tests/unit/test_run_history_endpoint.py tests/unit/test_admin_trigger_endpoint.py -v

## Observability Impact

admin_manual_trigger_queued (INFO) fires on successful admin trigger with workflow_id, run_id, triggered_by (admin user_id), trigger_payload_keys
