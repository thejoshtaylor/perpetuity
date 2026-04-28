---
estimated_steps: 29
estimated_files: 8
skills_used: []
---

# T01: s08 push_subscriptions migration + SystemSetting VAPID keys + admin VAPID generate one-shot + pywebpush dep

Schema and operator-side prerequisites for the push channel — must land first because every other task in this slice imports the table or reads/decrypts the VAPID keys.

What to build:

1. **Migration `s08_push_subscriptions.py`** — `down_revision = 's07_notifications'`. Single new table:
   - `id UUID PK`
   - `user_id UUID NOT NULL FK→user(id) ON DELETE CASCADE` — phone + laptop = two rows for one user; user delete purges them.
   - `endpoint TEXT NOT NULL` — the Mozilla / FCM / APNs Web URL the browser handed us.
   - `keys JSONB NOT NULL` — the `{p256dh, auth}` browser-issued blob.
   - `user_agent VARCHAR(500) NULL` — best-effort device hint, truncated.
   - `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`
   - `last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()` — bumped on each successful delivery.
   - `last_status_code INTEGER NULL` — last HTTP status from MPS.
   - `consecutive_failures INTEGER NOT NULL DEFAULT 0` — pruned at 5 by the dispatcher.
   - `UNIQUE(user_id, endpoint)` — same browser re-subscribing is an upsert, not a duplicate.
   - Index `ix_push_subscriptions_user_id (user_id)` — fan-out lookup.
   - Comment block at the top of the migration explaining the schema choices, mirroring s07's docstring style.
   - `downgrade()` drops index then table.

2. **SQLModel `PushSubscription` table class in `backend/app/models.py`**, plus public DTOs `PushSubscriptionCreate` (the body the frontend POSTs: `{endpoint, keys: {p256dh, auth}}` — extracted from the browser `PushSubscription.toJSON()`), `PushSubscriptionPublic` (id + endpoint_hash sha256[:8] + created_at — NEVER raw endpoint), `PushSubscriptionsList` ({data, count}). Match the SQLModel patterns from `Notification` and `GitHubAppInstallation`.

3. **System settings registration in `backend/app/api/routes/admin.py`:**
   - `VAPID_PUBLIC_KEY_KEY = 'vapid_public_key'` — non-sensitive JSONB string. Validator: `_validate_vapid_public_key(value)` — must be a non-empty url-safe-base64 ASCII string ≤ 256 chars (P-256 raw public key serialized as URL-safe base64 is 87 chars). bool-rejection like the other validators.
   - `VAPID_PRIVATE_KEY_KEY = 'vapid_private_key'` — sensitive (Fernet-encrypted). `_VALIDATORS` entry has `validator=None` (server-side seed only) and `generator=_generate_vapid_keypair_private_part`. Same pattern as `github_app_webhook_secret`.
   - **Generator function `_generate_vapid_keypair() -> tuple[str, str]`** that produces (public_b64url, private_b64url) using `cryptography.hazmat.primitives.asymmetric.ec.generate_private_key(SECP256R1)` and serializes the raw bytes per RFC 8292 §3.2 (uncompressed public point 65 bytes for `vapid_public_key`; raw 32-byte private scalar for `vapid_private_key`). Both base64-url encoded with no padding (`b64encode(...).rstrip(b'=')`).
   - Module-load assertion line for `vapid_public_key` matching the existing `_spec.generator → _spec.sensitive` invariant — public is non-sensitive but server-generated. Adjust the assertion: a generator implies sensitive OR the spec carries an explicit `_NON_SENSITIVE_GENERATOR_OK = True` flag for the public-key-also-generated-server-side case. Implementation choice: split the generate handler so the public key is written via the same admin path that wrote the private key — the public key gets a generator too but the assertion is widened with a comment naming the VAPID exception. Keep the assertion meaningful — never silently disable.

4. **One-shot endpoint `POST /admin/settings/vapid_keys/generate`** in admin.py (mirrors `POST /admin/settings/{key}/generate` shape but writes BOTH keys atomically — public into JSONB `value`, private encrypted into `value_encrypted`). Returns `{public_key, private_key, overwrote_existing: bool}` exactly once. Re-calling is intentionally destructive (every existing subscription becomes unverifiable; documented in the response copy).

