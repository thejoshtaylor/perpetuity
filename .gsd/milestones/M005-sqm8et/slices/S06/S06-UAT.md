# S06: Final Integrated Acceptance — real Anthropic + real OpenAI + real GitHub test org — UAT

**Milestone:** M005-sqm8et
**Written:** 2026-04-29T11:09:32.877Z

# S06 UAT Script — Final Integrated Acceptance

## Preconditions

1. `docker compose build backend orchestrator celery-worker` — workspace image has `claude` CLI @1.0.30 and `codex` CLI @0.20.0 baked in.
2. `docker compose up -d db redis orchestrator celery-worker` — full stack running.
3. Env vars set:
   - `ANTHROPIC_API_KEY_M005_ACCEPTANCE=sk-ant-...`
   - `OPENAI_API_KEY_M005_ACCEPTANCE=sk-...`
   - `GITHUB_TEST_ORG_PAT=ghp_...`
   - `GITHUB_TEST_REPO_FULL_NAME=my-org/my-repo` (repo the PAT has write access to)
4. `POSTGRES_DB=perpetuity_app` set.

## UAT 1 — Dashboard AI Button (real Anthropic + real OpenAI)

**Run:**
```bash
cd backend && POSTGRES_DB=perpetuity_app uv run pytest -m e2e \
  tests/integration/test_m005_s06_acceptance_e2e.py::test_m005_s06_dashboard_ai_button_real_api -v
```

**Expected outcomes:**
1. `_direct_claude` workflow triggers, step_run exits 0, stdout is non-empty (real Claude response), duration_ms > 0.
2. `_direct_codex` workflow triggers, step_run exits 0, stdout is non-empty (real Codex response).
3. After claude key deleted: run status = `failed`, error_class = `missing_team_secret` on both run and step_run.
4. Test exits `PASSED`.

## UAT 2 — Multi-step Workflow with {prev.stdout} Substitution

**Run:**
```bash
cd backend && POSTGRES_DB=perpetuity_app uv run pytest -m e2e \
  tests/integration/test_m005_s06_acceptance_e2e.py::test_m005_s06_multistep_prev_stdout_substitution_real_api -v
```

**Expected outcomes:**
1. 4-step workflow created (git checkout, npm install, npm run lint, claude summarize).
2. All 4 step_runs present in the run response after terminal.
3. step[0..2] all have status=succeeded, exit_code=0.
4. step[2] (npm run lint) stdout is non-empty — the echo-based lint script fires.
5. step[3] (claude) stdout is non-empty — real Claude model summarized the lint output via {prev.stdout} substitution.
6. step[3] snapshot.config contains `prompt_template` field (snapshot semantics confirmed).
7. Run retrievable via GET /workflow_runs/{id} after 1s delay.
8. Test exits `PASSED`.

## UAT 3 — GitHub Webhook → Workflow Dispatch

**Run:**
```bash
cd backend && POSTGRES_DB=perpetuity_app uv run pytest -m e2e \
  tests/integration/test_m005_s06_acceptance_e2e.py::test_m005_s06_github_webhook_dispatch_real_api -v
```

**Expected outcomes:**
1. Branch `s06-acceptance-<token>` created on test repo, PR opened via GitHub REST API.
2. HMAC-signed webhook POST to `/api/v1/github/webhooks` returns 200, `duplicate=false`.
3. `GET /teams/{id}/runs?trigger_type=webhook` returns a run within 30s.
4. Run status in {succeeded, failed} (Claude diff review acceptance — either is valid).
5. run.trigger_type == 'webhook'.
6. step[0].snapshot.action == 'claude'.
7. Second POST with same delivery_id returns `duplicate=true`, DB count = 1.
8. GitHub PR closed and branch deleted in teardown.
9. Test exits `PASSED`.

## UAT 4 — Round-robin Team Scope + Run History

**Run:**
```bash
cd backend && POSTGRES_DB=perpetuity_app uv run pytest -m e2e \
  tests/integration/test_m005_s06_acceptance_e2e.py::test_m005_s06_round_robin_team_scope_and_run_history -v
```

**Expected outcomes:**
1. 2-member team created (admin + member B).
2. 4 round-robin runs fired, all reach terminal status within 60s each.
3. All 4 runs have target_user_id set (non-null).
4. Run history endpoint returns all 4 run IDs with count >= 4.
5. Offline fallback: workspace container stopped; 5th run's target_user_id = admin_id.
6. Drill-down GET /workflow_runs/{id}: step[0] has exit_code, duration_ms, stdout, stderr fields.
7. Test exits `PASSED`.

## UAT 5 — Redaction Sweep + Observability Discriminator Audit

**Run:**
```bash
cd backend && POSTGRES_DB=perpetuity_app uv run pytest -m e2e \
  tests/integration/test_m005_s06_acceptance_e2e.py::test_m005_s06_redaction_sweep -v
```

**Expected outcomes:**
1. Combined log blob from backend + celery-worker + orchestrator contains zero `sk-ant-*` matches.
2. Combined log blob contains zero `sk-[A-Za-z0-9_-]{20,}` matches.
3. All non-optional observability discriminators (workflow_run_dispatched, workflow_run_started, workflow_run_succeeded, step_run_started, step_run_succeeded, oneshot_exec_started, oneshot_exec_completed, webhook_dispatched, webhook_run_enqueued, webhook_dispatch_push_rule_evaluated) present at least once.
4. Optional discriminators (cap enforcement, orphan recovery) skip rather than fail if absent.
5. Test exits `PASSED` or `SKIPPED` (if optional discriminators absent — acceptable).

## Full Suite Run

```bash
cd backend && \
  ANTHROPIC_API_KEY_M005_ACCEPTANCE=sk-ant-... \
  OPENAI_API_KEY_M005_ACCEPTANCE=sk-... \
  GITHUB_TEST_ORG_PAT=ghp_... \
  GITHUB_TEST_REPO_FULL_NAME=my-org/my-repo \
  POSTGRES_DB=perpetuity_app uv run pytest -m e2e \
    tests/integration/test_m005_s06_acceptance_e2e.py -v
```

All 5 tests must pass (or PASSED+SKIPPED for test 5 if optional discriminators not exercised). No failures.

## CI Behavior (no real API keys)

```bash
cd backend && POSTGRES_DB=perpetuity_app uv run pytest -m e2e \
  tests/integration/test_m005_s06_acceptance_e2e.py -v
```

All 5 tests must exit with `SKIPPED` and message: `real API keys not set — set ANTHROPIC_API_KEY_M005_ACCEPTANCE, OPENAI_API_KEY_M005_ACCEPTANCE, GITHUB_TEST_ORG_PAT to run acceptance tests`. Exit code 0.
