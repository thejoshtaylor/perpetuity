---
estimated_steps: 35
estimated_files: 3
skills_used: []
---

# T03: GET /teams and POST /teams endpoints + router wiring

Add a new router `app/api/routes/teams.py` mounted at `/api/v1/teams` exposing GET and POST. Both require cookie auth via existing `CurrentUser` dependency.

**GET /api/v1/teams**
Returns `{data: [TeamWithRole, ...], count: int}` where TeamWithRole (defined by T01) = TeamPublic + role (the caller's role in that team).

Query shape — single SELECT JOIN, not N+1:
```
SELECT team.*, team_member.role
FROM team_member
JOIN team ON team.id = team_member.team_id
WHERE team_member.user_id = :current_user_id
ORDER BY team.created_at DESC
```
Implement via SQLModel: `statement = select(Team, TeamMember.role).join(TeamMember, TeamMember.team_id == Team.id).where(TeamMember.user_id == current_user.id).order_by(Team.created_at.desc())`. Iterate results, build `TeamWithRole(**team.model_dump(), role=role)` for each.

No pagination in this slice (typical user has <10 teams; noted for later if needed).

**POST /api/v1/teams**
Body: `TeamCreate` (just `{name}`). Response: `TeamWithRole` (the just-created team with role=admin).

Implementation via new CRUD helper `crud.create_team_with_admin(*, session, name, creator_id)` — parallel shape to T02's personal-team helper but with is_personal=False and accepting explicit name:
1. Build slug via `_slugify(name) + '-' + short_suffix` (reuse T02's helper; collisions on slug raise IntegrityError → 409 in the endpoint).
2. Insert Team(name, slug, is_personal=False).
3. Insert TeamMember(user_id=creator_id, team_id=team.id, role=admin).
4. Commit, return team.

On slug IntegrityError (extremely rare given 8-char suffix but possible): raise HTTPException(409, 'Team slug conflict — retry'). Log `team_create_slug_conflict slug=<attempted> user_id=<uuid>` at WARNING.

**POST /api/v1/teams/{team_id}/invite** (stub — delivers the S02→S03 boundary contract)
- Path: `team_id: uuid.UUID`.
- Require CurrentUser.
- Load team via `session.get(Team, team_id)`. If None → 404.
- Verify caller is a member with role=admin — if not, 403 'Only team admins can invite'. (This check is minimal; S03 will extend with invite creation logic.)
- If `team.is_personal is True` → 403 with detail exactly `Cannot invite to personal teams` (the boundary contract checked by the S04 test and by T04's negative test).
- Otherwise → 501 `{"detail": "Invite endpoint not yet implemented — see S03"}`. S03 will replace this body with real invite-code issuance.

**Router wiring:**
`app/api/main.py` — import the new teams router and include it: `api_router.include_router(teams.router)`. Order after `users` is conventional.

**Must-haves:**
- GET /teams never leaks teams the caller isn't a member of (single WHERE clause — verified by T04 test).
- Creating a team automatically makes the creator an admin (the SQL transaction, not a separate call).
- Empty body for POST /teams → 422 (pydantic handles).
- Name length 1..255 enforced by TeamCreate model from T01.

## Inputs

- ``backend/app/models.py` — Team, TeamMember, TeamPublic, TeamCreate, TeamWithRole (from T01).`
- ``backend/app/crud.py` — `_slugify` helper (from T02), existing patterns for helpers.`
- ``backend/app/api/deps.py` — CurrentUser, SessionDep (S01 — no changes needed).`
- ``backend/app/api/main.py` — where the new router gets mounted.`

## Expected Output

- ``backend/app/api/routes/teams.py` — new router with GET /teams, POST /teams, POST /teams/{id}/invite; uses CurrentUser dep; returns TeamWithRole shapes.`
- ``backend/app/api/main.py` — includes teams.router.`
- ``backend/app/crud.py` — adds `create_team_with_admin(session, name, creator_id)` helper.`

## Verification

cd backend && uv run pytest tests/api/routes/ -v (existing tests still pass) && python -c "from app.main import app; routes={getattr(r,'path',None) for r in app.routes}; assert '/api/v1/teams/' in routes or '/api/v1/teams' in routes"

## Observability Impact

Signals added: `team_created team_id=<uuid> is_personal=false creator_id=<uuid>` INFO on POST /teams success; `team_create_slug_conflict slug=<attempted> user_id=<uuid>` WARNING on rare slug collision; `invite_rejected_personal team_id=<uuid> caller_id=<uuid>` INFO on the 403 path (useful to spot UI bugs attempting invites on personal teams). Inspection: caller can self-inspect via GET /teams; DB side via `SELECT * FROM team JOIN team_member USING (...)`. Team name is NEVER logged — only UUIDs.