5. **Public endpoint `GET /api/v1/push/vapid_public_key`** in a new `backend/app/api/routes/push.py` (router stub — full subscribe/unsubscribe routes ship in T03). Returns `{public_key: <stored value>}` or 503 if not configured. NO auth gate — browsers fetch this before any user is in scope.

6. **Add `pywebpush>=1.14.0` to `backend/pyproject.toml` dependencies** and run `uv lock`. (`cryptography` is already pinned.)

7. **Mount `push.router` in `backend/app/api/main.py`** alongside `notifications.router`.

8. **Migration test `backend/tests/migrations/test_s08_push_subscriptions_migration.py`** following the MEM016 autouse session-release pattern from `test_s07_notifications_migration.py`: assert table exists with all columns + the UNIQUE(user_id, endpoint) constraint + the user_id index, that two rows with the same (user_id, endpoint) collide on insert, that two rows with same user_id but different endpoints coexist, that user delete CASCADEs, downgrade drops cleanly, downgrade then re-upgrade is byte-identical.

9. **Backend route test `backend/tests/api/routes/test_push.py`** exercising: GET /push/vapid_public_key returns 503 when unset and 200 with the value when set; POST /admin/settings/vapid_keys/generate as superuser returns both keys + writes encrypted private + plain public (assert by reading the rows directly); calling it twice sets overwrote_existing=true on the second; non-superuser → 403.

Assumptions documented in the plan: P-256 raw-bytes serialization is what `pywebpush` expects for the `vapid_private_key` parameter (string form is also accepted by pywebpush; we standardize on b64url). RFC 8292 mandates this shape on the wire.

## Inputs

- ``backend/app/alembic/versions/s07_notifications.py``
- ``backend/app/alembic/versions/s06_system_settings_sensitive.py``
- ``backend/app/api/routes/admin.py``
- ``backend/app/core/encryption.py``
- ``backend/app/models.py``
- ``backend/tests/migrations/test_s07_notifications_migration.py``
- ``backend/pyproject.toml``
- ``backend/app/api/main.py``

## Expected Output

- ``backend/app/alembic/versions/s08_push_subscriptions.py``
- ``backend/app/models.py` (PushSubscription table + DTOs added)`
- ``backend/app/api/routes/admin.py` (vapid keys registered + vapid_keys/generate endpoint)`
- ``backend/app/api/routes/push.py` (router stub + GET vapid_public_key)`
- ``backend/app/api/main.py` (push router mounted)`
- ``backend/pyproject.toml` (pywebpush dependency)`
- ``backend/uv.lock` (regenerated)`
- ``backend/tests/migrations/test_s08_push_subscriptions_migration.py``
- ``backend/tests/api/routes/test_push.py``

## Verification

Run from `backend/`: `uv sync` (pulls pywebpush), then `uv run alembic upgrade head` against a fresh DB, then `uv run pytest tests/migrations/test_s08_push_subscriptions_migration.py tests/api/routes/test_push.py -x`. All assertions in both test files pass; downgrade test demonstrates schema symmetry. `grep -q 'pywebpush' backend/uv.lock` confirms the dependency landed. `grep -q 'vapid_public_key' backend/app/api/routes/admin.py` confirms registration.

## Observability Impact

New INFO logs `admin.vapid_keys.generated actor_id=<uuid> overwrote=<bool>` (must NEVER log raw key — only the public-key prefix). New INFO `push.vapid_public_key.served key_prefix=<first_4_of_b64>` on the public GET. New ERROR `push.vapid_decrypt_failed key=vapid_private_key` translated to 503 if the encrypted private key fails to decrypt at any later read site (matches D025 fail-loud posture). The generator one-shot returns the plaintext private key in the response body once — that is the only moment the plaintext crosses the backend→UI boundary; subsequent admin GET on the row returns the M004/S01 redacted shape (`value=null, has_value=true, sensitive=true`).
