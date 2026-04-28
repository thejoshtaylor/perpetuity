---
estimated_steps: 21
estimated_files: 7
skills_used: []
---

# T04: Service-worker push + notificationclick handlers + PushPermissionPrompt UI + subscribe wire-up

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

## Inputs

- ``frontend/src/sw.ts``
- ``frontend/src/main.tsx``
- ``frontend/src/routes/_layout.tsx``
- ``frontend/src/components/Common/InstallBanner.tsx``
- ``frontend/src/client/sdk.gen.ts``
- ``frontend/src/components/ui/button.tsx``
- ``frontend/tests/m005-oaptsz-mobile-audit.spec.ts``
- ``frontend/tests/utils/audit.ts``

## Expected Output

- ``frontend/src/sw.ts` (push handler body + notificationclick + message debug branch)`
- ``frontend/src/components/notifications/PushPermissionPrompt.tsx``
- ``frontend/src/routes/_layout.tsx` (mounts PushPermissionPrompt)`
- ``frontend/src/lib/vapid.ts``
- ``frontend/src/lib/vapid.test.ts``
- ``frontend/tests/m005-oaptsz-mobile-audit.spec.ts-snapshots/` (updated baselines)`

## Verification

From `frontend/`: `bun run build` succeeds; `dist/sw.js` contains the strings `notificationclick` and `showNotification` and `BroadcastChannel`. `bunx vitest run src/lib/vapid.test.ts` passes. `bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts --update-snapshots` — re-record baseline (new banner in the header), then run again without `--update-snapshots` and confirm pass. `bunx playwright test --project=iphone-13-mobile-safari m005-oaptsz-mobile-audit.spec.ts` — passes (re-record snapshots only on the chromium project to keep WebKit baselines stable; iOS prompt branch may render differently — accept that and adjust the spec to only assert touch-target/no-horizontal-scroll if the visual-diff is too unstable).

## Observability Impact

Replaces S01's `pwa.push.received_stub` with `pwa.push.received kind=<kind>` from the real handler. Adds `pwa.push.permission_prompt_shown`, `pwa.push.permission_granted`, `pwa.push.permission_denied`, `pwa.push.subscribed endpoint_hash=<sha256:8>` (client-side hash via SubtleCrypto), `pwa.push.subscribe_failed cause=<message>`, `pwa.push.notification_clicked target_path=<url>`. localStorage key `pwa.push.dismissed_at` adds to the existing pwa.* state surfaces from S01.
