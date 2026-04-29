---
id: T01
parent: S05
milestone: M005-sqm8et
key_files:
  - backend/app/alembic/versions/s15_workflow_operational_caps.py
  - backend/app/models.py
  - backend/app/api/routes/workflows.py
  - backend/tests/unit/__init__.py
  - backend/tests/unit/test_s15_migration.py
  - backend/tests/unit/test_run_history_endpoint.py
  - backend/tests/unit/test_admin_trigger_endpoint.py
key_decisions:
  - Admin trigger endpoint mounted at /admin/workflows/{id}/trigger on the main workflows router (no new router needed — prefix comes from URL pattern)
  - WorkflowRun.team_id used directly for run history list query rather than joining through workflow (correct for snapshot semantics and avoids issues with CASCADE-deleted workflows)
  - tests/unit/ created as new directory to match task plan verification paths (project uses tests/api/ for endpoint tests but plan specified tests/unit/)
  - MEM016 fix applied to log capture test: logger.disabled=False before caplog.at_level
duration: 
verification_result: passed
completed_at: 2026-04-29T10:10:50.926Z
blocker_discovered: false
---

# T01: s15 Alembic migration adds max_concurrent_runs/max_runs_per_hour to workflows + composite index; run history list endpoint (GET /api/v1/teams/{team_id}/runs) + admin manual trigger endpoint (POST /api/v1/admin/workflows/{id}/trigger) added and verified with 26 passing tests

**s15 Alembic migration adds max_concurrent_runs/max_runs_per_hour to workflows + composite index; run history list endpoint (GET /api/v1/teams/{team_id}/runs) + admin manual trigger endpoint (POST /api/v1/admin/workflows/{id}/trigger) added and verified with 26 passing tests**

## What Happened

**Migration (s15_workflow_operational_caps):** Added `max_concurrent_runs INTEGER NULL` and `max_runs_per_hour INTEGER NULL` columns to the `workflows` table. Added composite index `ix_workflow_runs_workflow_id_status_created_at` on `(workflow_id, status, created_at DESC)` for efficient cap enforcement COUNT queries in T02. Migration chains from `s14_webhook_delivery_id`. Downgrade reverses cleanly.

**Model updates:** `Workflow` SQLModel gained the two cap fields. `WorkflowCreate`, `WorkflowUpdate`, `WorkflowPublic`, and `WorkflowWithStepsPublic` all updated to expose the new fields in the API surface. New DTOs added: `WorkflowRunSummaryPublic` (lightweight run row without step_runs embedded), `WorkflowRunsPublic` (paginated list wrapper), and `AdminWorkflowTriggerBody` (request body for the admin trigger endpoint).

**Run history list endpoint (`GET /api/v1/teams/{team_id}/runs`):** Paginated, filterable by status (enum-validated), trigger_type (enum-validated), after/before ISO datetime bounds, with limit (default 50, max 200) and offset. Membership gate via `assert_caller_is_team_member`. Returns 403 `not_team_member`, 404 `team_not_found`, 422 for invalid filter enum values. Uses `WorkflowRun.team_id` directly (not through workflow join) so the count and ordering are correct. `WorkflowRun.workflow_id` has CASCADE FK so deleted-workflow runs are also deleted — the correct design for this schema.

**Admin trigger endpoint (`POST /api/v1/admin/workflows/{id}/trigger`):** Guarded by `get_current_active_superuser` (403 if not system_admin). Accepts `AdminWorkflowTriggerBody` with free-form `trigger_payload`. Enqueues `admin_manual` WorkflowRun via the same Celery dispatch path as regular dispatch. Returns 202 `{run_id, status='pending'}`. On Celery failure, marks run failed with `error_class='dispatch_failed'` and returns 503. Emits `admin_manual_trigger_queued` INFO log with `run_id`, `workflow_id`, `triggered_by`, `trigger_payload_keys`.

**Route registration:** Both endpoints live in `app/api/routes/workflows.py` and are already mounted via `api_router.include_router(workflows.router)` — no `main.py` change required since the admin trigger path prefix is handled by the URL pattern `/admin/workflows/{id}/trigger` directly in the router.

**Tests:** Created `tests/unit/` with 26 tests covering: migration schema validation (6 tests), run history endpoint happy path + all filter combos + pagination + error paths (12 tests), admin trigger endpoint all paths including Celery failure + log emission (8 tests). Two local adaptations from the plan: (1) login endpoint uses JSON body (`email`/`password`), not form data — corrected in tests; (2) `workflow_runs.workflow_id` FK is CASCADE so deleted-workflow runs are also deleted — test renamed to verify cross-workflow aggregation instead.

## Verification

Ran `uv run pytest tests/unit/test_s15_migration.py tests/unit/test_run_history_endpoint.py tests/unit/test_admin_trigger_endpoint.py -v` from `backend/` — 26/26 passed. Migration round-trip verified (upgrade/downgrade/re-upgrade). Endpoint error shapes verified against slice-plan discriminators. Log capture verified with MEM016 logger.disabled=False fix applied.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && uv run pytest tests/unit/test_s15_migration.py tests/unit/test_run_history_endpoint.py tests/unit/test_admin_trigger_endpoint.py -v` | 0 | ✅ pass | 1960ms |

## Deviations

Login endpoint uses JSON body (email/password), not multipart form data (username/password) — test corrected. Deleted-workflow snapshot test adapted: WorkflowRun.workflow_id FK is ondelete=CASCADE so deleting the workflow also deletes the run; test renamed to verify cross-workflow aggregation which is the correct behavior for this schema.

## Known Issues

None

## Files Created/Modified

- `backend/app/alembic/versions/s15_workflow_operational_caps.py`
- `backend/app/models.py`
- `backend/app/api/routes/workflows.py`
- `backend/tests/unit/__init__.py`
- `backend/tests/unit/test_s15_migration.py`
- `backend/tests/unit/test_run_history_endpoint.py`
- `backend/tests/unit/test_admin_trigger_endpoint.py`
