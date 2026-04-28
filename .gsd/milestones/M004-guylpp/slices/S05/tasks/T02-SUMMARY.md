---
id: T02
parent: S05
milestone: M004-guylpp
key_files:
  - backend/app/api/routes/github_webhooks.py
  - backend/app/services/__init__.py
  - backend/app/services/dispatch.py
  - backend/app/api/main.py
  - backend/tests/api/routes/test_github_webhooks.py
key_decisions:
  - Read raw body via `await request.body()` BEFORE JSON parse so HMAC verification runs against GitHub's exact signed bytes — `request.json()` would re-encode and break the digest
  - `dispatch_github_event` is a true no-op (returns None), NOT a `raise NotImplementedError` — raising would force the route to grow a defensive try/except until M005 lands, exactly the kind of churn the M004 stub avoids
  - Unconfigured secret (no row OR `has_value=false`) returns 503 `webhook_secret_not_configured` and does NOT write a `webhook_rejections` row — that surface is for bad-actor probes, not operator misconfiguration
  - Valid signature + malformed JSON returns 400 `invalid_json` and does NOT write a rejection row — the HMAC step proved the request is signed, so it is not a probe; we just cannot persist it as an event
  - Duplicate-delivery detection uses `ON CONFLICT (delivery_id) DO NOTHING RETURNING id` — `result.first() is not None` is the cross-driver way to know if the insert won; rowcount is unreliable
  - Receiver re-raises `SystemSettingDecryptError(key=GITHUB_APP_WEBHOOK_SECRET_KEY)` rather than letting the inner `key=None` bubble up — keeps the global handler's ERROR log line attributable to the right setting
  - Receiver does NOT require auth or admin gate — the HMAC IS the auth and the route is registered as public so GitHub can post directly
duration: 
verification_result: passed
completed_at: 2026-04-28T01:13:35.476Z
blocker_discovered: false
---

# T02: Add POST /api/v1/github/webhooks with HMAC verify, idempotent persistence, and no-op dispatch hook

**Add POST /api/v1/github/webhooks with HMAC verify, idempotent persistence, and no-op dispatch hook**

## What Happened

Built the M004/S05 webhook receiver. Created `backend/app/api/routes/github_webhooks.py` exposing `POST /api/v1/github/webhooks` (public — the HMAC IS the auth). The route reads `await request.body()` first so HMAC is computed over the exact bytes GitHub signed (NOT `request.json()`, which would re-encode), then loads the `github_app_webhook_secret` system_settings row, calls `decrypt_setting` on the BYTEA `value_encrypted` field, and runs `hmac.compare_digest` between `hmac.new(secret, body, sha256).hexdigest()` and the `sha256=<hex>` portion of `X-Hub-Signature-256`. On HMAC pass: parses JSON, runs `INSERT ... ON CONFLICT (delivery_id) DO NOTHING RETURNING id` into `github_webhook_events` (UNIQUE constraint from T01 enforces GitHub's 24h-retry idempotency per D025/MEM229), and only invokes `dispatch_github_event(event_type, payload, delivery_id=...)` if a row was actually inserted; duplicates emit `webhook_duplicate_delivery` and skip dispatch. On HMAC fail (or absent header): inserts a `webhook_rejections` audit row (delivery_id, signature_present, signature_valid=false, source_ip from `request.client.host`) and returns 401 `invalid_signature` — the body is NOT persisted on rejection (probe surface). Decrypt failure re-raises `SystemSettingDecryptError(key=GITHUB_APP_WEBHOOK_SECRET_KEY)` — the global handler in `app/main.py` translates that to 503 + the `system_settings_decrypt_failed` ERROR log per the S01 contract (decrypt sites raise; never catch). Unconfigured secret (missing row or `has_value=false`) returns 503 `webhook_secret_not_configured` + WARNING log without writing a rejection row (operator misconfiguration ≠ bad-actor probe). Created `backend/app/services/__init__.py` and `backend/app/services/dispatch.py` — the dispatch hook is a no-op stub for M004 emitting `webhook_dispatched delivery_id=<id> event_type=<type> dispatch_status=noop`; M005 fills the body. The receiver route does NOT raise NotImplementedError from dispatch (that would force defensive try/except in the route just to swallow it). Wired the new module into `app/api/main.py` via `api_router.include_router(github_webhooks.router)`. Wrote `backend/tests/api/routes/test_github_webhooks.py` with 9 tests covering all 7 task-plan scenarios: (a) valid signature → 200 + event row + dispatch invoked + INFO logs; (b) invalid signature → 401 + rejection row + WARNING; (c) absent signature header → 401 + rejection signature_present=false; (d) duplicate delivery_id → 200 idempotent + only one row + dispatch invoked exactly once; (e) malformed JSON with valid signature → 400 + no event row + no rejection (HMAC was fine, body is the contract break); (f) decrypt failure (mocked `decrypt_setting`) → 503 via global handler with `key=github_app_webhook_secret`; (g) unconfigured secret → 503 `webhook_secret_not_configured`; plus a `has_value=false` row variant for (g). Tests use the existing `client`/`db` fixtures from `tests/conftest.py` — no live HTTP, no compose stack — and an autouse `_ensure_encryption_key` monkeypatch that sets a deterministic test key and clears the `_load_key` cache (per MEM230/MEM243). The dispatch helper is monkeypatched on `app.api.routes.github_webhooks.dispatch_github_event` (the binding the route resolves at call time); a separate test verifies the real `app.services.dispatch` no-op emits the contract log line.

## Verification

Ran `cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_github_webhooks.py -v` — 9/9 passed in 0.18s. Ran `cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_github_webhooks.py tests/api/routes/test_github_install.py tests/api/routes/test_admin_settings.py -q` — 91/91 passed in 1.88s, confirming the new tests do not regress sibling routers or the admin-settings encryption suite. The 10 failures observed when running with `test_github_webhooks_schema.py` (T01) in the same pytest invocation are pre-existing test pollution from the alembic round-trip in that schema test — unrelated to T02 and out of scope. Verified all four contract log lines fire by `caplog` capture in the happy-path test (`webhook_received`, `webhook_verified`, `webhook_dispatched dispatch_status=noop`) and that WARNING `webhook_signature_invalid` and `webhook_secret_not_configured` fire on their respective branches. Confirmed dispatch is invoked exactly once on duplicate-delivery POSTs (the second POST hits ON CONFLICT DO NOTHING and short-circuits before dispatch). Confirmed plaintext webhook secret never appears in any log line — only the row key name surfaces in the decrypt-failure ERROR log.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_github_webhooks.py -v` | 0 | pass | 180ms |
| 2 | `cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_github_webhooks.py tests/api/routes/test_github_install.py tests/api/routes/test_admin_settings.py -q` | 0 | pass | 1880ms |

## Deviations

None — implemented exactly the seven scenarios in the task plan plus one additional happy-path test that exercises the real (non-mocked) `app.services.dispatch.dispatch_github_event` to confirm the `webhook_dispatched` log line fires from the real module, not just the spy.

## Known Issues

Pre-existing: `test_github_webhooks_schema.py` (T01 output) leaks alembic round-trip state into `test_github_install.py` and `test_admin_settings.py` when run in the same pytest invocation, causing 10 unrelated failures. This is not in scope for T02 and was already documented in T01-SUMMARY (the autouse fixture pattern there mitigates SOME of the pollution but not all).

## Files Created/Modified

- `backend/app/api/routes/github_webhooks.py`
- `backend/app/services/__init__.py`
- `backend/app/services/dispatch.py`
- `backend/app/api/main.py`
- `backend/tests/api/routes/test_github_webhooks.py`
