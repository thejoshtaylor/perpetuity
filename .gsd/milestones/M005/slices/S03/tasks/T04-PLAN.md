---
estimated_steps: 1
estimated_files: 5
skills_used: []
---

# T04: Implement workflow CRUD API routes and cancellation endpoint

Create backend/app/api/routes/workflows_crud.py with: POST /teams/{team_id}/workflows (admin), GET /workflows/{id}, PUT /workflows/{id} (admin), DELETE /workflows/{id} (admin), POST /workflow_runs/{run_id}/cancel (member). System-owned workflows (_direct_ prefix) are rejected on PUT/DELETE. form_schema validated on create/update. Cancellation sets status=cancelled + audit fields. Write pytest coverage for all routes including role gates.

## Inputs

- `backend/app/services/workflow_dispatch.py`
- `backend/app/models.py`
- `backend/app/schemas.py`
- `backend/app/api/router.py`

## Expected Output

- `backend/app/api/routes/workflows_crud.py`
- `backend/tests/api/test_workflow_crud_routes.py`
- `backend/tests/api/test_workflow_cancel_route.py`

## Verification

cd backend && python -m pytest tests/api/test_workflow_crud_routes.py tests/api/test_workflow_cancel_route.py -x -q
