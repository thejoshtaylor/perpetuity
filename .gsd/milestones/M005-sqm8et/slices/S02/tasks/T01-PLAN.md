---
estimated_steps: 2
estimated_files: 5
skills_used: []
---

# T01: Minimal workflows + workflow_runs + step_runs schema + SQLModels + DTOs

Land the slim subset of the M005 schema that S02 needs. Workflows is shaped to accommodate S03's eventual multi-step + scope + per-step target_container so S03 doesn't migrate. workflow_runs persists trigger + status + timing + scope target. step_runs persists snapshot + stdout + stderr + exit + duration + error_class. Add `system_owned BOOLEAN` to `workflows` so S03's CRUD UI can filter `_direct_claude` / `_direct_codex` out (D028). All FKs ON DELETE CASCADE on team and workflow (orphan run history is meaningless). Composite uniqueness on `(team_id, name)` for workflows so duplicate seed attempts are caught. Add SQLModel rows + Pydantic Public/Create DTOs covering only what S02 reads — additional DTOs (Update, full step list create, etc.) land in S03. Migration test runs upgrade-from-s09 + downgrade round-trip.

Assumptions documented inline: (1) `workflow_runs.scope` is omitted — it lives on `workflows.scope` and the run inherits at dispatch. S02 only uses scope='user'; round-robin and team_specific land in S03's dispatcher. (2) `workflow_steps` is a sibling table (not JSONB on `workflows`) so S03 can ALTER and add per-step fields without rewriting; for S02 each system workflow has exactly one step. (3) `step_runs.snapshot` is JSONB capturing the WorkflowStep row at run-dispatch time — S02 writes the whole snapshot row.

## Inputs

- ``backend/app/alembic/versions/s09_team_secrets.py``
- ``backend/app/models.py``
- ``backend/tests/migrations/test_s09_team_secrets_migration.py``

## Expected Output

- ``backend/app/alembic/versions/s10_workflows.py``
- ``backend/app/alembic/versions/s11_workflow_runs.py``
- ``backend/app/models.py``
- ``backend/tests/migrations/test_s10_workflows_migration.py``
- ``backend/tests/migrations/test_s11_workflow_runs_migration.py``

## Verification

cd backend && POSTGRES_DB=perpetuity_app uv run pytest tests/migrations/test_s10_workflows_migration.py tests/migrations/test_s11_workflow_runs_migration.py -v
