---
estimated_steps: 1
estimated_files: 3
skills_used: []
---

# T01: Add system_settings Postgres table + SQLModel + s05 alembic migration

Land the persistence shape D015 calls for: a generic key/value store backing the admin settings API. This task is Postgres-only — no backend route or orchestrator changes — so it can be reviewed via the existing migration-test pattern (MEM016/MEM025) before T02 wires the API on top. Schema: `key VARCHAR(255) NOT NULL PRIMARY KEY`, `value JSONB NOT NULL`, `updated_at TIMESTAMPTZ NOT NULL` (default applied by app via get_datetime_utc). No FKs (system-wide setting, not user-scoped). No additional indexes — PK on `key` covers the only lookup pattern. Migration discipline (MEM016): the migration test must release+dispose the autouse `db` fixture's session before running alembic and dispose again on restore, copying the pattern from `backend/tests/migrations/test_s04_migration.py` verbatim. Migration file name `s05_system_settings.py`; revision id `s05_system_settings`; down_revision `s04_workspace_volume`. Downgrade drops the table (PK index goes with it; no separate named indexes to drop). Add `SystemSetting(SQLModel, table=True)` to `backend/app/models.py` plus Pydantic request/response shapes: `SystemSettingPublic(key: str, value: Any, updated_at: datetime|None)` and `SystemSettingPut(value: Any)`. After-migration MEM147 reminder: backend image bakes /app/backend/app/alembic/versions/, so `docker compose build backend` is required before any e2e test that hits the new revision. Document this in the task summary so T04 does not get blindsided.

## Inputs

- ``backend/app/alembic/versions/s04_workspace_volume.py` — prior alembic head; new revision chains off this rev id`
- ``backend/app/models.py` — existing SQLModel models (User, Team, TeamMember, WorkspaceVolume); add SystemSetting alongside`
- ``backend/tests/migrations/test_s04_migration.py` — copy the MEM016 lock-hazard pattern verbatim into the new test`

## Expected Output

- ``backend/app/alembic/versions/s05_system_settings.py` — new alembic revision creating system_settings(key VARCHAR(255) PK, value JSONB NOT NULL, updated_at TIMESTAMPTZ); fully reversible`
- ``backend/app/models.py` — SystemSetting(SQLModel, table=True) + SystemSettingPublic + SystemSettingPut Pydantic shapes`
- ``backend/tests/migrations/test_s05_migration.py` — round-trip up/down/up + shape assertions following MEM016 fixtures`

## Verification

cd backend && POSTGRES_PORT=5432 uv run alembic upgrade head && POSTGRES_PORT=5432 uv run alembic downgrade -1 && POSTGRES_PORT=5432 uv run alembic upgrade head && POSTGRES_PORT=5432 uv run pytest tests/migrations/test_s05_migration.py -v
