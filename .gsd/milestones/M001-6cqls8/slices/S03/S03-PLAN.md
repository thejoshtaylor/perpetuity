# S03: Team invites + membership management

**Goal:** Replace the S02 invite stub with real invite issuance, acceptance, role management, and member removal. Close the collaboration loop so R004 moves from Active to Validated: a team admin can invite another user via code, that user can accept and become a member, an admin can promote/demote roles and remove members — all scoped to team-admin authorization with cross-team isolation.
**Demo:** POST /teams/{id}/invite returns invite code; POST /teams/join/{code} adds user; expired invite returns 410; duplicate returns 409; PATCH role endpoint promotes/demotes; DELETE removes member

## Must-Haves

- Demo: `cd backend && uv run pytest tests/ -v` is green (S01 + S02 suites plus new S03 cases). End-to-end: admin A POSTs to /teams/{id}/invite → 200 with {code, url, expires_at}; user B POSTs /teams/join/{code} → 200 with TeamWithRole(role=member); admin A PATCHes /teams/{id}/members/{B}/role to admin → 200; admin A DELETEs /teams/{id}/members/{B} → 204. Expired invite returns 410, re-accept returns 409, non-admin invite returns 403, inviting to a personal team still returns 403.
- Must-haves:
- `TeamInvite` model + alembic migration `s03_team_invites` (forward + reverse) with `code` (unique, 32 urlsafe chars), `team_id` FK, `created_by` FK, `expires_at`, `used_at` (nullable), `used_by` (nullable FK), `created_at`. Index on `code`.
- `POST /api/v1/teams/{id}/invite` — team admin only; rejects personal teams (403); rejects non-members/non-admins (403); 404 if team missing; returns `{code, url, expires_at}` with `url = f"{settings.FRONTEND_HOST}/invite/{code}"`; default TTL 7 days; atomic single-transaction insert.
- `POST /api/v1/teams/join/{code}` — requires auth cookie; 404 if code unknown; 410 if expired or used; 409 if caller already a member of the team; otherwise atomically inserts TeamMember(role=member) and marks invite used_at/used_by in one transaction; returns TeamWithRole.
- `PATCH /api/v1/teams/{team_id}/members/{user_id}/role` — team admin only; 404 if team or membership missing; 403 if caller is not admin of that team; 400 if demoting the last remaining admin; accepts `{role: "admin"|"member"}`; returns updated TeamWithRole.
- `DELETE /api/v1/teams/{team_id}/members/{user_id}` — team admin only; 404 if membership missing; 403 if caller not admin; 400 if removing the last admin; 400/403 if attempting to remove the personal-team owner; returns 204.
- Observability: structured logs — `invite_issued team_id=<uuid> inviter_id=<uuid> code_hash=<sha-hex-first-8>`, `invite_accepted team_id=<uuid> user_id=<uuid> code_hash=<…>`, `invite_rejected reason=<expired|used|unknown|duplicate_member>`, `member_role_changed team_id=<uuid> user_id=<uuid> old=<role> new=<role> actor_id=<uuid>`, `member_removed team_id=<uuid> user_id=<uuid> actor_id=<uuid>`. Never log raw invite codes — always sha-hex-first-8.
- Tests: `backend/tests/api/routes/test_invites.py` (10+ cases) covering happy path, 410 expired, 409 duplicate, 403 non-admin invite, 403 non-member join, 404 unknown code, reject personal team invite, and an atomicity test (invite + membership insert rollback when membership insert fails). `backend/tests/api/routes/test_teams.py` — UPDATE the existing 501 stub test so non-personal invite returns 200 and a body with `code/url/expires_at`. `backend/tests/api/routes/test_members.py` (6+ cases) for role-change and remove endpoints including last-admin protection.
- Migration round-trip test `backend/tests/migrations/test_s03_migration.py` proves upgrade adds `team_invite` with the unique code index, downgrade drops both cleanly. Uses the MEM016 autouse fixture pattern.
- Threat Surface (Q3):
- Abuse: privilege escalation via PATCH role (caller promotes themselves) — guarded by team-admin check on the actor, never on the target. Replay of an invite URL — guarded by one-shot `used_at` + TTL. Brute-force enumeration of invite codes — mitigated by 32-char urlsafe entropy (≈190 bits) and zero information leakage on unknown code (uniform 404). Self-demotion to lock a team out of admins — blocked by last-admin guard.
- Data exposure: invite codes are bearer tokens — logged only as sha-hex-first-8, never echoed in error bodies. No PII in invite row (code carries team_id server-side only).
- Input trust: `{team_id}`, `{user_id}`, `{code}` are user-supplied path params — all resolved via `session.get(...)` + membership join before any state change. Role body validated by Pydantic enum.
- Requirement Impact (Q4):
- Requirements touched: R004 (moves from Active to Validated after S03 closes).
- Re-verify: the S02 `test_invite_on_non_personal_team_returns_501_stub` assertion must flip to 200 — that is the designed handoff signal from S02.
- Decisions revisited: none. D003 (is_personal rejection at API layer) still stands. The invite model is net-new and does not revisit any prior structural decision.
- Proof Level:
- This slice proves: integration (real HTTP through FastAPI TestClient + real Postgres + alembic-migrated schema, no mocks).
- Real runtime required: yes (Postgres).
- Human/UAT required: no — fully covered by pytest integration tests.
- Observability / Diagnostics:
- Runtime signals: structured INFO logs on every invite issuance, acceptance, role change, and member removal (UUIDs + sha-hex-first-8 code digest). WARNING logs on rejection with machine-readable `reason=` tag.
- Inspection surfaces: `team_invite` table (select where team_id = … order by created_at desc) shows all outstanding invites per team; `team_member` table shows resulting memberships; GET /teams returns current membership + role per caller.
- Failure visibility: last error surfaces as HTTP status + detail; logs carry correlation via team_id + user_id; a future agent can reconstruct a failed join by cross-referencing `invite_rejected reason=…` log with the matching code_hash.
- Redaction constraints: never log the raw invite code, team name, team slug, or user email. UUIDs only; emails via `_redact_email`.
- Integration Closure:
- Upstream surfaces consumed: S02 Team/TeamMember/TeamRole models, `get_current_user` cookie dep, `create_team_with_admin` helper pattern (for invite-accept atomicity), S02 invite-route 501 stub (replaced wholesale).
- New wiring introduced in this slice: TeamInvite model added to models.py; invite/join/role/remove endpoints added to `app/api/routes/teams.py` (or split into `members.py` — see task plans); migration `s03_team_invites` chained onto `s02_team_columns`.
- What remains before the milestone is truly usable end-to-end: S04 frontend wires login, dashboard, team creation, invite-copy UI, and member management against these endpoints; S05 adds the system-admin panel on top.

