---
id: T02
parent: S01
milestone: M004-guylpp
key_files:
  - backend/app/alembic/versions/s06_system_settings_sensitive.py
  - backend/app/models.py
key_decisions:
  - Migration column order on upgrade: add value_encrypted/sensitive/has_value first, then ALTER value to NULLABLE — keeps the schema visit forward-only and matches the structured logger sequence
  - Downgrade re-tightens value to NOT NULL without a backfill check — safe because all M002 rows have non-null value today (workspace_volume_size_gb, idle_timeout_seconds)
  - SystemSetting model stays purely declarative — encryption logic lives only in T01's encryption.py and the T03 API layer, preserving the architectural isolation MEM224 calls for
  - SystemSettingPublic always carries sensitive + has_value (no per-key conditional shape) so admin GET semantics are uniform across plain and sensitive keys
duration: 
verification_result: passed
completed_at: 2026-04-26T00:01:24.902Z
blocker_discovered: false
---

# T02: Add s06 alembic migration and extend SystemSetting models for sensitive (encrypted) storage

**Add s06 alembic migration and extend SystemSetting models for sensitive (encrypted) storage**

## What Happened

Implemented the schema and model layer needed by the encrypted-storage path landing in T03. Created `backend/app/alembic/versions/s06_system_settings_sensitive.py` with revision `s06_system_settings_sensitive` and down_revision `s05_system_settings`. The upgrade adds three columns to `system_settings` — `value_encrypted BYTEA NULL`, `sensitive BOOLEAN NOT NULL DEFAULT FALSE`, `has_value BOOLEAN NOT NULL DEFAULT FALSE` — and relaxes the existing `value` JSONB column to NULLABLE so sensitive rows can store NULL there. The downgrade reverses in reverse order (re-tightens `value` to NOT NULL, drops the three new columns); back-compat for existing M002 rows (`workspace_volume_size_gb`, `idle_timeout_seconds`) is preserved because they all have non-null `value` payloads and take the `sensitive=false, has_value=false` defaults. Mirrored s05's per-step `logger.info` pattern so operators tailing alembic output see one INFO line per upgrade/downgrade step.

Extended `backend/app/models.py`: the `SystemSetting` table model gained `value_encrypted: bytes | None`, `sensitive: bool` (default false, NOT NULL), and `has_value: bool` (default false, NOT NULL); the existing `value: Any` field became `Any | None` with `nullable=True` on the JSONB column. The model layer stays purely declarative — no encryption logic touches it, which keeps T01's encryption module free of SQLAlchemy/SQLModel coupling and lets T03 own all encrypt/decrypt call sites. Replaced `SystemSettingPublic` with the new shape — `key, sensitive, has_value, value: Any | None, updated_at` — so admin GET handlers always carry the metadata flags. Added `SystemSettingGenerateResponse(key, value: str, has_value=True, generated=True, updated_at)` for the one-time-display POST landing in T03. Existing `SystemSettingPut`, `SystemSettingShrinkWarning`, and `SystemSettingPutResponse` are unchanged (M002-only path).

Tested the migration end-to-end against the live e2e Postgres (host port 5432, since the compose `db` service publishes to 5432 even though .env has a stale 55432 dev port — verified via `nc`). Round-tripped `upgrade head` → `downgrade -1` → `upgrade head` cleanly and confirmed `\d system_settings` shows all three new columns with correct types/defaults and `value` nullable. The single-head invariant holds: `alembic heads` returns exactly `s06_system_settings_sensitive (head)`.

Note for downstream: T03 must seed `has_value` to true at PUT/generate time so admin GET reflects state correctly — the migration only sets the column default; runtime writes own the flag's truthiness.

## Verification

Ran the four checks defined in the task plan, all passing:
1. `cd backend && alembic heads` → `s06_system_settings_sensitive (head)` (single head, equals expected revision id).
2. `alembic upgrade head` against the live e2e Postgres → succeeded; `docker compose exec db psql -U postgres -d app -c '\d system_settings'` shows `value_encrypted bytea`, `sensitive boolean NOT NULL DEFAULT false`, `has_value boolean NOT NULL DEFAULT false`, and `value jsonb` now nullable.
3. `alembic downgrade -1 && alembic upgrade head` round-tripped cleanly with logger output confirming all four steps in each direction.
4. `grep -q 'value_encrypted' backend/app/models.py` and `grep -q 'class SystemSettingGenerateResponse' backend/app/models.py` both matched.

Note: alembic was run from the host (uv run) with `POSTGRES_SERVER=localhost POSTGRES_PORT=5432` overriding the .env's 55432 (stale value — actual published compose port is 5432). The pre-built backend image only has s05 baked in (per MEM162), which is expected and will be resolved by `docker compose build backend` before any e2e test that spins up a fresh backend container against this revision.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && uv run alembic heads` | 0 | ✅ pass — exactly one head: s06_system_settings_sensitive | 2000ms |
| 2 | `POSTGRES_SERVER=localhost POSTGRES_PORT=5432 ... uv run alembic upgrade head + docker compose exec db psql -c '\d system_settings'` | 0 | ✅ pass — value_encrypted/sensitive/has_value columns present, value now nullable | 4500ms |
| 3 | `alembic downgrade -1 && alembic upgrade head` | 0 | ✅ pass — round-trip succeeds with all 4 logger lines per direction | 5000ms |
| 4 | `grep -q 'value_encrypted' backend/app/models.py && grep -q 'class SystemSettingGenerateResponse' backend/app/models.py` | 0 | ✅ pass — both grep checks match | 50ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `backend/app/alembic/versions/s06_system_settings_sensitive.py`
- `backend/app/models.py`
