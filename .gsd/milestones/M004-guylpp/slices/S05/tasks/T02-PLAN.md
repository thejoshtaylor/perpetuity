---
estimated_steps: 1
estimated_files: 5
skills_used: []
---

# T02: Implement POST /api/v1/github/webhooks with HMAC verify, persistence branches, and dispatch hook

Add the webhook receiver route on the existing github APIRouter and wire the no-op dispatch hook. The route reads the raw request body BEFORE any JSON parsing (HMAC must be computed over the exact bytes GitHub signed — request.body() not request.json()), reads the X-Hub-Signature-256, X-GitHub-Event, X-GitHub-Delivery, and X-GitHub-Hook-Installation-Target-Id headers, fetches the github_app_webhook_secret SystemSetting row, calls decrypt_setting on its value_encrypted bytes, and runs hmac.compare_digest against the computed sha256 HMAC of the raw body. Decrypt failures must NOT be caught — the existing global SystemSettingDecryptError handler in app/main.py translates them to 503 + the structured ERROR log (this is the contract S01 established; the call site MUST raise SystemSettingDecryptError(key=GITHUB_APP_WEBHOOK_SECRET_KEY) so the handler logs the key correctly). On HMAC pass: parse the body as JSON, INSERT...ON CONFLICT DO NOTHING into github_webhook_events keyed by delivery_id (idempotency under GitHub's 24h retry per D025); if the insert returned a row (rowcount==1) call dispatch_github_event(event_type, payload) and emit the contract logs (webhook_received, webhook_verified, webhook_dispatched); if the insert was a no-op (duplicate delivery), still return 200 but emit a single INFO webhook_duplicate_delivery delivery_id=<id> (do NOT call dispatch). On HMAC fail: INSERT a webhook_rejections row with the delivery_id (or NULL if header absent), signature_present, signature_valid=false, source_ip from request.client.host (or 'unknown' if absent), then return 401 with body {detail: 'invalid_signature'}. The payload body MUST NOT be persisted on rejection. If the X-Hub-Signature-256 header is absent entirely, treat as bad signature (signature_present=false). If the github_app_webhook_secret row doesn't exist or has_value is false, return 503 with {detail: 'webhook_secret_not_configured'} and emit a WARNING log webhook_secret_not_configured (no rejection row — this is operator misconfiguration, not a bad-actor probe). Create the new module backend/app/services/__init__.py and backend/app/services/dispatch.py — `dispatch_github_event(event_type: str, payload: dict) -> None` is a no-op for M004 that emits INFO webhook_dispatched delivery_id=<id> event_type=<type> dispatch_status=noop and contains an explicit comment marking M005 as the slice that fills in the body (NotImplementedError is NOT raised — this is a stub, not an unimplemented method). The route is registered on the existing github router (no new include_router call needed in api/main.py). Write focused unit tests in backend/tests/api/routes/test_github_webhooks.py that cover: (a) valid signature → 200 + event row + dispatch invoked + three INFO logs, (b) invalid signature → 401 + rejection row + WARNING log + no event row + no dispatch, (c) absent signature header → 401 + rejection row with signature_present=false, (d) duplicate delivery_id → 200 + only one event row + dispatch invoked exactly once, (e) malformed JSON body with valid signature → 400 + no event row, (f) decrypt failure (mock decrypt_setting to raise SystemSettingDecryptError(key=GITHUB_APP_WEBHOOK_SECRET_KEY)) → 503 via global handler with the named key in the response and the system_settings_decrypt_failed log line, (g) unconfigured webhook secret → 503 webhook_secret_not_configured. Use the existing TestClient pattern from test_github_install.py — no live HTTP, no compose stack.

## Inputs

- ``backend/app/alembic/versions/s06e_github_webhook_events.py` — schema target (T01 output)`
- ``backend/app/models.py` — GitHubWebhookEvent and WebhookRejection classes (T01 output)`
- ``backend/app/core/encryption.py` — decrypt_setting and SystemSettingDecryptError to import`
- ``backend/app/main.py` — confirm the global SystemSettingDecryptError handler is already wired (read-only — no change needed)`
- ``backend/app/api/routes/admin.py` — import GITHUB_APP_WEBHOOK_SECRET_KEY constant`
- ``backend/app/api/routes/github.py` — read existing github router and routing pattern (the new route lives in a sibling module github_webhooks.py to keep file size under the 500-line limit; the new module's router is included in api/main.py)`
- ``backend/tests/api/routes/test_github_install.py` — TestClient pattern + admin login fixture pattern to mirror`

## Expected Output

- ``backend/app/api/routes/github_webhooks.py` — new module exposing POST /api/v1/github/webhooks with HMAC verify, ON CONFLICT DO NOTHING insert into github_webhook_events, rejection insert path, and the structured logs`
- ``backend/app/services/__init__.py` — new package init (empty)`
- ``backend/app/services/dispatch.py` — dispatch_github_event(event_type, payload) no-op stub with M005 marker comment and the webhook_dispatched INFO log`
- ``backend/app/api/main.py` — added `from app.api.routes import github_webhooks` and `api_router.include_router(github_webhooks.router)``
- ``backend/tests/api/routes/test_github_webhooks.py` — comprehensive unit tests covering all seven scenarios (valid sig, invalid sig, absent sig, duplicate delivery, malformed JSON, decrypt failure, unconfigured secret)`

## Verification

cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_github_webhooks.py -v

## Observability Impact

Adds the slice's contract log keys: INFO webhook_received delivery_id=<id> event_type=<type> source_ip=<ip>; INFO webhook_verified delivery_id=<id> event_type=<type>; INFO webhook_dispatched delivery_id=<id> event_type=<type> dispatch_status=noop; INFO webhook_duplicate_delivery delivery_id=<id>; WARNING webhook_signature_invalid delivery_id=<id|NA> source_ip=<ip> signature_present=<bool>; WARNING webhook_secret_not_configured. Failure visibility: SELECT delivery_id, event_type, dispatch_status, dispatch_error FROM github_webhook_events; SELECT delivery_id, signature_present, signature_valid, source_ip FROM webhook_rejections. Decrypt-failure path raises SystemSettingDecryptError(key='github_app_webhook_secret') and never catches — the global handler in app/main.py emits the system_settings_decrypt_failed ERROR + 503.