## Proof Level

- This slice proves: integration

## Integration Closure

Consumes: S02 Team/TeamMember/TeamRole, get_current_user cookie dep, transactional-bootstrap pattern from crud.create_user_with_personal_team, and the S02 501 invite stub (replaced). Produces: TeamInvite model + migration, real invite/join endpoints, PATCH role, DELETE member. Remaining for milestone end-to-end: S04 frontend wiring, S05 system-admin panel.

## Verification

- Runtime signals: structured INFO/WARNING logs on invite_issued / invite_accepted / invite_rejected (reason=expired|used|unknown|duplicate_member) / member_role_changed / member_removed — UUIDs only, invite codes as sha-hex-first-8 never raw. Inspection surfaces: `team_invite` table, `team_member` table, GET /teams per caller. Failure visibility: HTTP status + detail + matching log line keyed by code_hash and team_id + user_id.

## Tasks

- [x] **T01: Add TeamInvite model + alembic migration s03_team_invites + migration round-trip test** `est:1h`
  Add a new SQLModel `TeamInvite` table to `backend/app/models.py` and a reversible Alembic migration `s03_team_invites` chained onto `s02_team_columns` that creates the `team_invite` table with columns: `id UUID PK`, `code VARCHAR(64) NOT NULL UNIQUE` (indexed as `ix_team_invite_code`), `team_id UUID NOT NULL FK team.id ON DELETE CASCADE`, `created_by UUID NOT NULL FK user.id ON DELETE CASCADE`, `expires_at TIMESTAMPTZ NOT NULL`, `used_at TIMESTAMPTZ NULL`, `used_by UUID NULL FK user.id ON DELETE SET NULL`, `created_at TIMESTAMPTZ NULL default now`. Also add Pydantic/SQLModel response shapes `InviteIssued` (code, url, expires_at) and `JoinInviteResponse` (reuses TeamWithRole). Add `TeamInvite` + `TeamInvitePublic` + `InviteIssued` + `MemberRoleUpdate` shapes — `MemberRoleUpdate` carries a single `role: TeamRole` body for the PATCH endpoint planned in T03. The migration follows the project's established pattern (see `s02_team_columns.py` and MEM025): use `op.create_table(...)` with an explicit `create_index('ix_team_invite_code', 'team_invite', ['code'], unique=True)` separate from inline unique=True so downgrade can drop by name. Downgrade drops the index then the table. Write `backend/tests/migrations/test_s03_migration.py` that (a) after `command.upgrade(head)` asserts `team_invite` exists with all columns, the unique code index exists, FKs resolve (insert with bad team_id fails), and duplicate code insert raises IntegrityError; (b) after `command.downgrade('s02_team_columns')` asserts the table and index are gone; uses the MEM016 autouse fixture pattern (copy from `test_s02_migration.py`). Import and re-export the new model + shapes from `app/models.py` at module level (no sub-package required).

