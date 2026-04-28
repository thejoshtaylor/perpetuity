---
id: T01
parent: S02
milestone: M001-6cqls8
key_files:
  - backend/app/models.py
  - backend/app/alembic/versions/s02_team_columns.py
  - backend/tests/migrations/test_s02_migration.py
key_decisions:
  - Unique index on slug created as a separate `ix_team_slug` step rather than inline on the column, so the name is explicit and downgrade can drop it by name.
  - is_personal keeps `server_default=sa.false()` after NOT NULL so forgotten INSERTs fail safe in the not-personal direction (defensive default).
  - Backfill uses substr(id::text, 1, 8) as the unique stem — deterministic and avoids collisions without needing ROW_NUMBER() or sequences.
  - Added TeamPublic / TeamCreate / TeamWithRole response models now, so T02 (CRUD) and T03 (endpoints) don't need to re-edit models.py.
duration: 
verification_result: passed
completed_at: 2026-04-24T23:06:51.112Z
blocker_discovered: false
---

# T01: Extended Team model with name/slug/is_personal and added s02_team_columns migration with unique slug index and backfill safety

**Extended Team model with name/slug/is_personal and added s02_team_columns migration with unique slug index and backfill safety**

## What Happened

Promoted the S01 Team stub to a real model by adding three columns (`name` VARCHAR(255) NOT NULL, `slug` VARCHAR(64) NOT NULL UNIQUE, `is_personal` BOOLEAN NOT NULL DEFAULT FALSE) and shipping `s02_team_columns` alembic migration. The migration follows the nullable→backfill→NOT NULL pattern: every column is added nullable first, a defensive UPDATE backfills any pre-existing rows with deterministic unique values derived from the team UUID (`'Legacy Team ' || substr(id::text, 1, 8)` / `'legacy-' || substr(id::text, 1, 8)`), then ALTER COLUMN … SET NOT NULL tightens the schema. A unique index `ix_team_slug` is created separately so the name is explicit. Fresh DBs hit zero-row backfill (logged as `S02 migration: backfilled 0 team rows`) but the defensive UPDATE means a manually seeded row can't block the migration.

Also added three response/request SQLModel types to `app/models.py`: `TeamPublic` (id, name, slug, is_personal, created_at), `TeamCreate` (name only — slug/is_personal are server-generated in later tasks), and `TeamWithRole` for GET /teams joined with the caller's membership role.

Wrote `tests/migrations/test_s02_migration.py` with three round-trip tests: (1) upgrade at head sets all three columns NOT NULL, unique index exists, and duplicate-slug insert raises IntegrityError; (2) downgrade to s01_auth_and_roles drops all three columns and the ix_team_slug index; (3) a team row seeded at S01 schema gets filled with `Legacy Team <hex8>` / `legacy-<hex8>` / is_personal=FALSE after upgrade. Tests use the MEM016 autouse fixture pattern (commit + close autouse session + engine.dispose()) to avoid AccessShareLock deadlocks on DROP COLUMN / ALTER COLUMN.

No endpoint, crud, or signup work — that's T02+. TeamMember, User, and other models are unchanged.

## Verification

Ran `cd backend && uv run alembic upgrade head` (succeeded, logged `backfilled 0 team rows`), `uv run pytest tests/migrations/test_s02_migration.py -v` (3/3 passed), and `uv run pytest tests/ -v` (79/79 passed — 76 pre-existing + 3 new S02 tests, zero regressions). Unique-slug enforcement was verified by an inline IntegrityError assertion inside the migration test, and the backfill test proves a pre-existing S01-schema row survives upgrade with a deterministic unique slug.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && uv run alembic upgrade head` | 0 | ✅ pass | 1500ms |
| 2 | `uv run pytest tests/migrations/test_s02_migration.py -v` | 0 | ✅ pass | 300ms |
| 3 | `uv run pytest tests/ -v` | 0 | ✅ pass | 4160ms |

## Deviations

None. T01 scope (DB schema only) was respected — no endpoint, crud, or signup changes.

## Known Issues

None.

## Files Created/Modified

- `backend/app/models.py`
- `backend/app/alembic/versions/s02_team_columns.py`
- `backend/tests/migrations/test_s02_migration.py`
