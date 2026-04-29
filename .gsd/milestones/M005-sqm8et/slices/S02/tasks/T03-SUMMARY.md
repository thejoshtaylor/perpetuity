---
id: T03
parent: S02
milestone: M005-sqm8et
key_files:
  - backend/app/core/celery_app.py
  - backend/app/workflows/__init__.py
  - backend/app/workflows/executors/__init__.py
  - backend/app/workflows/executors/ai.py
  - backend/app/workflows/tasks.py
  - backend/tests/api/test_workflow_executor_ai.py
  - backend/tests/api/test_workflow_runner.py
  - backend/pyproject.toml
key_decisions:
  - Deterministic session_id = uuid5(_NAMESPACE, f'{user}:{team}:{run}') so Celery double-deliveries hit the same workspace container
  - API key + rendered prompt go in `env` dict (env={'ANTHROPIC_API_KEY': ..., 'PROMPT': ...}); cmd argv only carries `[claude, -p, $PROMPT, --dangerously-skip-permissions]` — secrets never reach cmd (MEM274)
  - Prompt template uses str.replace('{prompt}', ...), NOT str.format — defends against user prompts containing `{` characters
  - shell/git actions in S02 are marked failed with error_class='unsupported_action' rather than raising — clean S03 handoff that doesn't crash the worker
  - Worker_crash path opens a FRESH Session for the breadcrumb write because the original session may be in an aborted-transaction state after the unhandled exception
  - celery==5.6.3 added to backend/pyproject.toml; installs into shared root .venv via uv workspace (MEM431)
duration: 
verification_result: passed
completed_at: 2026-04-29T02:54:03.197Z
blocker_discovered: false
---

# T03: Added Celery app + workflow runner Celery task + AI step executor with full error_class taxonomy and 20 unit tests proving the dispatch/idempotency/failure-propagation contract

**Added Celery app + workflow runner Celery task + AI step executor with full error_class taxonomy and 20 unit tests proving the dispatch/idempotency/failure-propagation contract**

## What Happened

Stood up the worker spine M005/S02 needs to drive a real `_direct_claude` / `_direct_codex` run end-to-end.

`backend/app/core/celery_app.py` is a single `Celery("perpetuity")` instance bound to the existing Redis broker (REDIS_HOST/REDIS_PORT/REDIS_PASSWORD env, same shape as `app.core.rate_limit`). No result backend — Postgres `workflow_runs.status` is the source of truth per MEM009. `task_acks_late=True` and `task_reject_on_worker_lost=True` are configured now so S05's orphan-recovery beat doesn't have to retroactively change worker config. `worker_prefetch_multiplier=1` because per-step cost is dominated by the orchestrator HTTP call, not Redis round-trips.

`backend/app/workflows/executors/ai.py::run_ai_step` owns the per-step lifecycle for `claude` and `codex` actions. It (a) reads the team API key via `get_team_secret` (S01 helper), (b) renders `snapshot.config.prompt_template` with `{prompt}` replaced from `WorkflowRun.trigger_payload['prompt']`, (c) POSTs the orchestrator's one-shot exec endpoint with the API key in `env={ANTHROPIC_API_KEY|OPENAI_API_KEY: ...}` and the prompt body in `env={PROMPT: ...}` — the cmd argv carries only `[claude|codex, -p, $PROMPT, --dangerously-skip-permissions]` so secrets never reach the cmd list (MEM274), (d) maps the failure space to the slice's error_class taxonomy (`missing_team_secret`, `team_secret_decrypt_failed`, `orchestrator_exec_failed`, `cli_nonzero`).

`derive_session_id(target_user_id, team_id, run_id)` returns a deterministic UUID5 (namespace pinned in module) so re-runs and Celery double-deliveries hit the same workspace container — captured as MEM429.

`backend/app/workflows/tasks.py::_drive_run` is the runner spine; the Celery task wrapper `run_workflow(run_id)` opens its own Session and delegates. The runner: idempotency-guards on `status != 'pending'`, transitions pending→running with started_at + last_heartbeat_at, iterates `WorkflowStep` rows in step_index order, dispatches `claude`/`codex` to `run_ai_step` and marks `shell`/`git` failed with `error_class='unsupported_action'` (clean handoff to S03), propagates the failed step's error_class to the parent run, marks succeeded only if every step succeeded. Wrapper catches unhandled exceptions, opens a FRESH session for a best-effort `error_class='worker_crash'` stamp, then re-raises so Celery's `task_reject_on_worker_lost` semantics kick in.

