---
id: T01
parent: S03
milestone: M004-guylpp
key_files:
  - backend/app/alembic/versions/s06c_team_mirror_volumes.py
  - backend/app/models.py
  - backend/app/api/routes/admin.py
  - backend/tests/migrations/test_s06c_team_mirror_volumes_migration.py
  - backend/tests/api/routes/test_admin_settings.py
key_decisions:
  - UNIQUE constraint on team_mirror_volumes.team_id (one mirror per team is the invariant, not just a soft preference) — keeps the orchestrator ensure/reap from ever needing a SELECT...LIMIT 1 race window
  - mirror_idle_timeout_seconds floor of 60s (not 1s like idle_timeout_seconds) — sub-60s would tear down the mirror container on every reaper tick, weaponizing the reaper
  - Added a second UNIQUE on volume_path so the orchestrator's uuid-keyed path collisions surface as IntegrityError at the DB rather than as silent overwrites
  - always_on column with server_default='false' (not just SQLModel default) so an INSERT without the column lands FALSE — the orchestrator's first ensure-spinup writes the row before the admin has any chance to PATCH always_on=true
duration: 
verification_result: passed
completed_at: 2026-04-26T02:52:27.163Z
blocker_discovered: false
---

# T01: Add team_mirror_volumes table + SQLModels + mirror_idle_timeout_seconds [60..86400] validator

**Add team_mirror_volumes table + SQLModels + mirror_idle_timeout_seconds [60..86400] validator**

## What Happened

Laid the schema and registry that S03's ensure/reap path and the team-admin PATCH endpoint will both read from. Five edits, all mirroring established S06b/S04 patterns.

**Migration (s06c_team_mirror_volumes.py):** New alembic revision with `down_revision='s06b_github_app_installations'` creating `team_mirror_volumes` with: UUID PK, `team_id UUID UNIQUE NOT NULL FK→team(id) ON DELETE CASCADE` (UNIQUE because we run at most one mirror per team), `volume_path VARCHAR(512) NOT NULL UNIQUE`, nullable `container_id VARCHAR(64)` (NULL between reaps), nullable `last_started_at` and `last_idle_at` TIMESTAMPTZ, `always_on BOOLEAN NOT NULL DEFAULT FALSE`, `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`. Logs `S06c migration: created team_mirror_volumes` on upgrade and `S06c downgrade: dropped team_mirror_volumes` on downgrade. Shape mirrors s06b.

**Models (models.py):** Added three SQLModels alongside `GitHubAppInstallation`: `TeamMirrorVolume(table=True)` with the two UniqueConstraints in `__table_args__`, `TeamMirrorVolumePublic` for the projection, and `TeamMirrorPatch` carrying `always_on: bool` for the future PATCH endpoint.

**Validator (admin.py):** Added `MIRROR_IDLE_TIMEOUT_SECONDS_KEY='mirror_idle_timeout_seconds'` constant and `_validate_mirror_idle_timeout_seconds` (bool-rejecting int in 60..86400, mirroring `_validate_idle_timeout_seconds` but with the stricter 60s floor — sub-60s would weaponize the reaper into a per-tick teardown). Registered it in `_VALIDATORS` between `IDLE_TIMEOUT_SECONDS_KEY` and `GITHUB_APP_ID_KEY` with `sensitive=False, generator=None`.

**Migration tests (test_s06c_team_mirror_volumes_migration.py):** 6 tests modeled after s06b — upgrade-shape (column set, types, nullability, PK/UQ/FK constraints, FK CASCADE on team delete), UNIQUE-team_id violation on second insert, FK CASCADE behavior, server-default `always_on=FALSE` on insert, downgrade drops table, downgrade→re-upgrade schema-byte-identity. Uses MEM014/MEM016 autouse fixture pattern (commit+expire+close+`engine.dispose()`) to avoid AccessShareLock deadlocks with the session-scoped autouse `db` fixture.

**Admin settings tests (test_admin_settings.py):** Extended with 10 mirror_idle_timeout_seconds tests covering happy-path 1800, GET round-trip with sensitive=False/has_value=True, boundary 59→422, boundary 60→200, boundary 86401→422, boundary 86400→200, bool→422, str→422, float→422, zero→422.

All 16 tests in the verification command pass in 0.61s. No deviations from the task plan; the only minor adaptation was including `column_default` (rather than `numeric_precision`) in the schema-snapshot helper since `always_on`'s DEFAULT FALSE is what drives the round-trip identity check.

## Verification

Ran the slice-defined verification command and observed all 16 selected tests pass, no failures, no skips.

```
cd /Users/josh/code/perpetuity/backend && POSTGRES_PORT=5432 uv run pytest \
  tests/migrations/test_s06c_team_mirror_volumes_migration.py \
  tests/api/routes/test_admin_settings.py -v -k 'mirror_idle or s06c'
```

Result: `16 passed, 45 deselected, 25 warnings in 0.61s`.

Slice-level verification surfaces from S03-PLAN that touch this task:
- Inspection: `psql -c "\d team_mirror_volumes"` is now answerable post-upgrade (covered by `_columns()` helper in the migration test).
- Inspection: `psql -c "SELECT key, value FROM system_settings WHERE key='mirror_idle_timeout_seconds'"` is now answerable after a PUT (covered by the get_after_put admin test).
- Slice's own runtime signals (`team_mirror_started`, `team_mirror_reaped`, etc.) land in T02/T03 — out of scope for T01 which is schema + registry only.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd /Users/josh/code/perpetuity/backend && POSTGRES_PORT=5432 uv run pytest tests/migrations/test_s06c_team_mirror_volumes_migration.py tests/api/routes/test_admin_settings.py -v -k 'mirror_idle or s06c'` | 0 | pass | 610ms |

## Deviations

None of substance. Schema-snapshot helper captures `column_default` instead of `numeric_precision` (relative to s06b) because the s06c table has no BIGINT to bound and the always_on DEFAULT FALSE is what matters for the round-trip identity check.

## Known Issues

None. MEM248 (test_s05_upgrade_creates_system_settings broken on main) is out of scope for this task and was not exercised by the verification command. All 16 selected tests in scope pass cleanly.

## Files Created/Modified

- `backend/app/alembic/versions/s06c_team_mirror_volumes.py`
- `backend/app/models.py`
- `backend/app/api/routes/admin.py`
- `backend/tests/migrations/test_s06c_team_mirror_volumes_migration.py`
- `backend/tests/api/routes/test_admin_settings.py`
