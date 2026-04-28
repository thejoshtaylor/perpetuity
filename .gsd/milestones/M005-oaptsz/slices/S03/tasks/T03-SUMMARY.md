---
id: T03
parent: S03
milestone: M005-oaptsz
key_files:
  - backend/app/api/routes/push.py
  - backend/app/models.py
  - backend/tests/api/routes/test_push.py
  - frontend/openapi.json
  - frontend/src/client/sdk.gen.ts
  - frontend/src/client/types.gen.ts
  - frontend/src/client/schemas.gen.ts
  - frontend/src/components/notifications/NotificationPreferences.tsx
key_decisions:
  - DELETE /push/subscribe takes a body (not a query param) carrying only {endpoint}, via a new PushSubscriptionDelete model. Browser PushSubscription.unsubscribe() never returns key material, so the unsubscribe schema deliberately diverges from PushSubscriptionCreate rather than re-using it with optional keys. (MEM367)
  - Subscribe log lines emit `ua=<leading-whitespace-token>` rather than the full User-Agent. The complete UA still lands on the row column (capped at 500 chars by the model) but never crosses the log surface — a long mobile UA would otherwise blow up grep lines and bloat log indexes. `unknown` is the sentinel when the header is missing. (MEM366)
  - List endpoint /push/subscriptions ORDER BY created_at — gives the UI a stable left-to-right device order in the operator hint and matches the natural insertion sequence (phone first, then laptop, etc.). The route projects through PushSubscriptionPublic so the raw endpoint never crosses the API surface even on private list reads.
  - Subscribe POST returns 201 on first-insert and 200 on upsert. Distinguishing the two status codes lets the SW (in T04) tell `subscribe` from `re-subscribe` without parsing the body — useful when the SW reconciles a pre-existing handle on resume.
duration: 
verification_result: passed
completed_at: 2026-04-28T11:59:32.869Z
blocker_discovered: false
---

# T03: Add POST/DELETE /push/subscribe + GET /push/subscriptions routes, regenerate frontend client, enable Push preference toggle.

**Add POST/DELETE /push/subscribe + GET /push/subscriptions routes, regenerate frontend client, enable Push preference toggle.**

## What Happened

Bridged the browser-side subscription handle to backend storage and lit up the Notifications-tab Push toggle. Three steps:

1. **Backend routes** (`backend/app/api/routes/push.py`). Extended T01's stub with three new endpoints, all gated on `CurrentUser`:
   - `POST /api/v1/push/subscribe` upserts on `(user_id, endpoint)`. First time → INSERT, returns 201 with `PushSubscriptionPublic`, logs `push.subscribe ... ua=<leading-token>`. Existing row → refreshes `keys` + `last_seen_at`, resets `consecutive_failures`/`last_status_code`, returns 200, logs `push.subscribe.upsert ... existing=true`. The `User-Agent` header is captured into the row (truncated to 500 chars) but only the leading whitespace-split token is logged — full UA never appears in log surfaces (MEM366).
   - `DELETE /api/v1/push/subscribe` takes `{endpoint}` only via a new `PushSubscriptionDelete` schema. Idempotent: missing row returns 204 with `deleted=false` log; deleted row returns 204 with `deleted=true` log. The split schema (vs reusing `PushSubscriptionCreate` with optional keys) is needed because the browser's `PushSubscription.unsubscribe()` doesn't return key material — captured as MEM367.
   - `GET /api/v1/push/subscriptions` returns the caller's rows projected through `PushSubscriptionPublic` (hash-only — raw endpoint never crosses the API boundary).
   Every log line uses `endpoint_hash=sha256(endpoint).hexdigest()[:8]` — same helper as `push_dispatch._endpoint_hash` to keep grep correlation working across subscribe → dispatch → unsubscribe.

