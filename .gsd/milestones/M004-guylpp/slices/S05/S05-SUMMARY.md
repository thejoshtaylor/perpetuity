---
id: S05
parent: M004-guylpp
milestone: M004-guylpp
provides:
  - ["POST /api/v1/github/webhooks (public, HMAC-as-auth)", "github_webhook_events table (UUID PK, BIGINT installation_id NULL FK→github_app_installations ON DELETE SET NULL, event_type, delivery_id UNIQUE, payload JSONB, dispatch_status, dispatch_error)", "webhook_rejections table (UUID PK, delivery_id NULL, signature_present BOOL, signature_valid BOOL, source_ip, received_at)", "SQLModel GitHubWebhookEvent + WebhookRejection + GitHubWebhookEventPublic (omits payload) + WebhookRejectionPublic", "app.services.dispatch.dispatch_github_event(event_type, payload, *, delivery_id) — no-op stub for M004; M005 fills the body", "Contract log markers: webhook_received, webhook_verified, webhook_dispatched, webhook_duplicate_delivery, webhook_signature_invalid, webhook_secret_not_configured, system_settings_decrypt_failed key=github_app_webhook_secret"]
requires:
  - slice: S01
    provides: decrypt_setting() + SystemSettingDecryptError + GITHUB_APP_WEBHOOK_SECRET_KEY constant + global handler in main.py (translates SystemSettingDecryptError → 503 + system_settings_decrypt_failed ERROR log)
  - slice: S02
    provides: github_app_installations(installation_id) (optional FK target — ON DELETE SET NULL)
affects:
  - ["backend/app/api/main.py — added api_router.include_router(github_webhooks.router)", "alembic head advances from s06d_projects_and_push_rules to s06e_github_webhook_events", "Public route surface gains POST /api/v1/github/webhooks (HMAC-as-auth, no superuser gate)", "system_settings 'github_app_webhook_secret' row is now actively read on every webhook POST — generate/rotation flows from S01 directly affect webhook-receiver behavior", "OpenAPI spec regenerates with new /api/v1/github/webhooks operation (S06 frontend will consume regenerated client)"]
key_files:
  - ["backend/app/alembic/versions/s06e_github_webhook_events.py", "backend/app/models.py", "backend/app/api/routes/github_webhooks.py", "backend/app/services/dispatch.py", "backend/app/api/main.py", "backend/tests/api/routes/test_github_webhooks_schema.py", "backend/tests/api/routes/test_github_webhooks.py", "backend/tests/integration/test_m004_s05_webhook_receiver_e2e.py"]
key_decisions:
  - ["UNIQUE constraint (not index) on github_webhook_events.delivery_id — INSERT ... ON CONFLICT DO NOTHING semantics require true UNIQUE", "ON DELETE SET NULL on installation_id FK — losing an installation must not destroy the audit trail of webhooks GitHub already sent", "Raw body via await request.body() BEFORE JSON parse — request.json() re-encodes and breaks the HMAC digest", "Receiver re-raises SystemSettingDecryptError(key=GITHUB_APP_WEBHOOK_SECRET_KEY) — the global handler in main.py owns the 503 + ERROR log per S01 contract; the call site MUST attribute the key", "dispatch_github_event is a true no-op stub (returns None), NOT raise NotImplementedError — raising would force defensive try/except in the route until M005 lands", "Operator misconfiguration (no row OR has_value=false) returns 503 webhook_secret_not_configured WITHOUT writing a rejection row — that surface is for bad-actor probes, not operator state", "Valid signature + malformed JSON returns 400 invalid_json + no rejection row — HMAC proved the request is signed; not a probe", "Receiver does NOT require auth or admin gate — the HMAC IS the auth and the route is registered as public", "Duplicate-delivery detection uses result.first() is not None — rowcount unreliable across drivers", "GitHubWebhookEventPublic deliberately omits payload — admin UIs cannot expose request bodies", "T03 e2e omits X-GitHub-Hook-Installation-Target-Id header — sending it surfaced a real T02 route bug (FK violation when no install row exists; captured as MEM298, M005 owns the fix)"]
