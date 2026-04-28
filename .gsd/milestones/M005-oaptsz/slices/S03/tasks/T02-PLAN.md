---
estimated_steps: 41
estimated_files: 4
skills_used: []
---

# T02: pywebpush dispatcher + replace `_push_stub` in notify() with real fan-out + redaction sweep

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

## Inputs

- ``backend/app/core/notify.py``
- ``backend/app/core/encryption.py``
- ``backend/app/api/routes/admin.py``
- ``backend/app/models.py``
- ``backend/tests/api/routes/test_notifications.py``
- ``backend/tests/conftest.py``
- ``backend/pyproject.toml``

## Expected Output

- ``backend/app/core/push_dispatch.py``
- ``backend/app/core/notify.py` (replaced _push_stub + added _resolve_push + _render_push)`
- ``backend/tests/api/routes/test_push_dispatch.py``
- ``backend/tests/api/routes/test_notifications.py` (added push-channel integration tests)`

## Verification

From `backend/`: `uv run pytest tests/api/routes/test_push_dispatch.py tests/api/routes/test_notifications.py::test_push_channel_routes_to_dispatcher tests/api/routes/test_notifications.py::test_push_channel_off_skips_dispatcher -x` — all 8 dispatcher tests + the 2 notify-integration tests pass. Then `uv run pytest tests/api/routes/test_push_dispatch.py -k 'redaction or endpoint_hash' -x` — proves no raw endpoint URL appears in caplog. Then run the existing `tests/api/routes/test_notifications.py` (the S02 test file) end-to-end — must STILL pass; the notify() change is additive.

## Observability Impact

Adds the full push.dispatch.* taxonomy listed in the slice's Observability Impact: start, delivered, pruned_410, pruned_max_failures, consecutive_failure (WARNING), send_failed (ERROR), vapid_decrypt_failed (ERROR). All endpoint mentions in logs use the 8-hex-char sha256 hash — verified by the redaction unit test. The notify.dispatched log line gains a `push=<bool>` field so per-event channel decisions are inspectable.