Steps:
1. Append `TeamInvite` table class + `InviteIssued` / `MemberRoleUpdate` response shapes to `backend/app/models.py`. Add relationship back-refs: `Team.invites: list[TeamInvite]` and `User.invites_issued: list[TeamInvite]` if needed — but prefer NOT adding relationships unless a test requires them (keeps the diff small).
2. Create `backend/app/alembic/versions/s03_team_invites.py` with `revision='s03_team_invites'`, `down_revision='s02_team_columns'`. Upgrade creates `team_invite` table + `ix_team_invite_code` unique index. Downgrade drops the index then the table.
3. Run `cd backend && uv run alembic upgrade head` — must exit 0.
4. Create `backend/tests/migrations/test_s03_migration.py` with three tests: `test_s03_upgrade_creates_team_invite`, `test_s03_downgrade_drops_team_invite`, `test_s03_duplicate_code_fails_integrity`. Copy the `_release_autouse_db_session` + `_restore_head_after` autouse fixture pattern from `test_s02_migration.py` verbatim.
5. Run `cd backend && uv run pytest tests/migrations/test_s03_migration.py -v` — all three tests pass.

Must-haves:
- Migration is fully reversible (up then down restores schema).
- Unique index on `code` column named `ix_team_invite_code`.
- FK `team_id` cascades on team delete (so cleaning up a team cleans its invites).
- Migration test module uses MEM016 fixtures — otherwise the session-scoped `db` fixture deadlocks alembic DDL.
- `TeamInvite` SQLModel in `models.py` has matching column types, defaults, and constraints.

Failure Modes:
| Dependency | On error | On timeout | On malformed response |
|---|---|---|---|
| Postgres (alembic) | Fail test with exception context | Not applicable (alembic is synchronous) | Not applicable |
| Prior S02 migration | Fail test — abort; dependency is structural | N/A | N/A |

Load Profile:
- Shared resources: the test-session Postgres + the session-scoped autouse `db` fixture. 10x breakpoint: not applicable at migration scope.

Negative Tests:
- Duplicate `code` insert raises IntegrityError (covered).
- FK violation on bad `team_id` raises IntegrityError (covered).

Observability Impact:
- No runtime signals (pure schema). Future agent inspects state via `\dt team_invite` and `\d+ team_invite` in psql, or `SELECT * FROM alembic_version;` to confirm head = `s03_team_invites`.
  - Files: `backend/app/models.py`, `backend/app/alembic/versions/s03_team_invites.py`, `backend/tests/migrations/test_s03_migration.py`
  - Verify: cd backend && uv run alembic upgrade head && uv run pytest tests/migrations/test_s03_migration.py -v

