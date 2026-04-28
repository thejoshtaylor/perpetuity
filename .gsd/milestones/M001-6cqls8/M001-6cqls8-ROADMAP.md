# M001-6cqls8: Foundation & Teams

**Vision:** Convert the FastAPI/React template into the real domain model: httpOnly cookie auth, three-role system (user/team admin/system admin), team management with invites, and a mobile-ready dashboard. Everything downstream — containers, projects, workflows — depends on this foundation.

## Success Criteria

- A new user signs up and is automatically assigned a personal team
- A user can create a team, invite another user, and manage roles
- System admin can view all teams and promote users to system admin with confirm
- All integration tests pass against real Postgres — no mocked database
- httpOnly cookie auth works for both REST and WebSocket upgrade requests
- Full flow is usable on a 375px mobile viewport

## Slices

- [x] **S01: S01** `risk:high` `depends:[]`
  > After this: Signup, login, logout all work via httpOnly cookies; GET /users/me returns role; WS connection authenticated via cookie passes integration test; migration runs cleanly on fresh DB

- [x] **S02: S02** `risk:high` `depends:[]`
  > After this: POST /auth/signup creates user + personal team; GET /teams returns user's teams with role; POST /teams creates team with creator as admin; POST /teams/{id}/invite returns 403 for personal teams

- [x] **S03: S03** `risk:medium` `depends:[]`
  > After this: POST /teams/{id}/invite returns invite code; POST /teams/join/{code} adds user; expired invite returns 410; duplicate returns 409; PATCH role endpoint promotes/demotes; DELETE removes member

- [x] **S04: S04** `risk:medium` `depends:[]`
  > After this: User can log in, see their teams dashboard, create a team, copy an invite link, and manage members — all working on a 375px mobile viewport in the browser

- [x] **S05: S05** `risk:low` `depends:[]`
  > After this: System admin sees all teams in a paginated list, can view members of any team, and can promote a user to system admin via confirm dialog — non-admins get 403

## Boundary Map

### S01 → S02

Produces:
- `UserRole` enum on User model (`user`, `system_admin`)
- httpOnly cookie session auth endpoints: POST /auth/signup, POST /auth/login, POST /auth/logout
- Updated `get_current_user` dependency reading from cookie
- `TeamMember` join table schema with `TeamRole` enum (`member`, `admin`)
- Alembic migration for role enum + TeamMember table

Consumes:
- nothing (first slice)

### S02 → S03

Produces:
- `Team` model with `is_personal` flag
- GET /teams — returns user's teams with role
- POST /teams — creates team, creator becomes admin
- POST /auth/signup — creates user AND personal team atomically

Consumes from S01:
- `TeamMember` schema (TeamRole enum)
- `get_current_user` dependency

### S03 → S04

Produces:
- POST /teams/{id}/invite — returns invite code + URL
- POST /teams/join/{code} — accepts invite (410 if expired, 409 if duplicate)
- PATCH /teams/{id}/members/{user_id}/role — promote/demote (team admin only)
- DELETE /teams/{id}/members/{user_id} — remove member (team admin only)

Consumes from S02:
- Team model (is_personal check), GET /teams, POST /teams

### S03 → S05

Produces:
- Same membership management endpoints as S03 → S04

Consumes from S02:
- Team model, GET /teams

### S04 → (done)

Produces:
- Login page, signup page, dashboard with team list + role badges
- Team creation modal/form, invite link UI, member management UI
- Mobile-first layout at 375px minimum

Consumes from S01–S03:
- All auth endpoints, GET /teams, POST /teams, invite/join endpoints, role management endpoints

### S05 → (done)

Produces:
- GET /admin/teams paginated, GET /admin/teams/{id}/members
- POST /admin/users/{id}/promote-system-admin with confirm
- System admin panel UI

Consumes from S01–S03:
- `UserRole` enum, `get_current_user` dependency, Team and TeamMember models
