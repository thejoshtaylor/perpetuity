# S01: Sensitive system_settings + GitHub App credentials registered — UAT

**Milestone:** M004-guylpp
**Written:** 2026-04-26T00:29:51.308Z

## UAT — Sensitive system_settings + GitHub App credentials registered

### Preconditions

- `docker compose build backend orchestrator` completed; `backend:latest` includes `s06_system_settings_sensitive.py`.
- `docker compose up -d db redis orchestrator backend` healthy.
- `SYSTEM_SETTINGS_ENCRYPTION_KEY` is set to a valid 32-byte url-safe base64 Fernet key in compose env (verify with `docker compose config | grep SYSTEM_SETTINGS_ENCRYPTION_KEY`).
- FIRST_SUPERUSER credentials available (`admin@example.com` / configured password).
- `system_settings` table empty of `github_app_*` rows: `docker compose exec db psql -U postgres -d app -c "DELETE FROM system_settings WHERE key LIKE 'github_app_%';"`

---

### Scenario 1 — PEM PUT redacts on GET

**Goal:** Admin pastes a GitHub App private key (PEM) once; subsequent reads return `has_value:true, value:null, sensitive:true`.

1. Log in as `admin@example.com` via `POST /api/v1/login/access-token`. Capture access token.
2. `PUT /api/v1/admin/settings/github_app_private_key` with body `{"value": "-----BEGIN RSA PRIVATE KEY-----\n<random base64 ~2KiB>\n-----END RSA PRIVATE KEY-----"}`.
   **Expected:** 200; response body has `key="github_app_private_key", value=null, updated_at=<iso>` (warnings absent or empty). Plaintext does NOT cross back.
3. `docker compose exec db psql -U postgres -d app -c "SELECT length(value_encrypted), value, sensitive, has_value FROM system_settings WHERE key='github_app_private_key';"`
   **Expected:** `(<positive int>, NULL, t, t)`.
4. `docker compose logs backend --since=30s | grep system_setting_updated`
   **Expected:** Contains `system_setting_updated actor_id=<admin-uuid> key=github_app_private_key sensitive=true previous_value_present=false`. Plaintext PEM body NEVER appears in any log line.
5. `GET /api/v1/admin/settings/github_app_private_key`
   **Expected:** 200, body `{"key": "github_app_private_key", "value": null, "has_value": true, "sensitive": true, "updated_at": "..."}`.
6. `GET /api/v1/admin/settings`
   **Expected:** 200, list contains the row with `value=null, has_value=true, sensitive=true`. Plaintext not present.

---

### Scenario 2 — Generate webhook secret yields one-shot plaintext

**Goal:** Admin clicks Generate; response shows the secret exactly once; subsequent GET shows redacted.

1. `POST /api/v1/admin/settings/github_app_webhook_secret/generate` with empty body.
   **Expected:** 200, body `{"key": "github_app_webhook_secret", "value": "<≥32 url-safe chars>", "has_value": true, "generated": true, "updated_at": "..."}`. Capture `value` as `secret_v1`.
2. `docker compose logs backend --since=10s | grep system_setting_generated`
   **Expected:** Line `system_setting_generated actor_id=<admin-uuid> key=github_app_webhook_secret`. The string `secret_v1` does NOT appear in any log line.
3. `GET /api/v1/admin/settings/github_app_webhook_secret`
   **Expected:** 200, body `{"key": "github_app_webhook_secret", "value": null, "has_value": true, "sensitive": true, "updated_at": "..."}`. One-time-display semantics confirmed.

---

### Scenario 3 — Destructive re-generate (D025)

**Goal:** Re-calling generate is destructive on every call.

1. `POST /api/v1/admin/settings/github_app_webhook_secret/generate` (second call) with empty body.
   **Expected:** 200, body carries a fresh `value` ≠ `secret_v1`. Capture as `secret_v2`.
2. Document expectation: in production, this would invalidate any in-flight GitHub webhook deliveries until the operator updates the upstream secret in GitHub. Operator workflow is documented in the S07 runbook.

---

### Scenario 4 — Negative validation shapes

**Goal:** Invalid PUT values, missing generators, and unknown keys all return 422 with structured detail.

1. `PUT /api/v1/admin/settings/github_app_private_key` with body `{"value": "not-a-pem"}`.
   **Expected:** 422, detail `{"detail": "invalid_value_for_key", "key": "github_app_private_key", ...}`.
