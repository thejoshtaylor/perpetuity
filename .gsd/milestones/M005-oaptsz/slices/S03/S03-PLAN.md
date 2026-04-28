# S03: Web Push delivery (VAPID + push_subscriptions + pywebpush dispatcher)

**Goal:** Light up the Web Push channel end-to-end: VAPID keypair stored in `system_settings` (public served to browsers, private Fernet-encrypted), `push_subscriptions` table + subscribe/unsubscribe routes, `pywebpush`-based dispatcher invoked from `notify()` when the push preference resolves true, service-worker `push`+`notificationclick` handlers that render the notification and route to run-detail, and an in-app permission-prompt UX that subscribes via the SW. HTTP 410 from a push endpoint auto-prunes the subscription; secrets and endpoint URLs never appear in logs (endpoint_hash=sha256[:8] only). Slice contract is provable end-to-end against respx-mocked Mozilla Push Service for delivery + a real SW Playwright spec for render+click-to-open.
**Demo:** User opens the app on a phone, grants notification permission via the prompt UX, configures workflow X with `failure → push`, backgrounds the app, triggers workflow X to fail, and receives a Web Push notification on the device within 30s; tapping the notification opens the app to the run-detail page. Same flow on a desktop browser confirms cross-device delivery. With a subscription that has been deleted upstream (browser uninstalled the PWA), the next push delivery returns HTTP 410, and the subscription row is automatically pruned without operator intervention.

## Must-Haves

- **Owned:** R023 (push subscription persists in `push_subscriptions` on POST /api/v1/push/subscribe; backend dispatches via `pywebpush` from inside `notify()`; HTTP 410 from the push endpoint prunes the subscription automatically; multi-device delivery — phone + laptop both subscribed → both notified). **Supported:** R024 (push channel of the existing per-user-per-event-type preference takes effect end-to-end — toggling `push=true` for a kind causes that kind's `notify()` calls to fan out to all of the user's subscriptions; toggling `push=false` suppresses delivery without affecting in-app row insert).
- **Verification gates (all must pass):**
- 1. `cd backend && uv run pytest tests/migrations/test_s08_push_subscriptions_migration.py` — s08 migration upgrade/downgrade + CASCADE on user delete + UNIQUE(user_id, endpoint) collision contract.
- 2. `cd backend && uv run pytest tests/api/routes/test_push.py` — subscribe upsert idempotency, delete-by-endpoint, GET /push/vapid_public_key returns the stored value (no auth), system-admin VAPID generate one-shot returns both keys exactly once and stores public plain + private encrypted.
- 3. `cd backend && uv run pytest tests/api/routes/test_push_dispatch.py` — pywebpush dispatcher (respx-mocked Mozilla Push Service): VAPID-signed POST shape correct, 201 success path, HTTP 410 prunes the subscription row, 5xx increments a per-row counter and prunes after 5 consecutive failures, secrets never log (assertions on caplog), endpoint logged only as `endpoint_hash=sha256:8`.
- 4. `cd backend && uv run pytest tests/api/routes/test_notifications.py::test_push_channel_routes_to_dispatcher` — notify() with push preference on calls dispatcher once per subscription for the user; with push preference off does NOT call dispatcher even when in_app=true.
- 5. `cd frontend && bunx playwright test --project=m005-oaptsz-push m005-oaptsz-push.spec.ts` — slice contract gate. New project (production preview, `serviceWorkers: 'allow'`, fresh storageState, `permissions: ['notifications']`). Spec: seed VAPID keys via admin API, render the permission prompt, accept, assert subscription POST'd. Inject a synthetic push payload into the SW via `await page.evaluate(() => navigator.serviceWorker.controller?.postMessage({type: 'TEST_PUSH', payload: {...}}))` plus a SW-side debug branch that triggers the same render path → assert `self.registration.showNotification` was called (via a `BroadcastChannel` echo the spec listens on); separately exercise `notificationclick` by dispatching a synthetic event and asserting a `clients.openWindow` URL via the same broadcast contract.
- 6. Slice contract grep gate (no real-device dep): `cd backend && uv run pytest tests/api/routes/test_push_dispatch.py -k "redaction or endpoint_hash"` — proves no full endpoint URL or VAPID prefix lands in caplog records.

## Proof Level

