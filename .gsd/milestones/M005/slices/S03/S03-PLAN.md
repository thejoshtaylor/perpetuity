# S03: S03

**Goal:** Full workflow CRUD API + run engine wiring + frontend editor UI + dashboard custom-workflow buttons + run-page cancel button, all guarded by admin/member role gates and backed by the scope-routing dispatch service
**Demo:** 

## Must-Haves

- pytest backend/tests/api/test_workflow_crud_routes.py test_workflow_cancel_route.py test_workflow_dispatch_service.py all pass; bunx playwright test --project=chromium tests/routes/WorkflowsList.spec.ts tests/routes/WorkflowEditor.spec.ts tests/components/CustomWorkflowButtons.spec.ts tests/components/RunCancelButton.spec.ts returns 20 passed

## Proof Level

- This slice proves: contract — full CRUD + cancel API tested with pytest mocks; frontend routes tested with Playwright component mocks

## Integration Closure

S02 shipped workflow_runs + step_runs schema and run engine; S03 adds the CRUD management surface (create/read/update/delete workflows), scope-routing via workflow_dispatch service, cancellation endpoint, and the frontend to drive all of it. S04 will add webhook/scheduled triggers and team_mirror executor.

## Verification

- WorkflowRun records error_class + cancelled_by_user_id + cancelled_at; step_runs track per-step exit_code + stdout + stderr for post-mortem inspection via GET /api/v1/workflow_runs/{run_id}

## Tasks

- [x] **T01: Add s13 migration: form_schema, target_user_id, round_robin_cursor, target_container, cancellation audit columns** `est:30m`
  Create Alembic migration s13_workflow_crud_extensions.py that adds the S03-required columns to workflows and workflow_steps and workflow_runs tables. These columns are prerequisites for all S03 CRUD and dispatch logic.
  - Files: `backend/app/models.py`, `backend/alembic/versions/s13_workflow_crud_extensions.py`, `backend/tests/api/test_s13_workflow_crud_extensions_migration.py`
  - Verify: cd backend && python -m pytest tests/api/test_s13_workflow_crud_extensions_migration.py -x -q

- [x] **T02: Extend models + DTOs for CRUD fields and scope routing** `est:45m`
  Add form_schema (JSONB), target_user_id, round_robin_cursor, WorkflowScope routing fields to Workflow model. Add target_container to WorkflowStep. Add cancelled_by_user_id + cancelled_at to WorkflowRun. Add corresponding Pydantic DTOs: WorkflowCreate, WorkflowUpdate, WorkflowWithStepsPublic, WorkflowFormFieldKind.
  - Files: `backend/app/models.py`, `backend/app/schemas.py`
  - Verify: cd backend && python -c 'from app.models import Workflow, WorkflowStep, WorkflowRun; from app.schemas import WorkflowCreate, WorkflowWithStepsPublic'

- [x] **T03: Implement workflow_dispatch service: resolve_target_user with user/team/round_robin scope routing** `est:1h`
  Create backend/app/services/workflow_dispatch.py implementing resolve_target_user(session, workflow, triggering_user_id) -> (target_user_id, fallback_reason). Handle user scope (always triggering user), team_specific (workflow.target_user_id), round_robin (atomic cursor increment over members with live workspaces, fallback to triggering user). Write full pytest coverage.
  - Files: `backend/app/services/workflow_dispatch.py`, `backend/tests/api/test_workflow_dispatch_service.py`
  - Verify: cd backend && python -m pytest tests/api/test_workflow_dispatch_service.py -x -q

- [x] **T04: Implement workflow CRUD API routes and cancellation endpoint** `est:1h30m`
  Create backend/app/api/routes/workflows_crud.py with: POST /teams/{team_id}/workflows (admin), GET /workflows/{id}, PUT /workflows/{id} (admin), DELETE /workflows/{id} (admin), POST /workflow_runs/{run_id}/cancel (member). System-owned workflows (_direct_ prefix) are rejected on PUT/DELETE. form_schema validated on create/update. Cancellation sets status=cancelled + audit fields. Write pytest coverage for all routes including role gates.
  - Files: `backend/app/api/routes/workflows_crud.py`, `backend/app/api/routes/workflows.py`, `backend/app/api/router.py`, `backend/tests/api/test_workflow_crud_routes.py`, `backend/tests/api/test_workflow_cancel_route.py`
  - Verify: cd backend && python -m pytest tests/api/test_workflow_crud_routes.py tests/api/test_workflow_cancel_route.py -x -q

- [x] **T05: Build frontend workflow CRUD UI, dashboard custom-workflow buttons, and run-page cancel button** `est:2h`
  Regenerate TypeScript client from updated OpenAPI spec. Create /workflows list route (workflows.tsx) with admin CRUD gates and member read view. Create /workflows/$workflowId editor route. Build WorkflowEditor, FormSchemaEditor, StepsEditor components. Add CustomWorkflowButtons to team dashboard (form-schema workflows open WorkflowFormDialog modal; others dispatch directly). Add optimistic cancel button to runs/$runId detail page. Regenerate routeTree.gen.ts.
  - Files: `frontend/src/routes/_layout/workflows.tsx`, `frontend/src/routes/_layout/workflows_.$workflowId.tsx`, `frontend/src/components/workflows/WorkflowEditor.tsx`, `frontend/src/components/workflows/FormSchemaEditor.tsx`, `frontend/src/components/workflows/StepsEditor.tsx`, `frontend/src/components/dashboard/CustomWorkflowButtons.tsx`, `frontend/src/components/dashboard/WorkflowFormDialog.tsx`, `frontend/src/routes/_layout/runs_.$runId.tsx`, `frontend/src/main.tsx`, `frontend/src/routeTree.gen.ts`, `frontend/tests/routes/WorkflowsList.spec.ts`, `frontend/tests/routes/WorkflowEditor.spec.ts`, `frontend/tests/components/CustomWorkflowButtons.spec.ts`, `frontend/tests/components/RunCancelButton.spec.ts`
  - Verify: cd frontend && bunx playwright test --project=chromium tests/routes/WorkflowsList.spec.ts tests/routes/WorkflowEditor.spec.ts tests/components/CustomWorkflowButtons.spec.ts tests/components/RunCancelButton.spec.ts

## Files Likely Touched

- backend/app/models.py
- backend/alembic/versions/s13_workflow_crud_extensions.py
- backend/tests/api/test_s13_workflow_crud_extensions_migration.py
- backend/app/schemas.py
- backend/app/services/workflow_dispatch.py
- backend/tests/api/test_workflow_dispatch_service.py
- backend/app/api/routes/workflows_crud.py
- backend/app/api/routes/workflows.py
- backend/app/api/router.py
- backend/tests/api/test_workflow_crud_routes.py
- backend/tests/api/test_workflow_cancel_route.py
- frontend/src/routes/_layout/workflows.tsx
- frontend/src/routes/_layout/workflows_.$workflowId.tsx
- frontend/src/components/workflows/WorkflowEditor.tsx
- frontend/src/components/workflows/FormSchemaEditor.tsx
- frontend/src/components/workflows/StepsEditor.tsx
- frontend/src/components/dashboard/CustomWorkflowButtons.tsx
- frontend/src/components/dashboard/WorkflowFormDialog.tsx
- frontend/src/routes/_layout/runs_.$runId.tsx
- frontend/src/main.tsx
- frontend/src/routeTree.gen.ts
- frontend/tests/routes/WorkflowsList.spec.ts
- frontend/tests/routes/WorkflowEditor.spec.ts
- frontend/tests/components/CustomWorkflowButtons.spec.ts
- frontend/tests/components/RunCancelButton.spec.ts
