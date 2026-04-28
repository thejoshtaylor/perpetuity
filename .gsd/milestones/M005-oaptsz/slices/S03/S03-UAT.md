# S03: Web Push delivery (VAPID + push_subscriptions + pywebpush dispatcher) — UAT

**Milestone:** M005-oaptsz
**Written:** 2026-04-28T18:16:45.822Z

# S03 UAT — Web Push delivery

## Preconditions

1. Backend API is running on `http://localhost:8000` against a migrated database at s08 or later.
2. Frontend production preview is running on `http://localhost:4173` so the built service worker registers.
3. A system admin user exists (`admin@example.com` in seeded local data) and can log in.
4. `POST /api/v1/admin/settings/vapid_keys/generate` has been run at least once, or the first test case will run it.
5. Browser supports Service Worker, PushManager, and Notification APIs. For real-device validation, use Android Chrome or iOS Safari 16.4+ installed PWA mode.

## Test Case 1 — Generate and serve VAPID keys

1. Log in as a system admin.
2. Call `POST /api/v1/admin/settings/vapid_keys/generate`.
   - Expected: Response contains `public_key`, `private_key`, and `overwrote_existing`; keys are shown only in this response.
   - Expected: `vapid_public_key` is stored plain in `system_settings.value`; `vapid_private_key` is stored encrypted with `has_value=true` and is not visible through normal settings reads.
3. Call `GET /api/v1/push/vapid_public_key` without auth.
   - Expected: 200 with the public key.
   - Expected: Backend log includes `push.vapid_public_key.served key_prefix=<first4>` and never logs the private key.

## Test Case 2 — Subscribe a browser/device

1. Open the app at `http://localhost:4173` in a supported browser and sign in.
2. When the push permission prompt appears, click **Allow**.
   - Expected: Browser notification permission is requested.
   - Expected: On grant, frontend fetches `/push/vapid_public_key`, calls `pushManager.subscribe({ userVisibleOnly: true, applicationServerKey })`, then posts the subscription JSON to `POST /api/v1/push/subscribe`.
   - Expected: UI does not expose the raw endpoint URL.
3. Call `GET /api/v1/push/subscriptions` as the signed-in user.
   - Expected: Response includes at least one row with `endpoint_hash`, timestamps, and no raw endpoint.
   - Expected: Backend logs `push.subscribe` or `push.subscribe.upsert` with `endpoint_hash=<8 hex chars>` only.

## Test Case 3 — Preference routes push fan-out

1. Go to Settings → Notifications.
2. For an event kind such as `workflow_run_failed` or `system`, toggle Push on while leaving in-app routing as desired.
   - Expected: Preference saves successfully.
   - Expected: If no subscriptions exist, the inline hint says to allow notifications when prompted.
3. Trigger a test notification of the same kind through the admin/system test route.
   - Expected: In-app notification behavior follows the in-app preference.
   - Expected: If push preference is true and subscriptions exist, backend logs `push.dispatch.start ... subscriptions=<n>` and one delivery attempt per subscription.
   - Expected: If push preference is false, notify still can insert in-app rows but dispatcher is not called.

## Test Case 4 — Multi-device fan-out

1. Subscribe the same user in two browser/device contexts (for example laptop Chrome and phone Chrome).
2. Confirm `GET /api/v1/push/subscriptions` returns two endpoint hashes.
3. Enable Push for a test event type and trigger that event.
   - Expected: Dispatcher attempts one send per subscription.
   - Expected: Successful sends update `last_seen_at`, set `last_status_code` to a 2xx code, and reset `consecutive_failures=0`.
   - Expected: No raw endpoint URL appears in backend logs.

## Test Case 5 — Dead subscription pruning

1. Seed or simulate a subscription whose push endpoint returns HTTP 410.
2. Trigger a push-routed notification for that user.
   - Expected: Dispatcher logs `push.dispatch.pruned_410 user_id=<uuid> endpoint_hash=<hash>`.
   - Expected: The subscription row is deleted automatically.
   - Expected: Other sibling-device subscriptions continue to deliver/update normally in the same fan-out.

## Test Case 6 — Repeated upstream 5xx handling

1. Seed or simulate a subscription endpoint returning HTTP 500.
2. Trigger the same push-routed notification five times.
   - Expected: Attempts 1–4 increment `consecutive_failures` and log `push.dispatch.consecutive_failure ... count=<n>`.
   - Expected: Attempt 5 prunes the row and logs `push.dispatch.pruned_max_failures`.
   - Expected: The row is gone from `push_subscriptions` after the fifth failure.

## Test Case 7 — Service-worker push render and click navigation

1. With the production preview service worker active, post a gated test message to the active service worker: `{ type: 'TEST_PUSH', _testRenderEcho: true, payload: { title: 'Workflow failed', body: 'Run abc failed', url: '/runs/abc', kind: 'workflow_run_failed' } }`.
   - Expected: SW reuses the same render path as a real push.
   - Expected: A `BroadcastChannel('pwa-push-test')` message `{ type: 'RECEIVED', kind: 'workflow_run_failed', title: 'Workflow failed', body: 'Run abc failed' }` is observed.
2. Post `{ type: 'TEST_CLICK', _testRenderEcho: true, payload: { url: '/runs/abc' } }`.
   - Expected: SW reuses the notification click path.
   - Expected: A broadcast `{ type: 'CLICKED', url: '/runs/abc' }` is observed.
   - Expected: If a client window is open, it receives a navigation postMessage; otherwise the SW attempts `clients.openWindow('/runs/abc')`.

## Edge Cases

- Unsupported browser: if `serviceWorker`, `PushManager`, or `Notification` is unavailable, prompt renders nothing/fallback instead of throwing.
- Permission denied: prompt logs `pwa.push.permission_denied`, dispatches the denied event, and does not attempt subscribe.
- Missing VAPID public key: `/push/vapid_public_key` returns 503; prompt logs `pwa.push.subscribe_failed cause=vapid_not_configured` and stops.
- Existing granted subscription: prompt silently re-POSTs the existing subscription on remount to refresh `last_seen_at`.
- Bad push payload: SW renders generic `Perpetuity / You have a new notification` instead of dropping the event.
- Redaction: grep backend and frontend logs for VAPID private key prefix and raw push endpoint URLs; expected zero matches except endpoint hashes.
