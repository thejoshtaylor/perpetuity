---
estimated_steps: 25
estimated_files: 3
skills_used: []
---

# T01: Add TeamInvite model + alembic migration s03_team_invites + migration round-trip test

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

## Inputs

- ``backend/app/models.py` — existing Team, TeamMember, TeamRole models to extend`
- ``backend/app/alembic/versions/s02_team_columns.py` — prior migration; new revision must chain from `s02_team_columns``
- ``backend/tests/migrations/test_s02_migration.py` — source of the MEM016 autouse fixture pattern to copy`

## Expected Output

- ``backend/app/models.py` — TeamInvite table class + InviteIssued + MemberRoleUpdate shapes appended`
- ``backend/app/alembic/versions/s03_team_invites.py` — new reversible migration`
- ``backend/tests/migrations/test_s03_migration.py` — three round-trip tests using MEM016 fixture pattern`

## Verification

cd backend && uv run alembic upgrade head && uv run pytest tests/migrations/test_s03_migration.py -v

## Observability Impact

No runtime signals added — pure schema. Future-agent inspection: `SELECT * FROM alembic_version` confirms head = s03_team_invites; `\d+ team_invite` in psql shows column shape + ix_team_invite_code index.
