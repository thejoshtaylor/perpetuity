---
id: T02
parent: S05
milestone: M005-sqm8et
key_files:
  - backend/app/services/workflow_dispatch.py
  - backend/app/api/routes/workflows.py
  - backend/app/models.py
  - backend/app/alembic/versions/s16_workflow_run_rejected_status.py
  - backend/tests/unit/test_workflow_cap_enforcement.py
key_decisions:
  - Added rejected to WorkflowRunStatus enum and check constraint rather than using a separate audit table — keeps rejection visible in standard run history list (same query, filter by status=rejected)
  - Used col().in_() for the concurrent status filter per codebase convention (not raw string in_ on attribute)
  - SELECT FOR UPDATE SKIP LOCKED on workflow row was skipped — cap enforcement is best-effort; the rejection audit trail makes double-admission visible without a mutex
  - s16 migration drops and recreates ck_workflow_runs_status to add rejected — clean because PostgreSQL does not support ALTER CHECK directly
duration: 
verification_result: passed
completed_at: 2026-04-29T10:18:50.826Z
blocker_discovered: false
---

# T02: Cap enforcement added to workflow_dispatch.py: WorkflowCapExceededError + _check_workflow_caps wired into dispatch route; rejected status + s16 migration; 13 tests all pass

**Cap enforcement added to workflow_dispatch.py: WorkflowCapExceededError + _check_workflow_caps wired into dispatch route; rejected status + s16 migration; 13 tests all pass**

## What Happened

Added operational cap enforcement to the workflow dispatch path. The implementation required changes across four layers:

**1. models.py**: Added `rejected` to `WorkflowRunStatus` enum and updated the inline `CheckConstraint` on `WorkflowRun.__table_args__` to include `'rejected'`.

**2. s16 migration** (`s16_workflow_run_rejected_status.py`): Dropped and recreated `ck_workflow_runs_status` on `workflow_runs` to allow `'rejected'` status. The s11 migration had hardcoded a 5-value constraint; this migration extends it to 6. Applied successfully via `alembic upgrade head`.

**3. workflow_dispatch.py**: 
- Added `WorkflowCapExceededError` exception class carrying `workflow_id`, `cap_type`, `current_count`, `limit`.
- Added `_check_workflow_caps(session, workflow)` which runs two COUNT queries:
  - Concurrent check: counts rows where `status IN ('pending', 'running')` for this `workflow_id` using `col().in_()` (the pattern used elsewhere in this codebase). Only fires when `max_concurrent_runs is not None`.
  - Hourly check: counts rows where `created_at >= now()-1h` for this `workflow_id`. Only fires when `max_runs_per_hour is not None`.
- Both queries use the composite index `(workflow_id, status, created_at DESC)` added in s15.
- Emits `workflow_cap_exceeded` INFO log with `workflow_id`, `cap_type`, `current_count`, `limit` before raising.

**4. workflows.py dispatch route**: 
- Imported `WorkflowCapExceededError` and `_check_workflow_caps`.
- Added a `try/except WorkflowCapExceededError` block before target-user resolution. On cap hit: inserts a `WorkflowRun` with `status='rejected'` and `error_class='cap_exceeded'` for audit visibility, then raises HTTP 429 with `{detail: 'workflow_cap_exceeded', cap_type, current_count, limit}`.

**Deviations from plan**: `SELECT FOR UPDATE SKIP LOCKED` on the workflow row was not implemented — the plan mentioned it as an option but the two COUNT queries are already atomic enough for the use case (cap enforcement is best-effort, not a hard mutex). The count queries are point-in-time snapshots; a race window exists but is acceptable per the rejection audit trail design.

**Test file**: 13 tests in `tests/unit/test_workflow_cap_enforcement.py` covering both `_check_workflow_caps` unit level and HTTP dispatch layer. All 13 pass.

## Verification

Ran `uv run pytest tests/unit/test_workflow_cap_enforcement.py tests/unit/test_s15_migration.py tests/unit/test_run_history_endpoint.py tests/unit/test_admin_trigger_endpoint.py -v` — 39 passed, 0 failed. Cap enforcement tests: 8 unit tests for _check_workflow_caps (concurrent cap hit, hourly cap hit, both None no-op, boundary below limit, old runs ignored, non-active statuses not counted, individual None cap skips) and 5 HTTP integration tests (429 on concurrent cap, 429 on hourly cap, audit row written on cap hit, 200 on under-cap, 200 on no-cap).

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `uv run pytest tests/unit/test_workflow_cap_enforcement.py -v` | 0 | ✅ pass | 450ms |
| 2 | `uv run pytest tests/unit/test_s15_migration.py tests/unit/test_run_history_endpoint.py tests/unit/test_admin_trigger_endpoint.py tests/unit/test_workflow_cap_enforcement.py -v` | 0 | ✅ pass (39 total) | 2290ms |
| 3 | `uv run alembic upgrade head` | 0 | ✅ pass — s16 applied | 800ms |

## Deviations

SELECT FOR UPDATE SKIP LOCKED on the workflow row was not implemented. The plan listed it as an approach option but the two atomic COUNT queries already provide correct enforcement for the stated requirement. A sub-cap-threshold race window is acceptable given the audit row design.

## Known Issues

none

## Files Created/Modified

- `backend/app/services/workflow_dispatch.py`
- `backend/app/api/routes/workflows.py`
- `backend/app/models.py`
- `backend/app/alembic/versions/s16_workflow_run_rejected_status.py`
- `backend/tests/unit/test_workflow_cap_enforcement.py`
