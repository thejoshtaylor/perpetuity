---
estimated_steps: 18
estimated_files: 3
skills_used: []
---

# T03: Add backend PATCH /api/v1/teams/{team_id}/mirror always_on toggle (team-admin gated)

Thin team-admin endpoint that flips the `always_on` flag on the team's `team_mirror_volumes` row. Backend does NOT call the orchestrator — the toggle just biases the next reaper tick which reads the row directly. Auto-creates the row with always_on=<requested> on first PATCH if no row exists yet (so an admin can pre-toggle a team that has never spun up a mirror), using a placeholder volume_path='pending:<team_id>' that the orchestrator's ensure path replaces on first cold-start. Mirrors the team_access + admin-gated PATCH shape used by the existing teams routes (MEM047/MEM115). Returns the updated TeamMirrorVolumePublic.

## Failure Modes

| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| Postgres | propagate (FastAPI default 500) | propagate | N/A |
| assert_caller_is_team_admin | 404 team missing / 403 not admin | N/A | N/A |

## Load Profile

- Shared resources: backend SQLModel session (per-request).
- Per-operation cost: 1 SELECT + 1 INSERT/UPDATE.
- 10x breakpoint: N/A — admin-only mutation, low frequency.

## Negative Tests

- Malformed inputs: PATCH body missing `always_on` → 422 (pydantic); PATCH `{always_on: 'yes'}` → 422; PATCH path with invalid uuid → 422.
- Error paths: non-admin caller → 403; non-member caller → 403; missing team → 404 (does not auto-create row for a team that doesn't exist).
- Boundary conditions: PATCH twice with same value is idempotent (200, no warning); PATCH on team that has no mirror row yet auto-inserts with placeholder volume_path='pending:<team_id>'.

## Observability Impact

- Signals added/changed: INFO `team_mirror_always_on_toggled team_id=<uuid> actor_id=<uuid> always_on=<bool> created_row=<bool>`.
- How a future agent inspects this: `psql -c 'SELECT team_id, always_on FROM team_mirror_volumes WHERE team_id=...'`; backend access log shows the PATCH.
- Failure state exposed: 403 / 404 are the audit trail; no orphan-row state because the auto-insert is gated by team-existence.

## Inputs

- ``backend/app/api/routes/teams.py` — extend with PATCH /{team_id}/mirror endpoint following the existing PATCH /{team_id}/members/{user_id}/role shape`
- ``backend/app/api/team_access.py` — `assert_caller_is_team_admin` (already lifted in M002 T05; reuse without modification)`
- ``backend/app/models.py` — TeamMirrorVolume + TeamMirrorVolumePublic + TeamMirrorPatch SQLModels added in T01`

## Expected Output

- ``backend/app/api/routes/teams.py` — PATCH /{team_id}/mirror endpoint, body model TeamMirrorPatch(always_on: bool), team-admin gated, auto-creates row with placeholder volume_path on first PATCH, INFO log `team_mirror_always_on_toggled team_id=<uuid> actor_id=<uuid> always_on=<bool> created_row=<bool>`, returns TeamMirrorVolumePublic`
- ``backend/app/models.py` — confirm TeamMirrorPatch + TeamMirrorVolumePublic exist (added in T01)`
- ``backend/tests/api/routes/test_teams_mirror.py` — 6+ tests: happy path admin toggle on (auto-create row), toggle off (idempotent UPDATE), non-admin → 403, non-member → 403, missing team → 404, malformed body (`{always_on: 'yes'}`) → 422, two-toggle idempotency`

## Verification

cd /Users/josh/code/perpetuity/backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_teams_mirror.py -v

## Observability Impact

Adds one INFO log line `team_mirror_always_on_toggled` with team_id + actor_id (uuid-safe per MEM134). No external service calls.
