# S01: Per-team AI credentials at rest

**Goal:** Land the `team_secrets` table + Fernet-encrypted at-rest storage + team-admin-only API + frontend paste-once UI for `claude_api_key` and `openai_api_key`. Reuse M004/S01's encryption discipline (same Fernet key, same decrypt-only-at-call-site rule, same loud failure on InvalidToken) without inventing a new module. Closes the credential-storage boundary that S02–S06 read from via a new `get_team_secret(team_id, key)` helper.
**Demo:** Team admin opens team settings, pastes Claude API key (`sk-ant-...`) into the new AI Credentials panel, clicks Save; subsequent GET shows `has_value: true` with no value flowing back to UI. Same for OpenAI key. Non-admin user gets 403 on PUT. Decrypt failure surfaces as 503 with `{detail: 'system_settings_decrypt_failed', key: 'claude_api_key'}` and an ERROR log naming team_id + key.

## Must-Haves

- (1) Alembic revision `s09_team_secrets.py` creates `team_secrets(team_id UUID FK PK CASCADE, key VARCHAR(64) PK, value_encrypted BYTEA NOT NULL, has_value BOOLEAN NOT NULL DEFAULT TRUE, sensitive BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())` with composite PK and FK CASCADE on team delete; upgrade/downgrade round-trip test passes from `s08_push_subscriptions` and back.
- (2) Per-key validator registry at `backend/app/api/team_secrets_registry.py` registers exactly two keys for M005: `claude_api_key` (validator: starts-with `sk-ant-`, length ≥ 40) and `openai_api_key` (validator: starts-with `sk-`, length ≥ 40). Sensitive=True on both. Registration shape mirrors `system_settings` `_VALIDATORS` registry (MEM089 pattern).
- (3) `PUT /api/v1/teams/{team_id}/secrets/{key}` accepts `{"value": "<plaintext>"}`, runs the registered validator, encrypts via existing `encrypt_setting` from `backend/app/core/encryption.py`, upserts the row with `has_value=true` and `updated_at=now()`. Team-admin gate via existing `assert_caller_is_team_admin`; non-admin → 403 with `{detail: "team_admin_required"}`. Unknown key → 400 `{detail: "unregistered_key", key}`. Validator failure → 400 `{detail: "invalid_value_shape", key, hint}`.
- (4) `GET /api/v1/teams/{team_id}/secrets/{key}` returns `{key, has_value, sensitive, updated_at}` only — never the value, even on accidental misuse. Team-member gate via existing `assert_caller_is_team_member`. Unknown row → 404 `{detail: "team_secret_not_set", key}`. Unknown key (not in registry) → 400.
- (5) `GET /api/v1/teams/{team_id}/secrets` returns the full registry status: `[{key, has_value, sensitive, updated_at | null}, ...]` for every registered key. Team-member gate.
- (6) `DELETE /api/v1/teams/{team_id}/secrets/{key}` removes the row; team-admin gate; idempotent (404 if not set). 204 on success.
- (7) New helper `get_team_secret(session, team_id, key) -> str` in `backend/app/api/team_secrets.py` (or a deps module): decrypts at call site via `decrypt_setting`; on `cryptography.fernet.InvalidToken` raises `TeamSecretDecryptError(team_id, key)` (mirrors M004's `SystemSettingDecryptError`); on missing row raises `MissingTeamSecretError(team_id, key)`. Both exceptions caught by global handlers → 503 (decrypt) and 404 (missing).
- (8) ERROR log emitted on decrypt failure: `team_secret_decrypt_failed team_id={team_id} key={key}` (no value, no value_prefix). INFO logs on PUT (`team_secret_set`) and DELETE (`team_secret_deleted`) — both with team_id + key, never the value.
- (9) Frontend AI Credentials panel: new component `frontend/src/components/team/TeamSecretsPanel.tsx` rendered inside the existing team settings route. Displays both registered keys with `has_value` badge ("Set" / "Not set"); team admin sees Replace + Delete buttons that open paste-once modals; non-admin sees read-only badges. Paste-once modal uses a password-type input with show/hide toggle; submits via React Query mutation; on success refreshes the list.
- (10) Integration test `test_m005_s01_team_secrets_e2e.py` exercises: paste-once via PUT (200) → GET shows has_value=true with no value field → PUT replace (200, updated_at advances) → DELETE (204) → GET shows has_value=false. Non-admin PUT → 403. Validator failure on bad-prefix → 400. `get_team_secret` round-trips a real value end-to-end. Decrypt-failure path: tamper a row's value_encrypted → call `get_team_secret` from a test endpoint → expect 503 with the exact error_class. Test ends with `docker compose logs` redaction sweep grep failing on `sk-ant-` or `sk-`.
- (11) `scripts/redaction-sweep.sh` extended to include `sk-ant-` and `sk-` patterns; passes against the S01 e2e logs.
- (12) MEM162 alembic skip-guard added to the e2e test (probes `s09_team_secrets` revision present in `backend:latest`, skips with rebuild instruction on miss).

## Proof Level

- This slice proves: Live integration. The slice ships a real frontend panel that a team admin uses to paste real keys; encryption + decryption verified via integration test that round-trips a value through the API and a downstream call site (`get_team_secret`). No fixture-only proof — the keys this slice persists are exactly the keys S02 will consume.

## Integration Closure

S01 closes the credential-storage boundary that S02–S06 read from. Every downstream slice that calls `claude` or `codex` reads from `team_secrets` via the new `get_team_secret(session, team_id, key)` helper that returns plaintext (decrypt at call site) or raises `MissingTeamSecretError` if `has_value=false`. The helper's contract is locked here so S02's executor can depend on it: caller catches `MissingTeamSecretError` and surfaces it as step failure with `error_class='missing_team_secret'`. The validator registry shape (key → validator + sensitive flag) is also locked here so future M005+ slices that add registered keys (e.g. `github_pat` for personal connections in M006) can extend without rewriting.

## Verification

- New INFO log keys emitted by this slice: `team_secret_set` (on successful PUT, with team_id + key, never the value), `team_secret_deleted` (on successful DELETE, with team_id + key). New ERROR log key: `team_secret_decrypt_failed` (with team_id + key, never the value or value prefix — distinct from M004's `system_settings_decrypt_failed` which is system-scoped). Redaction sweep extended at this slice to cover `sk-ant-` and `sk-` prefixes — every M005 e2e from S01 onward must pass the extended sweep. No metric counters added in S01; S05 introduces run-level metrics.

## Tasks

- [x] **T01: Migration s09_team_secrets + SQLModel + Pydantic DTOs** `est:1 day`
  Create alembic revision `s09_team_secrets.py` adding the `team_secrets` table with composite PK (team_id, key), FK CASCADE on team delete, columns per success criteria (1). Add `TeamSecret` SQLModel to `backend/app/models.py` with the same field shape and Pydantic Public DTO (`TeamSecretPublic`) that excludes `value_encrypted` entirely (never serialized) and a Status DTO (`TeamSecretStatus`) with `{key, has_value, sensitive, updated_at}` for GET responses. Add migration test `test_s09_team_secrets_migration.py` running upgrade-from-s08 + downgrade round-trip with the existing `_release_autouse_db_session` autouse fixture (per the project memory note about session-scoped autouse `db` fixture holding AccessShareLock).
  - Files: `backend/app/alembic/versions/s09_team_secrets.py`, `backend/app/models.py`, `backend/tests/migrations/test_s09_team_secrets_migration.py`
  - Verify: cd backend && uv run pytest tests/migrations/test_s09_team_secrets_migration.py -v

- [x] **T02: Per-key validator registry + service helpers (`get_team_secret`, encrypt/store)** `est:1 day`
  Add `backend/app/api/team_secrets_registry.py` with the `_VALIDATORS` dict shape mirroring `system_settings` (key → `{validator: Callable[[str], None], sensitive: bool}`). Register `claude_api_key` (sk-ant- prefix, length ≥ 40) and `openai_api_key` (sk- prefix, length ≥ 40). Add `backend/app/api/team_secrets.py` service module with: (a) `set_team_secret(session, team_id, key, plaintext)` — validates against registry, encrypts via `encrypt_setting`, upserts the row, commits; (b) `get_team_secret(session, team_id, key) -> str` — fetches the row (raises `MissingTeamSecretError` if not found), decrypts via `decrypt_setting`, raises `TeamSecretDecryptError(team_id, key)` on `cryptography.fernet.InvalidToken`; (c) `delete_team_secret(session, team_id, key) -> bool`; (d) `list_team_secret_status(session, team_id) -> list[TeamSecretStatus]`. Add unit tests covering each helper including the decrypt-failure path (tamper the value_encrypted, expect TeamSecretDecryptError).
  - Files: `backend/app/api/team_secrets_registry.py`, `backend/app/api/team_secrets.py`, `backend/tests/api/test_team_secrets_helpers.py`
  - Verify: cd backend && uv run pytest tests/api/test_team_secrets_helpers.py -v

- [x] **T03: Team-admin API router (PUT/GET/DELETE/list)** `est:1 day`
  Add `backend/app/api/routes/team_secrets.py` FastAPI router exposing `PUT /api/v1/teams/{team_id}/secrets/{key}`, `GET /api/v1/teams/{team_id}/secrets/{key}`, `GET /api/v1/teams/{team_id}/secrets`, `DELETE /api/v1/teams/{team_id}/secrets/{key}`. Use existing `assert_caller_is_team_admin` for write paths (PUT, DELETE) and `assert_caller_is_team_member` for read paths (both GETs). Map exceptions: unknown key → 400 `unregistered_key`; validator failure → 400 `invalid_value_shape`; missing row on single GET → 404 `team_secret_not_set`; `MissingTeamSecretError` from helper → 404 (used downstream); `TeamSecretDecryptError` → 503 `team_secret_decrypt_failed` via global exception handler in `backend/app/main.py` (mirroring M004's `SystemSettingDecryptError` handler). Emit INFO log `team_secret_set` on successful PUT and `team_secret_deleted` on successful DELETE (team_id + key only, never the value). Register the router in `backend/app/api/main.py`.
  - Files: `backend/app/api/routes/team_secrets.py`, `backend/app/api/main.py`, `backend/app/main.py`, `backend/tests/api/test_team_secrets_routes.py`
  - Verify: cd backend && uv run pytest tests/api/test_team_secrets_routes.py -v

- [x] **T04: Frontend AI Credentials panel + paste-once modal** `est:1 day`
  Add `frontend/src/components/team/TeamSecretsPanel.tsx` rendered inside the existing team settings route (`frontend/src/routes/_layout/teams_.$teamId.tsx` or whichever route holds team settings — inspect the M002+M004 layout to confirm). Panel fetches `GET /api/v1/teams/{team_id}/secrets` via React Query; displays both registered keys with a `has_value` badge ('Set' green / 'Not set' gray) plus `updated_at` timestamp when set. Team admin sees Replace + Delete buttons; non-admin sees read-only badges (use existing role-check hook from `frontend/src/hooks/useTeamRole.ts` or similar). Replace button opens a paste-once modal with a password-type input + show/hide toggle, validates non-empty client-side, submits via React Query mutation that calls PUT, on success invalidates the list query and closes the modal. Delete button confirms then issues DELETE. Add Playwright/Vitest component test covering: panel renders both keys, admin sees buttons, non-admin sees read-only, paste-once modal submits + closes + refreshes list.
  - Files: `frontend/src/components/team/TeamSecretsPanel.tsx`, `frontend/src/routes/_layout/teams_.$teamId.tsx`, `frontend/src/api/teamSecrets.ts`, `frontend/tests/components/TeamSecretsPanel.test.tsx`
  - Verify: cd frontend && npm test -- TeamSecretsPanel

- [x] **T05: Integration e2e + redaction sweep extension + alembic skip-guard** `est:1 day`
  Add `backend/tests/integration/test_m005_s01_team_secrets_e2e.py` running against the full compose stack. Test plan: (a) team admin pastes Claude + OpenAI keys via PUT; GET shows has_value=true; (b) PUT replace bumps updated_at; (c) DELETE clears; subsequent GET shows has_value=false; (d) non-admin PUT → 403; (e) bad-prefix value → 400; (f) round-trip via `get_team_secret` from a test-only endpoint that returns the helper's plaintext to a system_admin caller (gated for tests only — NOT shipped in production routes); (g) tamper test — directly UPDATE the row's value_encrypted to garbage, call the test endpoint, expect 503 `team_secret_decrypt_failed`; (h) at end of test, run `scripts/redaction-sweep.sh` against `docker compose logs` and assert no `sk-ant-` or `sk-` matches. Include MEM162 alembic skip-guard autouse fixture probing for `s09_team_secrets` revision in `backend:latest`. Extend `scripts/redaction-sweep.sh` to grep for `sk-ant-` and `sk-` (in addition to existing `gho_/ghu_/ghr_/github_pat_/-----BEGIN`).
  - Files: `backend/tests/integration/test_m005_s01_team_secrets_e2e.py`, `scripts/redaction-sweep.sh`, `backend/app/api/routes/_test_helpers.py`
  - Verify: cd backend && uv run pytest tests/integration/test_m005_s01_team_secrets_e2e.py -v && bash scripts/redaction-sweep.sh

## Files Likely Touched

- backend/app/alembic/versions/s09_team_secrets.py
- backend/app/models.py
- backend/tests/migrations/test_s09_team_secrets_migration.py
- backend/app/api/team_secrets_registry.py
- backend/app/api/team_secrets.py
- backend/tests/api/test_team_secrets_helpers.py
- backend/app/api/routes/team_secrets.py
- backend/app/api/main.py
- backend/app/main.py
- backend/tests/api/test_team_secrets_routes.py
- frontend/src/components/team/TeamSecretsPanel.tsx
- frontend/src/routes/_layout/teams_.$teamId.tsx
- frontend/src/api/teamSecrets.ts
- frontend/tests/components/TeamSecretsPanel.test.tsx
- backend/tests/integration/test_m005_s01_team_secrets_e2e.py
- scripts/redaction-sweep.sh
- backend/app/api/routes/_test_helpers.py
