---
id: T01
parent: S03
milestone: M005-oaptsz
key_files:
  - backend/app/alembic/versions/s08_push_subscriptions.py
  - backend/app/models.py
  - backend/app/api/routes/admin.py
  - backend/app/api/routes/push.py
  - backend/app/api/main.py
  - backend/pyproject.toml
  - uv.lock
  - backend/tests/migrations/test_s08_push_subscriptions_migration.py
  - backend/tests/api/routes/test_push.py
key_decisions:
  - P-256 raw-bytes b64url-no-padding serialization for both VAPID halves (RFC 8292 §3.2): uncompressed point (65 bytes, 0x04‖X‖Y) for public, raw 32-byte scalar for private. pywebpush accepts both PEM and raw-bytes; standardizing on b64url means the public key can be served verbatim to browsers without re-encoding.
  - Public VAPID key is non-sensitive (browsers fetch it unauthenticated) but server-generated as half of an atomic keypair. Widened the module-load 'generator-implies-sensitive' assertion via an explicit _NON_SENSITIVE_GENERATOR_OK frozenset rather than silently disabling it — keeps the principle visible and forces the next exception to be named.
  - POST /admin/settings/vapid_keys/generate is the SOLE writer for both VAPID rows; the per-key generate endpoint refuses both keys with 422 'use_atomic_endpoint_for_vapid_keys'. Per-key generator hooks raise RuntimeError as defense-in-depth.
  - GET /push/vapid_public_key returns 503 (not 404) when unset — matches the M004/S01 fail-loud posture for missing-config sensitive paths and gives the FE a structured remediation hint pointing at the operator runbook.
  - PushSubscriptionPublic NEVER carries the raw endpoint URL — only endpoint_hash (sha256(endpoint)[:8]). The endpoint is treated as a bearer-style secret on every read surface.
duration: 
verification_result: passed
completed_at: 2026-04-28T11:38:09.645Z
blocker_discovered: false
---

# T01: Add s08 push_subscriptions migration, SystemSetting VAPID public+private keys, atomic /admin/settings/vapid_keys/generate endpoint, public /push/vapid_public_key route, and pywebpush dependency.

**Add s08 push_subscriptions migration, SystemSetting VAPID public+private keys, atomic /admin/settings/vapid_keys/generate endpoint, public /push/vapid_public_key route, and pywebpush dependency.**

## What Happened

Schema and operator-side prerequisites for the M005/S03 Web Push channel — every other task in the slice imports the push_subscriptions table or reads/decrypts the VAPID keys.

Migration s08_push_subscriptions.py was added with down_revision='s07_notifications'. The single new table push_subscriptions carries (id UUID PK, user_id UUID NOT NULL FK→user CASCADE, endpoint TEXT, keys JSONB, user_agent VARCHAR(500) NULL, created_at + last_seen_at TIMESTAMPTZ DEFAULT NOW(), last_status_code INT NULL, consecutive_failures INT DEFAULT 0) with UNIQUE(user_id, endpoint) — phone+laptop=two rows, re-subscribe is upsert — and a non-unique ix_push_subscriptions_user_id index for the dispatcher's per-user fan-out. Comment block at top mirrors s07's docstring style; downgrade drops index then table.

In models.py I added the SQLModel PushSubscription table class plus DTOs PushSubscriptionCreate (browser POST body — endpoint + {p256dh, auth} keys), PushSubscriptionPublic (id + endpoint_hash sha256[:8] + ua + timestamps — never raw endpoint), PushSubscriptionsList ({data, count}), VapidKeysGenerateResponse, and VapidPublicKeyResponse.

