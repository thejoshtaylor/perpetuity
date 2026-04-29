---
estimated_steps: 35
estimated_files: 2
skills_used: []
---

# T01: Write four-scenario real-API acceptance test suite

Create backend/tests/integration/test_m005_s06_acceptance_e2e.py with four test functions covering the M005 UAT contract. Tests use real Anthropic + OpenAI + GitHub APIs and skip cleanly when env vars are absent.

**Why this task exists:** S06's only deliverable is the acceptance test file. There is no product code to write — only the proof that the assembled system works end-to-end against real external services.

**Skip-guard pattern (from S02/S03/S04):** At module level, define a pytest fixture or autouse marker that checks for `ANTHROPIC_API_KEY_M005_ACCEPTANCE`, `OPENAI_API_KEY_M005_ACCEPTANCE`, and `GITHUB_TEST_ORG_PAT`. If any are absent, `pytest.skip('real API keys not set — set ANTHROPIC_API_KEY_M005_ACCEPTANCE, OPENAI_API_KEY_M005_ACCEPTANCE, GITHUB_TEST_ORG_PAT to run acceptance tests')` on all four tests. Also probe the live stack for the s16 alembic revision (same skip pattern as S05).

**Real API key injection pattern:** The test fixture calls `PUT /api/v1/teams/{team_id}/secrets/claude_api_key` with the value from `os.environ['ANTHROPIC_API_KEY_M005_ACCEPTANCE']` and `PUT /api/v1/teams/{team_id}/secrets/openai_api_key` with `os.environ['OPENAI_API_KEY_M005_ACCEPTANCE']`. This is the same route that team admins use; no test-shim CLI is dropped into the container — the real `claude` and `codex` CLIs (pinned in workspace-image Dockerfile at @anthropic-ai/claude-code@1.0.30 and @openai/codex@0.20.0) are invoked.

