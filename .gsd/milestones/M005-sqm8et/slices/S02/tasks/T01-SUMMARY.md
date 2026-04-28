---
id: T01
parent: S02
milestone: M005-sqm8et
key_files:
  - backend/app/alembic/versions/s10_workflows.py
  - backend/app/alembic/versions/s11_workflow_runs.py
  - backend/app/models.py
  - backend/tests/migrations/test_s10_workflows_migration.py
  - backend/tests/migrations/test_s11_workflow_runs_migration.py
key_decisions:
  - workflow_steps is a sibling table (not JSONB on workflows) so S03 can ALTER for per-step fields without rewriting JSON
  - step_runs.snapshot is JSONB capturing the full WorkflowStep at dispatch time — frozen forever (R018)
  - step_runs.stdout/stderr are TEXT NOT NULL DEFAULT '' and persisted in full per R018; the rest of the system never logs them
  - error_class kept as free-text VARCHAR(64) rather than CHECK-bound or enum so S03/S04/S05 can layer in 'webhook_validation_failed' / 'dispatch_failed' / etc. without an ALTER
  - user FKs on workflow_runs use SET NULL (not CASCADE) so user delete preserves the audit trail
  - Public DTOs type enum-valued columns as Python Enum classes per MEM352 so openapi-ts emits literal unions to frontend
duration: 
verification_result: passed
completed_at: 2026-04-28T22:51:03.620Z
blocker_discovered: false
---

# T01: Added s10_workflows + s11_workflow_runs migrations, SQLModel rows + DTOs, and 20 round-trip migration tests for the M005/S02 engine schema

**Added s10_workflows + s11_workflow_runs migrations, SQLModel rows + DTOs, and 20 round-trip migration tests for the M005/S02 engine schema**

## What Happened

Landed the slim M005/S02 workflow engine schema across two new alembic revisions and the SQLModel layer. `s10_workflows` creates `workflows` (UUID PK, team FK CASCADE, UNIQUE(team_id,name), `system_owned BOOLEAN` for D028 filtering, `scope` 3-valued CHECK for S03 dispatcher) and `workflow_steps` (sibling table — not JSONB on workflows so S03 can ALTER for per-step columns; UNIQUE(workflow_id,step_index); `action` 4-valued CHECK covering claude/codex/shell/git, with shell/git reserved for S03; JSONB `config` server-default `{}`). `s11_workflow_runs` creates `workflow_runs` (workflow + team FKs CASCADE, triggered_by/target_user FKs SET NULL so user delete preserves audit history, `trigger_type` 5-valued CHECK, `status` 5-valued CHECK, JSONB `trigger_payload`, `last_heartbeat_at` reserved for S05 recovery, `error_class` as free-text VARCHAR so S03/S04/S05 can layer in discriminators without an ALTER) and `step_runs` (workflow_run FK CASCADE, JSONB `snapshot` of the WorkflowStep at dispatch time, persisted `stdout`/`stderr` per R018 forever-debuggable history with TEXT NOT NULL DEFAULT '', `exit_code`/`error_class`/`duration_ms`, UNIQUE(workflow_run_id,step_index), step `status` 5-valued CHECK).

Added eleven new SQLModel rows + DTOs in `app/models.py`: `Workflow`, `WorkflowStep`, `WorkflowRun`, `StepRun` table models plus `WorkflowPublic`, `WorkflowsPublic`, `WorkflowStepPublic`, `WorkflowWithStepsPublic`, `WorkflowRunPublic` (with embedded `step_runs` list), `StepRunPublic`, `WorkflowRunCreate`, `WorkflowRunDispatched`. Per MEM352 convention, public DTOs use Python Enum classes (`WorkflowScope`, `WorkflowAction`, `WorkflowRunTriggerType`, `WorkflowRunStatus`, `StepRunStatus`) on enum-valued columns so OpenAPI emits string-literal unions to the frontend.

Wrote two test modules totalling 20 cases. Both follow the MEM016 autouse-fixture pattern (release the session-scoped `db` Session and dispose the engine pool before alembic DDL, restore head after every test). Tests cover: column shape + types + nullability, FK CASCADE/SET NULL semantics per edge, UNIQUE rejection of duplicate (team_id,name) and (workflow_id,step_index) and (workflow_run_id,step_index), CHECK rejection of bogus scope/action/trigger_type/status values, parent-team cascade through workflows + workflow_steps + workflow_runs + step_runs, user-delete SET NULL on triggered_by/target_user_id, server-default landing for scope/system_owned/config/trigger_payload/run-status/step-status/stdout/stderr, downgrade-then-upgrade schema-byte-identical round-trip, and `models.py` import sanity that asserts each enum literal set matches its CHECK constraint.

One environmental setup nudge: `localhost:55432` test postgres (per .env per MEM408) ships with only the `app` DB, but the verify command pins `POSTGRES_DB=perpetuity_app` — `CREATE DATABASE perpetuity_app` had to run once via `docker exec perpetuity-testdb-55432 psql ...` before the first `alembic upgrade head`. Captured as MEM425 for the next executor.

One small bug caught and fixed during verification: the user-delete FK SET NULL test inserted into `"user"` without setting the `role` column, which is `NOT NULL` with no server default. Added `role` to the raw INSERT (value `'user'` to satisfy the lowercase userrole enum per MEM020) and the test went green. Captured as MEM424.

20/20 tests pass; existing s09 migration test still passes (8/8) so the head chain is intact.

## Verification

Ran the slice-plan verification command exactly as written: `cd backend && POSTGRES_DB=perpetuity_app uv run pytest tests/migrations/test_s10_workflows_migration.py tests/migrations/test_s11_workflow_runs_migration.py -v`. 20 tests pass, 0 fail. Also re-ran s09 migration tests as a regression check — 8/8 pass. `alembic upgrade head` against perpetuity_app applies all migrations through s11_workflow_runs cleanly with the documented INFO logs for both new revisions.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && POSTGRES_DB=perpetuity_app uv run pytest tests/migrations/test_s10_workflows_migration.py tests/migrations/test_s11_workflow_runs_migration.py -v` | 0 | ✅ pass | 1080ms |
| 2 | `cd backend && POSTGRES_DB=perpetuity_app uv run pytest tests/migrations/test_s09_team_secrets_migration.py -v` | 0 | ✅ pass (regression check) | 470ms |
| 3 | `cd backend && POSTGRES_DB=perpetuity_app uv run alembic upgrade head` | 0 | ✅ pass — both s10 and s11 apply cleanly with documented INFO log lines | 2100ms |

## Deviations

No deviations from the inlined task plan. Files created match the listed Expected Output exactly. Schema choices honor the three documented assumptions: (1) `workflow_runs.scope` is omitted, (2) workflow_steps is a sibling table, (3) `step_runs.snapshot` is JSONB-frozen at dispatch.

## Known Issues

None. T02's auto-seed migration (`s12_seed_direct_workflows`) will need to populate `_direct_claude` / `_direct_codex` workflows; the schema is ready for it (UNIQUE constraints + CHECKs already in place; `system_owned` defaults FALSE so the seed must explicitly set it TRUE). Test `localhost:55432` postgres needed `CREATE DATABASE perpetuity_app` once — captured as MEM425 for the next executor.

## Files Created/Modified

- `backend/app/alembic/versions/s10_workflows.py`
- `backend/app/alembic/versions/s11_workflow_runs.py`
- `backend/app/models.py`
- `backend/tests/migrations/test_s10_workflows_migration.py`
- `backend/tests/migrations/test_s11_workflow_runs_migration.py`
