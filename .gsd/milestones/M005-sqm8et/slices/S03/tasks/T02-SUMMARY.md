---
id: T02
parent: S03
milestone: M005-sqm8et
key_files:
  - backend/app/workflows/substitution.py
  - backend/app/workflows/executors/_retry.py
  - backend/app/workflows/executors/shell.py
  - backend/app/workflows/executors/git.py
  - backend/app/workflows/executors/ai.py
  - backend/app/workflows/tasks.py
  - backend/tests/api/test_workflow_substitution.py
  - backend/tests/api/test_workflow_executor_shell.py
  - backend/tests/api/test_workflow_executor_git.py
  - backend/tests/api/test_workflow_executor_retry.py
  - backend/tests/api/test_workflow_runner.py
  - backend/tests/api/test_workflow_executor_ai.py
key_decisions:
  - Use str.replace chain (not str.format) for substitution so user prompts with literal { characters survive unchanged
  - Recognize {prompt} as shorthand for trigger_payload['prompt'] to support AI step prompt_template fields without dual-substitution
  - Snapshot stores FULLY RESOLVED config (not template) per R018 — history shows what executor actually saw
  - 4xx and 504 bypass retry in _orchestrator_exec_with_retry — permanent conditions don't benefit from backoff
  - shell/git executors use the same _orchestrator_exec_with_retry helper as AI executor for parity
duration: 
verification_result: passed
completed_at: 2026-04-29T05:44:03.619Z
blocker_discovered: false
---

# T02: Added shell + git step executors, {prev.stdout}/{form.field} substitution engine, and 3x exponential orchestrator retry shared across all executors

**Added shell + git step executors, {prev.stdout}/{form.field} substitution engine, and 3x exponential orchestrator retry shared across all executors**

## What Happened

Three deliverables landed in one transaction:

**Substitution engine (`app/workflows/substitution.py`):**
- `render_step_inputs(snapshot, trigger_payload, prior_step_runs) -> dict` deep-copies snapshot.config and resolves `{prev.stdout}`, `{prev[N].stdout}`, `{form.<field>}`, `{trigger.<key>}`, and `{prompt}` (shorthand for trigger_payload["prompt"] used by AI steps' prompt_template). Uses `str.replace` chains (NOT str.format) so user prompts with unrelated `{` characters survive unchanged. Unknown variable → raises `SubstitutionError(missing=<var>)`.

**Retry helper (`app/workflows/executors/_retry.py`):**
- `_orchestrator_exec_with_retry(client_factory, url, body, headers, *, run_id, step_index)` retries on transport errors and 5xx with exponential backoff (0.5s, 1s, 2s) up to 3 attempts. Non-retryable codes (4xx, 504) bypass retry and raise `OrchestratorExecFailed` immediately. After 3 failures: error_class='orchestrator_exec_failed_after_retries'. Emits `orchestrator_exec_retry` INFO log per retry attempt.

**Shell executor (`app/workflows/executors/shell.py`):**
- `run_shell_step(session, step_run_id)` reads rendered config `{cmd, cwd?, env?}`, posts to orchestrator, marks step succeeded/failed. `team_mirror` target_container → `unsupported_action_for_target`. Missing cmd → `orchestrator_exec_failed`. Uses `_orchestrator_exec_with_retry`.

**Git executor (`app/workflows/executors/git.py`):**
- `run_git_step(session, step_run_id)` renders `[git, subcommand, *args]` from config `{subcommand, args}`. Validates subcommand ∈ {checkout, pull, fetch, push}. Same target_container guard and retry pattern.

**AI executor refactored (`app/workflows/executors/ai.py`):**
- Replaced inline `try/except httpx.HTTPError` + status check with `_orchestrator_exec_with_retry` for parity. Existing error_class taxonomy preserved.

**Runner updated (`app/workflows/tasks.py`):**
- `_execute_one_step` now: (1) calls `render_step_inputs` before storing the snapshot → resolved config frozen in step_runs.snapshot (R018: history shows what executor actually saw); (2) maps `SubstitutionError` → step failed with `error_class='substitution_failed'`; (3) dispatches shell/git via their executors instead of falling through to `unsupported_action`; (4) passes `prior_step_runs` list (accumulated stdout per step) to each call so `{prev.stdout}` chains work.

**Key implementation decisions:**
- `{prompt}` recognized as a shorthand for `{trigger.prompt}` so existing AI step `prompt_template` fields (which use `{prompt}`) resolve through the same substitution pass — the AI executor's own `_render_prompt` then becomes a no-op since the placeholder is already resolved.
- Runner test `test_drive_run_unknown_action_fails_step` updated: shell is now a valid action (DB CHECK constraint prevents truly invalid actions), so the test was rewritten to verify shell dispatch succeeds end-to-end.
- Snapshot test updated: now asserts the RESOLVED config (not the template), which is the correct R018 behavior.

## Verification

Ran `POSTGRES_DB=perpetuity_app uv run pytest tests/api/test_workflow_substitution.py tests/api/test_workflow_executor_shell.py tests/api/test_workflow_executor_git.py tests/api/test_workflow_executor_retry.py tests/api/test_workflow_executor_ai.py tests/api/test_workflow_runner.py -v` — 60 tests passed in 1.31s.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `POSTGRES_DB=perpetuity_app uv run pytest tests/api/test_workflow_substitution.py tests/api/test_workflow_executor_shell.py tests/api/test_workflow_executor_git.py tests/api/test_workflow_executor_retry.py tests/api/test_workflow_executor_ai.py tests/api/test_workflow_runner.py -v` | 0 | ✅ pass | 1310ms |

## Deviations

Runner test `test_drive_run_unknown_action_fails_step` was renamed and rewritten to `test_drive_run_shell_action_dispatched` because: (1) shell is now a valid action with a real executor, and (2) the DB CHECK constraint prevents inserting truly unknown actions. The snapshot freezing test assertion was updated to expect the RESOLVED config rather than the template, which is the correct R018 behavior now that substitution runs before snapshot storage.

## Known Issues

None.

## Files Created/Modified

- `backend/app/workflows/substitution.py`
- `backend/app/workflows/executors/_retry.py`
- `backend/app/workflows/executors/shell.py`
- `backend/app/workflows/executors/git.py`
- `backend/app/workflows/executors/ai.py`
- `backend/app/workflows/tasks.py`
- `backend/tests/api/test_workflow_substitution.py`
- `backend/tests/api/test_workflow_executor_shell.py`
- `backend/tests/api/test_workflow_executor_git.py`
- `backend/tests/api/test_workflow_executor_retry.py`
- `backend/tests/api/test_workflow_runner.py`
- `backend/tests/api/test_workflow_executor_ai.py`