In admin.py I registered VAPID_PUBLIC_KEY_KEY ('vapid_public_key') as non-sensitive JSONB with a structural validator (non-empty url-safe-base64 ASCII ≤ 256 chars; bool rejected) and VAPID_PRIVATE_KEY_KEY ('vapid_private_key') as sensitive Fernet-encrypted with no PUT validator (server-seed only). The atomic generator _generate_vapid_keypair() builds a P-256 keypair via cryptography.hazmat.ec.generate_private_key(SECP256R1) then serializes per RFC 8292 §3.2 — uncompressed point (65 bytes) for public, raw 32-byte scalar for private — both b64url-no-padding via _b64url_no_pad(). The per-key generators (_generate_vapid_public_part / _generate_vapid_private_part) deliberately raise RuntimeError if invoked alone; the generate_system_setting route now refuses both VAPID keys with 422 detail 'use_atomic_endpoint_for_vapid_keys'. The module-load assertion was widened with an explicit _NON_SENSITIVE_GENERATOR_OK = frozenset({VAPID_PUBLIC_KEY_KEY}) allowlist (assertion text names the rule and where to add new exceptions). Net new endpoint POST /admin/settings/vapid_keys/generate (declared BEFORE the /settings/{key}/generate catch-all so FastAPI matches the literal segment first — comment in the route docstring) writes public into JSONB value via _upsert_jsonb and private into BYTEA value_encrypted via _upsert_encrypted, single transaction commit, returns {public_key, private_key, overwrote_existing}; emits INFO 'admin.vapid_keys.generated actor_id=<uuid> overwrote=<bool> key_prefix=<first_4>'.

push.py is a new router stub at /api/v1/push exposing GET /vapid_public_key (no auth gate — browsers fetch this before any user is in scope). Returns {public_key} or 503 with detail 'vapid_public_key_not_configured' + remediation hint when unset. Logs INFO 'push.vapid_public_key.served key_prefix=<first_4>'. Mounted in app/api/main.py.

pywebpush>=1.14.0 added to backend/pyproject.toml dependencies; uv lock pulled pywebpush==1.14.1, http-ece==1.2.1, py-vapid==1.9.2.

Migration test test_s08_push_subscriptions_migration.py mirrors test_s07_notifications_migration's MEM016 autouse session-release pattern: introspects information_schema for column shape/types/nullability, asserts the UNIQUE constraint and user_id index exist, exercises duplicate (user, endpoint) → IntegrityError, two endpoints same user coexist, user CASCADE deletes subscriptions, downgrade drops cleanly, and downgrade+re-upgrade leaves _schema_snapshot byte-identical.

Route test test_push.py covers GET /push/vapid_public_key 503-when-unset, no-auth-required path, served-log shape; POST /admin/settings/vapid_keys/generate happy path (returns valid 65-byte public + 32-byte private after b64url-decode, public starts with 0x04 uncompressed-point marker), DB persistence (public plain JSONB, private encrypted, plaintext absent from ciphertext bytes), redacted GET on the private row after generation, re-call sets overwrote_existing=true with a fresh keypair, audit-log shape (key_prefix only), 401 unauth + 403 normal-user, and the per-key /generate refusal for both VAPID keys.

While running the verification I hit a pre-existing test-isolation issue: alembic.command.upgrade() invokes logging.config.fileConfig() which defaults to disable_existing_loggers=True, silently flipping logger.disabled=True on app.api.routes.*. After the migration test runs, caplog stops capturing INFO from those loggers and audit-log assertions fail in adjacent route tests (this also breaks test_admin_settings::test_generate_emits_system_setting_generated_log when run after the s07 migration test — predates this slice). Worked around in test_push.py with an autouse fixture that re-enables app.api.routes.push and app.api.routes.admin loggers per test. Captured as MEM359 so future agents writing route caplog tests see the fix immediately.