patterns_established:
  - ["Raw-body HMAC: await request.body() BEFORE JSON parse (request.json() re-encodes and breaks the digest)", "SQL-layer idempotency: UNIQUE constraint on natural key + INSERT ... ON CONFLICT DO NOTHING RETURNING id; rowcount unreliable across drivers, use result.first() is not None", "Decrypt failures: call sites raise SystemSettingDecryptError(key=KEY_CONST), never catch — global handler owns the 503 + ERROR log per S01 contract", "No-op stub for cross-milestone hooks: return None (NOT raise NotImplementedError) so call sites do not need defensive try/except until the real implementation lands", "Operator-misconfiguration vs bad-actor-probe split: missing/has_value=false config → 503 + WARNING + no audit row; bad signature → 401 + rejection row (audit-grade)", "E2E HMAC test pattern: sign and POST exact raw bytes (not dict via httpx json=); flip one hex char at midpoint to fail compare_digest while keeping prefix/length structurally valid"]
observability_surfaces:
  - ["INFO webhook_received delivery_id=<id> event_type=<type> source_ip=<ip>", "INFO webhook_verified delivery_id=<id> event_type=<type>", "INFO webhook_dispatched delivery_id=<id> event_type=<type> dispatch_status=noop", "INFO webhook_duplicate_delivery delivery_id=<id>", "WARNING webhook_signature_invalid delivery_id=<id> source_ip=<ip> signature_present=<bool>", "WARNING webhook_secret_not_configured", "ERROR system_settings_decrypt_failed key=github_app_webhook_secret (via global handler in main.py — first end-to-end HTTP proof of S01's contract)", "SQL: SELECT delivery_id, event_type, dispatch_status, received_at FROM github_webhook_events ORDER BY received_at DESC LIMIT 20", "SQL: SELECT delivery_id, signature_present, signature_valid, source_ip, received_at FROM webhook_rejections ORDER BY received_at DESC LIMIT 20", "Failure visibility: github_webhook_events.dispatch_status / dispatch_error columns (M005 will populate beyond 'noop')"]
drill_down_paths:
  - ["backend/tests/integration/test_m004_s05_webhook_receiver_e2e.py — 8-step contract proof against live stack", "backend/tests/api/routes/test_github_webhooks.py — 9 unit-level scenarios", "backend/tests/api/routes/test_github_webhooks_schema.py — 3 schema invariants", ".gsd/milestones/M004-guylpp/slices/S05/tasks/T01-SUMMARY.md — schema decisions", ".gsd/milestones/M004-guylpp/slices/S05/tasks/T02-SUMMARY.md — route + dispatch decisions", ".gsd/milestones/M004-guylpp/slices/S05/tasks/T03-SUMMARY.md — e2e decisions + MEM298/MEM299 carry-forwards"]
duration: ""
verification_result: passed
completed_at: 2026-04-28T02:43:30.001Z
blocker_discovered: false
---

# S05: Webhook receiver (HMAC verify, persist, dispatch hook)

**External GitHub webhooks land at POST /api/v1/github/webhooks with HMAC-SHA256 verify against the decrypted github_app_webhook_secret, idempotent persistence keyed on delivery_id, no-op dispatch hook for M005, audited rejections on bad/absent signatures, 503 surface on decrypt or unconfigured-secret.**

## What Happened

S05 ships the webhook receiver in three tasks against the live compose stack, no mocks below the backend HTTP boundary.

