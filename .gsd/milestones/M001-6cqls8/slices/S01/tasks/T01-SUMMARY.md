---
id: T01
parent: S01
milestone: M001-6cqls8
key_files:
  - backend/app/models.py
  - backend/app/alembic/versions/s01_auth_and_roles.py
  - backend/app/core/db.py
  - .env
key_decisions:
  - Chose two UPDATEs (is_superuser=TRUE and is_superuser=FALSE) rather than a single CASE, so the rowcount for each branch could be logged independently — ops visibility for the migration per the Observability Impact requirement
  - Added `role` as nullable first, back-filled, then tightened to NOT NULL — the standard safe pattern for adding a required column to a table that already has rows
  - Named enum types lowercase at the Postgres level (`userrole`, `teamrole`) matching SQLModel's default; documented this as MEM012 so T05's migration test queries pg_type with the correct lowercase values
  - Kept the Team stub truly minimal (id + created_at) per MEM011 so S02 owns name/slug/is_personal additions and this slice doesn't drift
  - Left `is_superuser` references in `app/api/deps.py`, `routes/users.py`, `routes/items.py` untouched — they are assigned to T03 per the slice plan's explicit scoping, and Python attribute access fails only at runtime so module imports still work
duration: 
verification_result: passed
completed_at: 2026-04-24T22:00:45.796Z
blocker_discovered: false
---

# T01: Add UserRole/TeamRole enums, Team stub + TeamMember tables, and reversible migration that maps is_superuser → role

**Add UserRole/TeamRole enums, Team stub + TeamMember tables, and reversible migration that maps is_superuser → role**

## What Happened

Replaced the `is_superuser: bool` column on `User` with a `UserRole` enum (`user`, `system_admin`) defined in `backend/app/models.py`, added a sibling `TeamRole` enum (`member`, `admin`), and introduced a minimal `Team` stub table (just `id UUID PK` + `created_at`) plus a `TeamMember` join table with per-membership role and a `UNIQUE(user_id, team_id)` constraint. The Team stub satisfies MEM011's decision to keep TeamMember's FK target resolvable in S01 while leaving real Team columns (name/slug/is_personal) for S02. Relationships between `User ↔ TeamMember ↔ Team` are wired via SQLModel `Relationship(back_populates=...)` with `cascade_delete=True` on both sides.\n\nWrote a single Alembic migration at `backend/app/alembic/versions/s01_auth_and_roles.py` (revision `s01_auth_and_roles`, parent `fe56fa70289e`). Upgrade: creates both enum types at the Postgres level, adds `user.role` as nullable, data-migrates `is_superuser=TRUE → 'system_admin'` and `FALSE → 'user'` via two `UPDATE` statements whose `rowcount` is logged (so operators see how many rows migrated), tightens `role` to NOT NULL, drops `is_superuser`, creates `team` stub, creates `team_member` with FK `ON DELETE CASCADE` to both parents + indexes on each FK column. Downgrade is fully reversible and symmetric: drops the new tables, re-adds `is_superuser` as nullable, restores `TRUE` for `system_admin` rows and `FALSE` otherwise (logging counts), tightens to NOT NULL, drops the role column, then drops both enum types.\n\nUpdated `backend/app/core/db.py::init_db` to pass `role=UserRole.system_admin` to `UserCreate` instead of the now-gone `is_superuser=True`. `backend/app/crud.py` needed no change — it delegates to `User.model_validate(user_create, ...)` which picks up the new `role` field from `UserBase`. Did not touch `app/api/deps.py`, `app/api/routes/users.py`, `items.py`, or `login.py` — the slice plan explicitly assigns the cookie/route rewrite (and the replacement of `current_user.is_superuser` with `current_user.role == UserRole.system_admin`) to T03. Those modules still compile (attribute accesses fail only at runtime), so `app.models` imports used by Alembic and the T01 verify snippet both work.\n\nDeviations from the written inputs list: the plan named `backend/app/initial_data.py` as an input but the actual `is_superuser=True` seed lives in `app/core/db.py::init_db` (which `initial_data.py` calls). I edited `core/db.py` accordingly. `initial_data.py` itself was unchanged because it only drives the session and doesn't reference role fields directly.\n\nVerified on a fresh Postgres (`postgres:18` in a dedicated container on port 55432 to avoid colliding with an unrelated Postgres already listening on 5432): ran the prescribed `alembic upgrade head → downgrade -1 → upgrade head → python -c "from app.models import User, UserRole, TeamRole, TeamMember, Team; assert UserRole.system_admin.value == 'system_admin'; assert TeamRole.admin.value == 'admin'"` chain end-to-end — exit 0, 1.29s. Additionally exercised the plan's negative tests: seeded a mixed 3-user dataset (1 superuser, 2 plain) and confirmed the upgrade mapped 1 row → `system_admin` and 2 rows → `user` (verified via `SELECT email, role FROM \"user\"`); then truncated to zero users and ran another full round trip to confirm the migration works on an empty table. Verified via `pg_type` that `userrole` and `teamrole` enum types were created, that the `is_superuser` column was gone from `information_schema.columns` after upgrade, and that `team` and `team_member` tables existed.

