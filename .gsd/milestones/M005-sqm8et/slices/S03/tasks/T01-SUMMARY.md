---
id: T01
parent: S03
milestone: M005-sqm8et
key_files:
  - backend/app/alembic/versions/s13_workflow_crud_extensions.py
  - backend/app/models.py
  - backend/tests/migrations/test_s13_workflow_crud_extensions_migration.py
key_decisions:
  - JSONB server_default in Alembic must use sa.text() wrapper — bare string gets double-escaped by SQLAlchemy
  - WorkflowStepTargetContainer enum lands in s13 even though team_mirror is S04-only — avoids future ALTER TABLE
  - round_robin_cursor is BigInteger not Integer so it survives long-lived teams with many dispatches
  - Cancellation FK is SET NULL not CASCADE — preserves run audit trail after user deletion
duration: 
verification_result: passed
completed_at: 2026-04-29T05:32:17.089Z
blocker_discovered: false
---

# T01: Added s13 alembic migration + model/DTO extensions for workflow form_schema, target_user_id, round_robin_cursor, per-step target_container, and cancellation audit columns

**Added s13 alembic migration + model/DTO extensions for workflow form_schema, target_user_id, round_robin_cursor, per-step target_container, and cancellation audit columns**

## What Happened

Extended the workflow schema on top of S02's spine with six new columns across three tables, new enums, Pydantic form-field models, and CRUD DTOs.

**Migration (s13_workflow_crud_extensions.py):**
- `workflows.form_schema JSONB NOT NULL DEFAULT '{}'::jsonb` — trigger form descriptor rendered on dashboard
- `workflows.target_user_id UUID NULL FK→user(id) ON DELETE SET NULL` — pins dispatch target for scope=team_specific
- `workflows.round_robin_cursor BIGINT NOT NULL DEFAULT 0` — monotonic counter for round-robin dispatch; BigInteger so it can grow past INT_MAX
- `workflow_steps.target_container VARCHAR(32) NOT NULL DEFAULT 'user_workspace' CHECK IN ('user_workspace','team_mirror')` — per-step container override; team_mirror reserved for S04 but column lands now so S04 needs no ALTER
- `workflow_runs.cancelled_by_user_id UUID NULL FK→user(id) ON DELETE SET NULL` — cancellation audit
- `workflow_runs.cancelled_at TIMESTAMPTZ NULL` — timestamp of the cancellation request

**Key fix during execution:** Alembic JSONB `server_default` requires `sa.text("'{}'::jsonb")` not a bare string. Bare strings get double-quoted by SQLAlchemy, producing `'''{}''::jsonb'` and a Postgres `InvalidTextRepresentation` error. Captured as MEM448.

**models.py updates:**
- New enums: `WorkflowStepTargetContainer` (user_workspace / team_mirror), `WorkflowFormFieldKind` (string / text / number)
- New Pydantic models: `WorkflowFormField`, `WorkflowFormSchema`
- New CRUD DTOs: `WorkflowStepCreate`, `WorkflowCreate`, `WorkflowUpdate`
- Extended SQLModel table classes: `Workflow` (3 new cols), `WorkflowStep` (target_container + CHECK in __table_args__), `WorkflowRun` (2 new cancellation cols)
- Extended public DTOs: `WorkflowStepPublic` (target_container typed as enum), `WorkflowPublic` + `WorkflowWithStepsPublic` (form_schema, target_user_id, round_robin_cursor), `WorkflowRunPublic` (cancelled_by_user_id, cancelled_at)
- `system_owned` remains False by default on all CRUD DTOs — only the seed helper writes True

**Tests (test_s13_workflow_crud_extensions_migration.py):** 9 tests covering column shape + defaults, JSONB payload round-trip, both FK SET NULL cascades (target_user_id and cancelled_by_user_id), CHECK rejection of bad target_container, CHECK acceptance of both valid values, BIGINT cursor update, workflow-not-deleted invariant, and full downgrade column removal verification.

## Verification

Ran `cd /Users/josh/code/perpetuity/backend && POSTGRES_DB=perpetuity_app uv run pytest tests/migrations/test_s13_workflow_crud_extensions_migration.py -v` — all 9 tests passed in 0.43s.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd /Users/josh/code/perpetuity/backend && POSTGRES_DB=perpetuity_app uv run pytest tests/migrations/test_s13_workflow_crud_extensions_migration.py -v` | 0 | ✅ pass | 430ms |

## Deviations

None — plan executed exactly as specified. The JSONB server_default fix was a minor implementation adaptation, not a plan deviation.

## Known Issues

None.

## Files Created/Modified

- `backend/app/alembic/versions/s13_workflow_crud_extensions.py`
- `backend/app/models.py`
- `backend/tests/migrations/test_s13_workflow_crud_extensions_migration.py`
