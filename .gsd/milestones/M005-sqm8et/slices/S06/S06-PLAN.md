# S06: Final Integrated Acceptance — real Anthropic + real OpenAI + real GitHub test org

**Goal:** Write the final integrated acceptance test suite (test_m005_s06_acceptance_e2e.py) that proves all four M005 UAT scenarios against real Anthropic, real OpenAI, and a real GitHub test org. No new product code ships — the test file is the deliverable. Tests skip cleanly when real API key env vars are absent.
**Demo:** All four 'Final Integrated Acceptance' scenarios from M005-sqm8et-CONTEXT.md pass end-to-end: (1) dashboard 'Run Claude' button → real Anthropic response in step stdout; (2) `[git checkout, npm install, npm run lint, claude -p 'summarize lint output']` workflow runs with `{prev.stdout}` substitution against a real repo; (3) external GitHub PR opens → webhook → manual_workflow push rule + webhook-trigger workflow → Claude reviews diff against team-mirror; (4) round-robin team scope distributes 4 triggers ≥1 to each of 2 members, falls back to triggering user when one member offline. Redaction sweep across all six e2e logs is clean of `sk-ant-` and `sk-` prefixes.

## Must-Haves

- All four test functions collected and skip cleanly without real API keys (pytest exit 0)
- When ANTHROPIC_API_KEY_M005_ACCEPTANCE + OPENAI_API_KEY_M005_ACCEPTANCE + GITHUB_TEST_ORG_PAT are set: all four scenarios pass against the live compose stack with real API calls
- Combined log redaction sweep (backend + celery-worker + orchestrator) finds zero sk-ant-/sk- plaintext leakage
- All prior-slice observability discriminators still emit (smoke check that nothing regressed)

## Proof Level

- This slice proves: final-assembly — real Anthropic + OpenAI + GitHub APIs required; compose stack with real containers; human-supplied API keys via env vars

## Integration Closure

Upstream surfaces consumed: S01 team_secrets (get_team_secret + PUT endpoint), S02 AI executor + orchestrator one-shot exec + run/step record shape, S03 workflow CRUD + substitution + round-robin dispatch + run history, S04 webhook → dispatch_github_event + delivery_id idempotency, S05 run history list endpoint + admin manual trigger. New wiring: none — acceptance test only reads/drives existing surfaces. What remains before milestone is truly usable end-to-end: nothing. S06 is the closure gate.

## Verification

- No new discriminators introduced. Acceptance test asserts all prior-slice discriminators still fire (workflow_run_dispatched, workflow_run_started, workflow_run_succeeded, step_run_started, step_run_succeeded, oneshot_exec_started, oneshot_exec_completed) plus verifies redaction sweep passes across combined logs from all four scenarios.

## Tasks

- [x] **T01: Write four-scenario real-API acceptance test suite** `est:3h`
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
  - Files: `backend/tests/integration/test_m005_s06_acceptance_e2e.py`, `backend/tests/integration/conftest.py`
  - Verify: cd /Users/josh/code/perpetuity/backend && POSTGRES_DB=perpetuity_app uv run pytest -m e2e tests/integration/test_m005_s06_acceptance_e2e.py --collect-only -q && POSTGRES_DB=perpetuity_app uv run pytest -m e2e tests/integration/test_m005_s06_acceptance_e2e.py -v

## Files Likely Touched

- backend/tests/integration/test_m005_s06_acceptance_e2e.py
- backend/tests/integration/conftest.py
