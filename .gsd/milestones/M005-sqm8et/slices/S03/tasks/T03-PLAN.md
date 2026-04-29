---
estimated_steps: 1
estimated_files: 2
skills_used: []
---

# T03: Build workflow_dispatch service with target_user resolution for user / team_specific / round_robin scopes plus live-workspace fallback

`app/services/workflow_dispatch.py` is the new module that owns target-user selection at dispatch time. Single function `resolve_target_user(session, workflow, triggering_user_id) -> tuple[uuid.UUID, str | None]` returning `(target_user_id, fallback_reason | None)`. Scope semantics: (a) `scope='user'` → returns `(triggering_user_id, None)`. Always falls back to triggering user; matches S02's existing direct-AI behavior. (b) `scope='team_specific'` → returns `(workflow.target_user_id, None)` — but if `target_user_id` is NULL (target user was deleted, FK SET NULL) OR the target user is no longer a team member, raises `TargetUserNoMembershipError` which the API maps to 400 `{detail: 'target_user_no_membership'}`. (c) `scope='round_robin'` → reads `workflow.round_robin_cursor`, lists active team members ordered by `team_member.created_at ASC` (dense indexing), picks `members[cursor % len(members)]`, increments cursor (atomic UPDATE workflows SET round_robin_cursor = round_robin_cursor + 1 WHERE id = :id RETURNING round_robin_cursor — wraps cleanly without locking). Round-robin live-workspace check: query the orchestrator-managed `workspace_volumes` table (already exists) for any volume with `(user_id=<picked>, team_id=<workflow.team_id>)` provisioned within the last 7 days; if absent, picked user is considered offline — try the NEXT cursor position, up to len(members) probes. If no member has a live workspace at all, fall back to `triggering_user_id` and emit INFO `workflow_dispatch_fallback run_id=<uuid> reason=no_live_workspace fallback_target=triggering_user`. The fallback is what makes UAT scenario 4 of the milestone-context viable. The dispatcher is invoked at the API boundary (T04) BEFORE `WorkflowRun` is inserted, so `target_user_id` is known at row-create time. Unit tests cover: scope='user' returns triggering user, scope='team_specific' happy path, scope='team_specific' with NULL target raises TargetUserNoMembership, scope='team_specific' with non-member target raises, scope='round_robin' picks next member + increments cursor, round-robin wraps at len(members), round-robin all-offline falls back to triggering user with the structured log line, round-robin some-offline skips them.

## Inputs

- ``backend/app/models.py``
- ``backend/app/api/team_access.py``

## Expected Output

- ``backend/app/services/workflow_dispatch.py``
- ``backend/tests/api/test_workflow_dispatch_service.py``

## Verification

cd /Users/josh/code/perpetuity/backend && uv run pytest tests/api/test_workflow_dispatch_service.py -v

## Observability Impact

Adds INFO `workflow_dispatch_round_robin_pick workflow_id=<uuid> target_user_id=<uuid> cursor_before=<n> cursor_after=<n>` on successful round-robin pick; INFO `workflow_dispatch_fallback workflow_id=<uuid> reason=no_live_workspace fallback_target=triggering_user` when no member has a live workspace; ERROR `workflow_dispatch_target_user_no_membership workflow_id=<uuid> target_user_id=<uuid>` before the API surfaces 400. Inspection: `psql perpetuity_app -c "SELECT name, round_robin_cursor FROM workflows WHERE scope = 'round_robin'"` shows cursor advance over time.
