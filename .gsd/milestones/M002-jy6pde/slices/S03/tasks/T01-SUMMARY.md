---
id: T01
parent: S03
milestone: M002-jy6pde
key_files:
  - backend/app/alembic/versions/s05_system_settings.py
  - backend/app/models.py
  - backend/tests/migrations/test_s05_migration.py
key_decisions:
  - Use sqlalchemy.dialects.postgresql.JSONB (Postgres-specific) — first JSONB usage in this codebase; project is Postgres-only
  - Type SystemSetting.value as Any (not dict) so D015's future scalar/list/dict payloads all fit without schema churn
  - No additional named indexes — PK on `key` covers the only lookup pattern; downgrade drops the implicit PK index with the table
duration: 
verification_result: passed
completed_at: 2026-04-25T11:40:18.039Z
blocker_discovered: false
---

# T01: Add system_settings Postgres table + SystemSetting SQLModel + s05 alembic migration

**Add system_settings Postgres table + SystemSetting SQLModel + s05 alembic migration**

## What Happened

Landed the persistence shape for D015 — a generic key/value store backing the upcoming admin settings API.

**Migration (`backend/app/alembic/versions/s05_system_settings.py`)**: Creates `system_settings(key VARCHAR(255) PK, value JSONB NOT NULL, updated_at TIMESTAMPTZ NULL)`. Chains off `s04_workspace_volume`. No FKs (system-wide setting). PK on `key` covers the only lookup pattern, so no extra named indexes — the implicit PK index goes with the table on downgrade. Downgrade drops the table; fully reversible.

**Models (`backend/app/models.py`)**: Added `SystemSetting(SQLModel, table=True)` using `Column(JSONB, nullable=False)` for the `value` column (project's first JSONB usage — pulled `from sqlalchemy.dialects.postgresql import JSONB`). Added `SystemSettingPublic(key, value, updated_at)` and `SystemSettingPut(value)` Pydantic shapes for the API layer in T02. Imported `Any` from typing for the JSONB-backed `value` field type.

**Migration test (`backend/tests/migrations/test_s05_migration.py`)**: Three tests following the MEM016 pattern (autouse `_release_autouse_db_session` + `_restore_head_after`) copied verbatim from `test_s04_migration.py`. Tests: (1) upgrade creates table with right column types (varchar / jsonb / timestamptz), exactly one PK constraint exists, and a JSONB scalar round-trips; (2) downgrade drops table and constraints; (3) duplicate `key` insert raises `IntegrityError`.

**Decisions made during execution**:
- JSONB import path: chose `sqlalchemy.dialects.postgresql.JSONB` (Postgres-specific; project is Postgres-only per existing models). The migration uses `postgresql.JSONB(astext_type=sa.Text())` to match the SQLAlchemy default.
- `value` field on the SQLModel: typed `Any` rather than `dict` — D015's spec says any future setting could be a scalar, dict, or list (e.g. `workspace_volume_size_gb` is just an int).
- Test ordering: `command.upgrade(...)` is called before `_truncate_system_settings()` in test 1 and 3 because the autouse fixture restores head between tests but `_truncate` only succeeds when the table exists.

**MEM147 reminder for T04**: backend image bakes `/app/backend/app/alembic/versions/`, so `docker compose build backend` will be required before any e2e test that hits revision `s05_system_settings`. Don't get blindsided.

## Verification

Ran the slice plan's literal verification command from `/Users/josh/code/perpetuity/backend`:

```
POSTGRES_PORT=5432 uv run alembic upgrade head
POSTGRES_PORT=5432 uv run alembic downgrade -1
POSTGRES_PORT=5432 uv run alembic upgrade head
POSTGRES_PORT=5432 uv run pytest tests/migrations/test_s05_migration.py -v
```

All three alembic commands succeeded with the expected `Running upgrade s04_workspace_volume -> s05_system_settings` / `Running downgrade s05_system_settings -> s04_workspace_volume` log lines. All 3 pytest tests passed in 0.16s. As a regression check, also ran `pytest tests/migrations/test_s04_migration.py -v` — 4 passed (S04 still green). Smoke-tested model imports: `SystemSetting(key='workspace_volume_size_gb', value=4)` constructs cleanly, `SystemSettingPublic` accepts nested dict values, `SystemSettingPut(value=42)` round-trips through `model_dump()`.

Slice-level signals not yet exercisable at T01 (route + orchestrator tasks own those): `system_setting_updated` / `system_setting_shrink_warnings_emitted` / `volume_size_gb_resolved` log lines come from T02/T03; this task ships only the schema.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `POSTGRES_PORT=5432 uv run alembic upgrade head` | 0 | pass | 2000ms |
| 2 | `POSTGRES_PORT=5432 uv run alembic downgrade -1` | 0 | pass | 1500ms |
| 3 | `POSTGRES_PORT=5432 uv run alembic upgrade head (re-up)` | 0 | pass | 1500ms |
| 4 | `POSTGRES_PORT=5432 uv run pytest tests/migrations/test_s05_migration.py -v` | 0 | pass (3 passed) | 160ms |
| 5 | `POSTGRES_PORT=5432 uv run pytest tests/migrations/test_s04_migration.py -v (regression)` | 0 | pass (4 passed) | 240ms |

## Deviations

None.

## Known Issues

MEM147 reminder: backend Docker image bakes `/app/backend/app/alembic/versions/`. Any e2e test in T02/T03/T04 that hits the `s05_system_settings` revision will need `docker compose build backend` first or prestart fails with `Can't locate revision identified by 's05_system_settings'`.

## Files Created/Modified

- `backend/app/alembic/versions/s05_system_settings.py`
- `backend/app/models.py`
- `backend/tests/migrations/test_s05_migration.py`
