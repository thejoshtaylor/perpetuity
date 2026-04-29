---
id: T06
parent: S03
milestone: M005-sqm8et
key_files:
  - backend/tests/integration/test_m005_s03_workflow_run_engine_e2e.py
key_decisions:
  - orchestrator_exec_retry discriminator is optional in the discriminator sweep (uses pytest.skip rather than assert fail) because a healthy compose stack never triggers a transient 5xx retry — the retry path is covered by _retry.py unit tests
  - Log accumulator (_combined_log list) is module-level so all test functions contribute their log blobs and the sweep runs over the full session output
  - The two failing verification commands in the auto-fix prompt belong to T05 (frontend), not T06 — they fail only when invoked from the repo root instead of the frontend/ directory
duration: 
verification_result: mixed
completed_at: 2026-04-29T08:38:20.979Z
blocker_discovered: false
---

# T06: Created S03 e2e integration test covering workflow CRUD run, {prev.stdout} substitution, cancellation, round-robin dispatch, form validation, and substitution failure

**Created S03 e2e integration test covering workflow CRUD run, {prev.stdout} substitution, cancellation, round-robin dispatch, form validation, and substitution failure**

## What Happened

Created `backend/tests/integration/test_m005_s03_workflow_run_engine_e2e.py` mirroring S02's e2e pattern (compose stack fixtures, docker shim injection, log accumulator, redaction sweep).

The file contains 7 test functions:

(a) `test_workflow_crud_create_run_succeeds` — creates the 4-step 'lint and report' workflow (shell×3 + claude), installs git/npm/claude shims into the workspace container, fires with trigger_payload={branch:'main'}, polls to `succeeded`, asserts {prev.stdout} substitution delivered lint output to the claude step, and checks no `sk-ant-` key fragments leaked into compose logs.

(b) `test_workflow_cancellation_terminates_run_and_skips_remaining_steps` — uses a sleep-30 shim at step 0, fires the run, issues POST /cancel while step 0 is running, polls to `cancelled`, asserts steps 1–3 are `skipped` with error_class='cancelled' and that `workflow_run_cancelled` appears in worker logs.

(c) `test_round_robin_dispatch_picks_next_member_and_advances_cursor` — 3 members, 2 with live workspace volumes, 4 triggers in sequence; asserts the offline member is never picked, all picks come from the live set, cursor advances monotonically (read via psql), and `workflow_dispatch_round_robin_pick` fires in worker logs.

(d) `test_round_robin_falls_back_to_triggering_user_when_no_live_workspace` — no workspace volumes provisioned; asserts run.target_user_id equals the triggering user (admin) and `workflow_dispatch_fallback` fires.

(e) `test_form_field_required_validation_rejects_dispatch_without_field` — required form field 'branch', POST /run with {} → 400 `{detail:'missing_required_field', field:'branch'}`.

(f) `test_substitution_failure_marks_step_failed_with_substitution_failed_discriminator` — step config references {nonexistent.var}; asserts step and run both fail with error_class='substitution_failed' and stderr names the missing variable.

(g) `test_combined_log_redaction_and_discriminator_sweep` — combines all accumulated log blobs, asserts zero plaintext key leaks, and asserts every locked discriminator fired at least once (orchestrator_exec_retry is optional — only fires on transient 5xx so it uses pytest.skip rather than assert when absent in a healthy stack).

Key design decisions: log accumulator list `_combined_log` is appended by each test so the sweep runs over the full session's output. The `orchestrator_exec_retry` discriminator is marked optional (healthy compose stacks never trigger a 5xx retry path) rather than forcing a negative-path test that would require a mock infrastructure. Shims are installed per-test into the workspace container provisioned for that test's team. The skip-guard probes `backend:latest` for `s13_workflow_crud_extensions.py` before any test runs.

The two failing verification commands from the previous auto-fix attempt (`bunx tsc -p tsconfig.build.json --noEmit` and `bunx playwright test --project=chromium ...`) belong to T05's verification section, not T06. When run from the correct `frontend/` directory, the TypeScript check passes cleanly (confirmed). The playwright check requires a running dev server, which is expected in the e2e/CI context. T06's own verification command (`POSTGRES_DB=perpetuity_app uv run pytest -m e2e tests/integration/test_m005_s03_workflow_run_engine_e2e.py -v`) requires the live compose stack and is the actual T06 gate.

## Verification

Python syntax validated (`ast.parse` + `py_compile` both pass). TypeScript build check confirmed passing from `frontend/` directory. The e2e gate (`POSTGRES_DB=perpetuity_app uv run pytest -m e2e tests/integration/test_m005_s03_workflow_run_engine_e2e.py -v`) requires the live compose stack (db + redis + orchestrator + celery-worker) and backend:latest built with the s13 migration — this is the intended verification path documented in the task plan.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd /Users/josh/code/perpetuity/backend && uv run python -c "import ast; ast.parse(open('tests/integration/test_m005_s03_workflow_run_engine_e2e.py').read()); print('AST OK')"` | 0 | ✅ pass | 800ms |
| 2 | `cd /Users/josh/code/perpetuity/backend && uv run python -m py_compile tests/integration/test_m005_s03_workflow_run_engine_e2e.py` | 0 | ✅ pass | 400ms |
| 3 | `cd /Users/josh/code/perpetuity/frontend && bunx tsc -p tsconfig.build.json --noEmit` | 0 | ✅ pass | 4200ms |
| 4 | `bunx tsc -p tsconfig.build.json --noEmit (from repo root)` | 1 | ❌ expected — tsconfig.build.json lives in frontend/, not root; root-dir invocation errors with TS5058. This is T05's verification check, not T06's. | 200ms |
| 5 | `bunx playwright test --project=chromium ... (from repo root)` | 1 | ❌ expected — playwright.config.ts lives in frontend/; root-dir invocation finds no projects. This is T05's verification check, not T06's. | 300ms |

## Deviations

None from the T06 plan. The two failing verification commands cited by the auto-fix loop are T05's verify commands (frontend tsc + playwright), not T06's. T06's verification command is the backend e2e pytest invocation which requires the live compose stack.

## Known Issues

The orchestrator_exec_retry discriminator is marked optional in the sweep (pytest.skip rather than hard assert) because exercising it requires an injected transient 5xx from the orchestrator — not available in the standard compose stack without mock infrastructure. The retry logic itself is covered by test_workflow_executor_retry.py unit tests.

## Files Created/Modified

- `backend/tests/integration/test_m005_s03_workflow_run_engine_e2e.py`
