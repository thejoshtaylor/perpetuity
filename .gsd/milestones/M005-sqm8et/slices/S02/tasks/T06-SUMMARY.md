---
id: T06
parent: S02
milestone: M005-sqm8et
key_files:
  - backend/tests/integration/test_m005_s02_dashboard_ai_buttons_e2e.py
  - backend/tests/integration/conftest.py
  - backend/app/workflows/tasks.py
key_decisions:
  - Conftest gets two new fixtures: `celery_worker_url` boots a sibling backend:latest running `celery -A app.workflows.tasks worker` (depends on backend_url so prestart has run); `orchestrator_on_e2e_db` swaps the compose orchestrator for a sibling carrying DATABASE_URL pointed at POSTGRES_DB=perpetuity_app when the e2e overrides the DB — no-op when POSTGRES_DB unset/`app`. Mirrors M002/S05 ephemeral-orchestrator pattern (MEM193) — captured as MEM437.
  - Fixed `_execute_one_step` in app/workflows/tasks.py to UPDATE the existing pending step_run row rather than INSERT a new one (the API dispatch route pre-creates pending step_runs at trigger time so the GET endpoint returns full step list immediately — captured as MEM436). Fallback INSERT path preserves backward compat with runs that lack the pre-create.
  - Test shim discipline: shims fail with exit 2 if `$ANTHROPIC_API_KEY` / `$OPENAI_API_KEY` is missing or empty — proves the env-injection code path is wired (negative test coverage), and shims echo the env `$PROMPT` so we can assert the prompt-body reached the in-container exec frame without ever logging it.
  - Observing the `pending → running` transition during the poll loop is best-effort; the shim is fast enough (run_id flips to `succeeded` in ~80 ms) that one poll interval can miss it. The slice's authoritative observability contract is the `workflow_run_started` log discriminator, which is asserted on the combined log stream — that gate stays strict.
duration: 
verification_result: passed
completed_at: 2026-04-29T03:38:47.999Z
blocker_discovered: false
---

# T06: Added the M005/S02 slice e2e (dashboard click → run page reflects success against the live compose stack) and fixed the worker's duplicate step_runs INSERT bug it surfaced

**Added the M005/S02 slice e2e (dashboard click → run page reflects success against the live compose stack) and fixed the worker's duplicate step_runs INSERT bug it surfaced**

## What Happened

Wrote `backend/tests/integration/test_m005_s02_dashboard_ai_buttons_e2e.py`: a single integration test that drives the full chain — admin signs up + creates a team + pastes claude+openai keys (S01 surface), POSTs `/api/v1/sessions` to provision the workspace container, drops deterministic `/usr/local/bin/{claude,codex}` test shims into that container via `docker exec`, looks up `_direct_claude` / `_direct_codex` workflow ids, triggers each via `POST /api/v1/workflows/{id}/run`, polls `GET /api/v1/workflow_runs/{run_id}` to terminal status, and asserts on snapshot/action/exit_code/stdout. The negative path then DELETEs the claude key and triggers another claude run, asserting the run terminates with `error_class='missing_team_secret'`. A combined log redaction sweep over the backend + worker + orchestrator log streams asserts zero `sk-ant-` / `sk-` plaintext leaks plus presence of every locked observability discriminator (`workflow_run_dispatched`, `workflow_run_started`, `workflow_run_succeeded`, `workflow_run_failed`, `step_run_started`, `step_run_succeeded`, `step_run_failed`, `oneshot_exec_started`, `oneshot_exec_completed`).

Two conftest extensions were needed for the test to run: (1) `celery_worker_url` boots a sibling `backend:latest` container running `celery -A app.workflows.tasks worker` on the compose network with the e2e Fernet key pinned, polling for the canonical `e2e-worker@<host> ready.` log line; (2) `orchestrator_on_e2e_db` masks the compose orchestrator (whose DATABASE_URL inherits `POSTGRES_DB=app` from .env) with a sibling `orchestrator:latest` carrying DATABASE_URL pointed at `perpetuity_app`, attached to the network with `--network-alias orchestrator` so the sibling backend + celery worker resolve it transparently. Mirrors the M002/S05 ephemeral-orchestrator pattern (MEM193).

The e2e exposed a real bug in `app/workflows/tasks._execute_one_step`: the API dispatch route already pre-creates one `step_runs` row per workflow step in `pending` status at trigger time (so `GET /workflow_runs/{id}` returns the full step list before worker pickup), but the runner was naively inserting a NEW row in `running` status — `UNIQUE (workflow_run_id, step_index)` violation, run terminating in `worker_crash`. Fixed by looking up the existing pending row and updating it in place; a fallback INSERT covers older runs that lack the pre-create. Backend image was rebuilt off this fix and the e2e then went green (`1 passed in 18.85s`).

Verification checks 11 of the 11 sub-cases the task plan specified plus the locked discriminator set. Skip-guard probes `backend:latest` for the `s12_seed_direct_workflows` revision and skips with the canonical `docker compose build backend orchestrator celery-worker` hint when absent (MEM162/MEM186/MEM247). Test wall-clock budget is 180 s defensively (actual ~19 s on this host).

## Verification

Ran the full e2e against the live compose stack with `POSTGRES_DB=perpetuity_app uv run pytest -m e2e tests/integration/test_m005_s02_dashboard_ai_buttons_e2e.py -v` — `1 passed in 18.85s`. The test internally exercised: claude happy path (run terminates `succeeded`, step_run.exit_code=0, stdout contains `stub-claude-output for prompt: list the files in this repo`); codex happy path (same shape with codex shim and `summarize the README` prompt); missing-key negative path (run terminates `failed` with `error_class='missing_team_secret'`); combined backend+worker+orchestrator log redaction sweep (zero `sk-ant-` / `sk-` matches, zero prompt-body leaks, all 9 locked observability discriminators present). Source-file redaction sweep `bash scripts/redaction-sweep.sh` exits 0 with `PASS` on all 7 checks.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `docker compose build backend orchestrator` | 0 | pass | 75000ms |
| 2 | `POSTGRES_DB=perpetuity_app uv run pytest -m e2e tests/integration/test_m005_s02_dashboard_ai_buttons_e2e.py -v` | 0 | pass | 18850ms |
| 3 | `bash scripts/redaction-sweep.sh` | 0 | pass | 250ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `backend/tests/integration/test_m005_s02_dashboard_ai_buttons_e2e.py`
- `backend/tests/integration/conftest.py`
- `backend/app/workflows/tasks.py`
