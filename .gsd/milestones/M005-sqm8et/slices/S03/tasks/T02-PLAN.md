---
estimated_steps: 1
estimated_files: 10
skills_used: []
---

# T02: Add shell + git step executors, cross-step `{prev.stdout}` / `{form.<field>}` substitution, and 3x exponential orchestrator retry shared across all executors

Three deliverables in one transaction since they share the same orchestrator-HTTP code path. (a) `app/workflows/substitution.py::render_step_inputs(snapshot, trigger_payload, prior_step_runs) -> dict` is the substitution engine: walks `snapshot.config` (deep-copy), looks for `{prev.stdout}` (resolves to the immediately-prior step's stdout), `{prev[<n>].stdout}` (Nth previous step, 0=immediate), `{form.<field>}` (resolves to `trigger_payload[<field>]` — required form fields are validated at API dispatch boundary in T04), `{trigger.<key>}` (catch-all). Unknown variable → raises `SubstitutionError(missing=<var>)` which the runner maps to step `error_class='substitution_failed'`. Substitution uses `str.replace` chain (NOT `str.format`) so user prompts containing `{` survive (extends MEM274 prompt-discipline). (b) `app/workflows/executors/shell.py::run_shell_step` and `app/workflows/executors/git.py::run_git_step` — both POST orchestrator `/v1/sessions/{sid}/exec` with the rendered cmd. Shell takes `config = {cmd: [str], cwd?: str, env?: dict}` and passes through; git takes `config = {subcommand: 'checkout'|'pull'|'fetch'|'push', args: [str]}` and renders to `[git, <subcommand>, *args]`. Both honor `target_container` — for S03, only `user_workspace` is implemented; `team_mirror` raises `unsupported_action_for_target` (S04 owns it). Both invoke `_orchestrator_exec_with_retry` (new shared helper in `app/workflows/executors/_retry.py`) which retries on transport errors + 5xx with exponential backoff (0.5s, 1s, 2s) up to 3 attempts; non-retryable codes (4xx, 504 timeout) bypass retry. After 3 failures: stamp `error_class='orchestrator_exec_failed_after_retries'`. Refactor `run_ai_step` to use `_orchestrator_exec_with_retry` for parity. (c) Wire shell + git into `app/workflows/tasks._execute_one_step` action dispatch table — the existing `unsupported_action` path for shell/git becomes the default dispatch. The runner ALSO passes the rendered config back to `_snapshot_step` so step_runs.snapshot.config carries the FULLY RESOLVED inputs (R018: history must show what the executor actually saw). Unit tests cover: substitution happy paths (prev.stdout, form.field, trigger.key, multiple in same string), missing-variable raises SubstitutionError, deep-copy preserves original snapshot, shell happy path, git happy path, retry-on-transport-error 3x then succeeds, retry exhaustion stamps after-retries discriminator, target_container=team_mirror short-circuits with unsupported_action_for_target.

## Inputs

- ``backend/app/workflows/executors/ai.py``
- ``backend/app/workflows/tasks.py``
- ``backend/app/models.py``
- ``orchestrator/orchestrator/routes_exec.py``

## Expected Output

- ``backend/app/workflows/substitution.py``
- ``backend/app/workflows/executors/_retry.py``
- ``backend/app/workflows/executors/shell.py``
- ``backend/app/workflows/executors/git.py``
- ``backend/app/workflows/executors/ai.py``
- ``backend/app/workflows/tasks.py``
- ``backend/tests/api/test_workflow_substitution.py``
- ``backend/tests/api/test_workflow_executor_shell.py``
- ``backend/tests/api/test_workflow_executor_git.py``
- ``backend/tests/api/test_workflow_executor_retry.py``

## Verification

cd /Users/josh/code/perpetuity/backend && uv run pytest tests/api/test_workflow_substitution.py tests/api/test_workflow_executor_shell.py tests/api/test_workflow_executor_git.py tests/api/test_workflow_executor_retry.py tests/api/test_workflow_executor_ai.py tests/api/test_workflow_runner.py -v

## Observability Impact

Adds INFO `orchestrator_exec_retry run_id=<uuid> step_index=<n> attempt=<n/3> error_class=<...>` per retry attempt; adds `step_run_failed` with new error_class values `orchestrator_exec_failed_after_retries`, `substitution_failed`, `unsupported_action_for_target`. Future inspection: `psql perpetuity_app -c "SELECT step_index, error_class FROM step_runs WHERE error_class LIKE 'orchestrator%' OR error_class = 'substitution_failed' ORDER BY created_at DESC LIMIT 20"`. Substitution engine NEVER logs the rendered config (could carry form values like a branch name plus prior step stdout) — only logs the variable NAME on failure.