20-test sweep: 10 in `test_workflow_executor_ai.py` (deterministic session_id, claude+codex happy paths, missing/decrypt secret failures, httpx errors, 5xx status, exit-code nonzero, prompt template substitution) + 10 in `test_workflow_runner.py` (pending→succeeded, idempotency guard, error_class propagation, missing-secret propagation, empty workflow succeeds, unknown-action fail, unknown run_id quiet return, snapshot freezing per R018, Celery task registration, bad-uuid handling).

Two minor adaptations from the plan: (a) `get_datetime_utc` lives in `app.models`, not `app.utils`, so the executor imports it from there; (b) `prompt_template` substitution uses `str.replace('{prompt}', ...)` instead of `str.format(prompt=...)` so a user prompt containing `{` characters can't trip a KeyError or leak format-spec features. Plan wording said "substituting `{prompt}`" so this matches intent.

`celery==5.6.3` added to `backend/pyproject.toml` (with kombu/billiard/amqp pulled in as transitive deps). Since the project is a uv workspace (`[tool.uv.workspace] members=["backend"]`) the install lands in the shared root `.venv` — captured as MEM431 since `backend/.venv` is stale/ignored.

## Verification

Ran the slice-plan verification command from `backend/`: `POSTGRES_DB=perpetuity_app uv run pytest tests/api/test_workflow_executor_ai.py tests/api/test_workflow_runner.py -v` — 20 passed, 0 failed, 0.73s. Also re-ran `tests/api/ tests/migrations/` (excluding integration) for regression sanity — all prior unit/migration tests still pass; only unrelated failure is `test_sessions.py::test_a_create_session_for_personal_team_returns_200` which fails with orchestrator_status_503 from a stale ephemeral docker setup (pre-existing, unrelated to T03 — log shows `POST http://localhost:55984/v1/sessions HTTP/1.1 503` from an integration-test ephemeral orchestrator that didn't have docker available).

Slice-level verification (per S02-PLAN.md) — partial pass since later T04 ships the API layer / dashboard glue:
- ✅ Structured INFO logs `workflow_run_started`, `workflow_run_succeeded|failed`, `step_run_started`, `step_run_succeeded`, `step_run_failed` all emitted with the contract-specified shape (run_id, step_index, action, exit, error_class, duration_ms). Verified by caplog assertions in tests.
- ✅ Logs NEVER carry the prompt body, API key, or stdout — verified by `_VALID_CLAUDE_KEY not in log_text` + `"Hello from claude" not in log_text` in `test_run_ai_step_happy_path_claude`.
- ✅ `step_runs.error_class` discriminator covers `missing_team_secret`, `team_secret_decrypt_failed`, `orchestrator_exec_failed`, `cli_nonzero`, `unsupported_action`, `worker_crash` (last is reserved for the worker_crash code path).
- ⏳ `GET /api/v1/workflow_runs/{id}` polling — T04.
- ⏳ Dashboard run page polling — T05.
- ⏳ Slice e2e against live compose — final task.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `POSTGRES_DB=perpetuity_app uv run pytest tests/api/test_workflow_executor_ai.py tests/api/test_workflow_runner.py -v` | 0 | ✅ pass | 1415ms |

## Deviations

"`get_datetime_utc` imported from `app.models` (not `app.utils` as the plan implied) — that's where the helper actually lives. `prompt_template` substitution uses `str.replace` instead of `str.format` to defend against user prompts containing `{` characters."

## Known Issues

"None blocking. Pre-existing unrelated failure: `tests/api/routes/test_sessions.py::test_a_create_session_for_personal_team_returns_200` returns 503 from an ephemeral orchestrator that can't reach docker — not caused by T03 changes (no overlap with the workflows package or celery_app)."

## Files Created/Modified

- `backend/app/core/celery_app.py`
- `backend/app/workflows/__init__.py`
- `backend/app/workflows/executors/__init__.py`
- `backend/app/workflows/executors/ai.py`
- `backend/app/workflows/tasks.py`
- `backend/tests/api/test_workflow_executor_ai.py`
- `backend/tests/api/test_workflow_runner.py`
- `backend/pyproject.toml`
