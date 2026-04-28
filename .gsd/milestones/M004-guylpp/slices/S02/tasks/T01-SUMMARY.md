---
id: T01
parent: S02
milestone: M004-guylpp
key_files:
  - backend/app/alembic/versions/s06b_github_app_installations.py
  - backend/app/models.py
  - backend/tests/migrations/test_s06b_github_app_installations_migration.py
key_decisions:
  - installation_id is BIGINT (sa.BigInteger / Column(BigInteger)) because GitHub installation ids are int64; pydantic-validated as Python int
  - FK on team_id uses ON DELETE CASCADE so the install row goes with the team; the GitHub-side install is operator-managed and not revoked here
  - account_type uses a CHECK constraint over a VARCHAR(64) rather than a Postgres ENUM type — keeps the migration simpler, downgrade is a clean drop_table, and the value set is small/stable (Organization, User)
  - No index on team_id beyond the FK — orchestrator looks up by team_id once per clone and team-scoped install cardinality is small; can add ix_github_app_installations_team_id later if needed
  - GitHubAppInstallation model uses table-level UniqueConstraint + CheckConstraint to mirror the alembic-emitted schema, so SQLAlchemy metadata stays in sync if anyone introspects models.metadata
duration: 
verification_result: passed
completed_at: 2026-04-26T00:47:30.644Z
blocker_discovered: false
---

# T01: Add github_app_installations alembic migration s06b + SQLModel pair with UNIQUE/CHECK/CASCADE constraints and full migration test

**Add github_app_installations alembic migration s06b + SQLModel pair with UNIQUE/CHECK/CASCADE constraints and full migration test**

## What Happened

Created alembic revision `s06b_github_app_installations` (down_revision=`s06_system_settings_sensitive`) that creates the `github_app_installations` table with UUID PK `id`, BIGINT `installation_id` (UNIQUE), team FK with ON DELETE CASCADE, VARCHAR(255) `account_login`, VARCHAR(64) `account_type` with a CHECK constraint pinning it to `{Organization, User}`, and a server-defaulted TIMESTAMPTZ `created_at`. Added `GitHubAppInstallation(SQLModel, table=True)` and `GitHubAppInstallationPublic(SQLModel)` to `backend/app/models.py`, declared next to the SystemSetting cluster; the SQLModel uses `Column(BigInteger, ...)` for the int64 `installation_id` and table-level `UniqueConstraint` + `CheckConstraint` so the SQLAlchemy metadata mirrors what alembic emits. Imports were extended with `BigInteger` and `CheckConstraint` from sqlalchemy. Wrote `tests/migrations/test_s06b_github_app_installations_migration.py` mirroring the MEM014/MEM016 pattern from `test_s01_migration.py`: an autouse fixture commits, expires, closes the session-scoped `db` fixture and disposes the engine pool BEFORE alembic runs to avoid the AccessShareLock deadlock, and a second autouse fixture restores head after every test. Six tests cover the upgrade shape (column types, nullability, PK/UQ/FK/CK presence, FK cascade type, CHECK clause text), UNIQUE violation on duplicate installation_id, CheckViolation on `account_type='Bot'`, parent-team delete cascade-deletes the row, downgrade drops the table, and a round-trip `downgrade → upgrade` produces a byte-identical schema snapshot. All six pass against the compose Postgres on POSTGRES_PORT=5432 in 0.42s. `alembic heads` confirms the new revision is the sole head. The model layer remains purely declarative — no API routes, no orchestrator wiring, that's T02–T04 territory. Discovered (and captured to memory as a gotcha — see MEM248) that `test_s05_migration.py::test_s05_upgrade_creates_system_settings` was already failing on `main` before this task: it asserts `system_settings.value` is NOT NULL, but s06_system_settings_sensitive relaxed it to NULLABLE. Confirmed via `git stash`. That's a separate fix-it ticket for whoever owns the S06 series and not part of this task.

## Verification

Ran `cd backend && POSTGRES_PORT=5432 uv run alembic heads` → `s06b_github_app_installations (head)` ✅. Ran `POSTGRES_PORT=5432 uv run pytest tests/migrations/test_s06b_github_app_installations_migration.py -v` → 6/6 passed in 0.42s. Ran the full `tests/migrations/` suite as a regression check → 20 passed, 1 unrelated pre-existing failure in test_s05_migration.py (verified via `git stash`/restore that the failure is not caused by this task; captured as MEM248). Ran a Python import smoke test on `app.models.GitHubAppInstallation` confirming the table name and columns match the migration. Ran the round-trip test under `-s` to surface the alembic INFO logs and confirmed real DDL (`Running downgrade s06b → s06`, `Running upgrade s06 → s06b`, plus the `S06b migration` and `S06b downgrade` logger lines) actually executes against compose Postgres.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && POSTGRES_PORT=5432 uv run alembic heads | grep -q 's06b_github_app_installations'` | 0 | pass | 800ms |
| 2 | `POSTGRES_PORT=5432 uv run pytest tests/migrations/test_s06b_github_app_installations_migration.py -v` | 0 | pass (6/6) | 420ms |
| 3 | `POSTGRES_PORT=5432 uv run python -c 'from app.models import GitHubAppInstallation, GitHubAppInstallationPublic'` | 0 | pass (import + columns match) | 600ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `backend/app/alembic/versions/s06b_github_app_installations.py`
- `backend/app/models.py`
- `backend/tests/migrations/test_s06b_github_app_installations_migration.py`
