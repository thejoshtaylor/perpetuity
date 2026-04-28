---
estimated_steps: 1
estimated_files: 3
skills_used: []
---

# T01: Migration s09_team_secrets + SQLModel + Pydantic DTOs

Create alembic revision `s09_team_secrets.py` adding the `team_secrets` table with composite PK (team_id, key), FK CASCADE on team delete, columns per success criteria (1). Add `TeamSecret` SQLModel to `backend/app/models.py` with the same field shape and Pydantic Public DTO (`TeamSecretPublic`) that excludes `value_encrypted` entirely (never serialized) and a Status DTO (`TeamSecretStatus`) with `{key, has_value, sensitive, updated_at}` for GET responses. Add migration test `test_s09_team_secrets_migration.py` running upgrade-from-s08 + downgrade round-trip with the existing `_release_autouse_db_session` autouse fixture (per the project memory note about session-scoped autouse `db` fixture holding AccessShareLock).

## Inputs

- `M004/S01 migration `s06_system_settings_sensitive.py` for column shape reference`
- `Existing `_release_autouse_db_session` autouse fixture from `backend/tests/migrations/test_s06c_team_mirror_volumes_migration.py``
- `Existing `Team` model (FK target with CASCADE)`
- `MEM089: append to existing routers/registries rather than create new modules`

## Expected Output

- `New `team_secrets` table exists in the DB after upgrade with composite PK + FK CASCADE`
- `Downgrade cleanly drops the table`
- ``TeamSecret` SQLModel imports without error from `app.models``
- `Pydantic DTO `TeamSecretStatus` does not expose `value_encrypted` field even when serializing a fully-populated row`

## Verification

cd backend && uv run pytest tests/migrations/test_s09_team_secrets_migration.py -v

## Observability Impact

No new log keys at this task level — storage-only.