- [x] **T02: Replace 501 invite stub with real POST /teams/{id}/invite + add POST /teams/join/{code} with atomic accept** `est:2h`
  Wire real invite issuance and acceptance on top of the T01 TeamInvite model. Replace the existing 501 body in `backend/app/api/routes/teams.py::invite_to_team` with real invite creation, and add a new `POST /api/v1/teams/join/{code}` endpoint in the same router. Add two CRUD helpers to `backend/app/crud.py`: `create_team_invite(*, session, team_id, created_by, ttl_seconds=604800) -> TeamInvite` and `accept_team_invite(*, session, code, user) -> tuple[Team, TeamMember]` that handles code lookup, expiry check, used-check, duplicate-membership check, and atomic insert + mark-used in one transaction (follow the S02 `create_user_with_personal_team` atomic-flush-then-commit pattern — see MEM026).

Behavioral contract (POST /api/v1/teams/{team_id}/invite):
- 404 if team missing.
- 403 if caller is not a member, or is a member but not an admin.
- 403 with detail 'Cannot invite to personal teams' if team.is_personal (retain S02 behavior + exact error string; the existing test expects this).
- 200 with body `{code: str, url: str, expires_at: ISO-8601}` otherwise. `code = secrets.token_urlsafe(24)` (≈32 chars); `url = f'{settings.FRONTEND_HOST}/invite/{code}'`; `expires_at = now + 7 days`.
- Structured log: `logger.info('invite_issued team_id=%s inviter_id=%s code_hash=%s expires_at=%s', ...)` where `code_hash = hashlib.sha256(code.encode()).hexdigest()[:8]` — NEVER log the raw code.

