# M001-6cqls8: Foundation & Teams

**Gathered:** 2026-04-24
**Status:** Ready for planning

## Project Description

Perpetuity is a collaborative developer workspace platform. Teams of developers work on shared projects, with each user getting their own isolated Docker container per team. AI coding assistants (Claude Code, Codex), GitHub integrations, and configurable automation workflows are core features. The frontend is a PWA with mobile-first design and Grok STT voice input throughout.

This milestone converts the FastAPI/React template into the real domain model: proper auth (httpOnly cookies replacing localStorage JWT), the three-role system, team management with invites, and a mobile-ready dashboard.

## Why This Milestone

The template has a placeholder user model (`is_superuser` bool, `Item` placeholder) and localStorage JWT auth. Everything downstream — containers, projects, workflows — depends on real teams and roles. This milestone is the non-negotiable foundation.

## User-Visible Outcome

### When this milestone is complete, the user can:

- Sign up and be automatically assigned a personal team
- Log in and be greeted by a dashboard showing their teams
- Create a new team, invite users to it, and promote them to team admin
- Navigate the app on a mobile phone and have the full experience
- A system admin can view all teams and promote other users to system admin

### Entry point / environment

- Entry point: Browser at `http://localhost` (Docker Compose dev stack)
- Environment: Local dev, Docker Compose
- Live dependencies involved: PostgreSQL

## Completion Class

- Contract complete means: All integration tests pass against real Postgres; role enforcement verified at API boundary
- Integration complete means: Frontend and backend wired — login → dashboard → team management flows work end-to-end in browser
- Operational complete means: none (no daemon or lifecycle concerns yet)

## Final Integrated Acceptance

To call this milestone complete, we must prove:

- A new user signs up, is assigned a personal team, and can immediately see their dashboard in browser
- A user creates a team, invites another user (via invite link/code), and the invited user accepts and appears as a member
- A team admin can promote a member to team admin; a system admin can promote any user to system admin (with confirm)
- The full flow works on a mobile viewport (375px) without broken layouts or inaccessible elements

## Scope

### In Scope

- Replace `is_superuser` bool with `UserRole` enum (`user`, `system_admin`) on User model
- Add `Team`, `TeamMember` models; `TeamRole` enum (`member`, `admin`) on `TeamMember`
- Personal team auto-created on signup (flagged as personal, non-invitable)
- Team CRUD API: create, read, update, delete (team admin or system admin)
- Team invite system: generate invite link/code, accept invite, join team as member
- Role management API: promote/demote team members (team admin), promote system admin (system admin + confirm)
- Replace localStorage JWT auth with httpOnly cookie sessions (works for REST + WS upgrade)
- Frontend: login, signup, dashboard showing user's teams, team creation, invite flow, team member management
- System admin frontend: view all teams, promote users to system admin
- Mobile-first layout throughout; all flows usable on 375px viewport
- Alembic migration for all schema changes

### Out of Scope / Non-Goals

- Docker containers, terminal, workspaces (M002)
- GitHub integrations (M003)
- Workflows, Celery (M005)
- PWA manifest, push notifications, STT (M006)
- Email delivery of invites (deferred; in-app invite link is sufficient for M001)

## Architectural Decisions

### Auth: httpOnly Cookie Sessions

**Decision:** Replace the current JWT localStorage pattern with httpOnly cookie-based sessions.

**Rationale:** httpOnly cookies are XSS-safe and work transparently for WebSocket upgrade requests (browser sends `Cookie` header on `Upgrade: websocket`). This is the right time to migrate — before any downstream WebSocket or mobile code is built on top of the existing pattern.

**Alternatives Considered:**
- Keep localStorage JWT — rejected; XSS-vulnerable, doesn't work cleanly for WS auth without adding a separate token-passing mechanism

### Role Model: Enum + Join Table

**Decision:** `UserRole` enum on User for system-level role; separate `TeamMember` join table with `TeamRole` enum for per-team roles.

**Rationale:** A user can be team admin on Team A and a regular member on Team B — this requires the role to live on the membership, not the user. System admin is user-level (one role covers all teams).

**Alternatives Considered:**
- Single role field on User — rejected; can't express different roles on different teams

### Personal Team Flag

**Decision:** `Team` model has an `is_personal` boolean field. Personal teams reject invite operations at the API layer.

**Rationale:** Simplest way to distinguish personal teams without a separate model. Enforcement is in the invite endpoint.

**Alternatives Considered:**
- Separate `PersonalTeam` model — rejected; unnecessary complexity for a flag

