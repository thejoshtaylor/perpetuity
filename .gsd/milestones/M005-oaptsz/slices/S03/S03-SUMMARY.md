---
id: S03
parent: M005-oaptsz
milestone: M005-oaptsz
provides:
  - S04 can mount voice UI beside an already-expanded header/prompt area without revisiting notification plumbing.
  - S05 can use the push subscription APIs, VAPID generation endpoint, dispatcher pruning behavior, and SW click navigation contract for real-device acceptance.
  - Future workflow-engine slices can call existing `notify()` with push preferences instead of integrating directly with pywebpush.
requires:
  - slice: S01
    provides: PWA manifest/service-worker registration and NetworkOnly /api/* contract that S03 extends with push handlers.
  - slice: S02
    provides: Notification model, preferences model, notify() helper, bell panel, and settings preferences UI that S03 extends with the push channel.
affects:
  - S05: final integrated acceptance and real-device push round-trip
  - Future workflow-engine/run-detail slices that will supply real workflow_run_failed notification call sites
key_files:
  - backend/app/alembic/versions/s08_push_subscriptions.py
  - backend/app/models.py
  - backend/app/api/routes/admin.py
  - backend/app/api/routes/push.py
  - backend/app/core/push_dispatch.py
  - backend/app/core/notify.py
  - backend/tests/migrations/test_s08_push_subscriptions_migration.py
  - backend/tests/api/routes/test_push.py
  - backend/tests/api/routes/test_push_dispatch.py
  - backend/tests/api/routes/test_notifications.py
  - frontend/src/components/notifications/PushPermissionPrompt.tsx
  - frontend/src/components/notifications/NotificationPreferences.tsx
  - frontend/src/lib/vapid.ts
  - frontend/src/lib/vapid.test.ts
  - frontend/src/sw.ts
  - frontend/tests/m005-oaptsz-push.spec.ts
  - frontend/playwright.config.ts
  - frontend/openapi.json
  - frontend/src/client/sdk.gen.ts
  - frontend/src/client/types.gen.ts
  - .gsd/PROJECT.md
key_decisions:
  - VAPID keys are generated atomically; public is non-sensitive/plain and private is Fernet-encrypted/write-only.
  - Push subscription endpoint URLs are bearer-style secrets; all API/log read surfaces use endpoint_hash only.
  - Dispatcher commits fan-out row state atomically and self-prunes HTTP 410 plus five consecutive 5xx failures.
  - Headless CI proves SW push/click behavior via gated TEST_PUSH/TEST_CLICK branches and BroadcastChannel echoes; real device push is deferred to S05.
patterns_established:
  - endpoint_hash redaction pattern for all push subscription observability
  - atomic keypair generation exception for non-sensitive generated public settings
  - pywebpush import-site monkeypatching for deterministic dispatcher tests
  - service-worker production-path test hooks gated by `_testRenderEcho`
  - Playwright APIRequestContext for backend calls from production preview projects
observability_surfaces:
  - Backend INFO: push.subscribe, push.subscribe.upsert, push.unsubscribe, push.dispatch.start, push.dispatch.delivered, push.dispatch.pruned_410, push.vapid_public_key.served, admin.vapid_keys.generated
  - Backend WARNING: push.dispatch.consecutive_failure, push.dispatch.pruned_max_failures
  - Backend ERROR: push.dispatch.send_failed, push.vapid_decrypt_failed, notify.push_failed
  - Frontend/SW console: pwa.push.permission_prompt_shown, pwa.push.permission_granted, pwa.push.permission_denied, pwa.push.subscribed, pwa.push.subscribe_failed, pwa.push.received, pwa.push.notification_clicked, pwa.push.show_failed
  - Forensics: `push_subscriptions` rows expose endpoint, last_seen_at, last_status_code, and consecutive_failures for operator inspection while API/log surfaces expose endpoint_hash only
drill_down_paths:
  - .gsd/milestones/M005-oaptsz/slices/S03/tasks/T01-SUMMARY.md
  - .gsd/milestones/M005-oaptsz/slices/S03/tasks/T02-SUMMARY.md
  - .gsd/milestones/M005-oaptsz/slices/S03/tasks/T03-SUMMARY.md
  - .gsd/milestones/M005-oaptsz/slices/S03/tasks/T04-SUMMARY.md
  - .gsd/milestones/M005-oaptsz/slices/S03/tasks/T05-SUMMARY.md
duration: ""
verification_result: passed
completed_at: 2026-04-28T18:16:45.821Z
blocker_discovered: false
---

# S03: Web Push delivery (VAPID + push_subscriptions + pywebpush dispatcher)

**S03 delivered the end-to-end Web Push integration layer: VAPID key generation/storage, push subscription APIs, pywebpush fan-out with self-pruning, notification preference routing, service-worker push/click handling, and a production-preview Playwright contract for subscribe/render/click behavior.**

## What Happened

S03 converted the notification center from in-app-only into a real Web Push-capable channel. The backend now has an s08 `push_subscriptions` table with per-user multi-device rows, unique `(user_id, endpoint)` upsert semantics, user-delete cascade, delivery metadata, and failure counters. VAPID configuration is stored in `system_settings`: `vapid_public_key` is plain/non-sensitive and served publicly to browsers, while `vapid_private_key` is Fernet-encrypted and minted only through the atomic superuser endpoint `POST /api/v1/admin/settings/vapid_keys/generate`, which writes both halves together and displays the keypair once.

The push API surface now exposes `GET /api/v1/push/vapid_public_key`, authenticated `POST /api/v1/push/subscribe`, `DELETE /api/v1/push/subscribe`, and `GET /api/v1/push/subscriptions`. All read models and logs project the endpoint as `endpoint_hash=sha256(endpoint)[:8]`; raw endpoint URLs never cross API read surfaces or logs. The frontend OpenAPI client was regenerated and `NotificationPreferences` now has a live push toggle wired through the existing preference mutation path, with an inline hint when the user has no registered devices.

The dispatcher is implemented in `backend/app/core/push_dispatch.py`. `dispatch_push()` loads/decrypts VAPID settings, selects all user subscriptions, sends VAPID-signed webpush payloads through pywebpush, updates successful rows, prunes HTTP 410 subscriptions immediately, increments 5xx failure counters, and prunes after five consecutive 5xx failures. `notify()` now resolves the push preference, renders kind-specific push title/body/url tuples, invokes the dispatcher when `push=true`, and catches push failures so the in-app notification path and caller route remain robust.

On the browser side, `frontend/src/sw.ts` replaced the S01 push stub with real `push` and `notificationclick` handlers. Push payloads render via `registration.showNotification` with sane fallback content; clicks focus an existing app window and post a navigation message or open the target URL. `PushPermissionPrompt` mounts under the install banner, detects Push API support, requests notification permission, fetches the VAPID public key, subscribes through `PushManager`, posts the browser subscription to the backend, and silently refreshes existing subscriptions on granted remount. The service worker also has gated `TEST_PUSH` and `TEST_CLICK` message branches that reuse the production render/click paths and echo through `BroadcastChannel('pwa-push-test')` so CI can prove behavior without a real Mozilla/FCM/APNs round-trip.

Important boundary: this slice proves Web Push to integration depth, not real-device delivery. Headless Playwright cannot reliably exercise real `pushManager.subscribe()` or OS notification rendering, so S03 proves subscription persistence with a synthetic browser-shaped subscription body, proves dispatcher behavior with monkeypatched pywebpush responses, and proves service-worker render/click intent with production-path test hooks. The phone-backgrounded real push round-trip remains intentionally deferred to S05 acceptance.

## Verification

Fresh slice-level verification was run after the task summaries were produced.

- `POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app uv --directory backend run pytest tests/migrations/test_s08_push_subscriptions_migration.py tests/api/routes/test_push.py tests/api/routes/test_push_dispatch.py tests/api/routes/test_notifications.py::test_push_channel_routes_to_dispatcher tests/api/routes/test_notifications.py::test_push_channel_off_skips_dispatcher -x` → 41 passed in 1.73s. This covers the s08 migration, subscribe/unsubscribe/list/public-key/admin-generate routes, dispatcher success/410/5xx/redaction/decrypt-failure behavior, and notify() push routing on/off.
- `bun run --cwd frontend build` → pass. Vite build and PWA injectManifest service-worker build completed; only the existing >500kB chunk warning was emitted.
- `grep -oE "notificationclick|showNotification|BroadcastChannel" frontend/dist/sw.js | sort | uniq -c` → pass with `2 BroadcastChannel`, `1 notificationclick`, `1 showNotification` in the built SW.
- `bunx vitest run frontend/src/lib/vapid.test.ts` → 4/4 passed in 145ms, covering base64url VAPID decoding and invalid input handling.
- Backend/frontend test environment was prepared for Playwright by starting FastAPI on localhost:8000 against `POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app` and reseeding `app.initial_data`.
- `bun run --cwd frontend playwright test --project=m005-oaptsz-push m005-oaptsz-push.spec.ts --reporter=line` → 2 passed in 10.5s. The dedicated production-preview project proved setup auth plus subscribe→list hash visibility, SW `TEST_PUSH` render echo, and SW `TEST_CLICK` navigation echo.
- `bun run --cwd frontend playwright test --project=m005-oaptsz-sw m005-oaptsz-sw-bypass.spec.ts --reporter=line` → 1 passed in 8.4s, proving S01's service-worker NetworkOnly `/api/*` boundary was not regressed.

Operational readiness confirmed: backend logs expose subscribe/upsert/unsubscribe, dispatch start/delivered/pruned_410/consecutive_failure/pruned_max_failures, VAPID decrypt failure, and send failure signals using endpoint hashes only; frontend logs expose prompt shown/granted/denied, subscribed, subscribe_failed, push received, notification clicked, and show_failed signals. Recovery procedures are clear: re-run VAPID generation if keys are missing/corrupt (with known subscription-rotation impact), inspect `push_subscriptions` by `user_id`/`endpoint`/`last_status_code`/`consecutive_failures`, prune/refresh device rows via unsubscribe/subscribe, and use the service-worker DevTools Push panel or the gated Playwright `TEST_PUSH` branch for SW diagnostics.

## Requirements Advanced

- R023 — Advanced the push half of notification delivery: push subscription persistence, backend dispatch through pywebpush, self-pruning dead subscriptions, frontend permission prompt, and service-worker render/click handling are implemented and integration-tested.
- R024 — Advanced notification routing by making the push column of existing notification preferences active end-to-end; toggling push=true now gates dispatcher fan-out.

## Requirements Validated

None.

## New Requirements Surfaced

None.

## Requirements Invalidated or Re-scoped

None.

## Operational Readiness

None.

## Deviations

No product-scope deviations. Verification uses integration-depth CI proof rather than a real Mozilla/FCM/APNs device round-trip; this is explicitly within the slice plan and S05 owns real-device acceptance. During closure, Playwright required the backend API to be started and seeded before rerunning the dedicated push project; this environment requirement is now captured as durable memory.

## Known Limitations

Real phone-backgrounded Web Push delivery through browser vendor push services is not proven by this slice; S05 owns that acceptance scenario. Headless Playwright cannot reliably exercise real `pushManager.subscribe()` or OS notification surfaces, so CI uses synthetic subscription JSON and gated SW test hooks. Existing mobile audit drift on `/admin/teams` from seeded team rows/DataTable pagination remains out of scope and should be handled in a future polish or S05 mobile UAT pass if it recurs.

## Follow-ups

S05 should run the real-device phone/laptop push round-trip, including backgrounded app delivery within 30s and tap-to-run-detail navigation. When the workflow engine/run-detail routes land, replace the current synthetic/admin test-event stand-in with real workflow failure call sites and verify `/runs/<id>` targets against real run IDs. Consider tightening PushSubscriptionCreate key validation from loose JSON object to `{p256dh, auth}` once field debugging needs justify the stricter boundary.

## Files Created/Modified

- `backend/app/alembic/versions/s08_push_subscriptions.py` — Push subscription schema migration with cascade, unique user+endpoint, and fan-out index.
- `backend/app/api/routes/push.py` — Public VAPID key route plus authenticated subscribe/unsubscribe/list routes.
- `backend/app/core/push_dispatch.py` — pywebpush dispatcher with VAPID signing, delivery metadata, 410 pruning, 5xx failure counters, and redacted logging.
- `backend/app/core/notify.py` — Push preference resolution and notify-to-dispatcher integration.
- `frontend/src/sw.ts` — Real push and notificationclick handlers plus gated test hooks.
- `frontend/src/components/notifications/PushPermissionPrompt.tsx` — Push permission prompt and browser subscription flow.
- `frontend/tests/m005-oaptsz-push.spec.ts` — Dedicated Playwright push contract spec.
