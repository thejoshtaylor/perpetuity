---
id: T03
parent: S03
milestone: M005-sqm8et
key_files:
  - backend/app/services/workflow_dispatch.py
  - backend/tests/api/test_workflow_dispatch_service.py
key_decisions:
  - TargetUserNoMembershipError is a plain Exception subclass (not HTTPException) so the service layer stays DB-only and the API layer owns the 400 mapping in T04
  - Atomic cursor increment uses raw SQL UPDATE…RETURNING to avoid read-modify-write race without a lock
  - Live workspace check queries workspace_volume by (user_id, team_id) with created_at >= now()-7d; 7-day window is a named constant for future tuning
  - Team table name in psqg is 'team' (not 'teams') — SQLModel infers from class name without __tablename__
duration: 
verification_result: passed
completed_at: 2026-04-29T05:46:59.760Z
blocker_discovered: false
---

# T03: Added workflow_dispatch service with user/team_specific/round_robin scope resolution and live-workspace fallback

**Added workflow_dispatch service with user/team_specific/round_robin scope resolution and live-workspace fallback**

## What Happened

Built `app/services/workflow_dispatch.py` with a single `resolve_target_user(session, workflow, triggering_user_id, *, run_id) -> tuple[uuid.UUID, str | None]` function.

**Scope semantics:**
- `scope='user'` → returns `(triggering_user_id, None)` always.
- `scope='team'` (team_specific) → returns `(workflow.target_user_id, None)` after verifying membership; raises `TargetUserNoMembershipError` (400-mappable) when target_user_id is NULL (FK SET NULL on delete) or the target is no longer a team member. Logs `ERROR workflow_dispatch_target_user_no_membership` before raising.
- `scope='round_robin'` → lists team members ordered by `team_member.created_at ASC`, picks `members[cursor % n]`, then probes up to `n` members for a live workspace_volume row provisioned within the last 7 days. Increments cursor atomically via `UPDATE … RETURNING round_robin_cursor`. When no member has a live workspace, falls back to `triggering_user_id` and emits `INFO workflow_dispatch_fallback reason=no_live_workspace`.

**Observability:** Three structured log lines land on the INFO/ERROR logger:
- `workflow_dispatch_round_robin_pick` on successful pick (workflow_id, target_user_id, cursor_before, cursor_after)
- `workflow_dispatch_fallback` when all-offline fallback fires (workflow_id, reason, fallback_target)
- `workflow_dispatch_target_user_no_membership` (ERROR) before TargetUserNoMembershipError surfaces as 400

**Deviation from plan:** The table name for Team in psycopg is `team` (not `teams`); fixed in test cleanup. The cleanup fixture also prefixes rollback before DELETE to handle the session-scoped DB fixture being in a failed state from a prior test run.

## Verification

Ran `POSTGRES_DB=perpetuity_app uv run pytest tests/api/test_workflow_dispatch_service.py -v` — 11 tests passed in 0.14s covering: user scope, team_specific happy path, team_specific NULL target raises, team_specific non-member raises, round_robin cursor pick + increment, round_robin wrap at len(members), round_robin all-offline fallback, round_robin some-offline skips, round_robin no-members fallback, and WorkflowScope enum variants for user/team.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `POSTGRES_DB=perpetuity_app uv run pytest tests/api/test_workflow_dispatch_service.py -v` | 0 | ✅ pass | 140ms |

## Deviations

Test cleanup fixture targets `team` (not `teams`) table — SQLModel infers lowercase singular for Team model. Also added `db.rollback()` at fixture start to handle the session-scoped shared DB fixture being in a failed-transaction state from prior test sessions.

## Known Issues

None.

## Files Created/Modified

- `backend/app/services/workflow_dispatch.py`
- `backend/tests/api/test_workflow_dispatch_service.py`
