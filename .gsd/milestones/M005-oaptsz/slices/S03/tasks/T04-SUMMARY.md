---
id: T04
parent: S03
milestone: M005-oaptsz
key_files:
  - frontend/src/sw.ts
  - frontend/src/components/notifications/PushPermissionPrompt.tsx
  - frontend/src/routes/_layout.tsx
  - frontend/src/lib/vapid.ts
  - frontend/src/lib/vapid.test.ts
  - frontend/tsconfig.build.json
key_decisions:
  - SW message-event TEST_PUSH branch (gated on `_testRenderEcho: true` sentinel) reuses production showPushNotification code path so Playwright asserts on real render behavior — not a stub. Alternative (driving real Mozilla Push Service from CI) is infeasible. (MEM370)
  - BroadcastChannel('pwa-push-test') is the spec-side observation channel for both push render and notificationclick. Production code only posts; nothing in production listens. Picked over console.info-scraping because Workbox sometimes swallows SW console output.
  - vapid.ts urlBase64ToUint8Array allocates a fresh ArrayBuffer (not ArrayBufferLike) so the returned Uint8Array satisfies PushManager's BufferSource type without `as` casts. The narrower buffer type matters at the type system; runtime is identical.
  - iOS Safari pre-16.4 branch in PushPermissionPrompt renders nothing (probes via `'PushManager' in window`) — S01's iOS Add-To-Home-Screen toast already covers the pre-installed surface, and re-prompting once installed would just be noise.
  - 503 from /push/vapid_public_key is treated as a structured failure (`cause=vapid_not_configured`) — the prompt does NOT proceed to subscribe with no key. Mirrors backend's fail-loud posture (D025/M004) at the UI boundary.
duration: 
verification_result: passed
completed_at: 2026-04-28T16:58:38.268Z
blocker_discovered: false
---

# T04: Wire service-worker push + notificationclick handlers, ship PushPermissionPrompt UI, and add VAPID base64url decoder helper.

**Wire service-worker push + notificationclick handlers, ship PushPermissionPrompt UI, and add VAPID base64url decoder helper.**

## What Happened

Browser-side push render + permission UX, end-to-end. Five pieces shipped:

1. **`frontend/src/sw.ts`** — Replaced S01's no-op `push` stub with a real handler that parses `event.data.json()` (T02 dispatcher's `{title, body, url, kind, icon?}` shape), falls back to `Perpetuity / "You have a new notification"` if parse fails (we never silently drop a push), and calls `self.registration.showNotification(title, { body, data: { url, kind }, icon, badge: '/pwa-192.png', tag: kind })`. The `tag: kind` collapses repeats of the same notification class on Android. Console.info `pwa.push.received kind=<kind>`. Added `notificationclick` handler that closes the notification, walks `clients.matchAll({type:'window', includeUncontrolled:true})`, focuses an existing window + postMessages `{type:'NAVIGATE', url}` if one is open, else `clients.openWindow(payload.url || '/')`. Console.info `pwa.push.notification_clicked target_path=<url>`. Added a `message` event branch gated on a `TEST_PUSH` sentinel (`_testRenderEcho: true`) that reuses the same `showPushNotification` code path so T05's Playwright spec can drive it without spinning up Mozilla Push Service. Both handlers also post `{type:'RECEIVED'|'CLICKED', ...}` to a `BroadcastChannel('pwa-push-test')` so the spec asserts on production behavior without depending on Workbox console.info plumbing (which sometimes swallows messages).

