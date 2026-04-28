---
estimated_steps: 2
estimated_files: 3
skills_used: []
---

# T02: Wire GET/PUT /api/v1/admin/settings[/{key}] with per-key validators and workspace_volume_size_gb partial-apply shrink

Add the admin settings endpoints to `backend/app/api/routes/admin.py` (the existing system_admin-gated router; do NOT create a new router — reuse the shared `dependencies=[Depends(get_current_active_superuser)]` already on the module so the 403/401 ordering matches every other admin endpoint and tests/api/routes/test_admin_teams.py's gate-test pattern is reusable). Endpoints: `GET /admin/settings` returns `{data: [SystemSettingPublic, ...], count}` ordered by key; `GET /admin/settings/{key}` returns the single SystemSettingPublic or 404 `{detail: 'setting_not_found'}`; `PUT /admin/settings/{key}` body `{value: <any>}` returns the (now-stored) SystemSettingPublic, plus a `warnings: [{user_id, team_id, size_gb, usage_bytes}, ...]` field for keys whose validator declares warnings (only `workspace_volume_size_gb` does so far). Per-key validator registry: `_VALIDATORS: dict[str, Callable]` keyed by setting key. The validator for `workspace_volume_size_gb` enforces `isinstance(value, int) and 1 <= value <= 256` (the same range the orchestrator's `volume_store` already imposes); on type/range failure, raise HTTPException(422, detail={'detail': 'invalid_value_for_key', 'key': key, 'reason': 'must be int in 1..256'}). Unknown keys reject with 422 `{detail: 'unknown_setting_key', key}` — reject-by-default closes the foot-gun where a typo in the key adds a new row that nothing reads. Partial-apply shrink computation for `workspace_volume_size_gb`: SELECT user_id, team_id, size_gb FROM workspace_volume WHERE size_gb > :new_value ORDER BY created_at; for each, compute usage_bytes via `os.statvfs(<workspace_root>/<user_id>/<team_id>)` if the mountpoint is reachable from the backend container — but the backend container does NOT mount the workspace_volume host bind, so usage_bytes is reported as `null` from this slice and the schema is ready for S04 to add a backend→orchestrator GET /v1/volumes/{volume_id}/usage call (deferred — out of scope for S03; document the null in the response model). Storage: UPSERT pattern using `INSERT ... ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW() RETURNING ...`. Logging: INFO `system_setting_updated actor_id=<uuid> key=<str> previous_value_present=<true|false>` (do NOT log the value — could carry secrets in future settings); INFO `system_setting_shrink_warnings_emitted key=workspace_volume_size_gb actor_id=<uuid> affected=<n>` only when warnings are non-empty (matches the existing `admin_teams_listed`/`system_admin_promoted` log discipline).

Test plan in `backend/tests/api/routes/test_admin_settings.py` (mirrors `test_admin_teams.py`): superuser GET happy path on empty + populated; superuser PUT `workspace_volume_size_gb=4` → 200 with empty warnings; create two workspace_volume rows directly via SQLModel (sizes 4 and 2), PUT to 1 → 200 with warnings listing both rows ordered deterministically + DB-side rows still have their old size_gb; PUT with non-int → 422 invalid_value_for_key; PUT with value=300 → 422; PUT to an unknown key → 422 unknown_setting_key; non-admin PUT → 403; unauthenticated → 401; idempotent PUT (same value twice) → 200 both times, second logs `previous_value_present=true`. Tests follow the MEM029 cookie-jar discipline already used in test_admin_teams.py.

## Inputs

- ``backend/app/alembic/versions/s05_system_settings.py` — T01 migration; head must be applied before tests run`
- ``backend/app/models.py` — T01 added SystemSetting + SystemSettingPublic + SystemSettingPut`
- ``backend/app/api/routes/admin.py` — existing system_admin-gated router; new endpoints land here`
- ``backend/app/api/deps.py` — `get_current_active_superuser` is the existing role gate (no changes)`
- ``backend/tests/api/routes/test_admin_teams.py` — copy fixtures + MEM029 cookie-jar pattern; do not modify`

## Expected Output

- ``backend/app/api/routes/admin.py` — three new handlers (list, get, put) + `_VALIDATORS` registry + `_compute_workspace_size_warnings(session, new_value)` helper`
- ``backend/app/models.py` — adds `SystemSettingShrinkWarning(user_id: UUID, team_id: UUID, size_gb: int, usage_bytes: int|None)` and `SystemSettingPutResponse(SystemSettingPublic, warnings: list[SystemSettingShrinkWarning])` if not already added in T01`
- ``backend/tests/api/routes/test_admin_settings.py` — full test suite (≥10 cases) covering happy path + 403/401/422/idempotency + warnings payload`

## Verification

cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_admin_settings.py tests/api/routes/test_admin_teams.py -v
