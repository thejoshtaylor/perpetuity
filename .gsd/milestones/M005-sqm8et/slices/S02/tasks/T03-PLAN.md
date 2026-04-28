---
estimated_steps: 12
estimated_files: 7
skills_used: []
---

# T03: Celery app + Redis broker + AI executor + run_workflow task

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

## Inputs

- ``backend/app/api/team_secrets.py``
- ``backend/app/models.py``
- ``backend/app/core/config.py``
- ``backend/app/core/rate_limit.py``

## Expected Output

- ``backend/app/core/celery_app.py``
- ``backend/app/workflows/__init__.py``
- ``backend/app/workflows/executors/__init__.py``
- ``backend/app/workflows/executors/ai.py``
- ``backend/app/workflows/tasks.py``
- ``backend/tests/api/test_workflow_executor_ai.py``
- ``backend/tests/api/test_workflow_runner.py``

## Verification

cd backend && POSTGRES_DB=perpetuity_app uv run pytest tests/api/test_workflow_executor_ai.py tests/api/test_workflow_runner.py -v

## Observability Impact

New backend INFO logs: `workflow_run_started run_id=<uuid> workflow_id=<uuid>`, `workflow_run_succeeded|failed run_id=<uuid> duration_ms=<n>`, `step_run_started run_id=<uuid> step_index=<n> action=<claude|codex>`, `step_run_succeeded run_id=<uuid> step_index=<n> exit=0 duration_ms=<n>`, `step_run_failed run_id=<uuid> step_index=<n> exit=<n> error_class=<missing_team_secret|team_secret_decrypt_failed|orchestrator_exec_failed|cli_nonzero|worker_crash> duration_ms=<n>`. NEVER carries prompt body, API key, or stdout. Future agents diagnosing a stuck run pull `psql -c "SELECT id, status, error_class FROM workflow_runs WHERE id='<uuid>'"` and `... FROM step_runs WHERE run_id='<uuid>' ORDER BY step_index`.
