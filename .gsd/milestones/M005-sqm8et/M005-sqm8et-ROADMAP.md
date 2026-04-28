# M005-sqm8et: AI Integrations + Workflows

**Vision:** Turn the team-collaboration loop M004 delivered (per-team GitHub App, projects materialized via two-hop clone, push-back rules stored but inert) into actual automation. Per-team Claude + OpenAI credentials drive `claude` and `codex` CLIs inside team-scoped containers via the M001 TTY workaround. A Celery-backed workflow engine takes those AI executors plus shell/git steps and wraps them in a definition + run + step record schema with three trigger sources (dashboard button + form, GitHub webhook, admin manual) and three target scopes (user, team round-robin, team specific). M004's `dispatch_github_event` no-op stub becomes live. M004's `mode='rule'` and `mode='manual_workflow'` push-rule rows light up. By the end, a team admin can paste two API keys, click "Run Claude" on the dashboard, build a multi-step workflow with form input, and watch a real PR webhook fire a Claude review against the team-mirror — all with forever-debuggable run history.

## Success Criteria

- Team admin can paste Claude + OpenAI API keys; encryption + has_value semantics + 503-on-decrypt-failure mirror M004/S01 exactly
- Dashboard 'Run Claude' and 'Run Codex' buttons execute real `claude -p` / `codex` CLI commands inside the user's workspace container with stdout streaming to the run page
- Team-admin can create workflows with trigger (button+form / webhook / manual) + ordered steps (shell / claude / codex / git) + per-step `target_container` + scope (user / team_round_robin / team_specific)
- Celery worker picks up runs, executes steps sequentially, persists snapshot + stdout + stderr + exit + duration per step, retries container acquisition 3x exponential, honors cancellation between steps
- GitHub webhook → HMAC verifies → `dispatch_github_event` resolves matching workflows → enqueues runs idempotently keyed on delivery_id; M004's no-op stub is now live
- Push rule `mode='rule'` matches branch pattern via fnmatch and delegates to M004 auto-push; `mode='manual_workflow'` enqueues a workflow run instead
- Round-robin scope distributes across active team members with monotonic per-workflow cursor; falls back to triggering user when no member has a live workspace
- Run history UI lists all runs with filters (status, trigger type, time range) and drill-down with full per-step stdout/stderr/exit/duration; survives Celery worker restart via Postgres-as-source-of-truth
- Operational caps `max_concurrent_runs` + `max_runs_per_hour` enforced by dispatcher; orphan-run recovery scheduled task marks worker-crashed runs failed
- All four 'Final Integrated Acceptance' scenarios pass against real Anthropic + real OpenAI + real GitHub test org; redaction sweep clean of `sk-ant-` and `sk-` prefixes

## Slices

- [x] **S01: S01** `risk:Medium — encryption pattern is proven (M004/S01) but team-scoped composite-PK + cascade-on-team-delete is a structural variant; admin role gate must be airtight` `depends:[]`
  > After this: Team admin opens team settings, pastes Claude API key (`sk-ant-...`) into the new AI Credentials panel, clicks Save; subsequent GET shows `has_value: true` with no value flowing back to UI. Same for OpenAI key. Non-admin user gets 403 on PUT. Decrypt failure surfaces as 503 with `{detail: 'system_settings_decrypt_failed', key: 'claude_api_key'}` and an ERROR log naming team_id + key.

- [ ] **S02: S02** `risk:High — this is the load-bearing technical proof. Celery → orchestrator HTTP → docker exec → `script -q /dev/null …` chain has never been exercised end-to-end. If TTY semantics degrade through the orchestrator HTTP boundary, every workflow step that uses `claude` or `codex` fails. This slice retires that risk by shipping the smallest possible real surface (one prompt → one Anthropic call) through the entire stack.` `depends:[]`
  > After this: Team user clicks 'Run Claude' button in dashboard, fills 'List the files in this repo' into the modal prompt form, clicks Submit. Run page opens, shows step status flip pending → running → succeeded with full stdout from a real `claude -p '...'` call inside their `(user, team)` workspace container. Same flow for 'Run Codex'. Missing API key → step fails with `error_class='missing_team_secret'` and an inline error in the run UI.

