---
estimated_steps: 12
estimated_files: 1
skills_used: []
---

# T03: E2e integration test suite for webhook dispatch

Write `backend/tests/integration/test_m005_s04_webhook_dispatch_e2e.py` covering the full S04 surface against a live compose stack.

Follow the S02/S03 e2e test pattern: `pytest.mark.e2e`, `@pytest.fixture(scope='module')` for compose stack, respx for mocking external GitHub API calls, log accumulator + discriminator sweep.

**Test functions (7):**

1. `test_webhook_pr_manual_workflow_push_rule_dispatches_run` — Create a project with push rule `mode='manual_workflow'` + `workflow_id=<uuid of a test workflow with claude step>`. POST a synthetic `pull_request` webhook event (opened) with matching `installation.id`. Assert: (a) `WorkflowRun` row created with `trigger_type='webhook'`, `trigger_payload` contains the PR payload, `webhook_delivery_id` matches the delivery_id header. (b) Celery picks up and transitions run to running/succeeded. (c) `webhook_run_enqueued` discriminator in logs.

2. `test_webhook_duplicate_delivery_id_no_double_trigger` — POST the same webhook payload twice with the same `X-GitHub-Delivery` header value. Assert: only ONE `WorkflowRun` row exists with that `webhook_delivery_id`. Second POST returns 200 (route still returns ok) but no second run is created.

3. `test_webhook_push_rule_mode_rule_branch_match_triggers_auto_push` — Create a project with push rule `mode='rule'` + `branch_pattern='feature/*'`. POST a `push` webhook event with `ref=refs/heads/feature/test-branch`. Assert: orchestrator auto-push-callback was called for that project_id (use respx to mock orchestrator, or check the log discriminator `webhook_dispatch_push_rule_evaluated mode=rule outcome=auto_push_triggered`).

4. `test_webhook_push_rule_mode_rule_branch_no_match_skips` — Same setup but push `ref=refs/heads/main`. Assert: no `WorkflowRun` created, log contains `auto_push_skipped reason=branch_pattern_no_match`.

5. `test_webhook_no_installation_graceful_skip` — POST webhook event with no `installation` key in payload. Assert: no WorkflowRun created, log contains `webhook_dispatch_no_installation`, route returns 200.

6. `test_webhook_run_target_is_team_mirror` — For `manual_workflow` push rule where the workflow step has `target_container='team_mirror'`: verify the WorkflowRun's `trigger_payload` contains the PR payload and the step snapshot shows `target_container='team_mirror'` after execution.

7. `test_discriminator_sweep` — POST two webhook events (one manual_workflow, one push/rule-match). Collect all logs. Assert: (a) `webhook_dispatched dispatch_status=dispatched` appears for both; (b) `webhook_run_enqueued` appears for manual_workflow event; (c) `webhook_dispatch_push_rule_evaluated` appears for push event; (d) no `sk-ant-` or `sk-` substrings in any log line.

**Note on orchestrator mocking:** For tests 3 and 4, mock the orchestrator auto-push-callback HTTP call via respx (pointing at the test orchestrator service's URL). The orchestrator itself doesn't need to actually perform a git push in tests — just return `{"result": "ok"}`.

**Fixture requirements:** Requires `team`, `project`, `push_rule`, `workflow` (with at least one claude step), `team_mirror_container` (mocked). Follow the same `shim_inject` + `log_accumulator` pattern established in S02/S03.

## Inputs

- `backend/tests/integration/test_m005_s03_workflow_run_engine_e2e.py`
- `backend/tests/integration/test_m005_s02_dashboard_ai_buttons_e2e.py`
- `backend/app/services/dispatch.py`
- `backend/app/models.py`
- `backend/app/api/routes/github_webhooks.py`

## Expected Output

- `backend/tests/integration/test_m005_s04_webhook_dispatch_e2e.py`

## Verification

cd backend && POSTGRES_DB=perpetuity_app uv run pytest -m e2e tests/integration/test_m005_s04_webhook_dispatch_e2e.py -v → 7 tests collected; without live compose stack all 7 skip (exit 0); with live stack all 7 pass.
