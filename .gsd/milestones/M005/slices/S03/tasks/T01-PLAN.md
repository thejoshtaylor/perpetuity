---
estimated_steps: 1
estimated_files: 3
skills_used: []
---

# T01: Add s13 migration: form_schema, target_user_id, round_robin_cursor, target_container, cancellation audit columns

Create Alembic migration s13_workflow_crud_extensions.py that adds the S03-required columns to workflows and workflow_steps and workflow_runs tables. These columns are prerequisites for all S03 CRUD and dispatch logic.

## Inputs

- `backend/app/models.py`
- `backend/alembic/versions/s12_seed_direct_workflows.py`

## Expected Output

- `backend/alembic/versions/s13_workflow_crud_extensions.py`
- `backend/tests/api/test_s13_workflow_crud_extensions_migration.py`

## Verification

cd backend && python -m pytest tests/api/test_s13_workflow_crud_extensions_migration.py -x -q
