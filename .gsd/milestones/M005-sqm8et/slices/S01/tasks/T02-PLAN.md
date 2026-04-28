---
estimated_steps: 1
estimated_files: 3
skills_used: []
---

# T02: Per-key validator registry + service helpers (`get_team_secret`, encrypt/store)

Add `backend/app/api/team_secrets_registry.py` with the `_VALIDATORS` dict shape mirroring `system_settings` (key → `{validator: Callable[[str], None], sensitive: bool}`). Register `claude_api_key` (sk-ant- prefix, length ≥ 40) and `openai_api_key` (sk- prefix, length ≥ 40). Add `backend/app/api/team_secrets.py` service module with: (a) `set_team_secret(session, team_id, key, plaintext)` — validates against registry, encrypts via `encrypt_setting`, upserts the row, commits; (b) `get_team_secret(session, team_id, key) -> str` — fetches the row (raises `MissingTeamSecretError` if not found), decrypts via `decrypt_setting`, raises `TeamSecretDecryptError(team_id, key)` on `cryptography.fernet.InvalidToken`; (c) `delete_team_secret(session, team_id, key) -> bool`; (d) `list_team_secret_status(session, team_id) -> list[TeamSecretStatus]`. Add unit tests covering each helper including the decrypt-failure path (tamper the value_encrypted, expect TeamSecretDecryptError).

## Inputs

- ``backend/app/core/encryption.py` (`encrypt_setting`, `decrypt_setting`, `SystemSettingDecryptError`)`
- ``backend/app/api/system_settings.py` `_VALIDATORS` shape (registry pattern)`
- `T01's TeamSecret model`

## Expected Output

- `Validator registry rejects unknown keys with KeyError`
- ``set_team_secret` round-trips a value through encrypt+decrypt successfully`
- ``get_team_secret` raises `MissingTeamSecretError` for absent rows`
- ``get_team_secret` raises `TeamSecretDecryptError` for corrupted ciphertext`
- ``delete_team_secret` returns True for present rows, False for absent`
- ``list_team_secret_status` returns one entry per registered key (with has_value=False for unset)`

## Verification

cd backend && uv run pytest tests/api/test_team_secrets_helpers.py -v

## Observability Impact

No log emission at this task level — emitted by API layer in T03.
