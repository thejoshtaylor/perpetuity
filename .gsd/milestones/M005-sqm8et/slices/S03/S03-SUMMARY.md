---
id: S03
parent: M005-sqm8et
milestone: M005-sqm8et
provides:
  - ["workflow-run-engine", "substitution-engine", "round-robin-dispatch", "workflow-crud-api", "cancellation-api", "frontend-workflow-ui"]
requires:
  []
affects:
  []
key_files:
  - ["backend/app/alembic/versions/s13_workflow_crud_extensions.py", "backend/app/workflows/substitution.py", "backend/app/workflows/executors/_retry.py", "backend/app/workflows/executors/shell.py", "backend/app/workflows/executors/git.py", "backend/app/services/workflow_dispatch.py", "backend/app/api/routes/workflows_crud.py", "backend/app/api/routes/workflows.py", "backend/app/workflows/tasks.py", "frontend/src/routes/_layout/workflows.tsx", "frontend/src/routes/_layout/workflows_.$workflowId.tsx", "frontend/src/components/dashboard/CustomWorkflowButtons.tsx", "frontend/src/routes/_layout/runs_.$runId.tsx", "backend/tests/integration/test_m005_s03_workflow_run_engine_e2e.py"]
key_decisions:
  - ["JSONB server_default in Alembic requires sa.text() wrapper — bare string causes double-escaping (MEM461)", "Substitution engine uses str.replace chains not str.format to preserve literal { chars in user prompts (MEM462)", "Snapshots store FULLY RESOLVED config post-substitution to satisfy R018 forever-debuggable history (MEM463)", "Cancel API writes terminal 'cancelled' directly — DB CHECK has no 'cancelling' state; worker watchpoint checks between steps (MEM464)", "round_robin_cursor is BIGINT not INT to survive long-lived teams with many dispatches", "WorkflowStepTargetContainer enum includes team_mirror in s13 so S04 needs no ALTER TABLE", "Cancellation FK is SET NULL not CASCADE to preserve audit trail after user deletion", "Round-robin cursor increment is atomic UPDATE…RETURNING to avoid read-modify-write race (MEM466)", "Frontend cancel mutation invalidates on error only — onSettled overwrites optimistic state before server confirms terminal (MEM467)"]
patterns_established:
  - ["Executor parity: shell, git, and AI executors all share _orchestrator_exec_with_retry helper — 4xx/504 bypass retry, 5xx retries 3x exponential", "Log discriminator contract: every significant state transition emits a named structured log line assertable in e2e sweeps", "Service layer raises plain Exceptions (not HTTPException) — API layer owns HTTP status mapping", "E2e test pattern from S02 (compose fixtures + shim injection + log accumulator + discriminator sweep) extended to S03"]
observability_surfaces:
  - ["workflow_dispatch_round_robin_pick (INFO) — fires on every round-robin dispatch with workflow_id, target_user_id, cursor_before, cursor_after", "workflow_dispatch_fallback (INFO) — fires when all-offline fallback triggers with workflow_id, reason, fallback_target", "workflow_dispatch_target_user_no_membership (ERROR) — fires before 400 on team_specific scope miss", "workflow_run_cancelled (INFO) — fires in worker when cancellation watchpoint detects cancelled status", "step_run_skipped (INFO) — fires per skipped step during cancellation", "orchestrator_exec_retry (INFO) — fires per retry attempt in _orchestrator_exec_with_retry (optional in healthy stacks)"]
drill_down_paths:
  []
duration: ""
verification_result: passed
completed_at: 2026-04-29T08:43:03.756Z
blocker_discovered: false
---

# S03: Workflow run engine: schema extensions, substitution, executors, dispatch, CRUD API, cancel, and frontend

**Delivered the full workflow run engine spine: s13 schema migration, {prev.stdout}/{form.field} substitution, shell/git executors, round-robin dispatch, CRUD + cancel APIs, and frontend workflow/run UI with 7 e2e tests registered.**

## What Happened

S03 built the spine of M005's workflow run engine across six tasks.

**T01 — Schema (s13 migration + model/DTO extensions):** Added six new columns across workflows, workflow_steps, and workflow_runs: `form_schema JSONB`, `target_user_id UUID FK SET NULL`, `round_robin_cursor BIGINT`, `target_container VARCHAR CHECK`, `cancelled_by_user_id UUID FK SET NULL`, and `cancelled_at TIMESTAMPTZ`. New enums `WorkflowStepTargetContainer` (user_workspace/team_mirror) and `WorkflowFormFieldKind` land here so S04 needs no ALTER. The `round_robin_cursor` is BigInteger to survive long-lived teams. Key fix: Alembic JSONB `server_default` requires `sa.text("'{}'::jsonb")` — bare strings get double-escaped (MEM461). 9 migration tests passed.