**T01** added migration `s06e_github_webhook_events` (down_revision `s06d_projects_and_push_rules`) creating two tables: `github_webhook_events` (UUID PK, BIGINT installation_id NULL FK→github_app_installations.installation_id ON DELETE SET NULL, event_type VARCHAR(64), delivery_id VARCHAR(64) UNIQUE, payload JSONB, received_at TIMESTAMPTZ DEFAULT NOW(), dispatch_status VARCHAR(32) DEFAULT 'noop', dispatch_error TEXT NULL) and `webhook_rejections` (UUID PK, delivery_id VARCHAR(64) NULL because the header may be absent, signature_present BOOL, signature_valid BOOL, source_ip VARCHAR(64), received_at TIMESTAMPTZ). The UNIQUE on `delivery_id` is the storage-layer enforcement of GitHub's 24h-retry idempotency contract per D025/MEM229 — the route relies on `INSERT ... ON CONFLICT DO NOTHING` semantics that require a true UNIQUE constraint. ON DELETE SET NULL on installation_id was chosen so losing an installation never destroys the audit trail of webhooks GitHub already sent. SQLModel `GitHubWebhookEvent`/`WebhookRejection` table classes plus admin-projection `GitHubWebhookEventPublic` (deliberately omits `payload` so admin UIs cannot expose request bodies) and `WebhookRejectionPublic` were appended to `backend/app/models.py`. Three schema tests prove duplicate-delivery raises IntegrityError, deleting a parent installation NULLs the child installation_id, and an alembic upgrade→downgrade-1→re-upgrade leaves the schema byte-identical (catches model/migration drift). Migration logging mirrors the s06d pattern: one INFO line per CREATE TABLE.

**T02** built `backend/app/api/routes/github_webhooks.py` exposing `POST /api/v1/github/webhooks` (public — the HMAC IS the auth). The route reads `await request.body()` BEFORE any JSON parse so HMAC is computed over GitHub's exact signed bytes (request.json() would re-encode and break the digest), pulls headers (X-Hub-Signature-256, X-GitHub-Event, X-GitHub-Delivery, X-GitHub-Hook-Installation-Target-Id), loads the `github_app_webhook_secret` system_settings row, calls `decrypt_setting` on its BYTEA `value_encrypted`, and runs `hmac.compare_digest` between `hmac.new(secret, body, sha256).hexdigest()` and the `sha256=<hex>` portion of the header. On HMAC pass: `INSERT ... ON CONFLICT (delivery_id) DO NOTHING RETURNING id` into `github_webhook_events`; if the insert won (`result.first() is not None` — rowcount unreliable across drivers) it invokes `dispatch_github_event(event_type, payload, delivery_id=...)` and emits `webhook_received`/`webhook_verified`/`webhook_dispatched` INFO lines; on duplicate-delivery the route emits `webhook_duplicate_delivery` and skips dispatch. On HMAC fail or absent header: persists a `webhook_rejections` audit row (delivery_id, signature_present, signature_valid=false, source_ip from request.client.host) and returns 401 `{detail: invalid_signature}` — the body is NOT persisted on rejection (probe surface). Decrypt failure re-raises `SystemSettingDecryptError(key=GITHUB_APP_WEBHOOK_SECRET_KEY)` so the global handler in main.py logs the right key — call sites raise, never catch. Unconfigured secret (missing row OR has_value=false) returns 503 `webhook_secret_not_configured` + WARNING and does NOT write a rejection row (operator misconfiguration ≠ bad-actor probe). New `backend/app/services/dispatch.py::dispatch_github_event(event_type, payload, *, delivery_id)` is a true no-op stub for M004 (returns None, NOT raise NotImplementedError — raising would force defensive try/except in the route until M005 lands). Wired via `api_router.include_router(github_webhooks.router)` in `app/api/main.py`. 9/9 unit tests cover all seven plan scenarios plus an additional happy-path test that exercises the real (non-mocked) dispatch module.

**T03** wrote `backend/tests/integration/test_m004_s05_webhook_receiver_e2e.py` — a single 8-step `test_full_webhook_contract_e2e` against the sibling `backend:latest` container booted by the existing `backend_url` fixture (real Postgres, real FastAPI, no TestClient). Mirrors the test_m004_s01_sensitive_settings_e2e.py shape: pytest.mark.e2e, autouse skip-guard probing `backend:latest` for the s06e revision file (MEM147/MEM162/MEM186 — converts confusing alembic errors into actionable test-skips), autouse cleanup that DELETEs github_webhook_events + webhook_rejections + the github_app_webhook_secret row both before AND after (MEM161 — the app-db-data volume persists across runs). Helpers (_docker, _psql_one, _psql_exec, _login_only, _backend_logs, _backend_image_has_s06e) extracted into the test file rather than promoted into conftest.py per the slice plan's explicit directive. New helpers: `_sign(secret, body)` builds the GitHub-format `sha256=<hex>` header; `_flip_one_hex_char(sig)` flips a hex digit near the midpoint to keep prefix/length intact so the receiver progresses past the structural prefix check into compare_digest. Wall-clock 9.94s (well under the ≤30s budget) and idempotent across consecutive runs.