**Test 1 — Dashboard AI button (real Anthropic + real OpenAI):**
- Setup: create user + team, inject real API keys via PUT /teams/{id}/secrets/*, provision workspace container via POST /api/v1/sessions (S02 pattern), look up _direct_claude/_direct_codex workflow ids via GET /api/v1/teams/{id}/workflows
- Claude path: POST /workflows/{id}/run with prompt='List the files in this repo', poll GET /workflow_runs/{run_id} to terminal status (60s budget with 3s interval), assert status='succeeded', step_run.exit_code=0, step_run.stdout is non-empty (real Anthropic response), step_run.duration_ms > 0
- Codex path: same shape with _direct_codex and openai_api_key
- Negative: DELETE claude key → POST run → assert status='failed', error_class='missing_team_secret'

**Test 2 — Multi-step workflow with {prev.stdout} substitution:**
- Create workflow via POST /api/v1/teams/{id}/workflows: trigger=button, form_schema=[{name:'branch',kind:'string',required:true}], steps=[{action:'git',config:{command:['git','checkout','{form.branch}']},target_container:'user_workspace'}, {action:'shell',config:{command:['npm','install']},target_container:'user_workspace'}, {action:'shell',config:{command:['npm','run','lint']},target_container:'user_workspace'}, {action:'claude',config:{prompt_template:'summarize lint output: {prev.stdout}'},target_container:'user_workspace'}], scope='user'
- The test repo in the workspace must have a package.json with a lint script. Use a known test repo that M004 has materialized, or inject a minimal package.json via docker exec before the test.
- POST /workflows/{id}/run with trigger_payload={branch:'main', prompt:'unused'} (form field), poll to terminal
- Assert: 4 step_runs with step_index 0-3, final step (claude) has non-empty stdout, step_run 3 snapshot.config.prompt_template contains '{prev.stdout}' (template stored), step_run 3 stdout contains text from a real Claude response
- Assert run still retrievable after waiting (snapshot semantics — no re-query of workflow definition needed)

**Test 3 — GitHub webhook → workflow dispatch:**
- Requires GITHUB_TEST_ORG_PAT and GITHUB_TEST_REPO_FULL_NAME env vars
- Setup: create team + project linked to the test repo; configure push rule mode='manual_workflow' with workflow 'ci-on-pr'; register the Perpetuity GitHub App webhook on the test repo (or use a pre-configured test org)
- Action: open a PR on the test repo via GitHub API using the PAT (POST /repos/{owner}/{repo}/pulls)
- Wait for webhook delivery: poll GET /api/v1/teams/{id}/runs (from S05) for a new run with trigger_type='webhook' (30s budget, 3s interval)
- Assert: run exists with trigger_type='webhook', step_run 0 action='claude', step_run 0 status='succeeded' or 'failed' (real Claude call may fail on diff_url access — accept both; assert step_run 0 error_class is None if succeeded)
- Assert idempotency: replay the same delivery_id via re-POST /api/v1/github/webhooks, assert no second run created

**Test 4 — Round-robin scope + run history:**
- Create team with 2 members (user A = team admin, user B = team member)
- Create workflow with scope='team_round_robin'
- Provision workspace containers for both users
- Trigger 4 times as user A (each POST /workflows/{id}/run)
- Poll all 4 runs to terminal status
- Retrieve run history via GET /api/v1/teams/{id}/runs (S05 endpoint)
- Assert distribution: count distinct target_user_ids in the 4 runs; both user A and user B must appear at least once
- Offline fallback: stop user B's container (docker stop <container>); trigger once more; poll to terminal; assert target_user_id = user A (triggering user)
- History drill-down: pick any completed run, GET /workflow_runs/{id}, assert step_run has stdout/stderr/exit_code/duration_ms populated (non-empty)

**Redaction sweep (inline, not subprocess):** After all four test functions, add a session-scoped autouse fixture or a final test function `test_redaction_sweep` that: collects combined docker logs (backend + celery-worker + orchestrator containers), asserts zero matches for regex `sk-ant-[A-Za-z0-9_-]{10,}` and `sk-[A-Za-z0-9_-]{20,}` (matches the redaction-sweep.sh patterns), asserts all core observability discriminators are present at least once across the log corpus.

**Container name resolution:** The celery_worker_url fixture (from conftest.py) returns the container name. Use `docker logs <container_name>` to collect per-service logs. The orchestrator container is resolved from the orchestrator_on_e2e_db fixture or hardcoded as 'perpetuity-orchestrator-1'.

**Polling helper:** Reuse or copy the polling pattern from S02's e2e: `httpx.get` with retries, 3s sleep between polls, 60s overall budget, raise on timeout.

## Inputs

- `backend/tests/integration/conftest.py`
- `backend/tests/integration/test_m005_s02_dashboard_ai_buttons_e2e.py`
- `backend/tests/integration/test_m005_s03_workflow_run_engine_e2e.py`
- `backend/tests/integration/test_m005_s04_webhook_dispatch_e2e.py`
- `backend/tests/integration/test_m005_s05_run_history_admin_e2e.py`
- `backend/app/workflows/executors/ai.py`
- `backend/app/services/workflow_dispatch.py`
- `backend/app/api/routes/workflows.py`
- `scripts/redaction-sweep.sh`

## Expected Output

- `backend/tests/integration/test_m005_s06_acceptance_e2e.py`

## Verification

cd /Users/josh/code/perpetuity/backend && POSTGRES_DB=perpetuity_app uv run pytest -m e2e tests/integration/test_m005_s06_acceptance_e2e.py --collect-only -q && POSTGRES_DB=perpetuity_app uv run pytest -m e2e tests/integration/test_m005_s06_acceptance_e2e.py -v

## Observability Impact

Test file adds inline log-corpus sweep asserting all prior-slice discriminators fire (workflow_run_dispatched, workflow_run_started, workflow_run_succeeded, step_run_started, step_run_succeeded, oneshot_exec_started, oneshot_exec_completed) and zero sk-ant-/sk- plaintext leakage across backend + celery-worker + orchestrator combined logs.