- [ ] **S03: Workflow definition CRUD + Celery run engine + button trigger** `risk:High — the workflow schema (snapshot semantics, trigger config heterogeneity, per-step target_container) and the Celery run engine (sequential step execution, container acquisition retry, cancellation between steps, scope dispatch) together form the spine of M005. If snapshot semantics are wrong, history is corrupt; if container retry is wrong, transient failures amplify; if scope dispatch is wrong, round-robin distribution breaks UAT scenario 4.` `depends:[S02]`
  > After this: Team admin opens /workflows, creates 'lint and report' with trigger=button + form field `branch:string`, steps = `[git checkout {branch} (target=user_workspace), npm install (target=user_workspace), npm run lint (target=user_workspace), claude -p 'summarize lint output: {prev.stdout}' (target=user_workspace)]`, scope=user, saves. Workflow appears as a custom button in the dashboard. User clicks it, fills `branch=main`, hits Submit. Run page shows steps flip pending → running → succeeded/failed in real time via 1.5s polling. Final Claude step receives the prior step's stdout via `{prev.stdout}` substitution. Every step record persists snapshot + stdout + stderr + exit + duration. Cancel button between steps actually stops further execution.

- [ ] **S04: Webhook → workflow dispatch + push rule executors** `risk:Medium-high — replaces M004's `dispatch_github_event` no-op stub (R052), lights up M004's `mode='rule'` and `mode='manual_workflow'` push rules (R051), and must be idempotent under GitHub's 24h delivery retry. The dispatch boundary is where webhook-event-dedup (M004) meets workflow-run-dedup (M005) — the seam must be airtight or duplicate events double-trigger.` `depends:[S03]`
  > After this: Team admin sets project push rule to `mode='manual_workflow'` with workflow 'ci-on-pr'. External collaborator opens a PR on the connected repo. Webhook delivered → HMAC verifies (M004) → `dispatch_github_event` resolves the matching workflow → Celery enqueues a run targeting team-mirror → workflow runs `[claude -p 'review this diff: {event.pull_request.diff_url}' (target=team_mirror)]` and the step record + run record show in the dashboard within seconds. Separately: team admin sets another project's push rule to `mode='rule'` with `branch_pattern='feature/*'`; pushing `feature/foo` triggers auto-push, pushing `main` does not (logged `auto_push_skipped reason=branch_pattern_no_match`). Duplicate webhook delivery (same delivery_id) does NOT double-trigger.

- [ ] **S05: Run history UI + admin manual trigger + worker crash recovery + operational caps** `risk:Medium — UI polish + operational robustness. Less novel than S02–S04 but indispensable for forever-debuggable history (R018) and operational sanity. The worker crash recovery task and operational caps must be transactionally correct or they leak.` `depends:[S03]`
  > After this: Team user opens /runs, sees list of all team runs with filters (status, trigger type, time range), clicks a finished run, drills in to see full per-step stdout/stderr/exit/duration. Drill-down works for runs whose workflow definitions have since been edited or deleted (snapshot semantics confirmed). System admin opens /admin/workflows/{id}/run, manually triggers a run with synthetic trigger payload. Operational caps in action: `max_concurrent_runs=2` set; trigger 3 simultaneous runs; 2 succeed, the 3rd returns 429 with audit row. Restart `celery-worker` mid-run; `recover_orphan_runs` Beat task (every 10min) marks the orphan failed with `error_class='worker_crash'`.

- [ ] **S06: Final Integrated Acceptance — real Anthropic + real OpenAI + real GitHub test org** `risk:Low-medium — proves the assembled system. No new code surface; the risk is that something subtle (CLI version mismatch, real-API rate limit, webhook delivery latency) only surfaces against real services.` `depends:[S01,S02,S03,S04,S05]`
  > After this: All four 'Final Integrated Acceptance' scenarios from M005-sqm8et-CONTEXT.md pass end-to-end: (1) dashboard 'Run Claude' button → real Anthropic response in step stdout; (2) `[git checkout, npm install, npm run lint, claude -p 'summarize lint output']` workflow runs with `{prev.stdout}` substitution against a real repo; (3) external GitHub PR opens → webhook → manual_workflow push rule + webhook-trigger workflow → Claude reviews diff against team-mirror; (4) round-robin team scope distributes 4 triggers ≥1 to each of 2 members, falls back to triggering user when one member offline. Redaction sweep across all six e2e logs is clean of `sk-ant-` and `sk-` prefixes.

## Boundary Map

## Boundary Map (Backend / Orchestrator / Frontend / Compose)

### Backend (FastAPI + SQLModel)
- `backend/app/models.py` — new SQLModels: `TeamSecret`, `Workflow`, `WorkflowStep`, `WorkflowRun`, `StepRun` + Pydantic Public/Create/Update DTOs
- `backend/app/alembic/versions/s09_team_secrets.py` — `team_secrets` table (S01)
- `backend/app/alembic/versions/s10_workflows.py` — `workflows` + `workflow_steps` tables (S03)
- `backend/app/alembic/versions/s11_workflow_runs.py` — `workflow_runs` + `step_runs` tables (S03)
- `backend/app/api/routes/team_secrets.py` — new router; `PUT/GET/DELETE /api/v1/teams/{id}/secrets/{key}` (S01)
- `backend/app/api/routes/workflows.py` — new router; CRUD + `POST /api/v1/workflows/{id}/run` + `GET /api/v1/workflow_runs/{id}` + admin manual trigger (S03–S05)
- `backend/app/services/dispatch.py` — replace M004 stub with real `dispatch_github_event` (S04)
- `backend/app/services/workflow_dispatch.py` — new module; `dispatch_workflow_run`, round-robin selection, cap enforcement (S03–S05)
- `backend/app/workflows/tasks.py` — Celery task `run_workflow(run_id)` + `recover_orphan_runs` (S03, S05)
- `backend/app/workflows/executors/` — `shell.py`, `git.py`, `ai.py` (Claude + Codex via TTY wrapper) (S02–S03)
- `backend/app/core/celery_app.py` — Celery app factory + Redis broker config (S02)
- `backend/scripts/prestart.sh` — extend with new alembic upgrade

### Orchestrator
- `orchestrator/orchestrator/routes_exec.py` — new router; `POST /v1/sessions/{session_id}/exec` for one-shot command execution with TTY discipline (S02)
- `orchestrator/orchestrator/routes_team_mirror.py` — extend with `POST /v1/team-mirrors/{team_id}/exec` for team-mirror-target steps (S03)
- `orchestrator/orchestrator/auto_push.py` — extend with `mode='rule'` branch fnmatch + `mode='manual_workflow'` workflow dispatch (S04)
- `orchestrator/workspace-image/Dockerfile` — pin `claude` and `codex` CLI versions; smoke test for `claude -p "echo test"` (S02)

### Frontend (React + TanStack Router)
- `frontend/src/routes/_layout/team_secrets.$teamId.tsx` — team-admin AI key paste UI (S01)
- `frontend/src/routes/_layout/workflows.tsx` + `workflows.$workflowId.tsx` — list + edit (S03)
- `frontend/src/routes/_layout/runs.tsx` + `runs.$runId.tsx` — run history list + drill-down with 1.5s polling (S05)
- `frontend/src/routes/_layout/index.tsx` (dashboard) — extend with "Run Claude" + "Run Codex" buttons + custom workflow trigger row (S02–S03)
- `frontend/src/components/ui/RunStream.tsx` — polled render of step records (S03)

### Compose / Infrastructure
- `docker-compose.yml` — new service `celery-worker` (same image as backend) running `celery -A app.workflows.tasks worker --loglevel=info --concurrency=4` + new `celery-beat` running `celery -A app.workflows.tasks beat` for `recover_orphan_runs` (S03, S05)
- `docker-compose.yml` — celery-worker gets `ORCHESTRATOR_API_KEY` env (D016 two-key shared-secret); does NOT mount Docker socket (D005)
- `scripts/redaction-sweep.sh` — extend with `sk-ant-` and `sk-` patterns (S01–S06)

### Integration Tests
- `backend/tests/integration/test_m005_s01_team_secrets_e2e.py` — paste-once / GET-has_value / 403 / 503-on-decrypt-failure
- `backend/tests/integration/test_m005_s02_dashboard_ai_buttons_e2e.py` — respx-mocked Anthropic + OpenAI; dashboard click → run → step record
- `backend/tests/integration/test_m005_s03_workflow_run_engine_e2e.py` — workflow CRUD + button trigger + scope + retry + cancellation
- `backend/tests/integration/test_m005_s04_webhook_dispatch_e2e.py` — mock-github sidecar webhook → dispatch → run; push rule executors
- `backend/tests/integration/test_m005_s05_run_history_admin_e2e.py` — UI history + admin manual + worker crash recovery
- `backend/tests/integration/test_m005_s06_acceptance_e2e.py` — four scenarios against real Anthropic + OpenAI + GitHub test org
