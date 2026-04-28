---
estimated_steps: 18
estimated_files: 5
skills_used: []
---

# T01: Add team_mirror_volumes table + SQLModel + mirror_idle_timeout_seconds registered setting

Lay the schema and registry that the orchestrator's ensure/reap and the backend PATCH endpoint will both read from. Adds the `team_mirror_volumes` table (one row per team, durable through reap), the matching SQLModel + public projection, and registers a new `mirror_idle_timeout_seconds` system_settings key with bounds [60, 86400] and default 1800 in the admin _VALIDATORS registry. Mirrors the S06b migration shape (uuid PK, team FK ON DELETE CASCADE, idempotent upgrade + reversible downgrade) and the existing `_validate_idle_timeout_seconds` validator pattern in `backend/app/api/routes/admin.py`.

## Failure Modes

| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| Postgres (alembic upgrade) | propagate (boot blocker — caught by prestart) | propagate | N/A |
| system_settings PUT validator | 422 invalid_value_for_key (must be int in 60..86400) | N/A | 422 |

## Load Profile

- Shared resources: none (one row per team, low cardinality; PK lookups only).
- Per-operation cost: 1 INSERT/UPDATE/SELECT per ensure or reap call.
- 10x breakpoint: N/A — table stays in the low hundreds of rows for the foreseeable scale.

## Negative Tests

- Malformed inputs: `mirror_idle_timeout_seconds` validator rejects bool, str, float, 0, 59, 86401 → 422.
- Boundary conditions: 60 and 86400 accepted; UNIQUE constraint on `team_id` rejects a second row for the same team; FK CASCADE on parent team delete drops the row.
- Migration: downgrade then re-upgrade leaves schema byte-identical (mirrors test_s06b round-trip).

## Observability Impact

- Signals added/changed: alembic logs `S06c migration: created team_mirror_volumes` on upgrade and `S06c downgrade: dropped team_mirror_volumes` on downgrade.
- How a future agent inspects this: `psql -c "\d team_mirror_volumes"`; `psql -c "SELECT key, value FROM system_settings WHERE key='mirror_idle_timeout_seconds'"`.
- Failure state exposed: schema absence shows as KeyError on any orchestrator ensure call surfacing 503 `workspace_volume_store_unavailable` (existing handler).

## Inputs

- ``backend/app/alembic/versions/s06b_github_app_installations.py` — shape reference (uuid PK, team FK CASCADE, idempotent upgrade/downgrade, alembic logger pattern)`
- ``backend/app/models.py` — extend with TeamMirrorVolume + TeamMirrorVolumePublic + TeamMirrorPatch SQLModels alongside GitHubAppInstallation`
- ``backend/app/api/routes/admin.py` — extend `_VALIDATORS` registry with `mirror_idle_timeout_seconds` mirroring `_validate_idle_timeout_seconds``
- ``backend/tests/migrations/test_s06b_github_app_installations_migration.py` — pattern to copy for the s06c migration test`

## Expected Output

- ``backend/app/alembic/versions/s06c_team_mirror_volumes.py` — new alembic revision with down_revision='s06b_github_app_installations'; CREATE TABLE team_mirror_volumes (id UUID PK, team_id UUID UNIQUE FK→team(id) ON DELETE CASCADE, volume_path VARCHAR(512) NOT NULL UNIQUE, container_id VARCHAR(64) NULL, last_started_at TIMESTAMPTZ NULL, last_idle_at TIMESTAMPTZ NULL, always_on BOOLEAN NOT NULL DEFAULT false, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())`
- ``backend/app/models.py` — adds class TeamMirrorVolume(SQLModel, table=True) + TeamMirrorVolumePublic + TeamMirrorPatch (always_on: bool)`
- ``backend/app/api/routes/admin.py` — registers MIRROR_IDLE_TIMEOUT_SECONDS_KEY='mirror_idle_timeout_seconds' constant + `_validate_mirror_idle_timeout_seconds` validator (bool-rejecting int in 60..86400) + entry in `_VALIDATORS``
- ``backend/tests/migrations/test_s06c_team_mirror_volumes_migration.py` — 6 tests: upgrade-shape, UNIQUE-team_id violation, FK-cascade on team delete, downgrade-drop, downgrade→re-upgrade schema-byte-identity, default-row-on-insert (always_on defaults false)`
- ``backend/tests/api/routes/test_admin_settings.py` — extend with: PUT mirror_idle_timeout_seconds=1800 → 200, =59 → 422, =86401 → 422, =true → 422, GET sensitive=False has_value=True`

## Verification

cd /Users/josh/code/perpetuity/backend && POSTGRES_PORT=5432 uv run pytest tests/migrations/test_s06c_team_mirror_volumes_migration.py tests/api/routes/test_admin_settings.py -v -k 'mirror_idle or s06c'

## Observability Impact

Adds alembic INFO log lines for the s06c upgrade/downgrade; no runtime observability changes (orchestrator and route logs land in T02/T03).
