# S03: Workflow run engine: schema extensions, substitution, executors, dispatch, CRUD API, cancel, and frontend — UAT

**Milestone:** M005-sqm8et
**Written:** 2026-04-29T08:43:03.756Z

# S03 UAT: Workflow Run Engine

## Preconditions
- Docker compose stack running with backend, celery-worker, orchestrator, postgres, redis
- backend:latest built with s13_workflow_crud_extensions migration applied
- Team admin user and at least one team member with active workspace session

---

## TC-01: Create a multi-step workflow and run it end-to-end with {prev.stdout} substitution

**Preconditions:** Team admin logged in; team with active workspace volume exists.

1. POST `/api/v1/teams/{team_id}/workflows` with:
   ```json
   {
     "name": "lint and report",
     "trigger": "button",
     "scope": "user",
     "form_schema": {"fields": [{"name": "branch", "kind": "string", "required": true, "label": "Branch"}]},
     "steps": [
       {"action": "git", "config": {"subcommand": "checkout", "args": ["{form.branch}"]}, "target_container": "user_workspace"},
       {"action": "shell", "config": {"cmd": "npm install"}, "target_container": "user_workspace"},
       {"action": "shell", "config": {"cmd": "npm run lint"}, "target_container": "user_workspace"},
       {"action": "claude", "config": {"prompt_template": "summarize lint output: {prev.stdout}"}, "target_container": "user_workspace"}
     ]
   }
   ```
   **Expected:** 201, workflow returned with `form_schema.fields[0].name='branch'`, all 4 steps with correct `target_container`.

2. POST `/api/v1/workflows/{workflow_id}/run` with `{"trigger_payload": {"branch": "main"}}`
   **Expected:** 202, `run_id` returned.

3. Poll `GET /api/v1/workflow_runs/{run_id}` until `status='succeeded'` (timeout 60s).
   **Expected:** `status='succeeded'`, all 4 steps with `exit_code=0`.

4. Inspect `step_runs[3].snapshot.config.prompt_template`.
   **Expected:** Value is the original template string `"summarize lint output: {prev.stdout}"` (frozen at dispatch time).

5. Inspect `step_runs[3].stdout`.
   **Expected:** Contains the npm lint output from step 2 (proves {prev.stdout} substitution delivered prior step output to Claude step).

6. Grep compose logs for `sk-ant-` or `sk-`.
   **Expected:** Zero matches.

---

## TC-02: Cancel a run between steps

1. Create a workflow with step 0 = `{"action": "shell", "config": {"cmd": "sleep 30"}}` and step 1 = `{"action": "shell", "config": {"cmd": "echo done"}}`.
2. POST `/api/v1/workflows/{id}/run` with `{}`.
3. Wait for `step_runs[0].status='running'`, then POST `/api/v1/workflow_runs/{run_id}/cancel`.
   **Expected:** 202 with `{"status": "cancelling"}`.
4. Poll until `workflow_run.status='cancelled'` (timeout 40s).
   **Expected:** `status='cancelled'`, `cancelled_by_user_id` set, `cancelled_at` set.
5. Inspect `step_runs[1].status`.
   **Expected:** `status='skipped'`, `error_class='cancelled'`.
6. Check worker logs for `workflow_run_cancelled` and `step_run_skipped`.
   **Expected:** Both discriminators present.

---

## TC-03: Cancel is rejected on terminal run

1. Let any run from TC-01 reach `status='succeeded'`.
2. POST `/api/v1/workflow_runs/{run_id}/cancel`.
   **Expected:** 409 `{"detail": "workflow_run_not_cancellable"}`.

---

## TC-04: Round-robin dispatch picks live workspace members only

**Preconditions:** Team with 3 members; workspace volumes provisioned for members A and B, NOT for member C.

1. Create workflow with `scope='round_robin'`.
2. Trigger 4 runs sequentially (wait for each to complete before next).
3. For each run, inspect `workflow_run.target_user_id`.
   **Expected:** All 4 `target_user_id` values are A or B (never C). Distribution: both A and B picked at least once. Worker logs contain `workflow_dispatch_round_robin_pick` for each run.
4. Query `SELECT round_robin_cursor FROM workflows WHERE id=?`.
   **Expected:** Cursor value is 4 (monotonically advanced once per dispatch).

---

## TC-05: Round-robin falls back to triggering user when no live workspace

**Preconditions:** Team with 2 members; NO workspace volumes provisioned for either.

1. Create workflow with `scope='round_robin'`.
2. POST `/api/v1/workflows/{id}/run` (triggering user = admin).
3. Inspect `workflow_run.target_user_id`.
   **Expected:** Equals the admin's user_id (triggering user). Worker logs contain `workflow_dispatch_fallback reason=no_live_workspace`.

---

## TC-06: Required form field validation

1. Create workflow with `form_schema={"fields": [{"name": "branch", "kind": "string", "required": true}]}`.
2. POST `/api/v1/workflows/{id}/run` with `{"trigger_payload": {}}` (no `branch` key).
   **Expected:** 400 `{"detail": "missing_required_field", "field": "branch"}`.

---

## TC-07: Substitution failure marks step failed

1. Create workflow with step config referencing `{nonexistent.var}`.
2. POST `/api/v1/workflows/{id}/run` with `{}`.
3. Poll until run reaches terminal state.
   **Expected:** `step_runs[0].status='failed'`, `step_runs[0].error_class='substitution_failed'`, stderr names the missing variable. `workflow_run.status='failed'`.

---

## TC-08: Admin gate on workflow CRUD

1. As non-admin team member, POST `/api/v1/teams/{team_id}/workflows` with valid payload.
   **Expected:** 403.
2. As admin, POST same payload.
   **Expected:** 201.

---

## TC-09: System workflow protection

1. Attempt PUT or DELETE on any workflow with `system_owned=true` (e.g., `_direct_claude`).
   **Expected:** 403 `{"detail": "cannot_modify_system_workflow"}`.

---

## TC-10: Dashboard custom workflow buttons and form dispatch (frontend)

**Preconditions:** Workflow from TC-01 exists; user on team dashboard.

1. Open `/` (team dashboard).
   **Expected:** "lint and report" button appears in the custom workflows section below DirectAIButtons.
2. Click "lint and report" button.
   **Expected:** `WorkflowFormDialog` opens showing a "Branch" text input field.
3. Leave "Branch" empty, click Submit.
   **Expected:** Client-side validation blocks submission; error shown on field.
4. Fill `branch=main`, click Submit.
   **Expected:** Dialog closes; browser navigates to `/runs/{run_id}`.
5. On run detail page, verify step status tiles flip from pending → running → succeeded/failed in real time (1.5s polling).
6. While a step is running, click Cancel.
   **Expected:** Cancel button disappears immediately (optimistic update). Run eventually shows `status='cancelled'` with remaining steps `skipped`.
