---
estimated_steps: 15
estimated_files: 1
skills_used: []
---

# T04: e2e: register PEM, generate webhook secret, redacted GET, decrypt-failure 503

Land `backend/tests/integration/test_m004_s01_sensitive_settings_e2e.py` proving the full sensitive-storage contract against the live compose stack via the sibling `backend_url` fixture. Mirrors the structure of `test_m002_s03_settings_e2e.py` (login as FIRST_SUPERUSER, hit admin endpoints over httpx, inspect `system_settings` rows via `docker exec ... psql`, scan backend container logs).

The test must include an autouse fixture that DELETEs every `github_app_*` key from `system_settings` before AND after the test (MEM161 — compose's `app-db-data` volume persists across runs). It also sets/asserts the `SYSTEM_SETTINGS_ENCRYPTION_KEY` env var on the sibling backend (T01 wired the conftest fixture; this test inherits it, but add a smoke-assert: `_psql_one("SELECT count(*) FROM system_settings WHERE sensitive=true")` returns `0` before the test and `2` after the PEM and webhook secret are written).

Flow:
  1. Skip-guard: `_backend_image_has_s06()` probes for `s06_system_settings_sensitive.py` in the baked image (mirror MEM147 / `_backend_image_has_s05` from the M002/S03 test); skip with the canonical `docker compose build backend` hint if absent.
  2. Log in as `admin@example.com`.
  3. PUT `/api/v1/admin/settings/github_app_private_key` with a synthetic PEM body (a real-shape PEM string starting `-----BEGIN RSA PRIVATE KEY-----` and ending `-----END RSA PRIVATE KEY-----` with random base64 in the middle — does NOT need to be a cryptographically valid key; the API validator is structural). Assert 200, response shape `{key, value: null, has_value: true, sensitive: true, updated_at: <iso>}`. Assert backend log contains `system_setting_updated actor_id=<admin_uuid> key=github_app_private_key sensitive=true previous_value_present=false`. Inspect DB: `SELECT length(value_encrypted), value, sensitive, has_value FROM system_settings WHERE key='github_app_private_key'` returns `(<positive int>, null, t, t)`.
  4. GET `/api/v1/admin/settings/github_app_private_key` → assert `value` is null and `has_value` is true and `sensitive` is true.
  5. POST `/api/v1/admin/settings/github_app_webhook_secret/generate` with empty body → 200 → response carries `value` as a non-empty string (assert `len(value) >= 32`), `has_value=true`, `generated=true`. Backend log contains `system_setting_generated`. The plaintext value MUST appear in the POST response body but MUST NOT appear in the backend log (assert by substring).
  6. GET `/api/v1/admin/settings/github_app_webhook_secret` → `value: null, has_value: true, sensitive: true` (one-time-display semantics).
  7. POST the same generate endpoint a second time → 200, value differs from step 5's value (proves destructive re-generate; D025).
  8. Negative cases: PUT `github_app_private_key` with a non-PEM string → 422 invalid_value_for_key with `key=github_app_private_key`; POST generate against `github_app_private_key` → 422 `no_generator_for_key`; POST generate against `bogus_key` → 422 `unknown_setting_key`.
  9. Decrypt-failure 503: directly corrupt one byte of the stored ciphertext via `psql -c "UPDATE system_settings SET value_encrypted = E'\\\\xdeadbeef' WHERE key='github_app_private_key'"`. Trigger a decrypt by hitting a future endpoint that calls `decrypt_setting` on the private key — but S01 has no such endpoint yet. Workaround: add a pytest-only `GET /admin/settings/{key}/_decrypt_probe` endpoint OR (preferred) call `decrypt_setting` directly inside the test via `docker exec backend python -c 'from app.core.encryption import decrypt_setting; ...'`. Decision documented inline: use the docker-exec path so the backend code stays free of test-only routes, mirroring the M002/S03 test's `_psql_one` discipline. Assert the `docker exec` exits non-zero and that the same operation through an HTTP handler (added in S02) would surface 503; for S01 we close the loop by asserting the backend container log carries `system_settings_decrypt_failed key=github_app_private_key` after the docker-exec invocation triggers the helper. (S02's first real HTTP consumer will flip this to a true 503-via-HTTP assertion.)
 10. Redaction sweep: scan `_backend_logs(container)` for the synthetic PEM body's middle base64 substring and the webhook secret string from step 5; assert neither appears.
 11. Tear down: the autouse fixture DELETEs the rows.

Mark `pytest.mark.e2e`. Wall-clock budget ≤ 30 s — no container provisioning, just admin API calls + one psql UPDATE + one docker-exec.

## Inputs

- ``backend/tests/integration/conftest.py``
- ``backend/tests/integration/test_m002_s03_settings_e2e.py``
- ``backend/app/api/routes/admin.py``
- ``backend/app/core/encryption.py``
- ``backend/app/alembic/versions/s06_system_settings_sensitive.py``

## Expected Output

- ``backend/tests/integration/test_m004_s01_sensitive_settings_e2e.py``

## Verification

From `/Users/josh/code/perpetuity`: (1) `docker compose build backend orchestrator` completes; (2) `docker compose up -d db redis orchestrator` healthy; (3) `cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m004_s01_sensitive_settings_e2e.py -v` passes; (4) the test's redaction sweep block asserts neither the synthetic PEM middle nor the generated webhook secret appears in backend logs.

## Observability Impact

Test asserts the slice's full observability taxonomy actually fires: `system_setting_updated key=github_app_private_key sensitive=true`, `system_setting_generated key=github_app_webhook_secret`, `system_settings_decrypt_failed key=github_app_private_key`. If any of these strings is missing from backend logs the assertion fails — closes the M002 pattern of having tests that lock the observability taxonomy in place.