2. **Backend tests** (`backend/tests/api/routes/test_push.py`). Added 11 new tests covering: first-time insert (201 + DB row), idempotent upsert (200, single row, advanced `last_seen_at`, `existing=true` log), two-device coexistence, delete happy path with `deleted=true`, unknown-endpoint delete is a 204 noop with `deleted=false`, 401 on subscribe/unsubscribe/list without auth, redaction gate (raw endpoint URL never appears in caplog across insert/upsert/delete), full UA never logged (only leading token), per-user list scoping. Updated `_clean_system_settings` fixture to also wipe `PushSubscription` rows so tests don't leak across files.

3. **Frontend client + UI** (`frontend/openapi.json`, `src/client/*.gen.ts`, `src/components/notifications/NotificationPreferences.tsx`). Ran `bash scripts/generate-client.sh` to regenerate. The new `PushService` exposes `getVapidPublicKey()`, `subscribe()`, `unsubscribe()`, `listSubscriptions()`, and the new `PushSubscriptionPublic` / `PushSubscriptionsList` / `PushSubscriptionCreate` types. Replaced the disabled "Available in S03" Switch with a working one wired to the same `updatePref.mutate({ eventType, in_app, push: checked })` shape as the in-app switch. Added a `PushService.listSubscriptions` query gating a small inline hint ("Allow notifications when prompted to receive push.") that renders only when the user has zero registered subscriptions — surfaces the silent-by-design state the slice plan calls out, without becoming a toast.

T04 will wire the actual SW-subscribe flow that fills in the subscriptions table; the dispatcher gate from T02 already handles the "push=true with no subscription" silent path.

## Verification

All three slice verification commands pass:

1. `cd backend && POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app uv run pytest tests/api/routes/test_push.py -x` → 23 passed in 0.63s (12 pre-existing T01 tests + 11 new T03 tests).
2. `bash scripts/generate-client.sh` → openapi.json regenerated, `bun run --filter frontend generate-client` exited 0, `bun run lint` exited 0 with one auto-fix applied to types.gen.ts ordering.
3. `cd frontend && grep -q 'PushService' src/client/sdk.gen.ts` → match. Slice contract greps from the plan — `grep -q 'PushSubscriptionPublic' frontend/src/client/types.gen.ts` and `grep -q 'subscribe' frontend/src/client/sdk.gen.ts` — both succeed.
4. `cd frontend && bun run build` → ✓ built in 1.76s, SW built in 47ms, PWA precache 33 entries — no TS errors.

Regression check: `cd backend && POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app uv run pytest tests/api/routes/test_notifications.py tests/api/routes/test_push_dispatch.py -x` → 34 passed in 1.44s. T02's notify→dispatcher path still routes correctly with the new subscribe surface in place.

Playwright spec `m005-oaptsz-notifications-preferences.spec.ts` was inspected — its only `push` reference is `requestBody: { in_app: ..., push: false }` in a mocked PUT request, which is unaffected by enabling the live toggle. No change needed.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app uv run pytest tests/api/routes/test_push.py -x` | 0 | ✅ pass (23 passed in 0.63s) | 630ms |
| 2 | `bash scripts/generate-client.sh` | 0 | ✅ pass (openapi regenerated, client regenerated, lint clean) | 4500ms |
| 3 | `cd frontend && grep -q 'PushService' src/client/sdk.gen.ts && grep -q 'PushSubscriptionPublic' src/client/types.gen.ts && grep -q 'subscribe' src/client/sdk.gen.ts` | 0 | ✅ pass (slice contract greps all match) | 80ms |
| 4 | `cd frontend && bun run build` | 0 | ✅ pass (vite build + SW build, no TS errors) | 2200ms |
| 5 | `cd backend && POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app uv run pytest tests/api/routes/test_notifications.py tests/api/routes/test_push_dispatch.py -x` | 0 | ✅ pass (34 passed in 1.44s — no T02/S02 regression) | 1440ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `backend/app/api/routes/push.py`
- `backend/app/models.py`
- `backend/tests/api/routes/test_push.py`
- `frontend/openapi.json`
- `frontend/src/client/sdk.gen.ts`
- `frontend/src/client/types.gen.ts`
- `frontend/src/client/schemas.gen.ts`
- `frontend/src/components/notifications/NotificationPreferences.tsx`
