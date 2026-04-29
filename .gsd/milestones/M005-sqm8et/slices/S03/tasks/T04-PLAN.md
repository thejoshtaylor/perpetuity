---
estimated_steps: 1
estimated_files: 7
skills_used: []
---

# T04: Wire workflow CRUD API + cancellation API + runner cancellation watchpoint behind admin gate

Three integrated additions on the HTTP boundary. (a) New router `app/api/routes/workflows_crud.py` mounted at `/api/v1/teams/{team_id}/workflows` exposing: `POST /` create (admin-only via assert_caller_is_team_admin; rejects `name` matching `_direct_*` reserved namespace; inserts workflow + workflow_steps in one transaction), `GET /` list (member; reuses S02's listing — already exists; this task moves the responsibility into the new module so all CRUD lives together), `GET /{workflow_id}` returns `WorkflowWithStepsPublic`, `PUT /{workflow_id}` update (admin; replaces all steps in a single transaction by DELETE-then-INSERT keyed on workflow_id; rejects updates to system_owned=true rows with 403 `{detail: 'cannot_modify_system_workflow'}`), `DELETE /{workflow_id}` (admin; rejects system_owned with same shape; CASCADE handles workflow_steps + workflow_runs + step_runs deletion). Form-schema validation: on create/update, validates form_schema is `{fields: [{name: str, label: str, kind: 'string'|'text'|'number', required: bool}]}` shape; bad shape → 400 `{detail: 'invalid_form_schema', reason: '<which check failed>'}`. (b) Modify the existing `app/api/routes/workflows.py::dispatch_workflow_run` to: call `resolve_target_user` (T03) at dispatch time; require `prompt` only for `_direct_*` workflows (existing) BUT for non-direct workflows validate every required form field is present in `body.trigger_payload`; bad → 400 `{detail: 'missing_required_field', field: '<name>'}` (existing discriminator). (c) New route `POST /api/v1/workflow_runs/{run_id}/cancel` — member-gated (any team member can cancel a run); accepts cancellation only when `status in {pending, running}` (otherwise 409 `{detail: 'workflow_run_not_cancellable', current_status: '<...>'}`); flips status to `cancelling`, stamps cancelled_by_user_id + cancelled_at, returns 202 `{status: 'cancelling'}`. (d) Modify `app/workflows/tasks._drive_run` to check `workflow_run.status` BETWEEN steps (re-fetch from DB after each `_execute_one_step`); if status == 'cancelling', stop iterating, mark remaining step_runs as `skipped` with `error_class='cancelled'`, transition workflow_run to terminal `cancelled` status, emit `workflow_run_cancelled run_id=<uuid> at_step_index=<n>`. (e) Integration tests cover: CRUD admin gate (member gets 403), CRUD with system_owned name rejection, CRUD form_schema validation, CRUD update replaces steps atomically (old step ids gone), DELETE cascades to runs/step_runs, dispatch with form-required-field missing returns 400, dispatch with all form fields present succeeds, cancellation happy path (run terminates `cancelled`, remaining steps `skipped`), cancellation on already-terminal run returns 409, cancellation by non-member 403, runner respects cancellation between steps using a 2-step workflow with a sleep shim.

## Inputs

- ``backend/app/api/routes/workflows.py``
- ``backend/app/api/main.py``
- ``backend/app/api/team_access.py``
- ``backend/app/services/workflow_dispatch.py``
- ``backend/app/workflows/tasks.py``
- ``backend/app/models.py``

## Expected Output

- ``backend/app/api/routes/workflows_crud.py``
- ``backend/app/api/routes/workflows.py``
- ``backend/app/api/main.py``
- ``backend/app/workflows/tasks.py``
- ``backend/tests/api/test_workflow_crud_routes.py``
- ``backend/tests/api/test_workflow_cancel_route.py``
- ``backend/tests/api/test_workflow_runner_cancellation.py``

## Verification

cd /Users/josh/code/perpetuity/backend && uv run pytest tests/api/test_workflow_crud_routes.py tests/api/test_workflow_cancel_route.py tests/api/test_workflow_runner_cancellation.py tests/api/test_workflow_run_routes.py -v

## Observability Impact

Adds INFO `workflow_run_cancelled run_id=<uuid> at_step_index=<n> cancelled_by=<uuid>` on user cancellation; INFO `step_run_skipped run_id=<uuid> step_index=<n> reason=cancelled` for each step skipped post-cancellation. Adds new error_class values: `cancelled` (step skipped due to cancellation), `cannot_modify_system_workflow`, `invalid_form_schema`, `workflow_run_not_cancellable`. Inspection: `psql perpetuity_app -c "SELECT id, status, cancelled_by_user_id, cancelled_at FROM workflow_runs WHERE status IN ('cancelling', 'cancelled') ORDER BY cancelled_at DESC LIMIT 20"`.
