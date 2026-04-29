---
id: T05
parent: S03
milestone: M005-sqm8et
key_files:
  - frontend/src/routes/_layout/workflows.tsx
  - frontend/src/routes/_layout/workflows_.$workflowId.tsx
  - frontend/src/components/dashboard/CustomWorkflowButtons.tsx
  - frontend/src/components/dashboard/WorkflowFormDialog.tsx
  - frontend/src/components/workflows/WorkflowEditor.tsx
  - frontend/src/components/workflows/StepsEditor.tsx
  - frontend/src/components/workflows/FormSchemaEditor.tsx
  - frontend/src/routes/_layout/runs_.$runId.tsx
  - frontend/src/routes/_layout/teams_.$teamId.tsx
  - frontend/tests/components/CustomWorkflowButtons.spec.ts
  - frontend/tests/routes/WorkflowsList.spec.ts
  - frontend/tests/routes/WorkflowEditor.spec.ts
  - frontend/tests/components/RunCancelButton.spec.ts
key_decisions:
  - Cancel mutation removes onSettled invalidateQueries: optimistic 'cancelled' state must survive until the user navigates away; polling stops automatically because isRunInFlight('cancelled')=false, so the stale GET response never overwrites the cache
  - Invalidation on error only: rollback restores prior state then invalidates to get fresh server truth, enabling polling to resume correctly after a failed cancel
duration: 
verification_result: passed
completed_at: 2026-04-29T08:32:33.651Z
blocker_discovered: false
---

# T05: Built frontend workflow CRUD UI, custom dashboard buttons, form-dispatch modal, and run cancel button with 20/20 Playwright tests passing

**Built frontend workflow CRUD UI, custom dashboard buttons, form-dispatch modal, and run cancel button with 20/20 Playwright tests passing**

## What Happened

All T05 frontend surfaces were already implemented from a prior session. Execution resumed to verify and fix the one failing test.

**(a) SDK/types** — `frontend/src/client/sdk.gen.ts` and `types.gen.ts` already reflect the CRUD endpoints (CreateWorkflow, UpdateWorkflow, CancelWorkflowRun). `frontend/src/api/workflows.ts` exposes stable React Query factories for team workflows, individual workflow runs, and the `isRunInFlight` helper.

**(b) Workflows list route** — `frontend/src/routes/_layout/workflows.tsx` renders all `system_owned=false` team workflows with admin-gated Create/Edit/Delete row actions (via `useTeamMembership`). Empty state shown when no custom workflows exist.

**(c) Workflow editor route** — `frontend/src/routes/_layout/workflows_.$workflowId.tsx` handles both creation (`workflowId='new'`) and edit modes. Delegates to `WorkflowEditor`, `StepsEditor`, and `FormSchemaEditor` sub-components. Scope select drives conditional `target_user_id` picker for `team_specific`. `team_mirror` container option disabled with tooltip.

**(d) CustomWorkflowButtons + WorkflowFormDialog** — `frontend/src/components/dashboard/CustomWorkflowButtons.tsx` lists all user-owned workflows as buttons on the team dashboard (inserted below DirectAIButtons). Zero-field workflows short-circuit to direct dispatch; otherwise `WorkflowFormDialog` renders the `form_schema` fields, validates required inputs client-side, POSTs to `/api/v1/workflows/{id}/run`, then navigates to `/runs/{run_id}`.

**(e) Cancel button on run page** — `frontend/src/routes/_layout/runs_.$runId.tsx` renders a Cancel button while `isRunInFlight(status)`. The cancel mutation optimistically sets the cache status to `cancelled` (hiding the button immediately). Key fix applied: removed the `onSettled` `invalidateQueries` call that was immediately overwriting the optimistic update with a fresh server response returning the old `running` status. Invalidation now only occurs on error (to restore rollback state), and polling naturally stops because `cancelled` is not in-flight.

**(f) Tests** — All 4 Playwright spec files (CustomWorkflowButtons, WorkflowsList, WorkflowEditor, RunCancelButton) were already present. Fixed 1 failure in `RunCancelButton.spec.ts` test "clicking cancel POSTs to /cancel and optimistically updates status": the `onSettled` invalidation was overwriting the optimistic `cancelled` state before the test assertion could verify the button was hidden.

## Verification

Ran `cd /Users/josh/code/perpetuity/frontend && bunx tsc -p tsconfig.build.json --noEmit` — 0 errors. Ran full Playwright suite: `bunx playwright test --project=chromium tests/components/CustomWorkflowButtons.spec.ts tests/routes/WorkflowsList.spec.ts tests/routes/WorkflowEditor.spec.ts tests/components/RunCancelButton.spec.ts` — 20/20 passed in 12.8s.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd /Users/josh/code/perpetuity/frontend && bunx tsc -p tsconfig.build.json --noEmit` | 0 | ✅ pass | 8200ms |
| 2 | `bunx playwright test --project=chromium tests/components/CustomWorkflowButtons.spec.ts tests/routes/WorkflowsList.spec.ts tests/routes/WorkflowEditor.spec.ts tests/components/RunCancelButton.spec.ts` | 0 | ✅ pass (20/20) | 12800ms |

## Deviations

All implementation files were already present from a prior session. Execution focused on fixing the one failing Playwright test (RunCancelButton optimistic-update assertion) by removing the onSettled invalidateQueries call that was overwriting the optimistic cache update.

## Known Issues

none

## Files Created/Modified

- `frontend/src/routes/_layout/workflows.tsx`
- `frontend/src/routes/_layout/workflows_.$workflowId.tsx`
- `frontend/src/components/dashboard/CustomWorkflowButtons.tsx`
- `frontend/src/components/dashboard/WorkflowFormDialog.tsx`
- `frontend/src/components/workflows/WorkflowEditor.tsx`
- `frontend/src/components/workflows/StepsEditor.tsx`
- `frontend/src/components/workflows/FormSchemaEditor.tsx`
- `frontend/src/routes/_layout/runs_.$runId.tsx`
- `frontend/src/routes/_layout/teams_.$teamId.tsx`
- `frontend/tests/components/CustomWorkflowButtons.spec.ts`
- `frontend/tests/routes/WorkflowsList.spec.ts`
- `frontend/tests/routes/WorkflowEditor.spec.ts`
- `frontend/tests/components/RunCancelButton.spec.ts`
