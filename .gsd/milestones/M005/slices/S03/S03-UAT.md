# S03: Workflow CRUD API + Dispatch Service + Frontend Editor — UAT

**Milestone:** M005
**Written:** 2026-04-29T07:53:32.592Z

# S03: Workflow CRUD API + Dispatch + Frontend — UAT

**Milestone:** M005
**Written:** 2026-04-29

## UAT Type

- UAT mode: artifact-driven
- Why this mode is sufficient: All routes are covered by pytest mocks with role-gate, error-path, and happy-path cases; all frontend routes/components are covered by Playwright component mocks. No live server required to prove contract correctness.

## Preconditions

- Backend test DB at alembic head (s13)
- `cd backend` is the working directory for all pytest commands
- `cd frontend` is the working directory for all Playwright commands
- Playwright browsers installed: `bunx playwright install chromium`

## Smoke Test

```bash
cd backend && python -m pytest tests/api/test_workflow_crud_routes.py -x -q
```
Expected: 15+ passed, no failures. Confirms CRUD routes, role gates, and form_schema validation are wired.

## Test Cases

### 1. Admin can create a workflow with form_schema

```bash
# POST /teams/{team_id}/workflows as team admin
# Payload: { name: "onboard", steps: [...], form_schema: { fields: [{ key: "email", kind: "string", label: "Email", required: true }] } }
```
1. Run `pytest tests/api/test_workflow_crud_routes.py::test_create_workflow_with_form_schema -x -v`
2. **Expected:** 201 response, workflow ID returned, form_schema stored correctly.

### 2. Member cannot create/update/delete a workflow (role gate)

1. Run `pytest tests/api/test_workflow_crud_routes.py::test_create_workflow_member_forbidden -x -v`
2. **Expected:** 403 with error detail indicating admin role required.

### 3. System-owned workflows rejected on PUT/DELETE

1. Run `pytest tests/api/test_workflow_crud_routes.py::test_update_system_owned_rejected -x -v`
2. Run `pytest tests/api/test_workflow_crud_routes.py::test_delete_system_owned_rejected -x -v`
3. **Expected:** Both return 403 with `cannot_modify_system_workflow` detail.

### 4. Cancellation — pending/running run transitions to cancelled

1. Run `pytest tests/api/test_workflow_cancel_route.py -x -v`
2. **Expected:** POST /workflow_runs/{run_id}/cancel returns 202 `{"status":"cancelling"}` for pending and running runs; stamps cancelled_by_user_id + cancelled_at audit fields.

### 5. Cancellation — terminal-status run returns 409

1. Run `pytest tests/api/test_workflow_cancel_route.py::test_cancel_already_terminal_returns_409 -x -v`
2. **Expected:** 409 conflict response when run is already succeeded/failed/cancelled.

### 6. workflow_dispatch scope routing — user scope

1. Run `pytest tests/api/test_workflow_dispatch_service.py::test_user_scope_returns_triggering_user -x -v`
2. **Expected:** resolve_target_user returns triggering_user_id, fallback_reason is None.

### 7. workflow_dispatch scope routing — round_robin with offline members

1. Run `pytest tests/api/test_workflow_dispatch_service.py::test_round_robin_all_offline_fallback -x -v`
2. **Expected:** Returns triggering_user_id with fallback_reason="no_live_workspace" when no member has a workspace_volume within 7 days.

### 8. Frontend — admin sees Create/Edit/Delete buttons on /workflows

1. Run `bunx playwright test --project=chromium tests/routes/WorkflowsList.spec.ts -g "admin sees" --reporter=line`
2. **Expected:** Create, Edit, Delete buttons visible; member view shows list only without mutation controls.

### 9. Frontend — WorkflowEditor saves form_schema fields

1. Run `bunx playwright test --project=chromium tests/routes/WorkflowEditor.spec.ts --reporter=line`
2. **Expected:** Editor loads workflow, form fields render, save dispatches PUT with updated form_schema.

### 10. Frontend — cancel button on run detail page

1. Run `bunx playwright test --project=chromium tests/components/RunCancelButton.spec.ts --reporter=line`
2. **Expected:** Cancel button visible for pending/running runs, absent for terminal runs; click POSTs to /cancel and optimistically updates displayed status.

## Edge Cases

### Invalid form_schema (missing fields key)

1. POST /teams/{id}/workflows with `form_schema: {}` (no `fields` array)
2. **Expected:** 400 with `{"detail":"invalid_form_schema","reason":"..."}` — distinguishable from 422 type errors.

### Workflow with _direct_ prefix name rejected

1. POST /teams/{id}/workflows with `name: "_direct_something"`
2. **Expected:** 403 cannot_create_system_name.

### Non-member GET returns 403

1. Run `pytest tests/api/test_workflow_crud_routes.py::test_get_workflow_non_member_forbidden -x -v`
2. **Expected:** 403 for user with no team membership.

## Failure Signals

- Any pytest test returning non-zero exit code
- Playwright test showing "1 failed" or timeout in RunCancelButton (known flaky test — re-run once before escalating)
- 500 errors in backend logs on workflow routes
- Missing WorkflowScope import from app.models

## Not Proven By This UAT

- Live end-to-end cancellation actually stopping a running container (requires live worker + container runtime — deferred to integration testing)
- round_robin cursor atomicity under concurrent load (proven by code pattern; concurrent load test deferred)
- Frontend form submission actually reaching the API (Playwright mocks intercept at network layer)
- Webhook/scheduled trigger dispatch (S04 scope)

## Notes for Tester

- RunCancelButton.spec.ts has one intermittently flaky test ("clicking cancel POSTs and optimistically updates status") — re-run once if it fails; it passes reliably on second run.
- All pytest tests run against the local test DB; ensure `alembic upgrade head` has been run if DB was recently reset.
- Test files for migration tests live in `backend/tests/migrations/` not `backend/tests/api/` — consistent with all other migration tests in this project.
