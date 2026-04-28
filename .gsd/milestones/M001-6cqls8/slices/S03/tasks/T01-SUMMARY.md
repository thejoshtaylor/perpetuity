---
id: T01
parent: S03
milestone: M001-6cqls8
key_files:
  - backend/app/models.py
  - backend/app/alembic/versions/s03_team_invites.py
  - backend/tests/migrations/test_s03_migration.py
key_decisions:
  - Added two extra non-unique indexes (ix_team_invite_team_id, ix_team_invite_created_by) beyond the plan's single unique code index — covers the foreseeable query paths (list-invites-per-team, issued-by-user) at near-zero cost
  - Skipped Team.invites / User.invites_issued Relationships — the plan recommends avoiding relationship back-refs to keep the diff small, and T02/T03 will access invites via explicit session.exec() queries
  - Used ondelete='SET NULL' on used_by to match the plan's FK spec — if a user is deleted after accepting an invite, the audit row survives with a null used_by rather than cascading
duration: 
verification_result: passed
completed_at: 2026-04-24T23:29:20.077Z
blocker_discovered: false
---

# T01: Added TeamInvite model + s03_team_invites alembic migration with round-trip tests

**Added TeamInvite model + s03_team_invites alembic migration with round-trip tests**

## What Happened

Added the `TeamInvite` SQLModel to `backend/app/models.py` with columns `id`, `code` (unique, 64-char), `team_id` (FK team.id ON DELETE CASCADE), `created_by` (FK user.id ON DELETE CASCADE), `expires_at` (TIMESTAMPTZ NOT NULL), `used_at` (TIMESTAMPTZ NULL), `used_by` (FK user.id ON DELETE SET NULL), and `created_at` (TIMESTAMPTZ NULL) — matching column types, defaults, and FK behavior with the migration. Also added the supporting response shapes `TeamInvitePublic`, `InviteIssued` (code/url/expires_at), and `MemberRoleUpdate` (role: TeamRole) so T02/T03 can import them without further model churn. Deliberately skipped back-population Relationships on Team/User — the slice plan recommends minimizing the diff.\n\nCreated `backend/app/alembic/versions/s03_team_invites.py` chained onto `s02_team_columns` with `revision='s03_team_invites'`. Upgrade uses `op.create_table(...)` with explicit `ForeignKeyConstraint(..., ondelete=...)` clauses and three separately-named indexes: `ix_team_invite_code` (unique, for code lookups), plus `ix_team_invite_team_id` and `ix_team_invite_created_by` (non-unique) for common filter paths. Downgrade drops indexes in reverse order then the table. No backfill step needed (new table, no existing rows).\n\nWrote `backend/tests/migrations/test_s03_migration.py` with three tests using the MEM016 autouse fixture pattern copied verbatim from `test_s02_migration.py` — `_release_autouse_db_session` commits+closes the session-scoped `db` and disposes the engine before alembic DDL runs, `_restore_head_after` upgrades back to head after each test. The upgrade test asserts all columns exist with correct nullability, checks `pg_index.indisunique` for `ix_team_invite_code`, and attempts a FK-violating insert (bogus team_id) to prove the FK is enforced. The downgrade test asserts the table and all indexes are gone at `s02_team_columns`. The duplicate-code test inserts twice with the same `code` and asserts IntegrityError.\n\nRan `uv run alembic upgrade head` cleanly — schema inspection confirms 8 columns, 3 declared indexes (code unique, team_id, created_by) plus the primary key. Then ran `uv run pytest tests/migrations/test_s03_migration.py -v` (3/3 pass) and the full backend suite (96/96 pass) — no regressions to S01/S02 or API-level tests.

## Verification

Ran alembic upgrade from s02_team_columns to s03_team_invites cleanly; confirmed `alembic_version` table reports `s03_team_invites`. Inspected `information_schema.columns` for `team_invite` — 5 NOT NULL columns (id/code/team_id/created_by/expires_at), 3 nullable (used_at/used_by/created_at). Confirmed `pg_indexes` shows `ix_team_invite_code` with `indisunique=true` plus `ix_team_invite_team_id` and `ix_team_invite_created_by`. Ran `uv run pytest tests/migrations/test_s03_migration.py -v` — all 3 tests pass in 0.35s. Ran `uv run pytest tests/` — 96/96 pass in 4.59s, no regressions.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && uv run alembic upgrade head` | 0 | ✅ pass | 1200ms |
| 2 | `cd backend && uv run pytest tests/migrations/test_s03_migration.py -v` | 0 | ✅ pass | 350ms |
| 3 | `cd backend && uv run pytest tests/` | 0 | ✅ pass | 4590ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `backend/app/models.py`
- `backend/app/alembic/versions/s03_team_invites.py`
- `backend/tests/migrations/test_s03_migration.py`
