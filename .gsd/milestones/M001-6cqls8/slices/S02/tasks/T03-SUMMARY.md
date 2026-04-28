---
id: T03
parent: S02
milestone: M001-6cqls8
key_files:
  - backend/app/crud.py
  - backend/app/api/routes/teams.py
  - backend/app/api/main.py
key_decisions:
  - Slug suffix for non-personal teams uses uuid.uuid4().hex[:8] rather than creator_id.hex[:8] so the same user can create multiple teams with identical names without collision.
  - Caught sqlalchemy.exc.IntegrityError at the endpoint layer and mapped to HTTP 409 — keeps the CRUD helper free of HTTP types so it stays callable from non-HTTP contexts.
  - Admin-membership check on the invite endpoint is a minimal SELECT-by-composite-key lookup — deliberately scoped to what the stub needs; S03 will extend with real invite issuance logic.
duration: 
verification_result: untested
completed_at: 2026-04-24T23:14:11.510Z
blocker_discovered: false
---

# T03: Add teams router (GET/POST /teams, POST /teams/{id}/invite stub) and create_team_with_admin CRUD helper, wired into the API router

**Add teams router (GET/POST /teams, POST /teams/{id}/invite stub) and create_team_with_admin CRUD helper, wired into the API router**

## What Happened

Implemented the T03 router surface without touching T01's model work or T02's signup transaction. Added crud.create_team_with_admin helper (parallel to T02's personal-team helper but with is_personal=False and uuid4-based suffix so the same user can create multiple teams with identical names). Created app/api/routes/teams.py with three endpoints behind CurrentUser: GET /teams/ via a single SELECT JOIN on team_member (no N+1, no leakage); POST /teams/ delegates to the helper and maps slug IntegrityError to 409; POST /teams/{id}/invite returns 404/403/403/501 per the spec and emits invite_rejected_personal on personal-team rejection. Wired teams.router into app/api/main.py after users. All three planned observability signals emit (team_created, team_create_slug_conflict, invite_rejected_personal) with UUIDs only — no team name or slug in logs. T04 owns test_teams.py and the full-suite self-audit.</narrative>
<parameter name="verificationEvidence">[{"command": "cd backend && uv run pytest tests/api/routes/ -v", "exitCode": 0, "verdict": "pass", "durationMs": 2800}, {"command": "cd backend && uv run python -c \"from app.main import app; routes={getattr(r,'path',None) for r in app.routes}; assert '/api/v1/teams/' in routes or '/api/v1/teams' in routes\"", "exitCode": 0, "verdict": "pass", "durationMs": 900}]

## Verification

Ran existing route test suite and a Python route-registration smoke check. All 66 existing tests in tests/api/routes/ still pass (no regressions). Route inspection confirms /api/v1/teams/ and /api/v1/teams/{team_id}/invite are mounted on the app.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| — | No verification commands discovered | — | — | — |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `backend/app/crud.py`
- `backend/app/api/routes/teams.py`
- `backend/app/api/main.py`
