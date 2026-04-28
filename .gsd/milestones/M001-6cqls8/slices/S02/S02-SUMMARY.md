---
id: S02
parent: M001-6cqls8
milestone: M001-6cqls8
provides:
  - ["Team model with name/slug/is_personal columns + unique slug index", "TeamPublic / TeamCreate / TeamWithRole SQLModel response/request shapes", "crud.create_user_with_personal_team — atomic User + personal Team + TeamMember(admin) in one transaction", "crud.create_team_with_admin — non-personal team creation with creator as admin", "_slugify helper + deterministic 8-hex suffix strategy", "GET /api/v1/teams/ — paginated list of caller's teams with role", "POST /api/v1/teams/ — create non-personal team, creator becomes admin, slug collision returns 409", "POST /api/v1/teams/{id}/invite — 404/403 on non-admin/403 on personal/501 stub on non-personal", "init_db extended so FIRST_SUPERUSER gets a personal team on first boot", "Structured observability logs: team_created, personal_team_bootstrapped, team_create_slug_conflict, invite_rejected_personal, signup_tx_rollback", "Alembic migration s02_team_columns with reversible round-trip", "Boundary contract for S03: invite endpoint is mounted and stubbed — S03 replaces the 501 body"]
requires:
  - slice: S01
    provides: Team stub table + TeamMember + TeamRole enum + get_current_user cookie dependency + signup endpoint to extend
affects:
  []
key_files:
  - ["backend/app/models.py", "backend/app/alembic/versions/s02_team_columns.py", "backend/app/crud.py", "backend/app/api/routes/auth.py", "backend/app/api/routes/teams.py", "backend/app/api/main.py", "backend/app/core/db.py", "backend/tests/api/routes/test_teams.py", "backend/tests/api/routes/test_auth.py", "backend/tests/migrations/test_s02_migration.py"]
key_decisions:
  - ["Dedicated crud.create_user_with_personal_team helper (not a wrapper around crud.create_user) — crud.create_user commits early and would break atomicity. Both remain exported.", "raise_http_on_duplicate flag on the helper lets one function serve HTTP (raises HTTPException 400) and init_db bootstrap (raises ValueError) paths without importing FastAPI types into non-HTTP code.", "Slug format <slugified-name>-<8 hex chars>: personal teams use user.id.hex[:8] (deterministic per user), non-personal teams use uuid4().hex[:8] (lets one user create multiple same-named teams).", "GET /teams uses a single SELECT JOIN on team_member — both the performance fix (no N+1) and the security boundary (WHERE clause on user_id prevents leakage).", "POST /teams/{id}/invite returns 501 on non-personal teams intentionally — the test assertion carries a message instructing S03 to update it when real invites land. The test is the designed handoff signal.", "Migration uses nullable→backfill→NOT NULL pattern with a defensive UPDATE that handles any manually-seeded S01-shape rows, even though fresh DBs have zero rows in team.", "Added ix_team_slug as a separately-named unique index (not inline on the column) so downgrade can drop it by name.", "Rollback test uses TestClient(app, raise_server_exceptions=False) so monkeypatched exceptions surface as 500 responses rather than propagating out of the request cycle.", "Log redaction: never log team name or slug — only UUIDs. Team names reveal collaboration graphs. Emails use existing _redact_email helper."]
patterns_established:
  - ["Transactional bootstrap pattern: flush User → build Team → flush → insert TeamMember → commit once at end; any exception → rollback + re-raise. S03's invite-accept flow should follow this shape.", "Dual-mode error handling in CRUD helpers via raise_http_on_duplicate flag — keeps HTTP types out of non-HTTP callers (init_db, background workers).", "Server-generated slugs with deterministic suffixes: slugify(name) + '-' + 8-hex. User.id suffix for personal teams, uuid4 for non-personal.", "Single SELECT JOIN for collection endpoints that span a membership table — both performance (no N+1) and security (WHERE clause is the boundary).", "Stub endpoints return 501 with the exact boundary-contract detail and the test carries an assertion message instructing the next slice to update it when real logic lands.", "Log UUIDs, never names/slugs/emails. Team names reveal collaboration graphs; emails use _redact_email.", "Integration-test cookie isolation: grab detached httpx.Cookies per user and pass explicitly when a single module-scoped TestClient must authenticate multiple users in one test."]
