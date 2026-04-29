---
estimated_steps: 1
estimated_files: 14
skills_used: []
---

# T05: Build frontend workflow CRUD UI, dashboard custom-workflow buttons, and run-page cancel button

Regenerate TypeScript client from updated OpenAPI spec. Create /workflows list route (workflows.tsx) with admin CRUD gates and member read view. Create /workflows/$workflowId editor route. Build WorkflowEditor, FormSchemaEditor, StepsEditor components. Add CustomWorkflowButtons to team dashboard (form-schema workflows open WorkflowFormDialog modal; others dispatch directly). Add optimistic cancel button to runs/$runId detail page. Regenerate routeTree.gen.ts.

## Inputs

- `backend/app/api/routes/workflows_crud.py`
- `backend/app/schemas.py`
- `frontend/src/routes/_layout/dashboard.tsx`

## Expected Output

- `frontend/src/routes/_layout/workflows.tsx`
- `frontend/src/routes/_layout/workflows_.$workflowId.tsx`
- `frontend/src/components/workflows/WorkflowEditor.tsx`
- `frontend/src/components/workflows/FormSchemaEditor.tsx`
- `frontend/src/components/workflows/StepsEditor.tsx`
- `frontend/src/components/dashboard/CustomWorkflowButtons.tsx`
- `frontend/src/components/dashboard/WorkflowFormDialog.tsx`
- `frontend/src/routes/_layout/runs_.$runId.tsx`
- `frontend/tests/routes/WorkflowsList.spec.ts`
- `frontend/tests/routes/WorkflowEditor.spec.ts`
- `frontend/tests/components/CustomWorkflowButtons.spec.ts`
- `frontend/tests/components/RunCancelButton.spec.ts`

## Verification

cd frontend && bunx playwright test --project=chromium tests/routes/WorkflowsList.spec.ts tests/routes/WorkflowEditor.spec.ts tests/components/CustomWorkflowButtons.spec.ts tests/components/RunCancelButton.spec.ts
