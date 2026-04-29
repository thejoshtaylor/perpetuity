---
id: T04
parent: S03
milestone: M005-sqm8et
key_files:
  - backend/app/api/routes/workflows_crud.py
  - backend/app/api/routes/workflows.py
  - backend/app/api/main.py
  - backend/app/workflows/tasks.py
  - backend/tests/api/test_workflow_crud_routes.py
  - backend/tests/api/test_workflow_cancel_route.py
  - backend/tests/api/test_workflow_runner_cancellation.py
key_decisions:
  - form_schema accepted as raw dict in API request body (not WorkflowFormSchema DTO) so custom _validate_form_schema() returns 400 {invalid_form_schema} instead of Pydantic 422
  - Cancel API writes terminal 'cancelled' directly (DB CHECK constraint has no 'cancelling' value); worker watchpoint checks for 'cancelled' between steps rather than 'cancelling'; API response returns {status:'cancelling'} to signal in-flight intent to client
  - local _WorkflowCreateBody / _WorkflowUpdateBody SQLModel DTOs defined in workflows_crud.py to decouple API request parsing from models.py enums, enabling custom error shapes
duration: 
verification_result: passed
completed_at: 2026-04-29T05:55:11.022Z
blocker_discovered: false
---

# T04: Wired workflow CRUD API, cancellation API, and runner cancellation watchpoint behind admin/member gates with 47 passing tests

**Wired workflow CRUD API, cancellation API, and runner cancellation watchpoint behind admin/member gates with 47 passing tests**

## What Happened

Built three integrated HTTP boundary additions:

**(a) `app/api/routes/workflows_crud.py`** — new router with POST/GET/PUT/DELETE for workflows under `/api/v1/teams/{team_id}/workflows` and `/api/v1/workflows/{workflow_id}`. Uses `_WorkflowCreateBody` / `_WorkflowUpdateBody` (local DTOs with `form_schema: dict`) to accept raw JSON and apply custom form-schema validation returning 400 `{detail:'invalid_form_schema', reason:'...'}` rather than Pydantic's 422. The Pydantic-level `WorkflowCreate/Update` DTOs validate enum fields at parse time, which conflicts with the plan's requirement for custom 400 errors on bad `kind` values — local DTOs accept raw strings, custom `_validate_form_schema()` checks them explicitly. Admin gate via `assert_caller_is_team_admin`; reserved `_direct_*` namespace rejected with 403 `cannot_modify_system_workflow`; system_owned rows rejected on PUT/DELETE. Steps replaced atomically by DELETE-then-INSERT. Empty `form_schema` (`{}`) accepted as valid (no form fields required).

**(b) `app/api/routes/workflows.py`** — modified `dispatch_workflow_run` to call `resolve_target_user` (T03 service) at dispatch time and set the run's `target_user_id` from scope resolution rather than always `current_user.id`. Added required form-field validation for non-direct workflows: iterates `form_schema.fields` with `required=True` and raises 400 `{detail:'missing_required_field', field:'<name>'}` for absent keys.

**(c) `app/api/routes/workflows_crud.py` cancel route** — `POST /api/v1/workflow_runs/{run_id}/cancel` member-gated; accepts only `pending/running` status (409 `workflow_run_not_cancellable` otherwise); writes `status='cancelled'` + `cancelled_by_user_id` + `cancelled_at`; returns 202 `{status:'cancelling'}`. The DB CHECK constraint does not include 'cancelling', so we write the terminal 'cancelled' directly and the worker watchpoint detects it between steps.

**(d) `app/workflows/tasks._drive_run`** — added cancellation watchpoint at the top of each step iteration: calls `session.refresh(workflow_run)` to detect `status='cancelled'` written by the cancel API. When detected, marks all remaining step_run rows (pre-created pending rows) as `skipped` with `error_class='cancelled'`, logs `step_run_skipped` for each, then returns early. The terminal status and audit columns are already set by the API endpoint.

**(e) Tests** — 3 new test modules:
- `test_workflow_crud_routes.py` (15 tests): create/get/update/delete CRUD, admin gate, system_owned rejection, form_schema validation, step replacement atomicity, cascade delete
- `test_workflow_cancel_route.py` (8 tests): cancel pending/running, 409 on terminal, 403 non-member, 404 unknown
- `test_workflow_runner_cancellation.py` (3 tests): cancel after step 0 fires → step 1 skipped, cancellation between steps, no-cancellation regression

**Key deviation:** The DB `ck_workflow_runs_status` CHECK constraint only allows `pending/running/succeeded/failed/cancelled` — it does not include `cancelling`. Rather than adding a migration (out of scope for T04), the cancel API writes `cancelled` directly (terminal state) and the worker watchpoint checks for `cancelled` between steps rather than `cancelling`. The API response still returns `{status:'cancelling'}` to signal transition intent to the client.

## Verification

Ran `POSTGRES_DB=perpetuity_app uv run pytest tests/api/test_workflow_crud_routes.py tests/api/test_workflow_cancel_route.py tests/api/test_workflow_runner_cancellation.py tests/api/test_workflow_run_routes.py -v` — 47 tests passed. Also ran prior T01-T03 regression tests: `tests/api/test_workflow_runner.py tests/api/test_workflow_dispatch_service.py` — 21 tests passed, no regressions.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `POSTGRES_DB=perpetuity_app uv run pytest tests/api/test_workflow_crud_routes.py tests/api/test_workflow_cancel_route.py tests/api/test_workflow_runner_cancellation.py tests/api/test_workflow_run_routes.py -v` | 0 | ✅ pass | 2410ms |
| 2 | `POSTGRES_DB=perpetuity_app uv run pytest tests/api/test_workflow_runner.py tests/api/test_workflow_dispatch_service.py -v` | 0 | ✅ pass | 520ms |

## Deviations

The cancel route transitions directly to 'cancelled' (terminal) rather than 'cancelling' (intermediate) because the DB CHECK constraint on workflow_runs.status does not include 'cancelling'. The s13 migration comment mentions 'cancelling' but it was not added to the constraint. The worker watchpoint was adapted to check for 'cancelled' rather than 'cancelling'. This preserves the full cancellation semantic: API stamps audit columns + terminal status, worker detects between steps and skips remaining work.

## Known Issues

None.

## Files Created/Modified

- `backend/app/api/routes/workflows_crud.py`
- `backend/app/api/routes/workflows.py`
- `backend/app/api/main.py`
- `backend/app/workflows/tasks.py`
- `backend/tests/api/test_workflow_crud_routes.py`
- `backend/tests/api/test_workflow_cancel_route.py`
- `backend/tests/api/test_workflow_runner_cancellation.py`