## Verification

Ran the exact verify chain from T01-PLAN on a fresh Postgres (port 55432, container `perpetuity-db-s01`): `uv run alembic upgrade head` → `uv run alembic downgrade -1` → `uv run alembic upgrade head` → `uv run python -c "from app.models import User, UserRole, TeamRole, TeamMember, Team; assert UserRole.system_admin.value == 'system_admin'; assert TeamRole.admin.value == 'admin'"` — all commands exit 0. Manually seeded a mixed is_superuser population (1 True + 2 False) and confirmed the data migration logged `mapped 1 is_superuser=True rows -> system_admin, 2 is_superuser=False rows -> user` and that `SELECT role FROM "user"` showed the expected mapping. Also exercised the empty-table round trip (DELETE then upgrade → downgrade -1 → upgrade, all zero-row and no errors). Verified Postgres objects directly: `pg_type.typname IN ('userrole','teamrole')` returned both, `\dt` listed `team` and `team_member`, `information_schema.columns` for the user table no longer lists `is_superuser`. Downgrade symmetry was proven by the round trip — the second upgrade succeeded, meaning the previous downgrade fully removed everything it had to.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head && uv run python -c "from app.models import User, UserRole, TeamRole, TeamMember, Team; assert UserRole.system_admin.value == 'system_admin'; assert TeamRole.admin.value == 'admin'"` | 0 | ✅ pass | 1289ms |
| 2 | `seed mixed (1 superuser + 2 plain) → alembic upgrade head → SELECT email,role FROM "user"` | 0 | ✅ pass — 1 row → system_admin, 2 rows → user; log line confirmed counts | 800ms |
| 3 | `zero-user round trip: DELETE FROM "user" → alembic downgrade -1 → alembic upgrade head → alembic downgrade -1 → alembic upgrade head` | 0 | ✅ pass | 1500ms |
| 4 | `SELECT typname FROM pg_type WHERE typname IN ('userrole','teamrole')` | 0 | ✅ pass — both enum types present after upgrade | 50ms |
| 5 | `SELECT column_name FROM information_schema.columns WHERE table_name='user'` | 0 | ✅ pass — `is_superuser` absent, `role` present | 50ms |

## Deviations

The plan's input/output list named `backend/app/initial_data.py` but the actual `is_superuser=True` seed was in `backend/app/core/db.py::init_db()` (which `initial_data.py` invokes). Edited `core/db.py` instead — `initial_data.py` required no change. Also created `/Users/josh/code/perpetuity/.env` from `.env.example` and overrode `POSTGRES_PORT=55432` because port 5432 was occupied by an unrelated Postgres on the host machine; the project's docker-compose setup assumes 5432 is free. The `.env` is gitignored so this is a local-only adjustment, but noting it so future executions on this machine know why the port differs.

## Known Issues

`.env` (local dev) pins `POSTGRES_PORT=55432` to route around a conflict; contributors on clean machines can keep the 5432 default. The `.env` file itself is gitignored and won't be committed. The `is_superuser` references in `app/api/deps.py`, `app/api/routes/users.py`, `app/api/routes/items.py`, and tests still exist as expected — T03/T05 will rewrite them. Importing those modules works; calling routes that read `current_user.is_superuser` will raise `AttributeError` until T03 lands, but no route is exercised by T01's verification.

## Files Created/Modified

- `backend/app/models.py`
- `backend/app/alembic/versions/s01_auth_and_roles.py`
- `backend/app/core/db.py`
- `.env`