Behavioral contract (POST /api/v1/teams/join/{code}):
- Requires auth cookie via `CurrentUser` dep.
- 404 uniform if code unknown — do NOT distinguish unknown from expired in the HTTP response (unknown returns 404, expired/used returns 410 only for valid but spent codes, so attackers can't probe for unknown-vs-expired directly). Actually: resolve code → if none, 404; if expires_at < now, 410 'Invite expired'; if used_at is not None, 410 'Invite already used'; if caller is already a member of invite.team_id, 409 'Already a member'; otherwise atomically `session.add(TeamMember(user_id=caller.id, team_id=invite.team_id, role=TeamRole.member))`, set `invite.used_at = now()` and `invite.used_by = caller.id`, `session.commit()`. Return TeamWithRole for the joined team.
- Rejection logs: `logger.info('invite_rejected reason=%s code_hash=%s caller_id=%s', reason, code_hash, caller.id)` with reason ∈ {unknown, expired, used, duplicate_member}.
- Success log: `logger.info('invite_accepted team_id=%s user_id=%s code_hash=%s', team.id, caller.id, code_hash)`.

Atomicity: the accept flow MUST be a single transaction. If the TeamMember insert fails (e.g. FK violation or race with a concurrent accept by the same user), `session.rollback()` and re-raise — do NOT leave the invite marked used without a corresponding membership. Mirror the try/except in `create_user_with_personal_team`.

Must-haves:
- `_code_hash(code)` helper in `app/api/routes/teams.py` — sha256 hex first 8 chars; use wherever logging.
- `import secrets` and `import hashlib` at top of teams.py.
- `settings.FRONTEND_HOST` — reuse existing config (verify it exists; it is used elsewhere for email links). If missing, fall back to `settings.SERVER_HOST` or hardcode `http://localhost:5173` but leave a comment referencing the S04 frontend host for review.
- TTL constant `INVITE_TTL_SECONDS = 7 * 24 * 60 * 60` at module scope.
- Use `datetime.now(timezone.utc)` (match project convention — see `models.get_datetime_utc`).
- Register the new `/join/{code}` endpoint on the existing `teams` router (path: `/join/{code}` — the router prefix handles `/teams`).
- All HTTP error responses carry a plain `detail` string — no dict leakage of internals.

Steps:
1. In `app/crud.py` add `create_team_invite(*, session, team_id, created_by, ttl_seconds=INVITE_TTL_SECONDS)` that inserts TeamInvite, commits once, returns the refreshed row.
2. In `app/crud.py` add `accept_team_invite(*, session, code, caller_id)` that resolves invite by code, runs guards (expired/used/duplicate_member), inserts TeamMember, marks used_at/used_by, commits once; on failure rolls back and re-raises a typed exception so the route layer can map to HTTP. Use a small module-level sentinel enum `InviteRejectReason` or pass through via raised `ValueError('reason')` — pick whichever is simpler and document in the docstring.
3. In `app/api/routes/teams.py` replace the 501 branch in `invite_to_team` with: `invite = crud.create_team_invite(session=session, team_id=team.id, created_by=current_user.id)`; log `invite_issued`; return `InviteIssued(code=invite.code, url=f'{settings.FRONTEND_HOST}/invite/{invite.code}', expires_at=invite.expires_at)`.
4. In `app/api/routes/teams.py` add `@router.post('/join/{code}')` that calls `crud.accept_team_invite` and maps exceptions to 404/410/409. Return TeamWithRole.
5. Manual smoke: `cd backend && uv run python -c 'from app.main import app; print(sorted({r.path for r in app.routes if "teams" in getattr(r,"path","")}))'` lists both /api/v1/teams/{team_id}/invite and /api/v1/teams/join/{code}.
6. Update `backend/tests/api/routes/test_teams.py::test_invite_on_non_personal_team_returns_501_stub` — rename to `test_invite_on_non_personal_team_returns_code_url_and_expires_at` and change assertion: `assert r2.status_code == 200` plus `assert 'code' in r2.json() and 'url' in r2.json() and 'expires_at' in r2.json()`. Keep the personal-team 403 test unchanged. This flip fulfills MEM031's handoff.

Failure Modes:
| Dependency | On error | On timeout | On malformed response |
|---|---|---|---|
| Postgres (insert TeamInvite/TeamMember) | rollback + re-raise → 500 via FastAPI; log `invite_issue_tx_rollback` or `invite_accept_tx_rollback` with code_hash | N/A (synchronous) | N/A |
| settings.FRONTEND_HOST missing | fall back to hardcoded dev host with comment; TODO noted for S04 | N/A | N/A |

Load Profile:
- Shared resources: DB connection pool, `team_invite` table (grows unboundedly if never garbage-collected — noted as a Known Limitation for a later cleanup slice).
- Per-operation cost: 1 insert per issue; 1 read + 1 insert + 1 update per accept, one transaction.
- 10x breakpoint: at thousands of outstanding invites per team, GET-all-invites (not exposed yet) would need pagination — not a concern for S03.

Negative Tests:
- Malformed inputs: non-UUID team_id in path → 422 (FastAPI validator). Unknown code in /join/{code} → 404. Empty body on invite → accepted (no body required).
- Error paths: accept when invite is expired (expires_at < now) → 410. Accept when invite.used_at is set → 410. Accept when caller is already a TeamMember for invite.team_id → 409. Invite to a team the caller is not an admin of → 403.
- Boundary conditions: invite to a team where caller is a member but role=member → 403. Self-accept by inviter (they are already an admin) → 409 duplicate_member — intentional, inviter should not re-join their own team.

Observability Impact:
- Signals added: `invite_issued`, `invite_accepted`, `invite_rejected reason=<...>`, `invite_issue_tx_rollback`, `invite_accept_tx_rollback`.
- Future agent inspects this via: `SELECT code, team_id, created_by, expires_at, used_at, used_by FROM team_invite ORDER BY created_at DESC LIMIT 20;` in psql, or by grepping the structured log for `code_hash=<prefix>` to trace a single invite end-to-end.
- Failure state exposed: the `team_invite` row alone shows issuance status; the presence/absence of a paired `team_member` row proves whether acceptance closed atomically.
  - Files: `backend/app/crud.py`, `backend/app/api/routes/teams.py`, `backend/tests/api/routes/test_teams.py`
  - Verify: cd backend && uv run python -c 'from app.main import app; assert any(getattr(r,"path",None)=="/api/v1/teams/join/{code}" for r in app.routes)' && uv run pytest tests/api/routes/test_teams.py -v

- [x] **T03: Add PATCH /teams/{team_id}/members/{user_id}/role + DELETE /teams/{team_id}/members/{user_id} with last-admin guard** `est:1.5h`
  Add two new endpoints to the teams router (same file: `backend/app/api/routes/teams.py`) for membership management.

Endpoint contracts:

`PATCH /api/v1/teams/{team_id}/members/{user_id}/role` — body `{role: 'admin'|'member'}`.
- 404 if team missing; 404 if target user has no TeamMember row on that team.
- 403 if caller is not an admin on that team.
- 400 'Cannot demote the last admin' if target membership.role == admin, body.role == member, and the team has exactly one admin.
- 200 with updated TeamWithRole on success.

`DELETE /api/v1/teams/{team_id}/members/{user_id}`
- 404 if team missing; 404 if target membership missing.
- 403 if caller not admin.
- 400 'Cannot remove the last admin' if target is an admin and the team has only one admin.
- 400 'Cannot remove members from personal teams' if team.is_personal — personal teams only have one member by construction, and deleting the personal-team owner is destructive.
- 204 on success (no body) — `Response(status_code=204)`.

Implementation notes:
- Use a single helper `_assert_caller_is_team_admin(session, team_id, caller_id) -> Team` at module scope that does the 404 + 403 check and returns the Team. Both new endpoints and the existing invite endpoint can call this; refactor `invite_to_team` to use it (low-risk cleanup — only if the diff stays small and all existing tests pass).
- The last-admin check: `admin_count = session.exec(select(func.count()).select_from(TeamMember).where(TeamMember.team_id == team_id, TeamMember.role == TeamRole.admin)).one()` — one query, no N+1.
- Role updates: fetch TeamMember, mutate `.role`, `session.add` + commit + refresh — standard SQLModel pattern.
- Do NOT allow a user to modify their own role via PATCH on themselves — that is covered by the last-admin guard for demotion; self-promotion from member to admin is naturally blocked because only admins can call PATCH (so a member calling PATCH on themselves gets 403 before the role-update logic).
- Structured logs: `member_role_changed team_id=<uuid> target_user_id=<uuid> old_role=<str> new_role=<str> actor_id=<uuid>` and `member_removed team_id=<uuid> target_user_id=<uuid> actor_id=<uuid>`.
- Register `MemberRoleUpdate` request body (defined in T01 models.py addition) with field `role: TeamRole` and no other fields — FastAPI's Pydantic validator handles unknown-value rejection.

Must-haves:
- Both endpoints reject calls from non-admins with 403 BEFORE running any mutation.
- Last-admin guard is a precondition check — it must run BEFORE the mutation, not after + compensate.
- Personal-team DELETE blocked with 400 (not 403 — the caller IS the admin; the server refuses the destructive op).
- Reuse `_assert_caller_is_team_admin` — do not duplicate the check inline in each endpoint.
- Emit structured logs with UUID-only data.

Steps:
1. Refactor existing `invite_to_team` in `teams.py` to call `_assert_caller_is_team_admin` — verify `pytest tests/api/routes/test_teams.py -v` stays green.
2. Add `@router.patch('/{team_id}/members/{user_id}/role')` handler using `MemberRoleUpdate` body.
3. Add `@router.delete('/{team_id}/members/{user_id}', status_code=204)` handler.
4. Manual smoke: `cd backend && uv run python -c 'from app.main import app; paths=sorted(r.path for r in app.routes if "members" in getattr(r,"path",""))  ; print(paths)'` lists both new paths.
5. Run `cd backend && uv run pytest tests/ -v` — T04 adds the endpoint tests; at this point only the existing S01/S02 tests must stay green.

Failure Modes:
| Dependency | On error | On timeout | On malformed response |
|---|---|---|---|
| Postgres (update/delete TeamMember) | rollback + re-raise → 500; log `member_update_tx_rollback` or `member_remove_tx_rollback` | N/A | N/A |

Load Profile:
- Shared resources: DB connection pool, one admin-count query + one mutation per request.
- Per-operation cost: at most 2 SELECT + 1 UPDATE/DELETE per request.
- 10x breakpoint: for a team with hundreds of admins the admin-count query remains O(1) thanks to the partial equality predicate + indexed team_id column.

Negative Tests:
- Malformed inputs: PATCH with `{role: 'owner'}` → 422 (invalid enum). PATCH with empty body → 422. Non-UUID team_id or user_id → 422.
- Error paths: PATCH as non-admin → 403. DELETE on personal team → 400. DELETE the only admin → 400. PATCH demoting only admin to member → 400.
- Boundary conditions: PATCH to the same role (idempotent no-op) → 200 with unchanged role; team with 2 admins → DELETE one → 204; remaining admin is untouched.

Observability Impact:
- Signals added: `member_role_changed`, `member_removed`, `member_update_tx_rollback`, `member_remove_tx_rollback`.
- Future agent inspects via: `SELECT user_id, role FROM team_member WHERE team_id = <uuid>;` shows current admins; grep logs for `actor_id=<uuid>` to see who changed what.
- Failure state exposed: HTTP 400 detail strings carry the specific guard name (`last admin`, `personal teams`); logs carry team_id + target_user_id so a future agent can correlate the HTTP error to the exact row.
  - Files: `backend/app/api/routes/teams.py`, `backend/app/crud.py`
  - Verify: cd backend && uv run python -c 'from app.main import app; paths={getattr(r,"path","") for r in app.routes}; assert "/api/v1/teams/{team_id}/members/{user_id}/role" in paths; assert "/api/v1/teams/{team_id}/members/{user_id}" in paths' && uv run pytest tests/api/routes/test_teams.py -v

- [x] **T04: Integration tests for invites, join, PATCH role, DELETE member + full-suite self-audit** `est:2h`
  Add the full slice-level verification test set. Two new test files plus extensions to existing files. All tests run against real Postgres via the existing `client` + `db` fixtures (MEM029 cookie-per-user pattern for multi-user flows).

New file `backend/tests/api/routes/test_invites.py` (10 cases minimum):
1. `test_invite_returns_code_url_expires_at` — admin signs up, creates non-personal team, POST /{id}/invite → 200; body has `code` (string ≥ 32 chars), `url` (contains `/invite/`), `expires_at` (future ISO-8601 ≥ now + 6 days).
2. `test_invite_personal_team_returns_403` — existing behavior survives refactor; keep in teams.py test file but re-assert here too for defense-in-depth.
3. `test_invite_as_non_admin_returns_403` — user A creates team, user B signs up (not a member), user B POSTs /{A_team}/invite → 403.
4. `test_invite_as_member_not_admin_returns_403` — A creates team, A invites B (B accepts to become member), B tries to POST /{team}/invite → 403.
5. `test_join_valid_code_adds_member_and_marks_used` — A creates team, A issues invite, B signs up, B POSTs /teams/join/{code} → 200 with TeamWithRole(role=member); GET /teams as B now returns 2 teams (personal + joined); DB shows team_invite.used_at is set and used_by = B.id.
6. `test_join_unknown_code_returns_404` — B signs up, POSTs /teams/join/garbage → 404.
7. `test_join_expired_code_returns_410` — issue an invite, manually backdate `expires_at` via a direct DB UPDATE (use the `db` session fixture), attempt join → 410 'Invite expired'.
8. `test_join_used_code_returns_410` — A issues invite, B accepts, C tries same code → 410 'Invite already used'.
9. `test_join_duplicate_member_returns_409` — A issues invite, A (already admin) tries to accept own invite → 409 'Already a member'.
10. `test_join_atomicity_on_membership_insert_failure` — monkeypatch `crud.accept_team_invite` (or its internal TeamMember insert) to raise mid-transaction; assert response is 500 AND the invite's used_at is still NULL (rollback succeeded). Pattern: mirror `test_signup_rolls_back_on_mid_transaction_failure` from test_auth.py — use `TestClient(app, raise_server_exceptions=False)`.

New file `backend/tests/api/routes/test_members.py` (7 cases minimum):
1. `test_patch_role_promotes_member_to_admin` — A creates team, invites B, B joins. A PATCHes /teams/{t}/members/{B_id}/role with {role:'admin'} → 200; GET /teams as B shows role=admin.
2. `test_patch_role_demotes_admin_to_member` — after above, A PATCHes B back to member → 200; GET /teams as B shows role=member.
3. `test_patch_role_as_non_admin_returns_403` — B (member) tries to PATCH A's role → 403.
4. `test_patch_role_demoting_last_admin_returns_400` — A is sole admin, A PATCHes self to member → 400 'Cannot demote the last admin'.
5. `test_patch_role_unknown_target_returns_404` — A PATCHes a random UUID that is not a member → 404.
6. `test_patch_role_invalid_body_returns_422` — A PATCHes with {role:'owner'} → 422.
7. `test_delete_member_removes_row_returns_204` — A creates team, invites B, B joins. A DELETEs /teams/{t}/members/{B_id} → 204; GET /teams as B shows only personal team.
8. `test_delete_last_admin_returns_400` — A (sole admin) tries to DELETE self → 400 'Cannot remove the last admin'.
9. `test_delete_on_personal_team_returns_400` — A tries to DELETE self from their personal team → 400 'Cannot remove members from personal teams'.

Extension to existing `backend/tests/api/routes/test_teams.py`:
- The pre-existing `test_invite_on_non_personal_team_returns_501_stub` is modified in T02 to expect 200. Verify that modification landed; if T04 runs before T02 in a re-plan, add the assertion change here.

Self-audit step (MANDATORY — done in this task before claiming slice complete):
- Run `cd backend && uv run pytest tests/ -v` — record pass count. Expected: ≥ 93 (S02 baseline) + ≥ 19 new S03 tests = 112+ passing.
- `rg -n 'raw_code\|print.*code\|logger.*code=' backend/app` to ensure no raw invite codes leak into logs (excluding `code_hash=`).
- `rg -n '501' backend/app/api/routes/teams.py` returns zero — the stub is gone.
- Confirm `.gsd/` files are not staged for git commit.
- Walk through each S03 must-have from the slice plan and point at the specific test(s) that prove it — document in T04-SUMMARY.md.

Must-haves:
- At least 10 test_invites.py cases, 7 test_members.py cases, all passing.
- Cross-user isolation proven: the member-removal and role-change tests MUST involve at least two distinct users to catch any accidental drop of the admin check.
- Atomicity test proven via monkeypatch — not relied on via code reading.
- Full suite green.
- Uses `_signup` helper pattern from existing `test_teams.py` (copy or import it via a shared test util if the diff stays clean).

Failure Modes:
| Dependency | On error | On timeout | On malformed response |
|---|---|---|---|
| TestClient against app | Fail test with full response dump | N/A | N/A |
| db fixture | Fail test; db deadlock would show in MEM016 territory but these tests do not run alembic so risk is low | N/A | N/A |

Load Profile:
- Shared resources: TestClient cookie jar across tests (MEM015 — use `_signup` detached-jar pattern for multi-user tests).
- Per-operation cost: ≈10–30 HTTP requests per test via TestClient.
- 10x breakpoint: not applicable at test scope.

Negative Tests (already enumerated in the cases above): unknown, expired, used, duplicate_member, non-admin, last-admin, invalid role body, personal-team DELETE.

Observability Impact:
- No runtime signals added (pure test work). Future-agent inspection: each test name is self-documenting; `pytest tests/api/routes/test_invites.py::test_join_expired_code_returns_410 -v` targets one behavior without re-reading the file. On failure, `assert r.status_code == 200, r.text` dumps the body so a fresh agent sees the server's reason immediately.
  - Files: `backend/tests/api/routes/test_invites.py`, `backend/tests/api/routes/test_members.py`, `backend/tests/api/routes/test_teams.py`
  - Verify: cd backend && uv run pytest tests/ -v && rg -n '501' backend/app/api/routes/teams.py

## Files Likely Touched

- backend/app/models.py
- backend/app/alembic/versions/s03_team_invites.py
- backend/tests/migrations/test_s03_migration.py
- backend/app/crud.py
- backend/app/api/routes/teams.py
- backend/tests/api/routes/test_teams.py
- backend/tests/api/routes/test_invites.py
- backend/tests/api/routes/test_members.py