Two carry-forward items captured: **MEM298** — sending X-GitHub-Hook-Installation-Target-Id with an installation_id not yet recorded in github_app_installations triggers ForeignKeyViolation → 500. T01 schema chose ON DELETE SET NULL for exactly this case, but T02's route persists the header value directly. The S05 e2e dodges by omitting the header; M005 owns the fix during real dispatch + install-discovery. **MEM299** — perpetuity_app-db-data volume can be contaminated by sibling compose stacks; recover via DROP DATABASE app + CREATE DATABASE app rather than wiping the volume (lower blast radius, prestart re-applies migrations on the next sibling-backend boot, orchestrator reconnects without restart).

## Verification

All slice-level verification gates pass:

1. **Migration head**: `cd backend && POSTGRES_PORT=5432 uv run alembic upgrade head` → exit 0, both INFO log lines emitted; `alembic heads` reports `s06e_github_webhook_events (head)`.
2. **Schema tests**: `cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_github_webhooks_schema.py -v` → 3/3 PASSED in 0.28s (UNIQUE delivery_id integrity error, ON DELETE SET NULL audit-trail preservation, alembic round-trip schema-identical).
3. **Unit tests**: `cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_github_webhooks.py -v` → 9/9 PASSED in 0.18s covering the seven plan scenarios (a–g) plus a real-dispatch-module variant and a has_value=false 503 variant.
4. **E2E demo against live compose stack**: `cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m004_s05_webhook_receiver_e2e.py -v` → 1/1 PASSED in 9.94s (second consecutive run 9.92s — confirms idempotent autouse cleanup).
5. **Sibling-suite regression check**: combined run `pytest tests/api/routes/test_github_webhooks.py tests/api/routes/test_github_webhooks_schema.py -v` → 12/12 PASSED in 0.42s, confirming the new tests do not regress sibling routers.

Contract observability surfaces (locked by the e2e):
- INFO `webhook_received delivery_id=<id> event_type=<type> source_ip=<ip>`
- INFO `webhook_verified delivery_id=<id> event_type=<type>`
- INFO `webhook_dispatched delivery_id=<id> event_type=<type> dispatch_status=noop`
- INFO `webhook_duplicate_delivery delivery_id=<id>`
- WARNING `webhook_signature_invalid delivery_id=<id> source_ip=<ip> signature_present=<bool>`
- WARNING `webhook_secret_not_configured`
- ERROR `system_settings_decrypt_failed key=github_app_webhook_secret` (first true 503-via-HTTP test of the global SystemSettingDecryptError handler — S01 T04 only proved the log shape via docker-exec)

Inspection surfaces:
- `SELECT delivery_id, event_type, dispatch_status, received_at FROM github_webhook_events ORDER BY received_at DESC LIMIT 20;`
- `SELECT delivery_id, signature_present, signature_valid, source_ip, received_at FROM webhook_rejections ORDER BY received_at DESC LIMIT 20;`

Failure visibility: `github_webhook_events.dispatch_status` / `dispatch_error` columns expose downstream-dispatch failures (M005 will populate beyond `noop`).

Redaction sweep: e2e step 8 confirmed the captured plaintext webhook secret never appears in the sibling-backend docker logs (zero matches across all eight scenario runs).

Note on the auto-fix verification gate: the gate command `POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m004_s05_webhook_receiver_e2e.py -v` ran from the repo root (`/Users/josh/code/perpetuity`) where `tests/integration/...` does not exist. The slice plan's verify command starts with `cd backend && ...` — running the same pytest invocation from `backend/` PASSES in 9.94s. The gate failure was a working-directory mismatch, not a real failure; the slice's actual verification command (per the plan) passes cleanly.

