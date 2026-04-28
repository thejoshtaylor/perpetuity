---
estimated_steps: 1
estimated_files: 4
skills_used: []
---

# T03: Team-admin API router (PUT/GET/DELETE/list)

Add `backend/app/api/routes/team_secrets.py` FastAPI router exposing `PUT /api/v1/teams/{team_id}/secrets/{key}`, `GET /api/v1/teams/{team_id}/secrets/{key}`, `GET /api/v1/teams/{team_id}/secrets`, `DELETE /api/v1/teams/{team_id}/secrets/{key}`. Use existing `assert_caller_is_team_admin` for write paths (PUT, DELETE) and `assert_caller_is_team_member` for read paths (both GETs). Map exceptions: unknown key → 400 `unregistered_key`; validator failure → 400 `invalid_value_shape`; missing row on single GET → 404 `team_secret_not_set`; `MissingTeamSecretError` from helper → 404 (used downstream); `TeamSecretDecryptError` → 503 `team_secret_decrypt_failed` via global exception handler in `backend/app/main.py` (mirroring M004's `SystemSettingDecryptError` handler). Emit INFO log `team_secret_set` on successful PUT and `team_secret_deleted` on successful DELETE (team_id + key only, never the value). Register the router in `backend/app/api/main.py`.

## Inputs

- ``backend/app/api/team_access.py` (`assert_caller_is_team_admin`, `assert_caller_is_team_member`)`
- `T02's `set_team_secret` / `get_team_secret` / `delete_team_secret` / `list_team_secret_status``
- `M004 webhook receiver pattern in `backend/app/api/routes/github_webhooks.py` for global exception handler shape`
- `MEM089: register router via existing `backend/app/api/main.py` include_router pattern`

## Expected Output

- `Team-admin can PUT a valid Claude key (200) and a valid OpenAI key (200)`
- `Team-member (non-admin) PUT returns 403 with `team_admin_required``
- `Bad-prefix value returns 400 `invalid_value_shape``
- `Unknown key returns 400 `unregistered_key``
- `GET single returns `{key, has_value, sensitive, updated_at}` with no `value` field`
- `GET list returns one entry per registered key`
- `DELETE 204; subsequent DELETE 404`
- `INFO log `team_secret_set` and `team_secret_deleted` emitted with team_id + key, no value`

## Verification

cd backend && uv run pytest tests/api/test_team_secrets_routes.py -v

## Observability Impact

INFO logs `team_secret_set` and `team_secret_deleted` emitted at PUT/DELETE success. ERROR log `team_secret_decrypt_failed` registered via global exception handler (fires when downstream `get_team_secret` raises). All three log lines redaction-clean by construction (no value field).
