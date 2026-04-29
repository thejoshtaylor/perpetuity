---
estimated_steps: 1
estimated_files: 2
skills_used: []
---

# T06: Slice e2e: admin creates 'lint and report' workflow with form field, fires it, verifies {prev.stdout} substitution, cancellation between steps, and round-robin dispatch against live compose stack

Single integration test `backend/tests/integration/test_m005_s03_workflow_run_engine_e2e.py` that drives S03's full surface against the live compose stack (mirrors S02's e2e pattern with the `celery_worker_url` + `orchestrator_on_e2e_db` ephemeral fixtures from MEM437). Steps (each its own pytest test function within the file): (a) `test_workflow_crud_create_run_succeeds` — admin signs up + creates team, POSTs `/api/v1/teams/{id}/workflows` with the 'lint and report' payload (4 steps: shell `git checkout {form.branch}`, shell `npm install`, shell `npm run lint`, claude `summarize: {prev.stdout}`), drops in-container `/usr/local/bin/git` + `/usr/local/bin/npm` + `/usr/local/bin/claude` test shims (git checkout/checkout/checkout returns 'on branch X'; npm install/lint return deterministic stdout; claude shim echoes its $PROMPT so we can assert the {prev.stdout} substitution arrived), POSTs `/api/v1/workflows/{id}/run` with `trigger_payload={branch: 'main'}`, polls `GET /workflow_runs/{id}` to terminal `succeeded`, asserts: every step exit_code=0, step[3].snapshot.config.prompt_template (frozen) is the original template with `{prev.stdout}`, step[3].stdout contains the expected lint shim output (proves substitution), trigger_payload persisted correctly, no `sk-ant-`/`sk-` leak in logs. (b) `test_workflow_cancellation_terminates_run_and_skips_remaining_steps` — same workflow but with a `sleep 30` shim in step 0; POST cancel after step 0 starts; assert run terminates `cancelled`, step[0] `failed` with cancelled OR `succeeded` if it finished before cancel landed, steps[1..3] `skipped` with error_class='cancelled', `workflow_run_cancelled` log present. (c) `test_round_robin_dispatch_picks_next_member_and_advances_cursor` — create workflow with `scope=round_robin`, create team with 3 members, provision workspace volumes for 2 of them (via existing /api/v1/sessions POSTs), trigger 4 runs in sequence, assert: each run.target_user_id is one of the 2 live members, the offline member is never picked, cursor advances monotonically (read via psql), all 4 runs reach `succeeded`. (d) `test_round_robin_falls_back_to_triggering_user_when_no_live_workspace` — same but provision NO workspace volumes; trigger 1 run; assert run.target_user_id == triggering_user_id, log carries `workflow_dispatch_fallback ... reason=no_live_workspace`. (e) `test_form_field_required_validation_rejects_dispatch_without_field` — workflow with `form_schema={fields: [{name: 'branch', required: true, kind: 'string'}]}`; POST `/run` with `{}` → 400 `{detail: 'missing_required_field', field: 'branch'}`. (f) `test_substitution_failure_marks_step_failed_with_substitution_failed_discriminator` — workflow with step config referencing `{nonexistent.var}`; trigger; assert step terminates with error_class='substitution_failed', run.error_class='substitution_failed', stderr names the missing variable. (g) Combined-log redaction sweep at end-of-file scope (mirroring S02's pattern): zero `sk-ant-`/`sk-` matches, every locked observability discriminator from `_REQUIRED_DISCRIMINATORS` (extends S02's list with `workflow_run_cancelled`, `step_run_skipped`, `workflow_dispatch_round_robin_pick`, `workflow_dispatch_fallback`, `orchestrator_exec_retry`) is observed at least once across all test functions. Skip guard probes for the `s13_workflow_crud_extensions` alembic revision in `backend:latest`, instructs to rebuild compose if absent. Reuses S02's `celery_worker_url` + `orchestrator_on_e2e_db` fixtures as-is.

## Inputs

- ``backend/tests/integration/test_m005_s02_dashboard_ai_buttons_e2e.py``
- ``backend/tests/integration/conftest.py``
- ``backend/app/api/routes/workflows_crud.py``
- ``backend/app/api/routes/workflows.py``
- ``backend/app/services/workflow_dispatch.py``
- ``backend/app/workflows/substitution.py``
- ``backend/app/workflows/executors/shell.py``
- ``backend/app/workflows/executors/git.py``
- ``backend/app/workflows/tasks.py``

## Expected Output

- ``backend/tests/integration/test_m005_s03_workflow_run_engine_e2e.py``
- ``backend/tests/integration/conftest.py``

## Verification

cd /Users/josh/code/perpetuity/backend && POSTGRES_DB=perpetuity_app uv run pytest -m e2e tests/integration/test_m005_s03_workflow_run_engine_e2e.py -v

## Observability Impact

The e2e is itself a slice-level observability check — asserts every locked discriminator emits at least once in the combined backend + celery-worker + orchestrator log capture. Adds zero new runtime signals; instead it codifies the SLO contract by failing the slice if any of the 14 discriminators (S02's 9 + S03's 5: workflow_run_cancelled, step_run_skipped, workflow_dispatch_round_robin_pick, workflow_dispatch_fallback, orchestrator_exec_retry) is missing.
