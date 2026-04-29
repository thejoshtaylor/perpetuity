---
id: T03
parent: S03
milestone: M005
key_files:
  - backend/app/services/workflow_dispatch.py
  - backend/tests/api/test_workflow_dispatch_service.py
key_decisions:
  - resolve_target_user accepts both string and WorkflowScope enum for scope comparison — guards against mixed str/enum comparison after SQLModel deserialization
  - round_robin cursor increment uses raw UPDATE...RETURNING for atomicity — avoids read-modify-write race under concurrent dispatch
  - _has_live_workspace uses a 7-day rolling window on workspace_volume.created_at — balances freshness signal with tolerance for containers that are running but not recently re-provisioned
  - TargetUserNoMembershipError is a domain exception carrying workflow_id+target_user_id for structured 409 responses at the API boundary (T04)
duration: 
verification_result: passed
completed_at: 2026-04-29T07:49:24.369Z
blocker_discovered: false
---

# T03: Implemented workflow_dispatch service with user/team/round_robin scope routing and 11 pytest tests; all pass.

**Implemented workflow_dispatch service with user/team/round_robin scope routing and 11 pytest tests; all pass.**

## What Happened

Both output files were already present from a prior session. `backend/app/services/workflow_dispatch.py` implements `resolve_target_user(session, workflow, triggering_user_id)` returning `(target_user_id, fallback_reason | None)` with full scope handling: `user` scope always returns the triggering user; `team`/`team_specific` scope validates `target_user_id` is non-null and still a team member, raising `TargetUserNoMembershipError` otherwise; `round_robin` scope atomically increments the `round_robin_cursor` via a raw `UPDATE ... RETURNING` and walks the ordered member list looking for anyone with a `workspace_volume` row created within the last 7 days, falling back to the triggering user with `reason="no_live_workspace"` when none qualify. The service accepts both string and enum forms of `WorkflowScope` values for forward/backward compatibility. `TargetUserNoMembershipError` carries `workflow_id` and `target_user_id` for structured error propagation at the API layer. The test file covers 11 scenarios: user-scope string and enum forms, team-specific happy path and enum form, null-target and non-member raise cases, round-robin cursor pick, cursor wrap-around, all-offline fallback with log assertion, partial-offline skip, and no-members fallback.

## Verification

Ran `cd backend && python -m pytest tests/api/test_workflow_dispatch_service.py -x -q` — 11 passed in 0.25s.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && python -m pytest tests/api/test_workflow_dispatch_service.py -x -q` | 0 | ✅ pass — 11 passed | 250ms |

## Deviations

Task plan listed `backend/app/schemas.py` as an input but that file does not exist — all models and schemas are co-located in `backend/app/models.py` (established in T02). Both output files were already fully implemented; execution consisted of verification only.

## Known Issues

None.

## Files Created/Modified

- `backend/app/services/workflow_dispatch.py`
- `backend/tests/api/test_workflow_dispatch_service.py`
