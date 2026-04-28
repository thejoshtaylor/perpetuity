---
estimated_steps: 4
estimated_files: 2
skills_used: []
---

# T02: Migration s06 + extend SystemSetting models for sensitive storage

Add the three columns the encrypted-storage path needs and grow the SQLModel + pydantic types so the API layer in T03 can consume them. Migration file: `backend/app/alembic/versions/s06_system_settings_sensitive.py`. Revision id `s06_system_settings_sensitive`, down_revision `s05_system_settings`. Upgrade: `op.add_column('system_settings', sa.Column('value_encrypted', sa.LargeBinary, nullable=True))`, `op.add_column('system_settings', sa.Column('sensitive', sa.Boolean, nullable=False, server_default=sa.false()))`, `op.add_column('system_settings', sa.Column('has_value', sa.Boolean, nullable=False, server_default=sa.false()))`. Downgrade: drop the three columns in reverse order. Crucially: `value JSONB` becomes nullable on upgrade so sensitive rows can store NULL there — modify the existing column with `op.alter_column('system_settings', 'value', nullable=True)` and reverse on downgrade with a backfill-or-fail check (M002 keys all have non-null `value` today so the downgrade can safely re-tighten without backfill). Existing M002 rows (`workspace_volume_size_gb`, `idle_timeout_seconds`) remain `sensitive=false, has_value=true (server_default)` and their `value` JSONB stays populated — back-compat preserved.

Models (`backend/app/models.py`): extend `SystemSetting` table model with `value_encrypted: bytes | None`, `sensitive: bool = Field(default=False)`, `has_value: bool = Field(default=False)`. The existing `value: Any` field stays but becomes `Any | None` (sensitive rows store None there). Replace `SystemSettingPublic` with a shape that always carries `key, sensitive, has_value, updated_at` and `value: Any | None` (None for sensitive). Add `SystemSettingGenerateResponse(key: str, value: str, has_value: bool = True, generated: bool = True, updated_at: datetime | None)` for the one-time-display POST. The `SystemSettingPut` body shape is unchanged. The shrink-warnings types stay as-is (M002-only path).

The model layer must NOT carry encryption logic — keep it purely declarative. The API layer (T03) is the only place that calls `encrypt_setting`/`decrypt_setting`. This isolation is what lets T01's encryption module stay free of SQLAlchemy / SQLModel coupling.

Note on alembic head: M002/S05 ends at `s05_system_settings`. There is no other branch open for this milestone yet so a single linear successor `s06` is correct.

## Inputs

- ``backend/app/alembic/versions/s05_system_settings.py``
- ``backend/app/models.py``

## Expected Output

- ``backend/app/alembic/versions/s06_system_settings_sensitive.py``
- ``backend/app/models.py``

## Verification

From `/Users/josh/code/perpetuity`: (1) `cd backend && uv run alembic heads` lists exactly one head and it equals `s06_system_settings_sensitive`; (2) `cd backend && uv run alembic upgrade head` against the e2e DB succeeds (run via `docker compose exec db psql -U postgres -d app -c '\d system_settings'` after running the prestart-equivalent on a sibling backend container; the columns `value_encrypted`, `sensitive`, `has_value` are visible and `value` is now nullable); (3) `cd backend && uv run alembic downgrade -1 && uv run alembic upgrade head` round-trips cleanly; (4) `grep -q 'value_encrypted' backend/app/models.py` and `grep -q 'class SystemSettingGenerateResponse' backend/app/models.py` both match.

## Observability Impact

Migration logger emits one INFO line per upgrade/downgrade step (matches s05's pattern at line 41-54) so operators tailing alembic logs can confirm the sensitive-column extension landed. No runtime observability change — model shape is data-only.
