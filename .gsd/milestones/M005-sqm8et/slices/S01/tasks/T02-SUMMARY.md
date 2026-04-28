---
id: T02
parent: S01
milestone: M005-sqm8et
key_files:
  - backend/app/api/team_secrets_registry.py
  - backend/app/api/team_secrets.py
  - backend/tests/api/test_team_secrets_helpers.py
key_decisions:
  - TeamSecretDecryptError and MissingTeamSecretError are team-scoped exceptions distinct from M004's SystemSettingDecryptError (slice plan locks the log key `team_secret_decrypt_failed` as separate from `system_settings_decrypt_failed`) — kept the two error classes structurally separate so dashboards and log searches can disambiguate.
  - get_team_secret catches BOTH SystemSettingDecryptError (the wrapped variant `decrypt_setting` raises) AND raw cryptography.fernet.InvalidToken — defense in depth against a future drift where the encryption module's contract changes; without the second branch a contract change would surface as a 500 instead of a 503.
  - ERROR log emission for decrypt failure lives in the helper (not the route) so a caller that catches and retries the exception cannot silently lose the corruption signal. The helper also remains the canonical decrypt site, mirroring M004/S01's discipline.
  - Validator failure messages carry short shape-only reasons (bad_prefix, too_short, must_be_string) and never the plaintext — exposed via `.reason` attribute on InvalidTeamSecretValueError so the API layer (T03) can map directly to the locked 400 shape `{detail: 'invalid_value_shape', key, hint}` without re-parsing.
  - set_team_secret uses a single INSERT … ON CONFLICT (team_id, key) DO UPDATE — the composite PK matches the conflict target exactly so the upsert is a single SQL round-trip with no read-then-write race window.
duration: 
verification_result: passed
completed_at: 2026-04-28T21:44:57.391Z
blocker_discovered: false
---

# T02: Added team_secrets validator registry and service helpers (set/get/delete/list_status) with team-scoped MissingTeamSecretError and TeamSecretDecryptError, all 17 unit tests including the decrypt-tamper path passing.

**Added team_secrets validator registry and service helpers (set/get/delete/list_status) with team-scoped MissingTeamSecretError and TeamSecretDecryptError, all 17 unit tests including the decrypt-tamper path passing.**

## What Happened

Built the storage-boundary helpers S02–S06 will read team API keys through.

`backend/app/api/team_secrets_registry.py` declares `_VALIDATORS: dict[str, _SecretSpec]` with two registered keys: `claude_api_key` (sk-ant- prefix, length ≥ 40) and `openai_api_key` (sk- prefix, length ≥ 40). The shape mirrors `app/api/routes/admin.py::_VALIDATORS` (MEM158/MEM153 pattern) but lives in its own module since the team-scoped registry has its own lifecycle and doesn't share `_SettingSpec`'s `generator` field. `lookup(key)` raises a typed `UnregisteredTeamSecretKeyError(key)` (KeyError subclass) instead of returning None, so call sites cannot accidentally treat "no spec" as "skip validation". Validators raise `InvalidTeamSecretValueError(key, reason)` carrying short shape-only reasons ("bad_prefix", "too_short", "must_be_string") that the API layer in T03 forwards to the caller as a hint — the plaintext never appears in the message.

`backend/app/api/team_secrets.py` exposes the four helpers. `set_team_secret` validates via the registry, encrypts via the existing `encrypt_setting` from `app/core/encryption.py` (no new encryption module — slice plan locks reuse), then runs a single `INSERT … ON CONFLICT (team_id, key) DO UPDATE` against the composite PK. `get_team_secret` does a session.get on the composite PK, decrypts via `decrypt_setting`, and translates `SystemSettingDecryptError` (or a raw `InvalidToken` for defense-in-depth) into `TeamSecretDecryptError(team_id, key)` while emitting an ERROR log line `team_secret_decrypt_failed team_id=... key=...`. The team-scoped exception is distinct from M004's `SystemSettingDecryptError` so log searches and dashboards can disambiguate system-vs-team decrypt failures (slice plan calls this out explicitly). `delete_team_secret` returns a bool so the route can emit a different log line for no-op DELETEs (idempotent — slice plan says 404 on missing, but the helper is reusable for the 204-return contract). `list_team_secret_status` pulls every existing row for the team in one query, then walks `registered_keys()` so the response carries one entry per registered key with `has_value=False` for unset rows — that shape is what the GET-list route returns verbatim in T03 and what the frontend panel renders in T04.

Captured two memories: MEM409 (the team-scoped helper pattern + dual decrypt catch) and MEM410 (Team fixture must use UUID suffix because the unit `db` fixture is session-scoped and Team rows persist between tests — the first attempt at the cross-team isolation test collided on the unique slug index).

## Verification

Ran the slice's verification command `cd backend && uv run pytest tests/api/test_team_secrets_helpers.py -v` — 17 passed in 0.15s. Tests cover (a) the registry boundary (unknown key → typed exception, both validators reject bad prefix / too short / non-string and accept canonical-shape values, registered_keys locks the M005 set to exactly Claude+OpenAI), (b) `set_team_secret` round-trip through encrypt+decrypt with assertions that the plaintext bytes do NOT appear in the ciphertext, (c) overwrite path bumps updated_at and replaces ciphertext, (d) `get_team_secret` raises `MissingTeamSecretError` on absent row, (e) the decrypt-tamper case — direct SQL UPDATE writes garbage into value_encrypted, helper raises `TeamSecretDecryptError` (the team-scoped variant, not the system-scoped one), exception message does NOT contain `sk-ant-`, ERROR log line names team_id+key but never the plaintext or prefix, (f) `delete_team_secret` returns True/False idempotently, (g) `list_team_secret_status` returns one entry per registered key for empty teams, reflects partial sets correctly, and isolates per team. Also re-ran `tests/migrations/test_s09_team_secrets_migration.py` — all 8 T01 tests still pass (no regression). `uv run ruff check` on the three new files is clean.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && uv run pytest tests/api/test_team_secrets_helpers.py -v` | 0 | ✅ pass | 150ms |
| 2 | `cd backend && uv run pytest tests/migrations/test_s09_team_secrets_migration.py -v` | 0 | ✅ pass (T01 regression check) | 460ms |
| 3 | `cd backend && uv run ruff check app/api/team_secrets.py app/api/team_secrets_registry.py tests/api/test_team_secrets_helpers.py` | 0 | ✅ pass | 50ms |

## Deviations

None — followed the task plan exactly. The only minor structural deviation worth noting: I initially imported `InvalidTeamSecretValueError` and `UnregisteredTeamSecretKeyError` into team_secrets.py for documentation clarity, but ruff flagged them unused (they're raised transitively via `lookup()` and `spec.validator()`, not directly), so I removed the imports. The docstring still names both exceptions in the `Raises:` section.

## Known Issues

None at this task layer. The slice still requires T03 (router that maps these helpers' exceptions to 400/404/503), T04 (frontend panel), and T05 (e2e + redaction sweep extension). No log keys at this task level beyond the ERROR `team_secret_decrypt_failed` already emitted by `get_team_secret` — INFO logs for set/delete are explicitly assigned to T03's router per the slice plan.

## Files Created/Modified

- `backend/app/api/team_secrets_registry.py`
- `backend/app/api/team_secrets.py`
- `backend/tests/api/test_team_secrets_helpers.py`