- This slice proves: **Integration-level.** The slice ships a route through three runtimes (FastAPI → Mozilla Push Service → browser SW) and a real-device round-trip cannot be forced from Playwright. The contract is provable to integration depth via: (a) respx-mocked pywebpush dispatcher unit tests that exercise the VAPID-signed POST shape, the 410-prune path, the multi-device fan-out, and the redaction posture; (b) a Playwright spec against the production preview that drives the real subscribe→permission→pushManager.subscribe→POST /push/subscribe path and exercises the SW push and notificationclick handlers via a synthetic-message debug branch in `sw.ts` (the same render code path real pushes hit, just without the cross-process MPS hop). Real round-trip (phone background → MPS → device) is explicitly deferred to S05's acceptance scenario 2.

## Integration Closure

**Backend → notify() → dispatcher → MPS:** the existing notify() helper signature stays frozen (S02 contract); only `_push_stub` becomes real. Wired call sites already exist (`team_invite_accepted`, `project_created`, `system`); enabling push for any of those kinds via the preferences UI is the seam this slice closes. The synthetic seed path `POST /notifications/test {kind: 'workflow_run_failed'}` is already wired and works as the slice's standin for the not-yet-shipped workflow engine — the demo path is `subscribe → enable push for workflow_run_failed → POST /notifications/test → push received`. **Frontend → SW → MPS:** the SW's empty `push` listener stub from S01 (`pwa.push.received_stub` console.info) is replaced with the real renderer; the existing `pwa-update-available` event dispatch pattern from S01 is the precedent for the new `pwa-push-permission-changed` event the prompt UX dispatches. **System settings:** the four new keys (`vapid_public_key`, `vapid_private_key`, `grok_stt_api_key` deferred to S04, `max_voice_transcribes_per_hour_global` deferred to S04) extend the existing `_VALIDATORS` registry from M004/S01 — sensitive marker reuses the Fernet path verbatim. **Forward closure:** the `source_workflow_run_id` column on `notifications` still has no FK; that closure is owned by whichever future slice ships the workflow engine.

## Verification