2. **`frontend/src/components/notifications/PushPermissionPrompt.tsx`** — New component. On mount it probes `'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window`; iOS Safari pre-16.4 (no PushManager) renders nothing — S01's iOS Add-To-Home-Screen toast already covers that pre-installed surface. Reads `Notification.permission`; renders an inline banner with Allow / Not now buttons only while permission is `'default'` and user has not dismissed (sticky via `localStorage.pwa.push.dismissed_at`, mirroring InstallBanner's pattern from S01/T03). On Allow: requestPermission → on `'granted'` fetches VAPID public key from `PushService.getVapidPublicKey()` (bails out structurally on 503 with `cause=vapid_not_configured`; we don't pretend we can subscribe without a key) → `navigator.serviceWorker.ready` → `pushManager.subscribe({userVisibleOnly:true, applicationServerKey: urlBase64ToUint8Array(vapidPublicKey)})` → POST `subscription.toJSON()` to `PushService.subscribe`. Also handles already-`'granted'` re-mounts: silently re-POSTs any existing subscription so `last_seen_at` stays fresh and the dispatcher's stale-row pruning never drops an actively-used browser. All required console.info points emitted (`pwa.push.permission_prompt_shown`, `pwa.push.permission_granted`, `pwa.push.permission_denied`, `pwa.push.subscribed endpoint_hash=<sha256:8>`, `pwa.push.subscribe_failed cause=<message>`). On `'denied'` dispatches a `CustomEvent('pwa-push-permission-denied')` so the bell mount can listen and surface a re-enable hint near the icon. Buttons use `min-h-11` to inherit the design-system touch-target floor (MEM337), keeping the mobile-audit gate green.

3. **`frontend/src/routes/_layout.tsx`** — Mounted `<PushPermissionPrompt />` directly under `<InstallBanner />` so install offer comes first, subscribe offer second.

4. **`frontend/src/lib/vapid.ts`** — Pure utility: `urlBase64ToUint8Array(b64)` decodes the backend's RFC 8292 §3.2 base64url-no-padding VAPID public key into a 65-byte uncompressed P-256 point usable as `applicationServerKey`. Allocates a fresh ArrayBuffer (not ArrayBufferLike) so the BufferSource type-narrows correctly — Push API's TS lib rejects the wider `Uint8Array<ArrayBufferLike>` form. Also exports `endpointHash(endpoint)`: SHA-256 → first 8 hex chars via SubtleCrypto, mirroring the backend's `endpoint_hash=sha256[:8]` log token so grep correlation works across frontend ↔ backend log surfaces.

5. **`frontend/src/lib/vapid.test.ts`** — 4 vitest cases: round-trip the 65-byte uncompressed P-256 fixture (asserts byte-perfect match), reconstruct missing padding (single-byte input), decode URL-safe alphabet (- and _), throw on empty input. All 4 pass in 155ms.

6. **`frontend/tsconfig.build.json`** — Minor adjust to keep the new files in the production build path.

**Test environment notes (carried in MEM368/MEM369):** Resuming this task required restoring the auth.setup.ts → /teams login redirect: backend was 500-ing because it pointed at the `app` DB (stale schema, no `role` column) instead of the migrated `perpetuity_app`. Reseed: `POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app uv run python -m app.initial_data`, then start fastapi with the same env. The `/admin/teams` mobile-audit page also failed touch-target validation because 145 orphaned teams from prior test runs forced DataTable pagination to render 32px chevrons (`DataTable.tsx:155-187`, h-8 w-8). Cleaned to 1 team via `DELETE FROM team WHERE id NOT IN (SELECT team_id FROM team_member WHERE user_id=...)`; the chevron bug itself is pre-existing and not part of M005, captured as MEM369 for follow-up.

## Verification

All slice-plan-T04 verification commands pass:

1. `cd frontend && bun run build` → ✓ vite build (1.97s, no TS errors), SW build (121ms), PWA precache 33 entries (1125 KiB).
2. `grep -o "notificationclick|showNotification|BroadcastChannel" dist/sw.js | sort | uniq -c` → 1 notificationclick, 1 showNotification, 3 BroadcastChannel — all three sentinel strings present in the built SW.
3. `cd frontend && bunx vitest run src/lib/vapid.test.ts` → 4 passed (155ms): 65-byte uncompressed P-256 round-trip, padding reconstruction, URL-safe alphabet, empty-input rejection.
4. `bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts --update-snapshots` → 16 passed (13.1s) on first run with snapshot rebuild.
5. `bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts` (no --update-snapshots) → 16 passed (11.0s) — baseline holds.
6. `bunx playwright test --project=iphone-13-mobile-safari m005-oaptsz-mobile-audit.spec.ts` → 16 passed (18.0s) — iOS branch correctly renders nothing (PushManager absent in WebKit device descriptor) so visual diff stays stable.

The `admin-teams-mobile-chrome-darwin.png` baseline was reverted (`git checkout`) after snapshot rebuild because the diff was caused by DB cleanup (145 orphaned teams → 1), not by T04 code changes. The original baseline still passes once the DB is at expected fixture state. PushPermissionPrompt's visible/hidden delta on the four other authenticated routes stayed within the existing 1% maxDiffPixelRatio so no other snapshot was modified.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd frontend && bun run build` | 0 | ✅ pass (vite + SW build, no TS errors, dist/sw.js 18.79 kB) | 2200ms |
| 2 | `grep -oE "notificationclick|showNotification|BroadcastChannel" frontend/dist/sw.js | sort | uniq -c` | 0 | ✅ pass (1 notificationclick, 1 showNotification, 3 BroadcastChannel) | 50ms |
| 3 | `cd frontend && bunx vitest run src/lib/vapid.test.ts` | 0 | ✅ pass (4/4 in 155ms) | 155ms |
| 4 | `cd frontend && bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts --update-snapshots` | 0 | ✅ pass (16/16, snapshots regenerated) | 13100ms |
| 5 | `cd frontend && bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts` | 0 | ✅ pass (16/16, baseline holds without --update-snapshots) | 11000ms |
| 6 | `cd frontend && bunx playwright test --project=iphone-13-mobile-safari m005-oaptsz-mobile-audit.spec.ts` | 0 | ✅ pass (16/16, iOS PushManager-absent branch renders nothing, stable visual diff) | 18000ms |

## Deviations

No code-level deviations from the task plan. Two test-environment side-effects from resuming after interruption: (a) had to reseed admin@example.com into perpetuity_app DB so Playwright auth.setup.ts could complete the /login → /teams redirect flow; (b) had to clean 145 orphaned `team` rows from prior test runs so DataTable on /admin/teams stayed under pagination threshold (the touch-target violation on h-8 w-8 chevrons is a pre-existing bug, captured as MEM369 — not part of T04 scope).

## Known Issues

DataTable.tsx pagination chevrons (`h-8 w-8`, 32px) violate the ≥44px touch-target floor whenever the table renders pagination. Pre-existing; surfaces in mobile-audit on /admin/teams once the DB has >25 teams. Captured as MEM369 — not in T04 scope, but should be addressed in a future polish slice or rolled into M005-oaptsz/S05's mobile UAT pass. Workaround: clean orphaned teams between test runs, or pass `pageSize` larger than fixture team count.

## Files Created/Modified

- `frontend/src/sw.ts`
- `frontend/src/components/notifications/PushPermissionPrompt.tsx`
- `frontend/src/routes/_layout.tsx`
- `frontend/src/lib/vapid.ts`
- `frontend/src/lib/vapid.test.ts`
- `frontend/tsconfig.build.json`
