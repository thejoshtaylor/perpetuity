---
estimated_steps: 1
estimated_files: 2
skills_used: []
---

# T03: Implement workflow_dispatch service: resolve_target_user with user/team/round_robin scope routing

Create backend/app/services/workflow_dispatch.py implementing resolve_target_user(session, workflow, triggering_user_id) -> (target_user_id, fallback_reason). Handle user scope (always triggering user), team_specific (workflow.target_user_id), round_robin (atomic cursor increment over members with live workspaces, fallback to triggering user). Write full pytest coverage.

## Inputs

- `backend/app/models.py`
- `backend/app/schemas.py`

## Expected Output

- `backend/app/services/workflow_dispatch.py`
- `backend/tests/api/test_workflow_dispatch_service.py`

## Verification

cd backend && python -m pytest tests/api/test_workflow_dispatch_service.py -x -q
