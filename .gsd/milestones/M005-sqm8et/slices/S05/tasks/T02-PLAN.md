---
estimated_steps: 8
estimated_files: 3
skills_used: []
---

# T02: Operational cap enforcement in workflow_dispatch.py

Implement max_concurrent_runs and max_runs_per_hour enforcement in backend/app/services/workflow_dispatch.py. Before enqueuing a run, check: (1) count of WorkflowRun rows for this workflow_id with status IN ('pending','running') — if >= max_concurrent_runs, raise WorkflowCapExceededError('concurrent'); (2) count of WorkflowRun rows for this workflow_id with created_at >= now()-1h — if >= max_runs_per_hour, raise WorkflowCapExceededError('hourly'). Both checks only fire when the cap field is non-None. The caller (dispatch route in workflows.py) catches WorkflowCapExceededError and returns HTTP 429 with body {detail: 'workflow_cap_exceeded', cap_type: 'concurrent'|'hourly', current_count: N, limit: M} and writes a structured log line workflow_cap_exceeded. An audit WorkflowRun row with status='rejected' and error_class='cap_exceeded' is inserted before raising so the rejection appears in run history.

The two count queries must be efficient — use the composite index added in T01's migration: (workflow_id, status, created_at DESC).

Why/Files/Do/Verify/Done-when:
- Why: Prevents runaway automated triggers from saturating containers and Celery queues; requirement R050 (implicit from milestone context).
- Files: backend/app/services/workflow_dispatch.py, backend/app/api/routes/workflows.py
- Do: Add WorkflowCapExceededError to workflow_dispatch.py. Add _check_workflow_caps(session, workflow) helper that queries both caps atomically under a SELECT FOR UPDATE SKIP LOCKED on the workflow row. Wire _check_workflow_caps into dispatch_workflow_run before target resolution. In workflows.py dispatch route, catch WorkflowCapExceededError → 429 + audit log. Add unit tests covering: cap=None skips check, concurrent cap hit, hourly cap hit, both caps None (no-op), audit row written on cap hit.
- Verify: cd backend && uv run pytest tests/unit/test_workflow_cap_enforcement.py -v
- Done when: 3 concurrent runs against a workflow with max_concurrent_runs=2: third returns 429 with correct body + audit row in DB.

## Inputs

- `backend/app/services/workflow_dispatch.py`
- `backend/app/api/routes/workflows.py`
- `backend/app/models.py`
- `backend/app/alembic/versions/s15_workflow_operational_caps.py`

## Expected Output

- `backend/app/services/workflow_dispatch.py`
- `backend/tests/unit/test_workflow_cap_enforcement.py`

## Verification

cd backend && uv run pytest tests/unit/test_workflow_cap_enforcement.py -v

## Observability Impact

workflow_cap_exceeded (INFO) structured log fires before 429 with workflow_id, cap_type, current_count, limit. audit WorkflowRun row with status='rejected' error_class='cap_exceeded' visible in run history list.
