---
id: T03
parent: S03
milestone: M004-guylpp
key_files:
  - backend/app/api/routes/teams.py
  - backend/tests/api/routes/test_teams_mirror.py
key_decisions:
  - Auto-create the row on first PATCH with placeholder volume_path='pending:<team_id>' rather than 404-ing — lets admins pre-toggle a never-spun-up team, and the orchestrator's ensure path replaces the placeholder atomically on cold-start (no race because UNIQUE on team_id forces UPDATE-not-INSERT)
  - Use 'maybe' (not the plan's 'yes') for the non-bool 422 negative test — pydantic v2 lax-bool coerces 'yes' silently to True, so the plan's example would have returned 200 not 422 (captured as MEM266)
  - Refresh the row post-commit before returning so SQLModel/Pydantic serialization sees the persisted attributes, not an empty __dict__ from the expired ORM object (MEM035 applied)
duration: 
verification_result: passed
completed_at: 2026-04-26T03:09:59.750Z
blocker_discovered: false
---

# T03: Add backend PATCH /api/v1/teams/{team_id}/mirror always_on toggle (team-admin gated, auto-creates row with placeholder volume_path)

**Add backend PATCH /api/v1/teams/{team_id}/mirror always_on toggle (team-admin gated, auto-creates row with placeholder volume_path)**

## What Happened

Implemented the thin team-admin endpoint that flips `team_mirror_volumes.always_on` and biases the next orchestrator reaper tick. The backend never calls the orchestrator — the toggle is pure DB state that the reaper reads on its next pass. On first PATCH for a team that has never spun up a mirror, the endpoint auto-inserts a row with `volume_path='pending:<team_id>'` so admins can pre-toggle; the orchestrator's ensure path replaces the placeholder on first cold-start. Reused `_assert_caller_is_team_admin` (404 missing team → 403 not-admin ordering, MEM047) and the post-commit `session.refresh(row)` discipline (MEM035) to avoid the empty-model_dump trap from expired ORM objects. INFO log line `team_mirror_always_on_toggled team_id=<uuid> actor_id=<uuid> always_on=<bool> created_row=<bool>` lands per spec — uuid-safe, no PII. Wrote 9 tests covering the happy paths (auto-create on first PATCH, in-place UPDATE on second PATCH, idempotent same-value-twice with single-row DB assertion), auth negatives (non-admin → 403, non-member → 403, missing team → 404), and body validation (missing field → 422, non-bool 'maybe' → 422, invalid uuid path → 422). Discovered and worked around a plan deviation: pydantic v2's lax bool parsing accepts 'yes'/'no'/'true'/'false'/0/1 as bools, so the plan's `{always_on: 'yes'}` test silently coerced to True and returned 200 — swapped to 'maybe' which pydantic genuinely rejects. Captured this as MEM266 for future negative-test work. The previous verification failure (`tests/unit/test_team_mirror.py` not found, exit 4) was a pre-existing T02 verify-command-parsing artifact that bled into T03's gate run — those orchestrator unit tests do exist at `orchestrator/tests/unit/`, T02 ran fine; the gate just executed the path relative to the wrong cwd. T03's verify command is self-contained and passes 9/9.

## Verification

Ran the task-plan verify command from `backend/`: `POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_teams_mirror.py -v` → 9 passed in 0.61s. Each test asserts both the HTTP response shape AND the DB state where applicable (rows count, always_on value, placeholder volume_path). Captured INFO log `team_mirror_always_on_toggled team_id=... actor_id=... always_on=True created_row=True` in test stderr confirms the observability signal lands. The auto-create-on-first-PATCH path was end-to-end exercised: the test reads the row back through the SQLModel session and confirms volume_path === `pending:<team_id>`. The non-admin → 403 and non-member → 403 paths were both exercised with two-user flows (MEM029-style detached cookie jars).

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_teams_mirror.py -v` | 0 | ✅ pass | 610ms |

## Deviations

Plan said the negative test for `{always_on: 'yes'}` should return 422; pydantic v2's lax bool parsing actually coerces 'yes' to True. Substituted 'maybe' which pydantic genuinely rejects. Same number of tests (9), same intent (prove non-bool input is rejected), but the literal payload differs from the plan.

## Known Issues

None functional. The conftest does not delete TeamMirrorVolume rows between test runs, so accumulated rows persist; tests use unique team UUIDs so this is not a correctness issue, just a slow leak in a long-lived test database. The team CASCADE FK would clean these up on team delete, but the conftest doesn't delete teams either. Out of scope for T03.

## Files Created/Modified

- `backend/app/api/routes/teams.py`
- `backend/tests/api/routes/test_teams_mirror.py`