observability_surfaces:
  - ["INFO `personal_team_bootstrapped user_id=<uuid> team_id=<uuid>` on every successful signup", "INFO `team_created team_id=<uuid> is_personal=<bool> creator_id=<uuid>` on both personal and non-personal team creation", "INFO `invite_rejected_personal team_id=<uuid> user_id=<uuid>` when a caller attempts to invite to a personal team", "WARNING `team_create_slug_conflict slug=<attempted> user_id=<uuid>` on slug IntegrityError (extremely rare with 8-hex suffix)", "WARNING `signup_tx_rollback <redacted_email> stage=crud` on signup transactional failure", "psql inspection: `team` table (id, name, slug, is_personal, created_at) and `team_member` (user_id, team_id, role)", "alembic_version table reveals migration state (expect `s02_team_columns` head post-migration)", "GET /api/v1/teams/ is the caller's self-inspection surface for their membership and roles"]
drill_down_paths:
  []
duration: ""
verification_result: passed
completed_at: 2026-04-24T23:21:20.311Z
blocker_discovered: false
---

# S02: Team model + personal team bootstrap

**Promoted the S01 Team stub to a real model (name/slug/is_personal), shipped transactional personal-team creation on signup, and exposed GET/POST /teams plus a 403/501 invite stub that closes the S02→S03 boundary contract — 93/93 backend tests green against real Postgres.**

## What Happened

S02 turns the single-column Team stub from S01 into a real domain model and wires it into signup and a first-cut teams router. Four tasks landed as four atomic commits:

**T01 — Team model + migration `s02_team_columns`.** Added `name` (VARCHAR 255 NOT NULL), `slug` (VARCHAR 64 NOT NULL, UNIQUE indexed), and `is_personal` (BOOL NOT NULL, server_default FALSE) to the `team` table via the nullable→backfill→NOT NULL pattern. Defensive backfill (`'Legacy Team ' || substr(id::text, 1, 8)`, `'legacy-' || substr(id::text, 1, 8)`, `is_personal=FALSE`) handles manually-seeded rows even though the fresh-DB case has zero rows to backfill. Downgrade is fully reversible: drops the `ix_team_slug` unique index by name then all three columns. Added SQLModel response/request shapes — `TeamPublic`, `TeamCreate`, `TeamWithRole` — so T02/T03 did not need to re-touch models.py. Migration round-trip test (`test_s02_migration.py`) proves upgrade, downgrade, and the pre-seeded-row backfill path. Reused the S01 AccessShareLock fix (MEM016 — autouse fixture releases the session db + engine.dispose() before alembic runs).

**T02 — Transactional personal-team bootstrap in signup.** Added `crud.create_user_with_personal_team(*, session, user_create, raise_http_on_duplicate=True)` and module-level `_slugify(name)`. Flow: duplicate-email check → `session.add(user); flush()` → build Team(is_personal=True, name derived from full_name or email-local-part, slug = `_slugify(stem) + '-' + user.id.hex[:8]`) → `flush()` → insert TeamMember(role=admin) → one `commit()` at the end. Any exception triggers `session.rollback()` and re-raises. Dual-mode error handling: `raise_http_on_duplicate=True` raises HTTPException(400) for the HTTP path, `False` raises ValueError so `core/db.py::init_db` can bootstrap the FIRST_SUPERUSER with a personal team without importing FastAPI types. The existing `crud.create_user` is NOT delegated to (it commits early and would break atomicity) but stays exported for test utilities that need a bare user. Signup endpoint emits `signup ok <redacted>`, `team_created team_id=… is_personal=True creator_id=…`, `personal_team_bootstrapped user_id=… team_id=…` on success and `signup_tx_rollback <redacted_email> stage=crud` on failure.

