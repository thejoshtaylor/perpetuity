---
estimated_steps: 1
estimated_files: 18
skills_used: []
---

# T05: Build frontend workflow CRUD UI + dashboard custom buttons row + form-rendered modal + cancel button on run page

Frontend SDK regen + four new/modified UI surfaces. (a) Run `bash scripts/generate-client.sh` to pull in the new endpoints + DTOs (CreateWorkflow, UpdateWorkflow, CancelWorkflowRun, getWorkflow, listTeamWorkflows already exists from S02 — extend its types with form_schema/target_user_id/round_robin_cursor). (b) New route `frontend/src/routes/_layout/workflows.tsx` — list page rendering all team workflows where `system_owned=false`, with 'New workflow' button and per-row 'Edit' / 'Delete' actions (admin-only — checks current user's TeamRole on the active team via existing `useTeamMembership` hook). (c) New route `frontend/src/routes/_layout/workflows_.$workflowId.tsx` — editor page. Form fields: name (text), description (textarea), scope (select: user/team_specific/round_robin), target_user_id (select rendered only when scope='team_specific', listing team members), form_schema editor (repeating-row editor for fields: name/label/kind/required), steps editor (sortable list of {step_index, action: select(claude/codex/shell/git), config: action-specific JSON editor, target_container: select(user_workspace) — team_mirror disabled with tooltip 'Reserved for S04'}). Save calls POST or PUT depending on creation vs edit. (d) New component `frontend/src/components/dashboard/CustomWorkflowButtons.tsx` — replaces / extends the dashboard's workflow row with a list of all `system_owned=false` workflows for the active team, each rendered as a button. Click → opens `WorkflowFormDialog` (new component) which renders the workflow's form_schema as actual form inputs, then POSTs to `/api/v1/workflows/{id}/run` with the trigger_payload, then navigates to `/runs/{run_id}`. The form_schema with zero fields short-circuits to direct dispatch (no modal). Insert this row INSIDE the existing `/teams/$teamId` page below DirectAIButtons. (e) Modify existing `frontend/src/routes/_layout/runs_.$runId.tsx` to render a 'Cancel run' button visible while `isRunInFlight(status)`; click POSTs `/api/v1/workflow_runs/{id}/cancel`, optimistically sets status to 'cancelling', polls until terminal. (f) Playwright specs: (i) `tests/components/CustomWorkflowButtons.spec.ts` — list mode with 0 user workflows / N user workflows / submit-with-form / submit-without-form / 503 error path. (ii) `tests/routes/WorkflowsList.spec.ts` — admin sees create/edit/delete; member sees only list. (iii) `tests/routes/WorkflowEditor.spec.ts` — create flow, validation rejects `_direct_*` name, edit flow loads existing data, delete confirmation. (iv) `tests/components/RunCancelButton.spec.ts` — appears only when in-flight, click sends POST and updates UI.

## Inputs

- ``frontend/src/api/workflows.ts``
- ``frontend/src/components/dashboard/DirectAIButtons.tsx``
- ``frontend/src/components/dashboard/PromptDialog.tsx``
- ``frontend/src/routes/_layout/runs_.$runId.tsx``
- ``frontend/src/routes/_layout/teams_.$teamId.tsx``
- ``backend/app/api/routes/workflows_crud.py``
- ``backend/app/api/routes/workflows.py``

## Expected Output

- ``frontend/openapi.json``
- ``frontend/src/client/sdk.gen.ts``
- ``frontend/src/client/types.gen.ts``
- ``frontend/src/api/workflows.ts``
- ``frontend/src/routes/_layout/workflows.tsx``
- ``frontend/src/routes/_layout/workflows_.$workflowId.tsx``
- ``frontend/src/components/dashboard/CustomWorkflowButtons.tsx``
- ``frontend/src/components/dashboard/WorkflowFormDialog.tsx``
- ``frontend/src/components/workflows/WorkflowEditor.tsx``
- ``frontend/src/components/workflows/StepsEditor.tsx``
- ``frontend/src/components/workflows/FormSchemaEditor.tsx``
- ``frontend/src/routes/_layout/runs_.$runId.tsx``
- ``frontend/src/routes/_layout/teams_.$teamId.tsx``
- ``frontend/src/routeTree.gen.ts``
- ``frontend/tests/components/CustomWorkflowButtons.spec.ts``
- ``frontend/tests/routes/WorkflowsList.spec.ts``
- ``frontend/tests/routes/WorkflowEditor.spec.ts``
- ``frontend/tests/components/RunCancelButton.spec.ts``

## Verification

cd /Users/josh/code/perpetuity/frontend && bunx tsc -p tsconfig.build.json --noEmit && bunx playwright test --project=chromium tests/components/CustomWorkflowButtons.spec.ts tests/routes/WorkflowsList.spec.ts tests/routes/WorkflowEditor.spec.ts tests/components/RunCancelButton.spec.ts

## Observability Impact

Frontend toast UX surfaces all backend discriminators (cannot_modify_system_workflow, invalid_form_schema, workflow_run_not_cancellable, target_user_no_membership, missing_required_field) using the existing `extractDetail` helper from S01. No new structured client logs.
