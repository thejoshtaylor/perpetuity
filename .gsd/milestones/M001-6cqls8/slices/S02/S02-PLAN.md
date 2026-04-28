# S02: Team model + personal team bootstrap

**Goal:** Extend the S01 Team stub into a real model (name, slug, is_personal), auto-create a personal team atomically on signup, and expose GET /teams + POST /teams endpoints; POST /teams/{id}/invite stub returns 403 for personal teams to close the S02→S03 boundary contract.
**Demo:** POST /auth/signup creates user + personal team; GET /teams returns user's teams with role; POST /teams creates team with creator as admin; POST /teams/{id}/invite returns 403 for personal teams

## Must-Haves

- **Demo:** A newly signed-up user's GET /teams returns exactly one team with `is_personal=true` and their role `admin`. POST /teams creates a non-personal team with the caller as admin and it appears in the next GET /teams. POST /teams/{id}/invite on the caller's personal team returns 403. Signup is atomic — if personal team creation fails, the user is not persisted.
- **Must-Haves:**
- Alembic migration `s02_team_columns` adds `name`, `slug`, `is_personal` to `team` (nullable→backfill→NOT NULL pattern; backfill fills no rows because S01 produced an empty `team` table in fresh DBs, but the migration must be safe if someone seeded rows manually).
- Team model gains `name: str`, `slug: str (unique, indexed)`, `is_personal: bool`.
- POST /auth/signup creates User + Team(is_personal=True) + TeamMember(role=admin) in a single SQL transaction. On failure anywhere in the chain, nothing persists.
- GET /api/v1/teams returns `{data: [{id, name, slug, is_personal, role, created_at}, ...], count}` filtered to teams the caller is a member of.
- POST /api/v1/teams body `{name}` creates a non-personal team, makes the caller an admin, returns the created team.
- POST /api/v1/teams/{id}/invite returns 403 with detail "Cannot invite to personal teams" when team.is_personal is true; otherwise returns 501 (not implemented — S03 delivers the happy path).
- Slug generation is deterministic from name + short suffix for collision avoidance; slug is URL-safe (lowercase, hyphenated).
- Existing S01 test suite (76/76) still passes after the migration.
- **Threat Surface:**
- **Abuse**: A user could attempt to read another team's membership by guessing team id in GET /teams (filtered by membership — mitigated by the query). POST /teams is rate-naive in this slice — spam creation accepted; noted for a later rate-limit slice. Signup racing (two concurrent signups with same email) — crud.create_user's unique email constraint handles it, but the personal-team transaction must not leak a ghost team if the user insert loses the race.
- **Data exposure**: Team name and slug are user-supplied strings; logged only as redacted team id, never the name (avoids leaking collaboration graphs in logs). No PII beyond what user controls.
- **Input trust**: `name` is user-supplied text → validated length (1..255) and trimmed. `slug` is server-generated, never accepted from client body. team `id` in path params is UUID-coerced by FastAPI — malformed → 422.
- **Requirement Impact:**
- **Requirements touched**: R003 (personal-team bootstrap), R004 (team creation + membership). Both advance from active→validated on slice completion.
- **Re-verify**: Signup flow (S01's cookie issuance must still work — now with transactional personal team attached). GET /users/me (unchanged, but ran via cookie fixtures that now also exercise personal-team existence).
- **Decisions revisited**: D003 (is_personal flag) — this slice implements it; no revision.
- **Proof Level:**
- This slice proves: integration (HTTP + DB transactional correctness).
- Real runtime required: yes (real Postgres via existing test fixtures, real FastAPI TestClient — no mocks per D001/D002 pattern).
- Human/UAT required: no (all assertions are programmatic).

## Proof Level

- This slice proves: integration — slice is proved when pytest asserts HTTP contract AND DB transactional invariants on real Postgres. No human UAT required.

## Integration Closure

- Upstream surfaces consumed: `app/models.py::Team` (S01 stub), `app/models.py::TeamMember`/`TeamRole` (S01), `app/api/deps.py::get_current_user` (S01 cookie auth), `app/api/routes/auth.py::signup` (S01 — mutate), `app/crud.py::create_user` (S01), `app/alembic/versions/s01_auth_and_roles.py` (S01 parent migration).
- New wiring introduced in this slice: new router `app/api/routes/teams.py` mounted in `app/api/main.py`; signup endpoint extended to call new `crud.create_user_with_personal_team`; new migration `s02_team_columns` chained after S01.
- What remains before the milestone is truly usable end-to-end: S03 invite code issuance + join + role management endpoints (this slice only stubs the 403-on-personal rejection); S04 frontend UI; S05 system-admin panel.

**Verification (slice-level, test file paths — all tracked in git):**
- `backend/tests/api/routes/test_teams.py` — asserts: GET /teams with no cookie → 401; GET /teams for a fresh signup → exactly 1 team with is_personal=true and role=admin; POST /teams creates non-personal team and caller is admin; POST /teams then GET /teams returns 2 teams (personal + new); POST /teams/{personal_id}/invite → 403 "Cannot invite to personal teams"; POST /teams/{non_personal_id}/invite → 501 (S03 TODO).
- `backend/tests/api/routes/test_auth.py` — extended: signup response + DB side-effect asserts personal team exists with TeamMember(role=admin); a new test `test_signup_rolls_back_on_team_failure` monkeypatches the team-creation crud to raise and asserts the user row is NOT persisted.
- `backend/tests/migrations/test_s02_migration.py` — upgrade from s01 head → assert team.name/slug/is_personal columns present with correct nullability; downgrade → assert columns removed; round-trip pre-seeds an S01-shape team row and asserts upgrade backfills (empty or sensible default) without violating NOT NULL.
- Full suite command: `cd backend && uv run pytest tests/ -v` → expect 76 + (new S02 tests) passing.

## Verification

- Runtime signals: INFO logs on `team_created` (team_id, is_personal, creator_id), `personal_team_bootstrapped` (user_id, team_id), `signup_tx_rollback` (redacted_email, stage) on transactional failure. All team identifiers are UUIDs — team names are never logged to avoid leaking collaboration graphs (MEM019-style redaction convention).
- Inspection surfaces: `team` and `team_member` tables queryable via psql; `GET /api/v1/teams` as authenticated caller's self-inspection surface; alembic_version table reveals migration state (s01_auth_and_roles vs s02_team_columns).
- Failure visibility: signup atomicity proved by a negative test that simulates a TeamMember insert failure (monkeypatching crud) and asserts the user row is also absent — future regressions would show up as an orphan user with no personal team.
- Redaction constraints: never log team name, team slug, or user email in raw form; user email already redacted via existing `_redact_email` helper in auth.py. Add inline `_redact_team_id(uuid)` only if needed — prefer bare UUIDs (non-sensitive).

## Tasks

- [x] **T01: Extend Team model + migration s02_team_columns** `est:45m`
  Promote the S01 Team stub to a real model by adding `name`, `slug`, and `is_personal` columns and shipping the corresponding Alembic migration. This task owns the DB schema change only — no endpoint work, no signup mutation. Every later task in S02 depends on these columns existing.

**Context from S01:** `team` currently has only `id` (UUID PK) + `created_at`. `team_member` (id, user_id, team_id, role enum, created_at, unique(user_id, team_id)) already exists. Migration chain head is `s01_auth_and_roles`. The migration test harness (`tests/migrations/test_s01_migration.py`) solved the AccessShareLock problem with an autouse fixture that releases the session-scoped db session + `engine.dispose()` before alembic runs (MEM016) — reuse that pattern.

**Schema decisions:**
- `name: str` NOT NULL, length 1..255, Postgres VARCHAR(255).
- `slug: str` NOT NULL, length 1..64, UNIQUE + indexed, Postgres VARCHAR(64). Server-generated, never client-supplied.
- `is_personal: bool` NOT NULL, default FALSE at DB level (so later inserts that forget it fail open in the `is not personal` direction — safer default).
- No foreign key to User (team is independent of creator; membership is via TeamMember; this matches the shared-team model where admins can be removed).

**Migration strategy (nullable→backfill→NOT NULL pattern per MEM025-style convention from S01):**
1. Add each column nullable.
2. Backfill rows: in practice, fresh DBs have 0 rows in `team` — the S01 migration created the table empty. Still, write defensive UPDATE: `UPDATE "team" SET name = 'Legacy Team ' || substr(id::text, 1, 8), slug = 'legacy-' || substr(id::text, 1, 8), is_personal = FALSE WHERE name IS NULL` so a manually seeded row doesn't break the migration.
3. ALTER COLUMN … SET NOT NULL for all three.
4. CREATE UNIQUE INDEX ix_team_slug on (slug).
5. Log backfilled rowcount via `logger.info` so ops can see migration impact.

**Downgrade:** drop the unique index, drop all three columns — no data preservation required (S02 introduces these; downgrade returns to S01 shape).

**Model update (`app/models.py`):**
- Add `name: str = Field(min_length=1, max_length=255)`, `slug: str = Field(min_length=1, max_length=64, unique=True, index=True)`, `is_personal: bool = Field(default=False)` to `Team`.
- Add a `TeamPublic(SQLModel)` response model: `id, name, slug, is_personal, created_at`.
- Add a `TeamCreate(SQLModel)` request model: `name: str = Field(min_length=1, max_length=255)`.
- Add a `TeamWithRole(SQLModel)` response model: all TeamPublic fields + `role: TeamRole` (for GET /teams which joins membership).

Do NOT change TeamMember or any other model.

**Must-haves (self-audit):**
- Model imports don't change — only Team extended; existing S01 tests still pass.
- Migration is reversible and the round-trip test below proves it.
- Slug uniqueness enforced at DB level — duplicate slug raises IntegrityError, not a silent overwrite.
  - Files: `backend/app/models.py`, `backend/app/alembic/versions/s02_team_columns.py`, `backend/tests/migrations/test_s02_migration.py`
  - Verify: cd backend && uv run alembic upgrade head && uv run pytest tests/migrations/test_s02_migration.py -v && uv run pytest tests/ -v (expect previous 76 + new migration test passing; zero regressions)

- [x] **T02: Transactional personal-team bootstrap in POST /auth/signup** `est:1h`
  Mutate the existing S01 signup endpoint so it creates User + Team(is_personal=True, name=email-derived, slug=generated) + TeamMember(role=admin) in a single SQL transaction. If any step fails, the whole thing rolls back — no orphan users, no orphan teams.

**Why this is a separate task:** keeping schema (T01) and signup flow mutation (T02) independent means T01 is committable and verifiable on its own; this task consumes T01's output. Also: the atomicity test here is the proof that delivers R003.

**Approach — new CRUD helper, NOT inline code:**
Add `crud.create_user_with_personal_team(*, session: Session, user_create: UserCreate) -> tuple[User, Team]` to `app/crud.py`. Inside:
1. Check existing user by email — if exists, raise the same HTTPException the signup endpoint currently raises (preserves S01 error-path behavior).
2. Build user via `User.model_validate(user_create, update={'hashed_password': get_password_hash(...)})` — do NOT commit yet.
3. `session.add(user)`, `session.flush()` — gets user.id without committing.
4. Build Team: `name = user.full_name or user.email.split('@')[0]` (trim to 255 if needed); `slug = _slugify(name) + '-' + short_suffix_from(user.id)` — ensures uniqueness even if two users have the same name. is_personal=True.
5. `session.add(team); session.flush()` — gets team.id.
6. `session.add(TeamMember(user_id=user.id, team_id=team.id, role=TeamRole.admin))`.
7. `session.commit()`; refresh both objects; return (user, team).

On ANY exception inside: `session.rollback()` then re-raise. The commit-only-at-end pattern guarantees atomicity. DO NOT call `crud.create_user` from within — that helper commits early and would break atomicity.

**Slug generation:**
New module-level helper `_slugify(name: str) -> str` in `app/crud.py`:
- Lowercase.
- Replace any run of non-[a-z0-9] with single `-`.
- Strip leading/trailing `-`.
- Truncate to 48 chars.
- If empty after normalization, fall back to `user`.
Then append `-` + first 8 chars of `user.id.hex` for uniqueness. Total slug ≤ 64 chars (fits the T01 column constraint).

**Signup endpoint changes (`app/api/routes/auth.py::signup`):**
- Replace the `existing = crud.get_user_by_email(...)` + `crud.create_user(...)` pair with a single call to `crud.create_user_with_personal_team(...)`.
- Handle the duplicate-email path inside the new helper (raise HTTPException(400, 'The user with this email already exists in the system')).
- Log `personal_team_bootstrapped user_id=<uuid> team_id=<uuid>` at INFO after success, in addition to the existing `signup ok <redacted_email>` log.
- On IntegrityError or any unexpected exception, log `signup_tx_rollback <redacted_email> stage=<crud|session>` at WARNING and re-raise (FastAPI → 500).

**System-admin seed consistency (`app/core/db.py::init_db`):**
The FIRST_SUPERUSER seed path calls `crud.create_user` directly and would NOT get a personal team. Update it to call `crud.create_user_with_personal_team` too (same contract — the superuser also deserves a personal team; R003 says every new user). If the helper rejects duplicate emails via HTTPException, wrap or add a `_internal` variant that raises a plain ValueError instead. Simplest: have the helper take `raise_http_on_duplicate: bool = True` and set it False for init_db.

**Failure Modes:**
| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| Postgres (insert) | rollback, re-raise as 500 | same (network timeout → SA OperationalError → rollback) | N/A (DB returns typed errors) |
| UserCreate validation | pydantic raises 422 before reaching crud | N/A | N/A |

**Load Profile:**
- Shared resources: one DB connection per request (pool already sized); signup adds 3 inserts in one tx vs 1 pre-S02 — 3x write amplification on signup only.
- Per-operation cost: 3 INSERTs + 1 SELECT (email check) per signup.
- 10x breakpoint: pool exhaustion at ~100 concurrent signups given default pool size of 5; acceptable — signup is not the hot path. Not addressed in this slice.

**Negative Tests (in test_auth.py):**
- Malformed: signup with 256-char name → 422 (pydantic validator).
- Error path: monkeypatch `crud.create_user_with_personal_team` to raise mid-transaction → assert response is 500 AND no user row persisted AND no team row with that slug persisted.
- Boundary: two users with identical full_name → both succeed, both get distinct slugs (suffix differs).
  - Files: `backend/app/crud.py`, `backend/app/api/routes/auth.py`, `backend/app/core/db.py`, `backend/tests/api/routes/test_auth.py`
  - Verify: cd backend && uv run pytest tests/api/routes/test_auth.py -v (expect all S01 tests + new signup-personal-team + rollback tests passing)

- [x] **T03: GET /teams and POST /teams endpoints + router wiring** `est:45m`
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
  - Files: `backend/app/api/routes/teams.py`, `backend/app/api/main.py`, `backend/app/crud.py`
  - Verify: cd backend && uv run pytest tests/api/routes/ -v (existing tests still pass) && python -c "from app.main import app; routes={getattr(r,'path',None) for r in app.routes}; assert '/api/v1/teams/' in routes or '/api/v1/teams' in routes"

- [x] **T04: Integration tests for teams router + self-audit full-suite run** `est:45m`
  Add `backend/tests/api/routes/test_teams.py` with the slice-level verification checks and run the full suite to prove no regressions. This task is the slice's objective stopping condition — when these tests + S01's tests + T01/T02 tests all pass, S02 is demonstrably done.

**Test inventory (test_teams.py — all against real Postgres via existing `client` + `db` fixtures):**

1. `test_get_teams_without_cookie_returns_401` — client.cookies.clear(); GET /teams → 401 'Not authenticated'.

2. `test_get_teams_after_signup_returns_only_personal_team` — signup fresh user → GET /teams returns 1 item with is_personal=true, role=admin, name matches email-local-part derivation, slug non-empty and matches [a-z0-9-]+.

3. `test_post_teams_creates_non_personal_team_with_creator_as_admin` — signup → POST /teams {name: 'Widgets Inc'} → 200, response has is_personal=false, role='admin', slug starts with 'widgets-inc-'. Next GET /teams returns 2 teams.

4. `test_post_teams_missing_name_returns_422` — POST /teams with empty body → 422.

5. `test_post_teams_name_too_long_returns_422` — 256-char name → 422.

6. `test_invite_on_personal_team_returns_403` — signup → get personal team id via GET /teams → POST /teams/{personal_id}/invite → 403 with detail 'Cannot invite to personal teams'.

7. `test_invite_on_non_personal_team_returns_501_stub` — signup, POST /teams to make a non-personal team, POST /teams/{id}/invite → 501 (stub — S03 will change to 200). This test intentionally encodes the stub contract so the S03 executor sees it flip red when they wire real invites — that's the test telling them to update this assertion, which is the expected handoff signal.

8. `test_get_teams_does_not_leak_other_users_teams` — user A signs up, creates Team X. User B signs up (gets only their own personal team). GET /teams as B → only 1 team (personal), Team X is absent.

9. `test_slug_collision_on_identical_names_still_succeeds` — two users each POST /teams {name: 'Research'} → both succeed, both get admin role, slugs differ (suffixes).

**Also extend `test_auth.py`** (T02 already adds two tests; T04 adds a third cross-check):
- `test_superuser_bootstrap_has_personal_team` — after init_db runs (which happens in the session-scoped `db` fixture), the FIRST_SUPERUSER has exactly one TeamMember row with TeamRole.admin on an is_personal=True team. Proves T02's db.py change wired correctly.

**Self-audit step (MANDATORY — done in this task before claiming slice complete):**
- Walk through each must-have from the slice goal and point at a specific test that proves it.
- Run `cd backend && uv run pytest tests/ -v` — record pass count in T04's summary.
- Diff-scan: `rg -n 'is_superuser' backend/app backend/tests` must return zero. `rg -n 'Bearer ' backend/app` must return zero (old auth fully gone).
- Confirm `.gsd/` files are not staged for git commit.

**Negative Tests focus:**
Other-user isolation (test 8) is the security-critical assertion — it proves the GET /teams query's WHERE clause isn't accidentally removed during refactors. Slug collision (test 9) proves the suffix strategy works under realistic name duplication. Stub-501 (test 7) is intentional red-flag bait for S03.
  - Files: `backend/tests/api/routes/test_teams.py`, `backend/tests/api/routes/test_auth.py`
  - Verify: cd backend && uv run pytest tests/ -v (expect all S01 tests + all T02 signup atomicity tests + all T04 team router tests passing; zero regressions) && rg -n 'is_superuser' backend/app backend/tests (expect no matches)

## Files Likely Touched

- backend/app/models.py
- backend/app/alembic/versions/s02_team_columns.py
- backend/tests/migrations/test_s02_migration.py
- backend/app/crud.py
- backend/app/api/routes/auth.py
- backend/app/core/db.py
- backend/tests/api/routes/test_auth.py
- backend/app/api/routes/teams.py
- backend/app/api/main.py
- backend/tests/api/routes/test_teams.py
