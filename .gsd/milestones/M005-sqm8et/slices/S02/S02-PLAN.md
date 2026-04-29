# S02: Dashboard direct AI buttons (Claude + Codex) — proves the AI executor end-to-end

**Goal:** Prove the AI executor end-to-end. Ship the smallest real surface that exercises Celery worker → orchestrator HTTP → docker exec → `script -q /dev/null` wrapped CLI: a minimal `workflows` + `workflow_runs` + `step_runs` schema; auto-seeded system workflows `_direct_claude` and `_direct_codex` (D028) per team; an orchestrator one-shot exec endpoint with TTY discipline; a Celery `run_workflow` task with a real `ai` step executor that reads the team's API key via `get_team_secret` (M005/S01); a backend `POST /api/v1/workflows/{id}/run` trigger endpoint plus `GET /api/v1/workflow_runs/{id}` polling shape; dashboard 'Run Claude' / 'Run Codex' buttons that open a prompt modal and route to a polled run page; and a slice e2e against the live compose stack. After this slice, S03 ships the broader CRUD on the same engine; the load-bearing technical risk in M005 (TTY semantics through Celery + docker exec) is retired.
**Demo:** Team user clicks 'Run Claude' button in dashboard, fills 'List the files in this repo' into the modal prompt form, clicks Submit. Run page opens, shows step status flip pending → running → succeeded with full stdout from a real `claude -p '...'` call inside their `(user, team)` workspace container. Same flow for 'Run Codex'. Missing API key → step fails with `error_class='missing_team_secret'` and an inline error in the run UI.

## Must-Haves

- Team user clicks 'Run Claude' on the team dashboard, fills 'List the files in this repo' into the prompt modal, hits Submit. The browser navigates to /runs/{run_id}. Within seconds the page reflects the run flipping pending → running → succeeded with the stdout from a real `claude -p '...'` call (a deterministic test-shim CLI in the e2e environment) executed inside the user's (user, team) workspace container. Same flow for 'Run Codex'. When the team's claude_api_key is unset, the run flips pending → running → failed with error_class='missing_team_secret' and an inline error message in the run UI. Run records persist snapshot + stdout + stderr + exit + duration per step; redaction sweep across new logs is clean of sk-ant- and sk- prefixes.

## Proof Level

- This slice proves: - This slice proves: integration. The slice ships a real frontend trigger that drives a real Celery → orchestrator → docker-exec chain, persists real run + step records, and surfaces them through a real polling UI. Real runtime required: yes (docker compose stack, including a new celery-worker service). Human/UAT required: no for slice closure (e2e covers the chain end-to-end with a test-mode CLI shim); real Anthropic + OpenAI are reserved for S06 per D029.

## Integration Closure

- Upstream surfaces consumed: `backend/app/api/team_secrets.py::get_team_secret` (S01 boundary; raises `MissingTeamSecretError` / `TeamSecretDecryptError`), `backend/app/api/deps.py` for current-user/team-membership gates, orchestrator session-provisioning routes (the dashboard ensures a session exists before triggering — reused, not modified), `orchestrator/orchestrator/sessions.py::_exec_collect` and the existing `aiodocker` patterns (MEM274 for env-injected secrets, MEM110/MEM124 for stream lifecycle).
- New wiring introduced in this slice: (a) Celery app factory + Redis broker (`app/core/celery_app.py`); (b) compose `celery-worker` service sharing the backend image and `ORCHESTRATOR_API_KEY` env; (c) auto-seed of `_direct_claude` and `_direct_codex` workflows (data-only migration backfilling existing teams + a `seed_system_workflows(session, team_id)` helper called from the team-create code path); (d) orchestrator `POST /v1/sessions/{session_id}/exec` reuses provision_container so a session is auto-spun-up if absent; (e) frontend dashboard buttons + run-detail route + 1.5s polling.
- What remains before the milestone is truly usable end-to-end: S03 lands custom workflow CRUD + multi-step + form fields + `{prev.stdout}` substitution + scope dispatch; S04 lands webhook dispatch + push-rule executors; S05 lands history list/filter + admin manual + worker crash recovery + ops caps; S06 runs against real Anthropic + OpenAI + GitHub.

## Verification

