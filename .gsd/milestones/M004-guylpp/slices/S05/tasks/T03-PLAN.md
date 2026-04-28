---
estimated_steps: 1
estimated_files: 1
skills_used: []
---

# T03: End-to-end webhook contract test against live compose stack

Write the e2e test that proves the slice demo against the live compose stack (sibling backend container, real Postgres, real FastAPI process). Pattern follows test_m004_s01_sensitive_settings_e2e.py: pytest.mark.e2e, autouse cleanup that DELETEs all rows from github_webhook_events and webhook_rejections before and after (the app-db-data volume persists across runs per MEM161), skip-guard that probes the baked backend:latest image for the s06e migration revision file (mirrors S01 T04's MEM147/MEM162 guard so a stale image doesn't produce confusing failures). The flow: (1) log in as FIRST_SUPERUSER; (2) POST /api/v1/admin/settings/github_app_webhook_secret/generate to seed the webhook secret — capture the plaintext from the one-time-display response; (3) build a synthetic webhook payload (JSON dict mimicking a push event with action/repository/installation), compute the HMAC-SHA256 over the raw bytes using the captured secret, POST it to /api/v1/github/webhooks with the X-Hub-Signature-256 header — assert 200, assert the row landed in github_webhook_events with the expected delivery_id and event_type, assert backend logs contain webhook_received + webhook_verified + webhook_dispatched all three with the same delivery_id; (4) POST the same payload + signature again with the SAME delivery_id — assert 200, assert the table still has only ONE row (idempotency); (5) POST the payload with an INVALID signature (modify one byte) — assert 401 with detail invalid_signature, assert webhook_rejections gained a row with signature_valid=false, assert no new github_webhook_events row, assert WARNING log webhook_signature_invalid; (6) POST with NO X-Hub-Signature-256 header — assert 401, assert webhook_rejections row with signature_present=false; (7) corrupt the github_app_webhook_secret ciphertext via psql UPDATE, then POST a valid-by-old-secret payload — assert 503 with detail system_settings_decrypt_failed and key=github_app_webhook_secret, assert ERROR log system_settings_decrypt_failed key=github_app_webhook_secret (this is the first true 503-via-HTTP test of the global handler — S01 T04 only proved the log shape via docker-exec); (8) redaction sweep across the sibling-backend docker logs for the captured plaintext webhook secret — must return zero matches. Wall-clock budget ≤ 30 s (no container provisioning beyond the existing sibling backend). Use the same _docker / _psql_one / _backend_logs helper pattern from test_m004_s01_sensitive_settings_e2e.py — extract them into the test file (do not refactor conftest.py for this slice).

## Inputs

- ``backend/app/api/routes/github_webhooks.py` — route under test (T02 output)`
- ``backend/app/services/dispatch.py` — dispatch stub under test (T02 output)`
- ``backend/app/alembic/versions/s06e_github_webhook_events.py` — schema target probed by skip-guard (T01 output)`
- ``backend/app/models.py` — GitHubWebhookEvent / WebhookRejection used for DB inspection assertions (T01 output)`
- ``backend/tests/integration/test_m004_s01_sensitive_settings_e2e.py` — pattern to mirror: pytestmark, autouse cleanup, skip-guard, _docker / _psql_one / _backend_logs helpers, sibling backend boot via the backend_url fixture`
- ``backend/tests/integration/conftest.py` — backend_url fixture and SYSTEM_SETTINGS_ENCRYPTION_KEY_TEST constant used in the sibling backend boot`

## Expected Output

- ``backend/tests/integration/test_m004_s05_webhook_receiver_e2e.py` — single test_full_webhook_contract_e2e function exercising all eight scenarios (generate secret, valid post, idempotent re-post, invalid signature, absent signature, decrypt failure 503-via-HTTP, redaction sweep, autouse cleanup)`

## Verification

cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m004_s05_webhook_receiver_e2e.py -v

## Observability Impact

The test asserts the contract log keys (webhook_received, webhook_verified, webhook_dispatched, webhook_signature_invalid, system_settings_decrypt_failed) appear in the sibling backend's stderr — locking the observability shape that S07's runbook depends on. Redaction sweep confirms the webhook secret plaintext never reaches logs.
