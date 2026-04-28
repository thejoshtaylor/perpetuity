---
estimated_steps: 21
estimated_files: 3
skills_used: []
---

# T06: Slice e2e — dashboard click → run page reflects success against live compose stack

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

## Inputs

- ``backend/tests/integration/conftest.py``
- ``backend/tests/integration/test_m005_s01_team_secrets_e2e.py``
- ``scripts/redaction-sweep.sh``
- ``backend/app/alembic/versions/s12_seed_direct_workflows.py``

## Expected Output

- ``backend/tests/integration/test_m005_s02_dashboard_ai_buttons_e2e.py``
- ``backend/tests/integration/conftest.py``
- ``scripts/redaction-sweep.sh``

## Verification

docker compose build backend orchestrator && docker compose up -d db redis orchestrator && cd backend && POSTGRES_DB=perpetuity_app uv run pytest -m e2e tests/integration/test_m005_s02_dashboard_ai_buttons_e2e.py -v && bash /Users/josh/code/perpetuity/scripts/redaction-sweep.sh

## Observability Impact

This task does NOT add new signals — it verifies the full taxonomy from T02/T03/T04 fires at expected times. Future agents debugging a workflow regression run the same e2e and compare its log assertions to find which discriminator stopped firing.