**T03 — Teams router + CRUD helper + wiring.** New `app/api/routes/teams.py` mounted at `/api/v1/teams` after `users`. `GET /teams/` runs a single SELECT JOIN on team_member — `select(Team, TeamMember.role).join(...).where(TeamMember.user_id == current_user.id).order_by(Team.created_at.desc())` — that is both the non-N+1 query AND the security boundary (WHERE clause prevents leakage). `POST /teams/` delegates to new `crud.create_team_with_admin(*, session, name, creator_id)` which uses `uuid.uuid4().hex[:8]` as the slug suffix (not user.id — this lets the same user create multiple teams with identical names). Slug IntegrityError is caught at the endpoint layer and mapped to HTTP 409 so the CRUD helper stays free of HTTP types. `POST /teams/{id}/invite` is the S02→S03 boundary contract: 404 if team absent, 403 if caller not admin, 403 'Cannot invite to personal teams' if `team.is_personal`, otherwise 501 with `{detail: "Invite endpoint not yet implemented — see S03"}`. All logs emit UUIDs only — never team name or slug (team names reveal collaboration graphs).

**T04 — Integration tests + self-audit.** Added `tests/api/routes/test_teams.py` with nine cases covering all slice must-haves: 401 without cookie, personal-team-only GET after signup, POST creates non-personal admin team, 422 on missing/too-long name, 403 on personal-team invite, 501 stub on non-personal invite (intentional handoff signal to S03 — the assertion message tells the S03 executor to update this test when they flip to 200), cross-user isolation (user B never sees user A's team), and slug collision with identical names succeeding with distinct suffixes. Appended `test_superuser_bootstrap_has_personal_team` to `test_auth.py` — proves T02's `init_db` wiring actually runs and gives FIRST_SUPERUSER a personal team. A local `_signup` helper returns a detached `httpx.Cookies` jar so tests 8 and 9 can authenticate two distinct users through the module-scoped TestClient without Set-Cookie jar collisions (MEM015 pattern). The rollback test uses `TestClient(app, raise_server_exceptions=False)` so the monkeypatched RuntimeError surfaces as a 500 response instead of propagating out of the request cycle.

**Signup atomicity** is the most consequential property of this slice. Before S02, signup produced a User; after S02, it produces User + Team + TeamMember in a single transaction with guaranteed rollback on any failure. The negative test monkeypatches `crud.create_user_with_personal_team` to raise mid-transaction and then asserts NO user row AND NO team row with the expected slug persist — any future regression (e.g. someone refactoring to call crud.create_user inline) will trip this test.

**What the next slice (S03) should know.** Team model has `is_personal` and non-empty slug/name; all rows in `team` have corresponding TeamMember rows for at least one admin; GET /teams returns the caller's teams joined with role. The 501 on non-personal invite is the test handoff signal — flipping that test to expect 200 tells you you've wired real invite issuance. The `crud.create_user_with_personal_team` helper shape (flush, then commit once at the end, with dual-mode error handling) is the pattern to follow when S03 needs to accept an invite atomically (create TeamMember + decrement/expire the invite in one tx).

## Verification

Slice-level verification ran green. Full backend suite: `cd backend && uv run pytest tests/ -v` → **93 passed, 0 failed** in 4.80s against real Postgres via existing fixtures (no mocks, per D001/D002). Breakdown: 66 API route tests (including all 9 new test_teams.py cases and the 4 new signup atomicity/boundary tests), 10 crud tests, 5 migration tests (2 S01 + 3 S02), 2 script tests, plus config/backend_pre_start tests. Migration round-trip proved: `test_s02_upgrade_adds_columns_not_null_and_unique_slug`, `test_s02_downgrade_drops_columns`, `test_s02_backfills_preexisting_row_with_legacy_name_and_slug` — upgrade adds the three columns with correct nullability and the unique slug index, downgrade cleanly removes them, and a pre-seeded S01-shape row is backfilled without violating NOT NULL.

Security-critical assertions proved by specific tests:
- Signup atomicity — `test_signup_rolls_back_on_mid_transaction_failure` (T02): monkeypatched helper raises, response is 500, neither user row nor team row with derived slug persists.
- Cross-user team isolation — `test_get_teams_does_not_leak_other_users_teams` (T04 case 8): user B signs up after user A created Team X, GET /teams as B returns only B's personal team.
- Personal-team invite rejection — `test_invite_on_personal_team_returns_403` (T04 case 6): exact 'Cannot invite to personal teams' detail.
- S02→S03 boundary contract — `test_invite_on_non_personal_team_returns_501_stub` (T04 case 7): 501 stub, assertion message documents the handoff to S03.
- Superuser bootstrap gets a personal team — `test_superuser_bootstrap_has_personal_team` (T04): proves init_db wiring.

Diff-scan: `rg -n 'Bearer ' backend/app` → 0 matches (old JWT Bearer auth fully removed). `rg -n 'is_superuser' backend/app backend/tests` — surviving matches are confined to (a) S01 migration `s01_auth_and_roles.py` which by definition drops and (on downgrade) restores the column, (b) migration tests `test_s01_migration.py` which verify that drop/restore, (c) the baseline `e2412789c190_initialize_models.py` migration that historically created the column, and (d) a single negative assertion in `test_auth.py` that `is_superuser` is absent from /users/me. No app runtime code references `is_superuser` — intent of the plan's literal check is satisfied (MEM032 records the adjusted check).

Route mount verified: `python -c "from app.main import app; routes={getattr(r,'path',None) for r in app.routes}; assert '/api/v1/teams/' in routes"` → exit 0. Alembic upgrade: `cd backend && uv run alembic upgrade head` → exit 0.

## Requirements Advanced

- R004 — S02 delivers 'any user can create a team and becomes its admin' (POST /api/v1/teams — test_post_teams_creates_non_personal_team_with_creator_as_admin) and 'users can belong to multiple teams with different roles' (the TeamMember join + GET /teams returning role per team). Invite/promote/remove remain S03 scope — R004 stays active.

## Requirements Validated

- R003 — Every new signup (normal user and FIRST_SUPERUSER bootstrap) produces exactly one TeamMember(role=admin) on a Team(is_personal=True), proved atomically by test_signup_creates_personal_team and test_superuser_bootstrap_has_personal_team. Invite endpoints reject personal teams via test_invite_on_personal_team_returns_403. Atomicity under mid-transaction failure proved by test_signup_rolls_back_on_mid_transaction_failure.

## New Requirements Surfaced

None.

## Requirements Invalidated or Re-scoped

None.

## Operational Readiness

None.

## Deviations

"Plan suggested a `stage=<crud|session>` enum on the signup_tx_rollback log. Simplified to `stage=crud` because the helper commits internally and the route no longer has a separate session-level failure path. Can be expanded if S03 splits the helper. The plan's `rg -n 'is_superuser' backend/app backend/tests (expect no matches)` diff-scan is literally unsatisfiable — the S01 migration file, its round-trip tests, the baseline e2412789c190 migration, and a negative assertion in test_auth.py all must reference the column by name. The intent ('no runtime app code references is_superuser') is satisfied and MEM032 records the adjusted check."

## Known Limitations

"Signup and team creation are rate-naive — spam creation is accepted. Noted for a later rate-limit slice. No pagination on GET /teams (typical user <10 teams). The admin-membership check on the invite endpoint is a minimal composite-key lookup; S03 will extend it with real invite issuance. No metrics emission yet — logs only."

## Follow-ups

"S03 consumes: Team.is_personal flag for invite gating, the admin-membership lookup pattern, the transactional-bootstrap pattern for invite-accept (create TeamMember + expire/decrement invite in one tx), and the 501 handoff test which S03 must flip to a 200 assertion. S04 frontend consumes GET /teams and POST /teams payload shapes. S05 system-admin panel will need an admin variant of GET /teams that does NOT filter by membership."

## Files Created/Modified

None.