Two more durable findings captured: MEM360 (FastAPI literal-segment routes must be declared before their catch-all sibling) and MEM361 (host-side Postgres env overrides — POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app — because the committed .env disagrees with the published compose port and the unrelated 'app' DB on the same server holds another project's migrations).

## Verification

Followed the verification block in T01-PLAN.md from /Users/josh/code/perpetuity/backend with POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app overrides:

1. uv lock confirmed pywebpush==1.14.1 + transitive deps (http-ece, py-vapid) added; uv sync rebuilt the venv successfully.
2. uv run alembic upgrade head applied s07_notifications → s08_push_subscriptions and emitted the expected `s08_push_subscriptions upgrade complete tables=1 indexes=1` info log; alembic current returns `s08_push_subscriptions (head)`.
3. uv run pytest tests/migrations/test_s08_push_subscriptions_migration.py tests/api/routes/test_push.py -x ran 19 tests in 0.56s, all pass — 7 migration tests (column shape, constraints+index, duplicate IntegrityError, multi-device coexist, user CASCADE, downgrade, round-trip schema-identical) + 12 route tests (503-when-unset, no-auth, served log, atomic generate happy path, DB persistence assertions, redaction-on-subsequent-GET, re-generate overwrote flag, audit log shape, 401/403 gating, per-key generate refusal × 2).
4. grep -q 'pywebpush' /Users/josh/code/perpetuity/uv.lock → 5 matches.
5. grep -q 'vapid_public_key' backend/app/api/routes/admin.py → registered.

Negative tests included by construction: per-key /generate for both VAPID keys returns 422 'use_atomic_endpoint_for_vapid_keys'; 401 unauthenticated and 403 normal-user gating on the atomic endpoint; 503 detail body shape on unconfigured public-key fetch; ciphertext-does-not-contain-plaintext byte assertion on the private row.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && uv lock` | 0 | ✅ pass | 750ms |
| 2 | `cd backend && uv sync` | 0 | ✅ pass | 350ms |
| 3 | `cd backend && POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app uv run alembic upgrade head` | 0 | ✅ pass (s07 → s08, tables=1 indexes=1) | 2400ms |
| 4 | `cd backend && POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app uv run alembic current` | 0 | ✅ pass (s08_push_subscriptions (head)) | 800ms |
| 5 | `cd backend && POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app uv run pytest tests/migrations/test_s08_push_subscriptions_migration.py tests/api/routes/test_push.py -x` | 0 | ✅ pass (19 passed in 0.56s) | 560ms |
| 6 | `grep -q 'pywebpush' /Users/josh/code/perpetuity/uv.lock` | 0 | ✅ pass (5 matches) | 20ms |
| 7 | `grep -q 'vapid_public_key' backend/app/api/routes/admin.py` | 0 | ✅ pass (key + endpoint registered) | 20ms |

## Deviations

Migration filename uses revision id 's08_push_subscriptions' rather than a hash-based id to match the s06/s07 sibling style — consistent with the convention already established in this milestone.

Added an autouse _reenable_route_loggers fixture in test_push.py to compensate for alembic's logging.config.fileConfig disabling existing loggers. The fix is local rather than centralizing it (e.g. in conftest) because changing global behavior was out of scope for this task; captured the pattern in MEM359 so the next slice with caplog assertions can lift it.

## Known Issues

- The pre-existing logger-disable issue (MEM359) still affects test_admin_settings.py::test_generate_emits_system_setting_generated_log when run after a migration test. Out of scope for this task — the right fix is a conftest-level autouse fixture that re-enables app.api.routes.* after alembic.fileConfig runs, but that touches a different test module.
- The committed .env's POSTGRES_PORT=55432 / POSTGRES_DB=app values disagree with the actual perpetuity_app DB on host port 5432 (MEM361 + already-recorded MEM344). All host-side test invocations in this slice prepend POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app.

## Files Created/Modified

- `backend/app/alembic/versions/s08_push_subscriptions.py`
- `backend/app/models.py`
- `backend/app/api/routes/admin.py`
- `backend/app/api/routes/push.py`
- `backend/app/api/main.py`
- `backend/pyproject.toml`
- `uv.lock`
- `backend/tests/migrations/test_s08_push_subscriptions_migration.py`
- `backend/tests/api/routes/test_push.py`