- Runtime signals: structured INFO logs `workflow_run_dispatched run_id=<uuid> workflow_id=<uuid> trigger_type=button`, `workflow_run_started run_id=<uuid>`, `step_run_started run_id=<uuid> step_index=<n> action=claude|codex`, `step_run_succeeded run_id=<uuid> step_index=<n> exit=0 duration_ms=<n>`, `step_run_failed run_id=<uuid> step_index=<n> exit=<n> error_class=<missing_team_secret|orchestrator_exec_failed|cli_nonzero> duration_ms=<n>`, `workflow_run_succeeded|failed run_id=<uuid> duration_ms=<n>`. Orchestrator emits `oneshot_exec_started session_id=<uuid> action=<claude|codex>` and `oneshot_exec_completed session_id=<uuid> exit=<n> duration_ms=<n>`. NEVER logs the prompt body, the API key, or the CLI stdout.
- Inspection surfaces: `GET /api/v1/workflow_runs/{run_id}` returns the run with ordered `step_runs` (snapshot, status, stdout, stderr, exit_code, duration_ms, started_at, finished_at, error_class). Dashboard `/runs/{run_id}` polls this every 1.5s. `psql perpetuity_app -c "SELECT id, workflow_id, status, error_class FROM workflow_runs ORDER BY created_at DESC LIMIT 5"` for ops drilldown.
- Failure visibility: `step_runs.error_class` is the discriminator: `missing_team_secret` (S01 boundary), `orchestrator_exec_failed` (HTTP error reaching orchestrator), `cli_nonzero` (CLI exited non-zero — stderr captured), `worker_crash` (reserved for S05). `last_heartbeat_at` reserved for S05's recovery; S02 sets it on transition into running.
- Redaction constraints: `step_runs.stdout` and `stderr` ARE persisted (R018: forever-debuggable history); the rest of the system never logs them. `claude_api_key` and `openai_api_key` plaintexts only ever exist in the executor's request frame and the `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` env dict passed to `container.exec(...)` (MEM274 pattern); never logged, never persisted in step_runs.snapshot. Redaction sweep extended in S01 covers `sk-ant-` and `sk-`; this slice's e2e re-runs it.

## Tasks

- [x] **T01: Minimal workflows + workflow_runs + step_runs schema + SQLModels + DTOs** `est:1 day`
  Land the slim subset of the M005 schema that S02 needs. Workflows is shaped to accommodate S03's eventual multi-step + scope + per-step target_container so S03 doesn't migrate. workflow_runs persists trigger + status + timing + scope target. step_runs persists snapshot + stdout + stderr + exit + duration + error_class. Add `system_owned BOOLEAN` to `workflows` so S03's CRUD UI can filter `_direct_claude` / `_direct_codex` out (D028). All FKs ON DELETE CASCADE on team and workflow (orphan run history is meaningless). Composite uniqueness on `(team_id, name)` for workflows so duplicate seed attempts are caught. Add SQLModel rows + Pydantic Public/Create DTOs covering only what S02 reads — additional DTOs (Update, full step list create, etc.) land in S03. Migration test runs upgrade-from-s09 + downgrade round-trip.

Assumptions documented inline: (1) `workflow_runs.scope` is omitted — it lives on `workflows.scope` and the run inherits at dispatch. S02 only uses scope='user'; round-robin and team_specific land in S03's dispatcher. (2) `workflow_steps` is a sibling table (not JSONB on `workflows`) so S03 can ALTER and add per-step fields without rewriting; for S02 each system workflow has exactly one step. (3) `step_runs.snapshot` is JSONB capturing the WorkflowStep row at run-dispatch time — S02 writes the whole snapshot row.
  - Files: `backend/app/alembic/versions/s10_workflows.py`, `backend/app/alembic/versions/s11_workflow_runs.py`, `backend/app/models.py`, `backend/tests/migrations/test_s10_workflows_migration.py`, `backend/tests/migrations/test_s11_workflow_runs_migration.py`
  - Verify: cd backend && POSTGRES_DB=perpetuity_app uv run pytest tests/migrations/test_s10_workflows_migration.py tests/migrations/test_s11_workflow_runs_migration.py -v

