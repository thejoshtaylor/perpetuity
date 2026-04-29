---
estimated_steps: 1
estimated_files: 2
skills_used: []
---

# T02: Extend models + DTOs for CRUD fields and scope routing

Add form_schema (JSONB), target_user_id, round_robin_cursor, WorkflowScope routing fields to Workflow model. Add target_container to WorkflowStep. Add cancelled_by_user_id + cancelled_at to WorkflowRun. Add corresponding Pydantic DTOs: WorkflowCreate, WorkflowUpdate, WorkflowWithStepsPublic, WorkflowFormFieldKind.

## Inputs

- `backend/app/models.py`
- `backend/app/schemas.py`
- `backend/alembic/versions/s13_workflow_crud_extensions.py`

## Expected Output

- `backend/app/models.py`
- `backend/app/schemas.py`

## Verification

cd backend && python -c 'from app.models import Workflow, WorkflowStep, WorkflowRun; from app.schemas import WorkflowCreate, WorkflowWithStepsPublic'
