---
estimated_steps: 19
estimated_files: 7
skills_used: []
---

# T03: POST/DELETE /push/subscribe routes + frontend client regen + Notifications-tab push toggle goes live

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

## Inputs

- ``backend/app/api/routes/push.py``
- ``backend/app/models.py``
- ``backend/app/api/deps.py``
- ``backend/tests/api/routes/test_push.py``
- ``backend/tests/api/routes/test_notifications.py``
- ``scripts/generate-client.sh``
- ``frontend/src/client/sdk.gen.ts``
- ``frontend/src/components/notifications/NotificationPreferences.tsx``
- ``frontend/openapi.json``

## Expected Output

- ``backend/app/api/routes/push.py` (POST/DELETE/GET subscribe routes added)`
- ``backend/tests/api/routes/test_push.py` (subscribe/unsubscribe tests added)`
- ``frontend/openapi.json` (regenerated)`
- ``frontend/src/client/sdk.gen.ts` (PushService surfaced)`
- ``frontend/src/client/types.gen.ts` (PushSubscriptionPublic etc. typed)`
- ``frontend/src/client/schemas.gen.ts` (regenerated)`
- ``frontend/src/components/notifications/NotificationPreferences.tsx` (Push switch goes live)`

## Verification

From `backend/`: `uv run pytest tests/api/routes/test_push.py -x` — all subscribe/unsubscribe/list tests pass. From repo root: `bash scripts/generate-client.sh` regenerates the frontend client without errors. From `frontend/`: `grep -q 'PushService' src/client/sdk.gen.ts` and `bun run build` succeeds with no TS errors. Manual: `bunx playwright test --project=chromium m005-oaptsz-notifications-preferences.spec.ts` still passes (push toggle change should be cosmetic to the existing spec).

## Observability Impact

Adds `push.subscribe`, `push.subscribe.upsert ... existing=true`, `push.unsubscribe ... deleted=<bool>` INFO logs. Every log line uses `endpoint_hash=<sha256:8>` — assertion in test_subscribe_log_uses_endpoint_hash_not_url proves raw endpoint never logs. user_agent is captured on the row but never logged.
