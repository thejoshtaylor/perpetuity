---
estimated_steps: 30
estimated_files: 3
skills_used: []
---

# T05: Slice contract gate Playwright spec + m005-oaptsz-push project + multi-device 410-prune integration test

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

## Inputs

- ``frontend/playwright.config.ts``
- ``frontend/tests/m005-oaptsz-sw-bypass.spec.ts``
- ``frontend/tests/m005-oaptsz-notifications.spec.ts``
- ``frontend/tests/utils/audit.ts``
- ``backend/tests/api/routes/test_push_dispatch.py``
- ``backend/app/core/push_dispatch.py``
- ``frontend/src/sw.ts``
- ``frontend/src/components/notifications/PushPermissionPrompt.tsx``
- ``frontend/src/client/sdk.gen.ts``

## Expected Output

- ``frontend/playwright.config.ts` (m005-oaptsz-push project added; testIgnore arrays updated)`
- ``frontend/tests/m005-oaptsz-push.spec.ts``
- ``backend/tests/api/routes/test_push_dispatch.py` (multi-device 410-prune integration test added)`

## Verification

From `frontend/`: `bunx playwright test --project=m005-oaptsz-push m005-oaptsz-push.spec.ts` — passes (Scenarios A + B + C). From `backend/`: `uv run pytest tests/api/routes/test_push_dispatch.py::test_multi_device_410_prune_end_to_end -x` — passes. Combined slice contract: from repo root, run both verify commands; both must succeed. Verify the audit grep gates: `cd frontend && bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts` still passes (T04's PushPermissionPrompt mount didn't regress mobile-audit). And `cd backend && uv run pytest tests/api/routes/test_push_dispatch.py -k 'redaction or endpoint_hash'` — proves no leak.

## Observability Impact

No new log lines — this task verifies the existing T02–T04 surfaces. The Playwright spec listens on BroadcastChannel('pwa-push-test') and on console.info for `pwa.push.subscribed` and `pwa.push.notification_clicked`; the integration test asserts caplog records for `push.dispatch.delivered`, `push.dispatch.pruned_410`, `push.dispatch.consecutive_failure`.