2. `POST /api/v1/admin/settings/github_app_private_key/generate` with empty body.
   **Expected:** 422, detail `{"detail": "no_generator_for_key", "key": "github_app_private_key"}`.
3. `POST /api/v1/admin/settings/bogus_key/generate` with empty body.
   **Expected:** 422, detail `{"detail": "unknown_setting_key", "key": "bogus_key"}`.

---

### Scenario 5 — Auth gating

**Goal:** Non-superusers cannot read or mutate sensitive settings.

1. Without auth header, `GET /api/v1/admin/settings/github_app_private_key`
   **Expected:** 401.
2. As a normal (non-superuser) user, `POST /api/v1/admin/settings/github_app_webhook_secret/generate`
   **Expected:** 403.

---

### Scenario 6 — Corrupted ciphertext surfaces as decrypt-failure

**Goal:** Fernet decrypt failure on a corrupted ciphertext produces 503 + structured ERROR log naming the key.

**Note:** S01 has no HTTP endpoint that calls `decrypt_setting` on a sensitive row directly (sensitive GETs are always redacted). This scenario proves the structured ERROR log shape via a docker-exec helper; the full HTTP-503 round-trip will be exercised by S02's JWT-sign call site.

1. Corrupt the stored ciphertext: `docker compose exec db psql -U postgres -d app -c "UPDATE system_settings SET value_encrypted = E'\\\\xdeadbeef' WHERE key='github_app_private_key';"`
2. Run a docker-exec script in the sibling backend that opens a SQLModel session, fetches the row, calls `decrypt_setting(row.value_encrypted)`, catches `SystemSettingDecryptError`, and replays the structured ERROR line on PID 1 stderr (the same stream the FastAPI handler writes to under HTTP).
3. `docker compose logs backend --since=10s | grep system_settings_decrypt_failed`
   **Expected:** Contains `system_settings_decrypt_failed key=github_app_private_key` at ERROR level. No plaintext appears.
4. (S02 forward-reference) Once S02 lands `GET /v1/installations/{id}/token`, that endpoint hitting a corrupted private key MUST return HTTP 503 with body `{"detail": "system_settings_decrypt_failed", "key": "github_app_private_key"}` via the global handler in `backend/app/main.py`.

---

### Scenario 7 — Redaction sweep

**Goal:** Backend logs never carry plaintext for sensitive keys.

1. `docker compose logs backend --since=5m | grep -E '(BEGIN RSA|<secret_v1 substr>|<secret_v2 substr>)' | wc -l`
   **Expected:** 0 matches.
2. Confirm three observability markers present in the same window:
   - `system_setting_updated ... key=github_app_private_key sensitive=true`
   - `system_setting_generated ... key=github_app_webhook_secret` (×2 from the two generate calls)
   - `system_settings_decrypt_failed key=github_app_private_key`

---

### Scenario 8 — Boot-time encryption key validation

**Goal:** Missing or malformed `SYSTEM_SETTINGS_ENCRYPTION_KEY` fails fast.

1. Stop backend: `docker compose stop backend`.
2. Unset the key in compose env (or set to malformed value like `"not-base64"`) and try `docker compose up backend`.
   **Expected (no key):** Compose exits with `"error while interpolating services.backend.environment ... required variable SYSTEM_SETTINGS_ENCRYPTION_KEY is missing"`.
   **Expected (malformed key):** Container starts, but the first encrypt/decrypt call (in any code path that touches sensitive settings) raises `RuntimeError` naming `SYSTEM_SETTINGS_ENCRYPTION_KEY` with a message about base64 decode or 32-byte length.
3. Restore valid key and `docker compose up -d backend`.
   **Expected:** Backend healthy; subsequent encrypt/decrypt round-trips succeed.

---

### Tear-Down

`docker compose exec db psql -U postgres -d app -c "DELETE FROM system_settings WHERE key LIKE 'github_app_%';"` to leave the table clean for the next slice.

---

### Pass Criteria

All 8 scenarios complete with the expected outcomes. The single automated e2e (`backend/tests/integration/test_m004_s01_sensitive_settings_e2e.py`) covers Scenarios 1, 2, 3, 4, 6, and 7 in 7.9 s on the live compose stack. Scenarios 5 and 8 are operator-checked once before the milestone closes (S07 captures the formal recording).
