---
estimated_steps: 2
estimated_files: 3
skills_used: []
---

# T03: Replace orchestrator's hardcoded default_volume_size_gb with a system_settings lookup on each fresh-volume create

On every `ensure_volume_for` fresh-row branch (i.e. when `get_volume` returns None), the orchestrator now reads `system_settings.workspace_volume_size_gb` and uses that integer as the new row's `size_gb`. Existing rows still use their own `size_gb` (D015 partial-apply rule — never re-derived). Implementation: add `_resolve_default_size_gb(pool: asyncpg.Pool) -> int` to `orchestrator/orchestrator/volume_store.py` that runs `SELECT value FROM system_settings WHERE key = 'workspace_volume_size_gb'` with the same 5s command_timeout as the rest of the module; on row-found, JSON-parse the `value` column (asyncpg returns JSONB as a Python value already — int comes back as int) and validate `isinstance(v, int) and 1 <= v <= 256`; on row-missing, missing-key, validation-fail, or pg error, log a WARNING `system_settings_lookup_failed key=workspace_volume_size_gb reason=<class>` and fall back to `settings.default_volume_size_gb` (the existing 4 GiB boot-time env). Wire the helper at the top of `ensure_volume_for`'s fresh-row branch — call it BEFORE `allocate_image` so the size used for `truncate -sNG` and `mkfs.ext4` matches the size persisted to the row. Emit INFO `volume_size_gb_resolved source=<system_settings|fallback> value=<n>` so an operator can confirm the new default is biting. Do NOT cache the value in-process (the slice acceptance demands a fresh PUT take effect on the very next provision; a 1-query overhead per provision is acceptable per the load profile — provision is rare, not a hot path).

Update `orchestrator/tests/integration/test_volumes.py` and/or `test_sessions_lifecycle.py`: add `test_resolve_default_size_gb_reads_system_settings` (insert a row with value=2, assert helper returns 2), `test_resolve_default_size_gb_falls_back_when_missing` (no row, assert returns settings.default_volume_size_gb=4), `test_resolve_default_size_gb_falls_back_on_invalid_value` (insert value="banana", assert fallback + WARNING emitted), `test_provision_uses_resolved_default` (PUT-equivalent — UPSERT a row to value=2 then call provision_container for a fresh (user, team) → assert workspace_volume row.size_gb == 2). Existing-volume idempotency test must continue to pass unchanged (D015 invariant: existing rows never re-derived).

## Inputs

- ``backend/app/alembic/versions/s05_system_settings.py` — schema must exist before orchestrator can SELECT from system_settings`
- ``orchestrator/orchestrator/volume_store.py` — existing module owning the asyncpg pool + ensure_volume_for; helper lands here`
- ``orchestrator/orchestrator/config.py` — settings.default_volume_size_gb stays as the fallback (no change)`
- ``orchestrator/tests/integration/test_volumes.py` — extend with the 3 new lookup cases`
- ``orchestrator/tests/integration/test_sessions_lifecycle.py` — extend with the 1 new provision-end-to-end case`

## Expected Output

- ``orchestrator/orchestrator/volume_store.py` — adds `_resolve_default_size_gb(pool)` helper + a single call site at the top of ensure_volume_for's fresh-row branch`
- ``orchestrator/tests/integration/test_volumes.py` — 3 new tests covering hit/missing/invalid lookup paths + WARNING emission`
- ``orchestrator/tests/integration/test_sessions_lifecycle.py` — 1 new test asserting the provisioned row's size_gb equals the system_settings value`

## Verification

docker compose build orchestrator && docker compose up -d --force-recreate orchestrator && docker cp orchestrator/tests perpetuity-orchestrator-1:/app/tests && docker compose exec orchestrator /app/.venv/bin/pytest tests/integration/test_volumes.py -v -k 'resolve_default or provision_uses_resolved' && cd backend && POSTGRES_PORT=5432 uv run pytest tests/integration/test_m002_s01_e2e.py tests/integration/test_m002_s02_volume_cap_e2e.py -v