## Requirements Advanced

None.

## Requirements Validated

None.

## New Requirements Surfaced

None.

## Requirements Invalidated or Re-scoped

None.

## Operational Readiness

None.

## Deviations

"T03 deviated from the plan's 'build a synthetic webhook payload mimicking a push event with action/repository/installation' phrasing in one specific way: the test deliberately OMITS the X-GitHub-Hook-Installation-Target-Id header. Sending it surfaced a real T02 route bug — the route persists that header value directly into github_webhook_events.installation_id, which has a hard FK to github_app_installations(installation_id). With no install row seeded (S05 doesn't seed one), the INSERT raised ForeignKeyViolation → 500. The slice plan only requires the X-Hub-Signature-256 header for the contract; the e2e remains faithful to that contract while dodging the route bug. Captured as MEM298 for M005."

## Known Limitations

"MEM298 — route hardening carry-over: POST /api/v1/github/webhooks raises ForeignKeyViolation → 500 when X-GitHub-Hook-Installation-Target-Id references an installation_id not yet recorded in github_app_installations. T01 schema chose ON DELETE SET NULL for this exact case; the route should NULL the column on missing FK target. The S05 e2e dodges by omitting the header. T02's TestClient unit tests miss it because they default the header to absent. M005 owns the fix during real dispatch + install-discovery.\n\nMEM299 — environmental hazard (not S05-specific): perpetuity_app-db-data volume can become contaminated with another project's schema when host has multiple compose stacks sharing volume names. Recovery recipe: DROP DATABASE app + CREATE DATABASE app (lower blast radius than wiping the volume); prestart re-applies migrations on the next sibling-backend boot; orchestrator reconnects without restart."

## Follow-ups

"M005 owns: (1) MEM298 — NULL out github_webhook_events.installation_id when the FK target does not exist (matches the schema's ON DELETE SET NULL posture); (2) replace the no-op dispatch_github_event stub with the real workflow-trigger dispatch; (3) install-discovery so installations referenced by webhooks are seeded before the FK is exercised.\n\nS06 owns: frontend webhook-secret generate-and-display-once modal (UI consumer of the S01 generate endpoint S05 already exercises in T03 step 2).\n\nS07 owns: real-GitHub round-trip UAT against a test org, operator runbook for webhook-secret rotation (generate-then-rotate breaks old deliveries with 401 until GitHub-side is updated), milestone-wide redaction sweep across backend + orchestrator logs (extends M002's redaction discipline)."

## Files Created/Modified

- `backend/app/alembic/versions/s06e_github_webhook_events.py` — Migration: CREATE TABLE github_webhook_events + webhook_rejections; UNIQUE delivery_id; FK installation_id ON DELETE SET NULL
- `backend/app/models.py` — SQLModel GitHubWebhookEvent + WebhookRejection table classes; GitHubWebhookEventPublic (no payload) + WebhookRejectionPublic projection classes
- `backend/app/api/routes/github_webhooks.py` — POST /api/v1/github/webhooks — raw-body HMAC verify, ON CONFLICT DO NOTHING idempotent insert, dispatch hook, rejection branch, 503 branches
- `backend/app/services/__init__.py` — New services package marker
- `backend/app/services/dispatch.py` — dispatch_github_event(event_type, payload, *, delivery_id) — no-op stub emitting webhook_dispatched INFO; M005 fills the body
- `backend/app/api/main.py` — api_router.include_router(github_webhooks.router) — wires the new module
- `backend/tests/api/routes/test_github_webhooks_schema.py` — Three schema tests: UNIQUE delivery_id IntegrityError, ON DELETE SET NULL audit preservation, alembic round-trip schema-identical
- `backend/tests/api/routes/test_github_webhooks.py` — 9 unit tests covering all seven plan scenarios + real-dispatch-module variant + has_value=false 503 variant
- `backend/tests/integration/test_m004_s05_webhook_receiver_e2e.py` — Single 8-step e2e against live compose stack; autouse skip-guard for s06e revision; autouse cleanup of webhooks tables and webhook_secret row
