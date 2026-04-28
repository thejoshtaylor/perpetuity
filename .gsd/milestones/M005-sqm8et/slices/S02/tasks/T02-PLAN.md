---
estimated_steps: 4
estimated_files: 9
skills_used: []
---

# T02: Pin claude/codex CLIs in workspace image + add orchestrator one-shot exec endpoint + auto-seed system workflows

Three deliverables that share a single context: the workspace-image change, the orchestrator HTTP endpoint that runs CLIs inside it, and the data-migration that gives every team `_direct_claude` and `_direct_codex` workflows ready to fire (D028).

(1) `orchestrator/workspace-image/Dockerfile`: install pinned `claude` and `codex` CLIs. The Anthropic CLI is npm-installable as `@anthropic-ai/claude-code`; pin a specific version. The OpenAI Codex CLI is `@openai/codex`; pin a specific version. Add a smoke step that runs `script -q /dev/null sh -c 'claude --version'` and `script -q /dev/null sh -c 'codex --version'` so the build fails if the TTY-wrapped invocation regresses.

(2) `orchestrator/orchestrator/routes_exec.py`: new router. `POST /v1/sessions/{session_id}/exec` accepts `{user_id, team_id, cmd: list[str], env: dict[str,str], timeout_seconds: int (cap at 600), cwd: str | None}`. Provisions/finds the workspace container via existing `provision_container` (idempotent). Wraps the cmd as `['script', '-q', '/dev/null', 'sh', '-c', '<shell-quoted cmd with $VARS>']` and passes secrets via the env dict (MEM274 pattern — never inline plaintext into cmd). Uses `_exec_collect` from `orchestrator/orchestrator/sessions.py` to capture stdout + exit_code (with `tty=True` so stdout/stderr merge — that's what `script -q /dev/null` discipline produces anyway; M005 takes the merged stream). Returns `{stdout: str, exit_code: int, duration_ms: int}`. On `DockerUnavailable` returns 503 (existing handler). Logs `oneshot_exec_started session_id action=` and `oneshot_exec_completed exit duration_ms`; never logs cmd, env values, or stdout.

(3) `backend/app/alembic/versions/s12_seed_direct_workflows.py`: data-only migration that backfills `_direct_claude` and `_direct_codex` workflows (with `system_owned=TRUE`, `scope='user'`) for every existing team. Each gets one `WorkflowStep` (step_index=0): action=`claude` for `_direct_claude` with config `{prompt_template: '{prompt}'}`; action=`codex` for `_direct_codex` with config `{prompt_template: '{prompt}'}`. ON CONFLICT DO NOTHING on `(team_id, name)` so re-running is safe. Add helper `seed_system_workflows(session, team_id)` in `backend/app/api/workflows_seed.py` and wire it into the existing team-create code path (`backend/app/api/routes/teams.py`).

## Inputs

- ``orchestrator/workspace-image/Dockerfile``
- ``orchestrator/orchestrator/sessions.py``
- ``orchestrator/orchestrator/main.py``
- ``backend/app/api/routes/teams.py``
- ``backend/app/models.py``
- ``backend/app/alembic/versions/s11_workflow_runs.py``

## Expected Output

- ``orchestrator/workspace-image/Dockerfile``
- ``orchestrator/orchestrator/routes_exec.py``
- ``orchestrator/orchestrator/main.py``
- ``orchestrator/tests/integration/test_routes_exec.py``
- ``backend/app/alembic/versions/s12_seed_direct_workflows.py``
- ``backend/app/api/workflows_seed.py``
- ``backend/app/api/routes/teams.py``
- ``backend/tests/api/test_workflows_seed.py``
- ``backend/tests/migrations/test_s12_seed_direct_workflows_migration.py``

## Verification

docker compose build --pull orchestrator && cd orchestrator && uv run pytest tests/integration/test_routes_exec.py -v && cd ../backend && POSTGRES_DB=perpetuity_app uv run pytest tests/api/test_workflows_seed.py tests/migrations/test_s12_seed_direct_workflows_migration.py -v

## Observability Impact

New orchestrator INFO logs: `oneshot_exec_started session_id=<uuid> user_id=<uuid> team_id=<uuid> action=<claude|codex|shell>` and `oneshot_exec_completed session_id=<uuid> exit=<n> duration_ms=<n>`. Future agents can grep `docker compose logs orchestrator` for these to localize a workflow step run. Logs MUST NOT carry the cmd list, the env values, or the stdout — those flow through to step_runs in T03 but never to the orchestrator log stream.