- [x] **T02: Pin claude/codex CLIs in workspace image + add orchestrator one-shot exec endpoint + auto-seed system workflows** `est:1.5 days`
  Three deliverables that share a single context: the workspace-image change, the orchestrator HTTP endpoint that runs CLIs inside it, and the data-migration that gives every team `_direct_claude` and `_direct_codex` workflows ready to fire (D028).

(1) `orchestrator/workspace-image/Dockerfile`: install pinned `claude` and `codex` CLIs. The Anthropic CLI is npm-installable as `@anthropic-ai/claude-code`; pin a specific version. The OpenAI Codex CLI is `@openai/codex`; pin a specific version. Add a smoke step that runs `script -q /dev/null sh -c 'claude --version'` and `script -q /dev/null sh -c 'codex --version'` so the build fails if the TTY-wrapped invocation regresses.

(2) `orchestrator/orchestrator/routes_exec.py`: new router. `POST /v1/sessions/{session_id}/exec` accepts `{user_id, team_id, cmd: list[str], env: dict[str,str], timeout_seconds: int (cap at 600), cwd: str | None}`. Provisions/finds the workspace container via existing `provision_container` (idempotent). Wraps the cmd as `['script', '-q', '/dev/null', 'sh', '-c', '<shell-quoted cmd with $VARS>']` and passes secrets via the env dict (MEM274 pattern — never inline plaintext into cmd). Uses `_exec_collect` from `orchestrator/orchestrator/sessions.py` to capture stdout + exit_code (with `tty=True` so stdout/stderr merge — that's what `script -q /dev/null` discipline produces anyway; M005 takes the merged stream). Returns `{stdout: str, exit_code: int, duration_ms: int}`. On `DockerUnavailable` returns 503 (existing handler). Logs `oneshot_exec_started session_id action=` and `oneshot_exec_completed exit duration_ms`; never logs cmd, env values, or stdout.

(3) `backend/app/alembic/versions/s12_seed_direct_workflows.py`: data-only migration that backfills `_direct_claude` and `_direct_codex` workflows (with `system_owned=TRUE`, `scope='user'`) for every existing team. Each gets one `WorkflowStep` (step_index=0): action=`claude` for `_direct_claude` with config `{prompt_template: '{prompt}'}`; action=`codex` for `_direct_codex` with config `{prompt_template: '{prompt}'}`. ON CONFLICT DO NOTHING on `(team_id, name)` so re-running is safe. Add helper `seed_system_workflows(session, team_id)` in `backend/app/api/workflows_seed.py` and wire it into the existing team-create code path (`backend/app/api/routes/teams.py`).
  - Files: `orchestrator/workspace-image/Dockerfile`, `orchestrator/orchestrator/routes_exec.py`, `orchestrator/orchestrator/main.py`, `orchestrator/tests/integration/test_routes_exec.py`, `backend/app/alembic/versions/s12_seed_direct_workflows.py`, `backend/app/api/workflows_seed.py`, `backend/app/api/routes/teams.py`, `backend/tests/api/test_workflows_seed.py`, `backend/tests/migrations/test_s12_seed_direct_workflows_migration.py`
  - Verify: docker compose build --pull orchestrator && cd orchestrator && uv run pytest tests/integration/test_routes_exec.py -v && cd ../backend && POSTGRES_DB=perpetuity_app uv run pytest tests/api/test_workflows_seed.py tests/migrations/test_s12_seed_direct_workflows_migration.py -v

- [x] **T03: Celery app + Redis broker + AI executor + run_workflow task** `est:2 days`
  Stand up the Celery worker spine and the AI executor that S02 proves end-to-end.

(1) `backend/app/core/celery_app.py`: Celery app factory bound to the existing Redis broker. URL composed from `REDIS_HOST`/`REDIS_PORT`/`REDIS_PASSWORD` env (same pattern as `app/core/rate_limit.py`). Database backend = none (status persisted in Postgres `workflow_runs.status`, not Celery's result backend — Celery's role is task dispatch, not source of truth, per MEM009). Configure `task_acks_late=True` and `task_reject_on_worker_lost=True` so a worker crash mid-task lets Celery requeue (S05 will add the orphan-recovery beat task; S02 ships the Celery-side flag so S05 doesn't have to retroactively change worker config).

(2) `backend/app/workflows/__init__.py` + `backend/app/workflows/executors/ai.py`: the `ai` executor function `run_ai_step(session, step_run_id) -> None` that (a) reads the StepRun + parent WorkflowRun + Team, (b) reads `claude_api_key` or `openai_api_key` via `get_team_secret` — on `MissingTeamSecretError` set `step_runs.status='failed'`, `error_class='missing_team_secret'`, `stderr='Team secret <key> not set'`, finished_at, duration_ms; commit; return, (c) renders the prompt from `snapshot.config.prompt_template` substituting `{prompt}` from `WorkflowRun.trigger_payload['prompt']`, (d) calls the orchestrator `POST /v1/sessions/{session_id}/exec` via httpx with the shared-secret header (D016) — session_id is generated per-run as a deterministic UUID5 from `(target_user_id, team_id, run_id)` so re-runs and dispatch retries hit the same workspace container, (e) cmd = `['claude', '-p', '<rendered prompt>', '--dangerously-skip-permissions']` (or `codex` equivalent), env = `{'ANTHROPIC_API_KEY': <plaintext>}` or `{'OPENAI_API_KEY': <plaintext>}`, (f) on response: persist stdout, stderr (orchestrator returns merged via `script -q /dev/null` discipline → both into stdout, leave stderr empty), exit_code, duration_ms, status='succeeded' if exit_code==0 else 'failed' with error_class='cli_nonzero', (g) on httpx error: `error_class='orchestrator_exec_failed'`. NEVER log the prompt, the env values, or the stdout.

(3) `backend/app/workflows/tasks.py`: Celery task `run_workflow(run_id: str)`. Loads `WorkflowRun` by id; if status != 'pending' → log `workflow_run_skipped_not_pending` and return (idempotency guard — Celery may double-deliver). Transition run to 'running', stamp `started_at` and `last_heartbeat_at`. For each `WorkflowStep` (S02 has exactly one, but the loop is shaped right for S03): create the `step_run` row with snapshot, transition to 'running', dispatch by action (`claude`/`codex` → `run_ai_step`; `shell`/`git` → reserved for S03, raise NotImplementedError if encountered). After step returns: if status=='failed', mark run as 'failed' with `error_class` propagated from the failed step, persist `finished_at` and `duration_ms`, log `workflow_run_failed`, return. After last step: mark run as 'succeeded', `finished_at`, `duration_ms`, log `workflow_run_succeeded`. The whole task is wrapped in a try/except: any unhandled exception sets `error_class='worker_crash'` (reserved discriminator) and re-raises so Celery's `task_reject_on_worker_lost=True` semantics kick in.

**Failure Modes:**
| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| `get_team_secret` (S01 helper) | step_runs.error_class='missing_team_secret' or 'team_secret_decrypt_failed'; status='failed'; commit | N/A (sync DB) | N/A |
| Orchestrator HTTP | error_class='orchestrator_exec_failed'; stderr=str(exc); status='failed' | httpx default timeout (30s) → same as error | error_class='orchestrator_exec_failed'; treat as failed step |
| Postgres (status writes) | Bubble up → Celery's `task_reject_on_worker_lost` requeues; S05 owns crash recovery | N/A | N/A |

**Load Profile:** Postgres connection pool (1 conn per running step), orchestrator HTTP (one in-flight exec per run), Redis broker. Per-op cost: 2 DB writes + 1 orchestrator HTTP + 1 final DB write per step. 10x breakpoint: Postgres pool first; S05 caps via `max_concurrent_runs`.

**Negative Tests:** run_id that doesn't exist (log + return); workflow with zero steps (succeed empty); orchestrator 5xx, exit_code=1, get_team_secret raises Missing/Decrypt; run_id already in 'running' (idempotency).
  - Files: `backend/app/core/celery_app.py`, `backend/app/workflows/__init__.py`, `backend/app/workflows/executors/__init__.py`, `backend/app/workflows/executors/ai.py`, `backend/app/workflows/tasks.py`, `backend/tests/api/test_workflow_executor_ai.py`, `backend/tests/api/test_workflow_runner.py`
  - Verify: cd backend && POSTGRES_DB=perpetuity_app uv run pytest tests/api/test_workflow_executor_ai.py tests/api/test_workflow_runner.py -v

- [ ] **T04: Workflow trigger + run-detail API + compose celery-worker service** `est:1 day`
  Backend HTTP boundary the dashboard calls plus the compose service that runs the Celery worker.

(1) `backend/app/api/routes/workflows.py`: new router. `POST /api/v1/workflows/{workflow_id}/run` — body `{trigger_payload: dict}` where for `_direct_claude` / `_direct_codex` the payload is `{prompt: str}`. Caller must be a member of the workflow's team (use existing `assert_caller_is_team_member`). Inserts `workflow_runs` row with status='pending', trigger_type='button', triggered_by_user_id=current_user.id, target_user_id=current_user.id (S02 scope='user'), trigger_payload as-is. Inserts `step_runs` rows from `workflow_steps` snapshot — each with status='pending', snapshot=row.dict() at dispatch time. Commits. Then dispatches `run_workflow.delay(run_id)`. Returns `{run_id: UUID, status: 'pending'}`. Failure modes: workflow_id not found → 404 `workflow_not_found`; non-member → 403 `not_team_member`; missing prompt for AI workflow → 400 `missing_required_field`. Logs `workflow_run_dispatched run_id workflow_id trigger_type=button`.

(2) Same router: `GET /api/v1/workflow_runs/{run_id}` — returns `WorkflowRunPublic` with ordered `step_runs: list[StepRunPublic]`. Caller must be a member of the run's team (joins through workflow → team). 404 if not found.

(3) Same router: `GET /api/v1/teams/{team_id}/workflows` — list query for the dashboard (filtered by team membership; T05's frontend uses this to find the `_direct_claude` / `_direct_codex` workflow ids).

(4) Wire router into `backend/app/api/main.py`.

(5) `docker-compose.yml`: new service `celery-worker` using the same backend image, `command: celery -A app.workflows.tasks worker --loglevel=info --concurrency=4`, env mirrors backend (Postgres, Redis, ORCHESTRATOR_BASE_URL, ORCHESTRATOR_API_KEY, SYSTEM_SETTINGS_ENCRYPTION_KEY) per D016 two-key shared-secret discipline; depends_on db + redis + prestart. Does NOT mount Docker socket (D005).

**Failure Modes:**
| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| Postgres (insert workflow_runs/step_runs) | 500 — let SQLAlchemy bubble; transactional rollback | N/A | N/A |
| Celery `.delay()` (Redis broker) | 503 `task_dispatch_failed`; mark run as failed with `error_class='dispatch_failed'`; client surfaces error inline | Default kombu timeout; same as error | N/A |

**Load Profile:** Postgres pool (one transaction per trigger), Redis broker (one publish per trigger). 2 DB writes + 1 publish per op. 10x breakpoint: Redis broker queue depth — S05 caps via `max_concurrent_runs`; S02 unguarded.

**Negative Tests:** empty trigger_payload, prompt absent, workflow_id not UUID, run_id not UUID, workflow_id valid but for a different team than caller, run_id of cascaded-deleted workflow.
  - Files: `backend/app/api/routes/workflows.py`, `backend/app/api/main.py`, `docker-compose.yml`, `backend/tests/api/test_workflow_run_routes.py`
  - Verify: cd backend && POSTGRES_DB=perpetuity_app uv run pytest tests/api/test_workflow_run_routes.py -v && docker compose config --services | grep -q celery-worker

- [ ] **T05: Dashboard direct AI buttons + run-detail polled page** `est:1.5 days`
  Frontend changes that make the dashboard demo-truth statement pass. Two surfaces: the team dashboard (extends the existing `teams_.$teamId.tsx` route which already hosts the S01 secrets panel — that route IS the team dashboard for S02's purposes) and a new `/runs/$runId` route.

(1) `frontend/src/components/dashboard/DirectAIButtons.tsx`: renders two buttons ('Run Claude', 'Run Codex'). On click, opens a `PromptDialog` modal with a Textarea for the prompt and Submit / Cancel. On Submit: looks up the `_direct_claude` (or `_direct_codex`) workflow id via `GET /api/v1/teams/{team_id}/workflows` (added in T04). Posts to `POST /api/v1/workflows/{id}/run` with `{trigger_payload: {prompt}}`. On success: navigates to `/runs/{run_id}`.

(2) `frontend/src/routes/_layout/runs_.$runId.tsx`: new TanStack route. `useQuery` against `GET /api/v1/workflow_runs/{run_id}` with `refetchInterval: 1500` while run.status is 'pending' or 'running'; stops polling when terminal. Renders run header (workflow name, status pill, started/finished timestamps, duration), then ordered step list each with status pill, action label, exit_code, error_class (if any), and a collapsed-by-default `<details>` block with stdout content. When the step transitions to running, show a small spinner; when failed, show the error_class prominently. Empty stdout shows a muted 'no output' note.

(3) Wire `DirectAIButtons` into `frontend/src/routes/_layout/teams_.$teamId.tsx` above the existing TeamSecretsPanel. Only render for users who have at least 'member' role on the team.

(4) Frontend SDK regen via existing tooling so generated types pick up the new endpoints (`frontend/src/client/sdk.gen.ts`, `frontend/src/client/types.gen.ts`, `frontend/openapi.json`).

(5) Playwright tests: dashboard renders both buttons for member; clicking 'Run Claude' opens modal; submitting routes to `/runs/<uuid>`; run page polls and reflects status transitions (T05's Playwright uses Playwright's `page.route()` to mock the trigger and run-detail endpoints and step the response shape through pending → running → succeeded — full end-to-end Celery integration is covered in T06).
  - Files: `frontend/src/components/dashboard/DirectAIButtons.tsx`, `frontend/src/components/dashboard/PromptDialog.tsx`, `frontend/src/routes/_layout/runs_.$runId.tsx`, `frontend/src/routes/_layout/teams_.$teamId.tsx`, `frontend/src/api/workflows.ts`, `frontend/src/client/sdk.gen.ts`, `frontend/src/client/types.gen.ts`, `frontend/openapi.json`, `frontend/tests/components/DirectAIButtons.spec.ts`, `frontend/tests/components/RunDetailPage.spec.ts`
  - Verify: cd frontend && npm run build && npx playwright test tests/components/DirectAIButtons.spec.ts tests/components/RunDetailPage.spec.ts

- [ ] **T06: Slice e2e — dashboard click → run page reflects success against live compose stack** `est:2 days`
  Slice closure. Single integration test runs the full chain against the live compose stack with a deterministic test-shim CLI replacing the real `claude` / `codex` (real-API acceptance is reserved for S06 per D029).

Test plan in `backend/tests/integration/test_m005_s02_dashboard_ai_buttons_e2e.py`:
  (1) Skip-guard via MEM162 pattern: probe `backend:latest` for `s12_seed_direct_workflows` revision; skip with `docker compose build backend orchestrator celery-worker` instruction on miss.
  (2) Bring up `db redis orchestrator backend celery-worker` via the existing `compose_stack_up` fixture, extended to start celery-worker.
  (3) Inject test shims into the workspace image: a small fixture that runs `docker exec <workspace-container> sh -c 'cat > /usr/local/bin/claude << EOF ...; chmod +x ...'` once a workspace container exists for the test team. The shims read `$ANTHROPIC_API_KEY` / `$OPENAI_API_KEY` from env and only succeed if non-empty so the missing-key path is genuine. Same for codex.
  (4) Sign up admin user, create team, paste claude_api_key + openai_api_key via the S01 endpoints. Sign up second member user.
  (5) Spin up a workspace session for the member user via existing session-create route so a workspace container exists. Inject the claude/codex shim.
  (6) Find `_direct_claude` workflow_id via `GET /api/v1/teams/{team_id}/workflows`; assert it exists with `system_owned=true`.
  (7) `POST /api/v1/workflows/{id}/run` with `{trigger_payload: {prompt: 'list the files'}}`; assert 200 with `run_id` and `status='pending'`.
  (8) Poll `GET /api/v1/workflow_runs/{run_id}` every 500ms up to 30s. Assert run transitions pending → running → succeeded; assert exactly one step_run, with snapshot.action='claude', exit_code=0, stdout containing 'stub-claude-output for prompt:' and the prompt text. Duration_ms > 0.
  (9) DELETE the team's claude_api_key. Trigger another run for `_direct_claude`. Poll. Assert run terminates with status='failed', step_run.status='failed', step_run.error_class='missing_team_secret'.
  (10) Repeat happy path for `_direct_codex` workflow with the codex shim and OPENAI_API_KEY check.
  (11) Final redaction sweep: run `bash scripts/redaction-sweep.sh` (already extended in S01 for sk-ant- and sk-) over `docker compose logs backend orchestrator celery-worker` — assert zero matches for both prefixes plus assert presence of locked log discriminators (`workflow_run_dispatched`, `workflow_run_started`, `workflow_run_succeeded`, `workflow_run_failed`, `step_run_started`, `step_run_succeeded`, `step_run_failed`, `oneshot_exec_started`, `oneshot_exec_completed`).

**Failure Modes:**
| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| Compose stack | Skip with rebuild instruction (existing `_e2e_env_check` pattern) | Skip | N/A |
| celery-worker pickup | Test fails with timeout — log dump for ops triage | 30s poll budget; fail with last polled state captured | N/A |
| Workspace shim install | Fail-fast: shim install via `docker exec` returns non-zero → assertion error with stderr | N/A | N/A |

**Load Profile:** full compose stack (one db, one redis, one orchestrator, one backend, one celery-worker, one workspace container). ≤ 6 trigger+poll cycles per test. 10x breakpoint not relevant.

**Negative Tests:** missing-key path explicit (case 9); test-shim deliberately fails when ANTHROPIC_API_KEY env is missing — proves the env-injection code path is wired; redaction sweep fails the test if any sk-ant-/sk- bearer-shape leaks into compose logs.
  - Files: `backend/tests/integration/test_m005_s02_dashboard_ai_buttons_e2e.py`, `backend/tests/integration/conftest.py`, `scripts/redaction-sweep.sh`
  - Verify: docker compose build backend orchestrator && docker compose up -d db redis orchestrator && cd backend && POSTGRES_DB=perpetuity_app uv run pytest -m e2e tests/integration/test_m005_s02_dashboard_ai_buttons_e2e.py -v && bash /Users/josh/code/perpetuity/scripts/redaction-sweep.sh

## Files Likely Touched

- backend/app/alembic/versions/s10_workflows.py
- backend/app/alembic/versions/s11_workflow_runs.py
- backend/app/models.py
- backend/tests/migrations/test_s10_workflows_migration.py
- backend/tests/migrations/test_s11_workflow_runs_migration.py
- orchestrator/workspace-image/Dockerfile
- orchestrator/orchestrator/routes_exec.py
- orchestrator/orchestrator/main.py
- orchestrator/tests/integration/test_routes_exec.py
- backend/app/alembic/versions/s12_seed_direct_workflows.py
- backend/app/api/workflows_seed.py
- backend/app/api/routes/teams.py
- backend/tests/api/test_workflows_seed.py
- backend/tests/migrations/test_s12_seed_direct_workflows_migration.py
- backend/app/core/celery_app.py
- backend/app/workflows/__init__.py
- backend/app/workflows/executors/__init__.py
- backend/app/workflows/executors/ai.py
- backend/app/workflows/tasks.py
- backend/tests/api/test_workflow_executor_ai.py
- backend/tests/api/test_workflow_runner.py
- backend/app/api/routes/workflows.py
- backend/app/api/main.py
- docker-compose.yml
- backend/tests/api/test_workflow_run_routes.py
- frontend/src/components/dashboard/DirectAIButtons.tsx
- frontend/src/components/dashboard/PromptDialog.tsx
- frontend/src/routes/_layout/runs_.$runId.tsx
- frontend/src/routes/_layout/teams_.$teamId.tsx
- frontend/src/api/workflows.ts
- frontend/src/client/sdk.gen.ts
- frontend/src/client/types.gen.ts
- frontend/openapi.json
- frontend/tests/components/DirectAIButtons.spec.ts
- frontend/tests/components/RunDetailPage.spec.ts
- backend/tests/integration/test_m005_s02_dashboard_ai_buttons_e2e.py
- backend/tests/integration/conftest.py
- scripts/redaction-sweep.sh
