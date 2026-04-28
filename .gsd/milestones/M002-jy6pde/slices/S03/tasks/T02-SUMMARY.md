---
id: T02
parent: S03
milestone: M002-jy6pde
key_files:
  - backend/app/api/routes/admin.py
  - backend/app/models.py
  - backend/tests/api/routes/test_admin_settings.py
key_decisions:
  - Reject-by-default on unknown setting keys via `_VALIDATORS` registry — typos can't silently add unread rows
  - Reject Python `bool` explicitly in the workspace_volume_size_gb validator (since bool is a subclass of int) so JSON `true` doesn't coerce to 1
  - Use raw `text()` UPSERT (INSERT ... ON CONFLICT DO UPDATE) with json.dumps + CAST(:value AS JSONB) — cleaner than SQLModel for `Any`-typed JSONB columns when the value can be a scalar
  - Compute shrink warnings via SELECT WHERE size_gb > :new_value ORDER BY created_at; do NOT mutate existing rows (D015 partial-apply: cap divergence allowed)
  - Report usage_bytes as `null` from this slice — backend container doesn't bind-mount the host workspace path; S04 will add an orchestrator usage lookup
  - Append the three handlers to the existing admin router (MEM089) rather than create a new module — keeps 403/401 ordering uniform
duration: 
verification_result: passed
completed_at: 2026-04-25T11:46:13.085Z
blocker_discovered: false
---

# T02: Wire GET/PUT /api/v1/admin/settings[/{key}] with per-key validators and workspace_volume_size_gb partial-apply shrink warnings

**Wire GET/PUT /api/v1/admin/settings[/{key}] with per-key validators and workspace_volume_size_gb partial-apply shrink warnings**

## What Happened

Added three system-admin endpoints to `backend/app/api/routes/admin.py`, reusing the existing router-level `dependencies=[Depends(get_current_active_superuser)]` so the 403/401 ordering matches every other admin endpoint (MEM061/MEM089).

GET /admin/settings returns `{data: [SystemSettingPublic, ...], count}` ordered by key. GET /admin/settings/{key} returns the single SystemSettingPublic or 404 `{detail: 'setting_not_found'}`. PUT /admin/settings/{key} validates via a per-key registry (`_VALIDATORS: dict[str, Callable]`), UPSERTs via raw `INSERT ... ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW() RETURNING ...`, then returns the new SystemSettingPutResponse with a `warnings` list. Reject-by-default on unknown keys returns 422 `{detail: 'unknown_setting_key', key}` — closes the foot-gun where a typo would silently add an unread row.

The `workspace_volume_size_gb` validator enforces `isinstance(value, int) and 1 <= value <= 256` (matching the orchestrator's volume_store range, MEM094). bool is rejected explicitly because `isinstance(True, int) == True` would otherwise coerce JSON booleans. Type/range failures raise HTTPException(422, detail={'detail': 'invalid_value_for_key', 'key', 'reason': 'must be int in 1..256'}).

Partial-apply shrink (D015): `_compute_workspace_size_warnings` runs `SELECT * FROM workspace_volume WHERE size_gb > :new_value ORDER BY created_at` and returns one `SystemSettingShrinkWarning(user_id, team_id, size_gb, usage_bytes)` per row. usage_bytes is `null` in this slice — the backend container does not bind-mount the host workspace path, so on-disk usage is unreachable; S04 will add a backend→orchestrator GET /v1/volumes/{volume_id}/usage call. Existing rows keep their old size_gb (cap divergence allowed); the schema is forward-compatible.

Logging follows the slice's redaction discipline: `system_setting_updated actor_id=<uuid> key=<str> previous_value_present=<true|false>` on every PUT (never logs the value because future settings could carry secrets); `system_setting_shrink_warnings_emitted key=workspace_volume_size_gb actor_id=<uuid> affected=<n>` only when warnings are non-empty. Lowercase booleans match the existing `already_admin=true|false` convention (MEM062).

Models: added `SystemSettingShrinkWarning(user_id, team_id, size_gb, usage_bytes: int|None)` and `SystemSettingPutResponse(key, value, updated_at, warnings)` to `backend/app/models.py`.

Tests in `backend/tests/api/routes/test_admin_settings.py` (17 cases, ≥10 required) cover: empty/populated GET happy paths; GET-by-key 200/404; PUT happy path with empty warnings; idempotent PUT logs `previous_value_present=true` on the second call; first-call logs `previous_value_present=false`; shrink warnings populated correctly with two seeded volumes ordered by created_at and DB rows unchanged after PUT; no shrink-warnings log line emitted when warnings are empty; non-int → 422 invalid_value_for_key; out-of-range (300, 0) → 422; unknown key → 422 unknown_setting_key; non-admin PUT → 403; unauthenticated PUT → 401; unauthenticated/normal-user GET list also gated. An autouse fixture wipes `system_settings` and `workspace_volume` before/after each test so state is isolated. Follows MEM029 detached-cookie-jar discipline.

All 17 new tests pass; the existing `test_admin_teams.py` 15 tests still pass (32 total in the verification command).

## Verification

Ran the task plan's verification command from /Users/josh/code/perpetuity/backend:

  POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_admin_settings.py tests/api/routes/test_admin_teams.py -v

All 32 tests passed in 0.86s. The Postgres instance on host port 5432 (perpetuity-db-1) was used; alembic head is `s05_system_settings`. Sanity-checked the new module imports cleanly via `uv run python -c "from app.api.routes import admin"`.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_admin_settings.py tests/api/routes/test_admin_teams.py -v` | 0 | pass | 860ms |
| 2 | `POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_admin_settings.py -v` | 0 | pass | 380ms |
| 3 | `POSTGRES_PORT=5432 uv run python -c 'from app.api.routes import admin'` | 0 | pass | 200ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `backend/app/api/routes/admin.py`
- `backend/app/models.py`
- `backend/tests/api/routes/test_admin_settings.py`