**T02 — Executors + substitution engine:** `app/workflows/substitution.py` resolves `{prev.stdout}`, `{prev[N].stdout}`, `{form.<field>}`, `{trigger.<key>}`, and `{prompt}` (shorthand for `trigger_payload['prompt']`) via str.replace chains (not str.format) so prompts with literal `{` characters survive unchanged (MEM462). Unknown variable → `SubstitutionError` → `error_class='substitution_failed'`. `_retry.py` provides `_orchestrator_exec_with_retry` with 3x exponential backoff (0.5/1/2s); 4xx and 504 bypass retry. `shell.py` and `git.py` executors wrap the retry helper. Crucially, the runner stores the FULLY RESOLVED config as the snapshot (not the template), satisfying R018 forever-debuggable history (MEM463). 60 unit tests passed.

**T03 — Dispatch service:** `app/services/workflow_dispatch.py` resolves `scope=user` (passthrough), `scope=team` (team_specific, membership-gated, 400 on missing/non-member target), and `scope=round_robin` (cursor-based pick with live-workspace probe, 7-day window, fallback to triggering user with `workflow_dispatch_fallback` log). Cursor increment is atomic UPDATE…RETURNING to avoid race conditions (MEM466). Three structured log discriminators: `workflow_dispatch_round_robin_pick`, `workflow_dispatch_fallback`, `workflow_dispatch_target_user_no_membership`. 11 service tests passed.

**T04 — CRUD API + cancel + runner watchpoint:** `workflows_crud.py` exposes POST/GET/PUT/DELETE under `/api/v1/teams/{team_id}/workflows` with admin gates, form-schema validation (custom 400 shape, not Pydantic 422), reserved `_direct_*` namespace rejection, and atomic step replacement. `workflows.py` dispatch path calls `resolve_target_user` at trigger time and validates required form fields. Cancel route (`POST /workflow_runs/{id}/cancel`) writes terminal `cancelled` directly — the DB CHECK constraint has no `cancelling` state (MEM464); worker watchpoint detects `cancelled` between steps and marks remaining steps `skipped` with `error_class='cancelled'`. 47 API tests + 21 regression tests passed.

**T05 — Frontend:** Workflows list (`/workflows`), workflow editor (`/workflows/new` and `/:id`), `CustomWorkflowButtons` component on team dashboard, `WorkflowFormDialog` for form-field dispatch, and cancel button on run detail page. Cancel mutation uses invalidate-on-error only (not onSettled) — the optimistic `cancelled` state must survive until navigation; polling stops automatically because `isRunInFlight('cancelled')=false` (MEM467). 20/20 Playwright tests passed.

**T06 — E2e test suite:** `backend/tests/integration/test_m005_s03_workflow_run_engine_e2e.py` contains 7 test functions covering the full S03 surface against a live compose stack: workflow CRUD + 4-step run with `{prev.stdout}` substitution, cancellation between steps, round-robin with live-workspace probe, round-robin fallback, required form field validation, `substitution_failed` discriminator, and combined-log redaction + discriminator sweep (adds 5 new discriminators to S02's 9). Tests collect cleanly (7/7) and are skipped without the live compose stack as expected.

## Verification

All task-level gates passed:
- T01: 9 migration tests passed (`test_s13_workflow_crud_extensions_migration.py`)
- T02: 60 unit tests passed (substitution, shell, git, retry, ai, runner)
- T03: 11 service tests passed (`test_workflow_dispatch_service.py`)
- T04: 47 API tests + 21 regressions passed (crud, cancel, runner cancellation, run routes)
- T05: TypeScript build 0 errors; 20/20 Playwright tests passed
- T06: 7 e2e tests collected cleanly (`cd backend && POSTGRES_DB=perpetuity_app uv run pytest -m e2e tests/integration/test_m005_s03_workflow_run_engine_e2e.py -v` → 7 skipped, exit 0); skipped is correct without live compose stack. Root-cause of verification failure: auto-fix ran from repo root where pyproject.toml does not live; correct invocation requires `cd backend/` first.

## Requirements Advanced

None.

## Requirements Validated

None.

## New Requirements Surfaced

None.

## Requirements Invalidated or Re-scoped

None.

## Operational Readiness

None.

## Deviations

None.

## Known Limitations

["orchestrator_exec_retry discriminator is optional in e2e sweep (pytest.skip) because exercising it requires injected transient 5xx not available in standard compose stack; retry logic covered by unit tests", "team_mirror target_container accepted by DB but executor returns unsupported_action_for_target — full support lands in S04", "Cancellation requires the run to be in pending/running state; there is no way to cancel a run after it has succeeded"]

## Follow-ups

None.

## Files Created/Modified

None.