## Error Handling Strategy

- **401 Unauthorized:** Silent refresh attempt; if refresh fails, redirect to login
- **403 Forbidden:** Surface "You don't have permission for this action" — no redirect
- **Invite expiry:** Invites have a TTL (default 7 days). Expired invites return 410 Gone with a clear message
- **Duplicate invite:** If user already a member, return 409 Conflict with "Already a member of this team"
- **System admin self-promotion:** Blocked at API layer — system admins can't promote themselves, only promote others

## Risks and Unknowns

- Cookie session migration — The existing template has auth spread across several places (login route, deps.py, useAuth hook). Need to audit all touch points before migrating.
- Alembic migration complexity — Replacing `is_superuser` with an enum and adding Team/TeamMember requires careful migration ordering to avoid data loss on existing records.

## Existing Codebase / Prior Art

- `backend/app/models.py` — Current User model with `is_superuser`; Item placeholder to be removed
- `backend/app/api/routes/login.py` — Current JWT login logic; target for httpOnly cookie migration
- `backend/app/api/deps.py` — `get_current_user` dependency; update to read from cookie
- `backend/app/core/security.py` — JWT helpers; will need session token helpers alongside
- `frontend/src/hooks/useAuth.ts` — Current auth hook reading from localStorage; update to cookie-transparent pattern
- `frontend/src/routes/_layout.tsx` — Auth guard; update for new cookie-based session check
- `backend/app/alembic/versions/` — Migration history; new migration needed for role enum + team models

## Relevant Requirements

- R001 — httpOnly cookie auth is the foundation of this milestone
- R002 — Three-tier role system built here
- R003 — Personal team auto-created on signup
- R004 — Team creation, invites, role management
- R022 — Mobile-first layout starts here (enforced throughout all subsequent milestones)

## Technical Constraints

- Must use Alembic for all schema changes — no raw SQL or SQLModel `create_all()`
- Integration tests must hit a real Postgres instance (not mocked)
- httpOnly cookies must work for both REST and WS upgrade — verify before closing S01
- Mobile layout must be verified at 375px viewport (iPhone SE) as the minimum bar

## Integration Points

- PostgreSQL — primary data store for all models
- Browser (cookie) — session token delivery; no localStorage usage after migration

## Testing Requirements

Full integration tests (pytest, real Postgres) for:
- Auth: signup, login, logout, session expiry, WS auth via cookie
- Role enforcement: each role's allowed and forbidden operations
- Team CRUD: create, read, update, delete with correct role checks
- Invite flow: generate invite, accept invite, reject expired invite, reject invite to personal team
- System admin: view all teams, promote user to system admin (with confirm)

No mocking of the database layer.

## Acceptance Criteria

**S01 (Auth + Role Model):**
- POST /auth/signup creates user + personal team, sets httpOnly cookie
- POST /auth/login sets httpOnly cookie, returns user info (no token in body)
- POST /auth/logout clears cookie
- GET /users/me returns current user with role
- WS connection authenticated via cookie (integration test confirming cookie forwarded on upgrade)
- All existing auth integration tests pass with new cookie-based implementation

**S02 (Team Model + Personal Team):**
- GET /teams returns user's teams with their role in each
- POST /teams creates team, creator becomes admin
- Personal team created on signup appears in GET /teams response
- POST /teams/{id}/invite returns 403 if team is personal
- Team deletion blocked if team has active members (except personal teams)

**S03 (Invites + Membership):**
- POST /teams/{id}/invite generates invite code (7-day TTL)
- POST /teams/join/{code} adds user as member; returns 410 if expired; returns 409 if already member
- PATCH /teams/{id}/members/{user_id}/role promotes/demotes; team admin only
- DELETE /teams/{id}/members/{user_id} removes member; team admin only (can't remove self if only admin)

**S04 (Frontend):**
- Login and signup pages work on 375px viewport
- Dashboard shows list of user's teams with role badge
- Team creation form accessible from dashboard; works on mobile
- Invite flow (generate link, copy link, join via link) works end-to-end in browser
- Team member list with role management UI for team admins

**S05 (System Admin):**
- System admin panel: paginated list of all teams
- System admin can promote any user to system admin (with confirmation dialog)
- System admin can view any team's members
- Non-system-admin users cannot access system admin routes (403 verified in tests)

## Open Questions

- Invite delivery mechanism — the milestone uses an in-app link/code (user copies and shares it). Email delivery is deferred. The invite endpoint should return the raw code/URL so frontend can display it. This is good enough for M001.