- **New INFO surfaces (backend):**
- `push.subscribe user_id=<uuid> endpoint_hash=<sha256:8> ua=<truncated_ua_or_unknown>` on POST /push/subscribe (insert path)
- `push.subscribe.upsert user_id=<uuid> endpoint_hash=<sha256:8> existing=true` on POST /push/subscribe (existing-row path)
- `push.unsubscribe user_id=<uuid> endpoint_hash=<sha256:8> deleted=<bool>` on DELETE /push/subscribe
- `push.dispatch.start user_id=<uuid> kind=<kind> subscriptions=<n>` per notify() fan-out
- `push.dispatch.delivered user_id=<uuid> endpoint_hash=<sha256:8> kind=<kind> status=<201|200>`
- `push.dispatch.pruned_410 user_id=<uuid> endpoint_hash=<sha256:8>` — subscription dropped by upstream
- `push.dispatch.consecutive_failure user_id=<uuid> endpoint_hash=<sha256:8> count=<n>` (WARNING when n=5 about to prune)
- `push.dispatch.pruned_max_failures user_id=<uuid> endpoint_hash=<sha256:8>` after 5 consecutive 5xx (WARNING)
- `push.vapid_public_key.served key_prefix=<first_4_of_b64>` on GET /push/vapid_public_key (no user)
- `admin.vapid_keys.generated actor_id=<uuid> overwrote=<bool>` (sensitive: never log raw key, just the public-key prefix)
- **New ERROR surfaces (backend):**
- `push.dispatch.send_failed user_id=<uuid> endpoint_hash=<sha256:8> cause=<class>` on unexpected exception (prune logic still runs)
- `push.vapid_decrypt_failed key=vapid_private_key` translated to 503 (matches D025/M004 fail-loud posture)
- **New frontend surfaces:**
- `pwa.push.permission_prompt_shown` console.info from PushPermissionPrompt
- `pwa.push.permission_granted` / `pwa.push.permission_denied`
- `pwa.push.subscribed endpoint_hash=<sha256:8>` (compute hash client-side for log symmetry)
- `pwa.push.subscribe_failed cause=<message>`
- `pwa.push.received endpoint_hash=<sha256:8>` from SW push handler (replaces S01's stub `pwa.push.received_stub`)
- `pwa.push.notification_clicked target_path=<path>` from SW notificationclick
- **Inspection paths:**
- `SELECT user_id, endpoint, last_seen_at, last_status_code, consecutive_failures FROM push_subscriptions WHERE user_id = '<uuid>'` for forensic state.
- DevTools → Application → Service Workers → Push (Chromium) lets the operator manually fire a test push to verify the SW handler.
- `chrome://serviceworker-internals` for SW lifecycle drilldown.
- **Redaction posture (carries forward to S05 grep):**
- VAPID private key first 8 chars must NEVER appear in logs.
- Push subscription endpoint URL must NEVER appear in logs (use `endpoint_hash` only).
- Notification payload values are the same redacted form notify() already produces (S02).

## Tasks

- [x] **T01: s08 push_subscriptions migration + SystemSetting VAPID keys + admin VAPID generate one-shot + pywebpush dep** `est:M`
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
  - Files: `backend/app/alembic/versions/s08_push_subscriptions.py`, `backend/app/models.py`, `backend/app/api/routes/admin.py`, `backend/app/api/routes/push.py`, `backend/app/api/main.py`, `backend/pyproject.toml`, `backend/tests/migrations/test_s08_push_subscriptions_migration.py`, `backend/tests/api/routes/test_push.py`
  - Verify: Run from `backend/`: `uv sync` (pulls pywebpush), then `uv run alembic upgrade head` against a fresh DB, then `uv run pytest tests/migrations/test_s08_push_subscriptions_migration.py tests/api/routes/test_push.py -x`. All assertions in both test files pass; downgrade test demonstrates schema symmetry. `grep -q 'pywebpush' backend/uv.lock` confirms the dependency landed. `grep -q 'vapid_public_key' backend/app/api/routes/admin.py` confirms registration.

- [x] **T02: pywebpush dispatcher + replace `_push_stub` in notify() with real fan-out + redaction sweep** `est:L`
  The send-and-prune engine. Owns the contract that `notify()` with `push=True` reliably reaches all of a user's subscriptions, prunes dead ones automatically, and never leaks secrets or endpoints to logs.

What to build:

1. **`backend/app/core/push_dispatch.py`** — module exposes one public function:

   ```python
   def dispatch_push(
       session: Session,
       *,
       user_id: UUID,
       kind: NotificationKind,
       title: str,
       body: str,
       url: str,           # target path on click — e.g. /runs/<id>
       icon: str | None = None,
   ) -> int:  # returns count of successful deliveries
   ```

   - Resolve VAPID public + private keys via `system_settings`. Public reads via `value` JSONB; private decrypts via `decrypt_setting` and `SystemSettingDecryptError(key='vapid_private_key')` on InvalidToken — log ERROR `push.vapid_decrypt_failed key=vapid_private_key` and return 0 (caller path is robust).
   - Compute `vapid_claims = {'sub': 'mailto:operator@perpetuity.invalid'}` (the `sub` claim is required by RFC 8292; the email is fixed string — operators don't surface here).
   - SELECT all `PushSubscription WHERE user_id = ?` rows.
   - For each row: build the JSON payload `{title, body, url, kind: kind.value, icon}` (≤4096 bytes per RFC 8030; we never approach this), then call `pywebpush.webpush(subscription_info=row.keys + endpoint, data=json.dumps(payload), vapid_private_key=<decrypted>, vapid_claims=vapid_claims, ttl=3600)`. Wrap in try/except.
   - On `WebPushException` whose `response.status_code == 410` → DELETE the row, log INFO `push.dispatch.pruned_410 user_id=<uuid> endpoint_hash=<sha256:8>`, do NOT count toward success.
   - On 5xx → bump `consecutive_failures` by 1, set `last_status_code = response.status_code`. If now ≥ 5 → DELETE the row, log WARNING `push.dispatch.pruned_max_failures`. Else log WARNING `push.dispatch.consecutive_failure ... count=<n>`.
   - On 2xx → set `consecutive_failures = 0`, `last_status_code = response.status_code`, `last_seen_at = NOW()`. Log INFO `push.dispatch.delivered`. Increment success counter.
   - On any other exception class → log ERROR `push.dispatch.send_failed cause=<class>`. Do NOT prune.
   - **Endpoint hash helper** `_endpoint_hash(endpoint: str) -> str` returning `hashlib.sha256(endpoint.encode()).hexdigest()[:8]`. ALL log lines that name an endpoint use this hash, NEVER the raw URL.
   - Wrap the entire fan-out in a single session.commit() so per-row state changes are atomic.

2. **Modify `backend/app/core/notify.py`:**
   - Add `_resolve_push(session, *, user_id, kind) -> bool` mirroring `_resolve_in_app` but reading the `push` column.
   - Replace `_push_stub` with a thin wrapper that calls `_resolve_push` and, when true, calls `dispatch_push(...)` from the new module — the wrapper is the only caller of dispatcher inside notify(); call sites do not change.
   - The wrapper synthesizes title/body/url from `(kind, payload, source_*)` via a small `_render_push(kind, payload, source_*) -> tuple[title, body, url, icon]` switch. For now: workflow_run_failed → ('Workflow failed', payload.message or 'A workflow run failed', `/runs/${source_workflow_run_id}` if present else `/`); team_invite_accepted → ('Team invite accepted', team_name from payload, '/teams'); project_created → ('Project created', project_name, '/projects'); system → ('Notification', payload.message or 'System notification', '/'); the rest → reasonable defaults.
   - On dispatcher exception or any error: catch and log `notify.push_failed user_id=… kind=… cause=<class>`. Like the in-app path, push must NEVER re-raise into the calling route.
   - Update the existing `notify.dispatched` log line to also carry `push=<bool>` so the slice's grep gate can confirm the channel decision per-row.

3. **Backend tests `backend/tests/api/routes/test_push_dispatch.py`** using `respx.mock` to stub `https://updates.push.services.mozilla.com/wpush/v2/<token>` (or whatever endpoint the seeded subscription rows carry — use a recognizable mock host like `https://mock-push.invalid/...`):
   - `test_dispatch_signs_with_vapid_and_posts_to_endpoint` — assert one POST per subscription, Authorization header carries `vapid` scheme, payload body is the encrypted blob (just assert presence + that the request reached the mock).
   - `test_dispatch_201_marks_last_seen_and_resets_failures` — seed a row with `consecutive_failures=3, last_status_code=503`; mock returns 201; row reads back with `consecutive_failures=0, last_status_code=201`, `last_seen_at` advanced.
   - `test_dispatch_410_prunes_subscription` — seeded row gone from DB; log captured `push.dispatch.pruned_410`.
   - `test_dispatch_5xx_increments_then_prunes_at_five` — five sequential dispatches against a mock returning 500; after the fifth the row is pruned; warnings captured at counts 1,2,3,4 and the prune warning at 5.
   - `test_dispatch_multi_device_fanout` — seed 2 subscriptions for one user; mock 201 for both; both rows updated.
   - `test_dispatch_endpoint_logged_as_hash_only` — caplog records contain `endpoint_hash=` and the exact 8-hex-char hash; NO raw endpoint URL substring appears anywhere in caplog.text.
   - `test_dispatch_vapid_decrypt_failure_logs_503_path` — corrupt the encrypted row, assert dispatch_push returns 0 and the ERROR log fires.

4. **Notify-layer integration test in `backend/tests/api/routes/test_notifications.py`** (new test): `test_push_channel_routes_to_dispatcher` — seed a user, seed two PushSubscription rows, set NotificationPreference push=True for `system`, monkeypatch `app.core.push_dispatch.dispatch_push` to record calls. Call `notify(...)` → assert dispatch_push called once with the right user_id, kind, and a payload synthesized from the kind. Repeat with push=False → dispatch_push NOT called even when in_app=True.

Assumptions documented inline: pywebpush 1.14+ accepts both `dict` and JSON-string for `subscription_info`, and `vapid_private_key` as a base64-url string; we pass the decrypted plaintext from `decrypt_setting` directly. The `sub` claim email is hardcoded `mailto:operator@perpetuity.invalid` since no operator email is configured at the system_settings level today.
  - Files: `backend/app/core/push_dispatch.py`, `backend/app/core/notify.py`, `backend/tests/api/routes/test_push_dispatch.py`, `backend/tests/api/routes/test_notifications.py`
  - Verify: From `backend/`: `uv run pytest tests/api/routes/test_push_dispatch.py tests/api/routes/test_notifications.py::test_push_channel_routes_to_dispatcher tests/api/routes/test_notifications.py::test_push_channel_off_skips_dispatcher -x` — all 8 dispatcher tests + the 2 notify-integration tests pass. Then `uv run pytest tests/api/routes/test_push_dispatch.py -k 'redaction or endpoint_hash' -x` — proves no raw endpoint URL appears in caplog. Then run the existing `tests/api/routes/test_notifications.py` (the S02 test file) end-to-end — must STILL pass; the notify() change is additive.

- [x] **T03: POST/DELETE /push/subscribe routes + frontend client regen + Notifications-tab push toggle goes live** `est:M`
  Bridges the browser-side subscription handle to backend storage. Owns the subscribe/unsubscribe API surface and unblocks the frontend wiring in T04.

What to build:

1. **In `backend/app/api/routes/push.py` (extending T01's stub):**

   - `POST /api/v1/push/subscribe` body `PushSubscriptionCreate {endpoint, keys: {p256dh, auth}}`. Behavior: SELECT existing row WHERE user_id = current_user.id AND endpoint = body.endpoint; if exists → UPDATE keys + last_seen_at + reset consecutive_failures, log `push.subscribe.upsert ... existing=true`, return PushSubscriptionPublic. Else → INSERT, log `push.subscribe`, return 201 with PushSubscriptionPublic. Capture `User-Agent` header (truncated to 500 chars) into the row; never log it.
   - `DELETE /api/v1/push/subscribe` body `{endpoint}` (or query param — pick one and document; recommend body for symmetry with POST). Deletes WHERE user_id AND endpoint. Log `push.unsubscribe ... deleted=<bool>`. Return 204.
   - `GET /api/v1/push/subscriptions` (private, current user only) — returns `PushSubscriptionsList` with endpoint_hash-only entries so the UI in S03 or beyond can render "this device + N others subscribed".
   - All routes hash the endpoint for any log line; raw endpoint never logs.

2. **Backend tests in `backend/tests/api/routes/test_push.py` (extending T01's file):**
   - `test_subscribe_creates_row_first_time` — POST → 201, row in DB.
   - `test_subscribe_idempotent_upsert` — POST same endpoint twice → 200, single row, last_seen_at advanced, `existing=true` log captured.
   - `test_subscribe_two_devices_for_one_user` — POST two distinct endpoints → two rows, both linked to user.
   - `test_unsubscribe_by_endpoint` — DELETE → row gone, `deleted=true` log.
   - `test_unsubscribe_unknown_endpoint_is_noop` — DELETE for endpoint that does not belong to user → 204 with `deleted=false` log; row count unchanged.
   - `test_subscribe_requires_auth` — no cookie → 401.
   - `test_subscribe_log_uses_endpoint_hash_not_url` — caplog assertion mirroring T02's redaction style.
   - `test_get_subscriptions_lists_only_callers_rows` — seed two users with subscriptions, assert each only sees their own.

3. **Regenerate frontend client** — add the new endpoints to `frontend/openapi.json` (auto via `bash scripts/generate-client.sh` from repo root), surface a typed `PushService` in `frontend/src/client/sdk.gen.ts` and types in `types.gen.ts`. **Verify the slice contract**: `grep -q 'PushSubscriptionPublic' frontend/src/client/types.gen.ts && grep -q 'subscribe' frontend/src/client/sdk.gen.ts` — both must succeed.

4. **Enable the Push toggle in `frontend/src/components/notifications/NotificationPreferences.tsx`** (currently disabled with `Available in S03` text). Replace the disabled Switch with a working one wired to the same `updatePref.mutate({ eventType, in_app: row.in_app, push: checked })` shape. Remove the "Available in S03" copy. NOTE: T04 wires the actual subscription flow; this toggle controls the dispatcher gate that T02 already wired. A user toggling push=true with no subscription registered yet sees no push notifications (silent — by design). Surface a small inline hint near the toggle: "Allow notifications when prompted to receive push." (Plain text, not a toast — it sits in the table row, e.g. a tooltip or a paragraph cell when no subscription rows exist for the user — fetch via the new GET /push/subscriptions endpoint).

Assumptions: the `keys` JSONB validates loosely at the API boundary — we accept any object shape and let pywebpush surface an error at first send. Tightening to {p256dh, auth} structural validation can ship later if it becomes a debugging pain point.
  - Files: `backend/app/api/routes/push.py`, `backend/tests/api/routes/test_push.py`, `frontend/openapi.json`, `frontend/src/client/sdk.gen.ts`, `frontend/src/client/types.gen.ts`, `frontend/src/client/schemas.gen.ts`, `frontend/src/components/notifications/NotificationPreferences.tsx`
  - Verify: From `backend/`: `uv run pytest tests/api/routes/test_push.py -x` — all subscribe/unsubscribe/list tests pass. From repo root: `bash scripts/generate-client.sh` regenerates the frontend client without errors. From `frontend/`: `grep -q 'PushService' src/client/sdk.gen.ts` and `bun run build` succeeds with no TS errors. Manual: `bunx playwright test --project=chromium m005-oaptsz-notifications-preferences.spec.ts` still passes (push toggle change should be cosmetic to the existing spec).

- [x] **T04: Service-worker push + notificationclick handlers + PushPermissionPrompt UI + subscribe wire-up** `est:L`
  Browser-side renderer + permission UX. Owns the contract that a fresh user can grant permission, subscribe, and have a delivered push render a notification + open the app to the right URL on click.

What to build:

1. **`frontend/src/sw.ts` — replace the no-op `push` stub:**
   - `self.addEventListener('push', (event) => { event.waitUntil(handlePush(event)) })` where `handlePush` parses `event.data?.json()` (JSON shape from T02's `_render_push`: `{title, body, url, kind, icon}`); on parse failure or empty data, render a generic title `"Perpetuity"` body `"You have a new notification"` so we never silently drop a push.
   - Calls `self.registration.showNotification(title, { body, data: { url, kind }, icon: icon ?? '/pwa-192.png', badge: '/pwa-192.png', tag: kind })` (the `tag` collapses repeats of the same kind on Android — operator-friendly default; can be widened later).
   - Console.info `pwa.push.received endpoint_hash=<TBD>` — the SW does not have the endpoint readily; instead log `pwa.push.received kind=<kind>` (the hash is observable backend-side).
   - Add a debug branch: `self.addEventListener('message', (event) => { if (event.data?.type === 'TEST_PUSH') { ... showNotification with the same code path ... ; postMessage echo for the spec to listen on ... } })`. This is the spec's hook into the render path without spinning up a real Mozilla Push Service round trip. Gate this branch on a sentinel (the test message includes a `_testRenderEcho: true` flag) so production usage is identical.

2. **`self.addEventListener('notificationclick', ...)`** — close the notification, then `clients.matchAll({type: 'window', includeUncontrolled: true})`; if a window is already open, focus it and `postMessage({type: 'NAVIGATE', url})`; else `clients.openWindow(payload.url || '/')`. Console.info `pwa.push.notification_clicked target_path=<url>`. Echo via the same `BroadcastChannel('pwa-push-test')` so T05's spec can assert.

3. **`frontend/src/components/notifications/PushPermissionPrompt.tsx`** — new component, mounted in `_layout.tsx` next to InstallBanner:
   - On mount, check `'serviceWorker' in navigator && 'PushManager' in window` — if false, render iOS-Safari-pre-16.4 fallback (or render nothing if the user is on Android; differentiate via UA detection borrowed from S01's InstallBanner iOS branch).
   - Check `Notification.permission`. If `'default'` and the user has not yet dismissed (localStorage `pwa.push.dismissed_at`), render a small banner with "Enable push notifications" + Allow / Not now buttons.
   - On Allow: `Notification.requestPermission()` → on `'granted'` → fetch VAPID public key from `/api/v1/push/vapid_public_key` → `navigator.serviceWorker.ready` → `registration.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(vapidPublicKey)})` → POST the resulting `subscription.toJSON()` body to `/api/v1/push/subscribe` via the typed PushService client.
   - On `'denied'` → render an inline message "Notifications disabled — re-enable in browser settings to receive alerts" near the bell icon (use a CustomEvent dispatched from this component; the bell mount can listen). Never re-prompt automatically.
   - On already-`'granted'` and existing subscription (verify via `pushManager.getSubscription()`): silently re-POST to keep `last_seen_at` fresh; treat 200 vs 201 the same.
   - All console.info points: `pwa.push.permission_prompt_shown`, `pwa.push.permission_granted`, `pwa.push.permission_denied`, `pwa.push.subscribed endpoint_hash=<sha256:8>` (compute hash client-side via `crypto.subtle.digest`), `pwa.push.subscribe_failed cause=<message>`.
   - Touch-target floor: use the existing Button primitive (size variant); the design-system-primitive-floor pattern (MEM337) means min-h-11/min-w-11 inherit automatically — do NOT regress the mobile-audit gate.

4. **Mount PushPermissionPrompt in `frontend/src/routes/_layout.tsx`** above `<InstallBanner />` (so install offer comes first; subscription offer second).

5. **Helper `frontend/src/lib/vapid.ts`** with `urlBase64ToUint8Array(base64)` per the standard MDN snippet — pure utility, exported for tests.

6. **Frontend Vitest unit test `frontend/src/lib/vapid.test.ts`** asserting urlBase64ToUint8Array round-trip on the b64url shape produced by T01's generator.

7. **Visual-diff baselines for the new prompt** — extend `frontend/tests/m005-oaptsz-mobile-audit.spec.ts` to include a new route (the prompt renders on `/`, no new route needed) but record a fresh baseline because the header layout changes when PushPermissionPrompt is visible vs hidden. Run the audit and update snapshots.

Assumptions documented in the plan: the synthetic-message debug branch in sw.ts is acceptable as a test hook because (a) it's only triggered by a sentinel field `_testRenderEcho`, (b) the same showNotification code path runs in production. The alternative — driving a real MPS round-trip from Playwright — is not feasible in CI.
  - Files: `frontend/src/sw.ts`, `frontend/src/components/notifications/PushPermissionPrompt.tsx`, `frontend/src/routes/_layout.tsx`, `frontend/src/lib/vapid.ts`, `frontend/src/lib/vapid.test.ts`, `frontend/tests/m005-oaptsz-mobile-audit.spec.ts`, `frontend/tests/m005-oaptsz-mobile-audit.spec.ts-snapshots`
  - Verify: From `frontend/`: `bun run build` succeeds; `dist/sw.js` contains the strings `notificationclick` and `showNotification` and `BroadcastChannel`. `bunx vitest run src/lib/vapid.test.ts` passes. `bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts --update-snapshots` — re-record baseline (new banner in the header), then run again without `--update-snapshots` and confirm pass. `bunx playwright test --project=iphone-13-mobile-safari m005-oaptsz-mobile-audit.spec.ts` — passes (re-record snapshots only on the chromium project to keep WebKit baselines stable; iOS prompt branch may render differently — accept that and adjust the spec to only assert touch-target/no-horizontal-scroll if the visual-diff is too unstable).

- [x] **T05: Slice contract gate Playwright spec + m005-oaptsz-push project + multi-device 410-prune integration test** `est:M`
  Closes the slice. Proves the end-to-end shape via two complementary gates: a real-SW Playwright run that exercises the subscribe→permission→pushManager→subscribe-POST→push-render→notificationclick path, and a backend integration test that exercises the multi-device delivery + 410-prune contract via respx-mocked Mozilla Push Service.

What to build:

1. **New Playwright project `m005-oaptsz-push` in `frontend/playwright.config.ts`** mirroring `m005-oaptsz-sw`:
   - `baseURL: 'http://localhost:4173'` (production preview — the SW only registers there; reuses the existing webServer entry from S01's webServer array).
   - `serviceWorkers: 'allow'`.
   - `permissions: ['notifications']` (Playwright pre-grants — the spec doesn't have to script the permission prompt accept).
   - `storageState: 'playwright/.auth/user.json'` so the spec lands authenticated as the seeded superuser.
   - `testMatch: /m005-oaptsz-push\.spec\.ts/`.
   - Add `'m005-oaptsz-push.spec.ts'` to the existing `testIgnore: [...]` arrays on chromium / mobile-chrome / iphone-13-mobile-safari / desktop-firefox so the spec only runs in its dedicated project.

2. **`frontend/tests/m005-oaptsz-push.spec.ts`** — slice contract gate. Before the spec runs (in beforeAll or as a setup step): use the typed admin SDK (page.evaluate + dynamic import per MEM347) to call POST /admin/settings/vapid_keys/generate so the VAPID keys exist in the DB. Then:
   - **Scenario A — subscribe round-trip:**
     1. page.goto('/'); wait for `navigator.serviceWorker.controller !== null`.
     2. Click PushPermissionPrompt's Allow button (Playwright auto-grants the permission since `permissions: ['notifications']`).
     3. Wait for `pwa.push.subscribed` console.info or for a network response on `POST /api/v1/push/subscribe`.
     4. Assert backend has a row by hitting `GET /api/v1/push/subscriptions` (typed client) and asserting count ≥ 1.
   - **Scenario B — push render via debug branch:**
     1. After subscribe completes, set up a `BroadcastChannel('pwa-push-test')` listener in page.evaluate.
     2. Send a synthetic message into the SW: `navigator.serviceWorker.controller.postMessage({type: 'TEST_PUSH', _testRenderEcho: true, payload: {title: 'Test', body: 'Body', url: '/items', kind: 'system'}})`.
     3. Listen on the broadcast channel for `{type: 'rendered', title: 'Test', body: 'Body'}` — proves showNotification fired with the right args.
   - **Scenario C — notificationclick navigation:**
     1. From within page.evaluate, dispatch a synthetic `notificationclick`-equivalent message (`{type: 'TEST_CLICK', _testRenderEcho: true, payload: {url: '/items'}}`).
     2. Listen for `{type: 'navigated', target: '/items'}` on the broadcast channel.
   - All three scenarios run sequentially in one test() to avoid SW re-registration churn.

3. **Backend integration test `backend/tests/api/routes/test_push_dispatch.py::test_multi_device_410_prune_end_to_end`** (extending T02's file): seed one user with three subscriptions; mock MPS to return 201 for endpoint A, 410 for endpoint B, 500 for endpoint C; call `dispatch_push(...)` directly; assert: A's `last_seen_at` advanced + counter reset; B is gone from DB + pruned_410 log captured; C's `consecutive_failures = 1` + pruned_max_failures NOT yet logged.

4. **Self-audit checklist run (you, the planner — but worth listing):**
   - Slice goal achievable from completed tasks? Yes — T01 schema + admin keys, T02 dispatcher, T03 routes + UI toggle live, T04 SW + prompt UX, T05 gate.
   - R023 advanced (subscription persists, dispatch via pywebpush, 410 prunes, multi-device): all four conditions tested in T02 + T03 + T05.
   - R024 supported (push channel of preferences works): wired in T02 + T03; visible in T05's preferences-driven push path.
   - No task references future work; ordering linear with one fan-out (T03/T04 both depend on T01+T02 and are independent of each other; T05 depends on all of T01-T04).
   - Real-device round-trip explicitly NOT required — that's S05 acceptance scenario 2.
  - Files: `frontend/playwright.config.ts`, `frontend/tests/m005-oaptsz-push.spec.ts`, `backend/tests/api/routes/test_push_dispatch.py`
  - Verify: From `frontend/`: `bunx playwright test --project=m005-oaptsz-push m005-oaptsz-push.spec.ts` — passes (Scenarios A + B + C). From `backend/`: `uv run pytest tests/api/routes/test_push_dispatch.py::test_multi_device_410_prune_end_to_end -x` — passes. Combined slice contract: from repo root, run both verify commands; both must succeed. Verify the audit grep gates: `cd frontend && bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts` still passes (T04's PushPermissionPrompt mount didn't regress mobile-audit). And `cd backend && uv run pytest tests/api/routes/test_push_dispatch.py -k 'redaction or endpoint_hash'` — proves no leak.

## Files Likely Touched

- backend/app/alembic/versions/s08_push_subscriptions.py
- backend/app/models.py
- backend/app/api/routes/admin.py
- backend/app/api/routes/push.py
- backend/app/api/main.py
- backend/pyproject.toml
- backend/tests/migrations/test_s08_push_subscriptions_migration.py
- backend/tests/api/routes/test_push.py
- backend/app/core/push_dispatch.py
- backend/app/core/notify.py
- backend/tests/api/routes/test_push_dispatch.py
- backend/tests/api/routes/test_notifications.py
- frontend/openapi.json
- frontend/src/client/sdk.gen.ts
- frontend/src/client/types.gen.ts
- frontend/src/client/schemas.gen.ts
- frontend/src/components/notifications/NotificationPreferences.tsx
- frontend/src/sw.ts
- frontend/src/components/notifications/PushPermissionPrompt.tsx
- frontend/src/routes/_layout.tsx
- frontend/src/lib/vapid.ts
- frontend/src/lib/vapid.test.ts
- frontend/tests/m005-oaptsz-mobile-audit.spec.ts
- frontend/tests/m005-oaptsz-mobile-audit.spec.ts-snapshots
- frontend/playwright.config.ts
- frontend/tests/m005-oaptsz-push.spec.ts
