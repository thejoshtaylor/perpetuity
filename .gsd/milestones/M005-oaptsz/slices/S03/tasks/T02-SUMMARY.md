---
id: T02
parent: S03
milestone: M005-oaptsz
key_files:
  - backend/app/core/push_dispatch.py
  - backend/app/core/notify.py
  - backend/tests/api/routes/test_push_dispatch.py
  - backend/tests/api/routes/test_notifications.py
key_decisions:
  - Mock pywebpush.webpush directly via monkeypatch on the dispatcher's import-site (`push_dispatch.webpush`) rather than respx — pywebpush uses `requests`, not httpx, so respx cannot intercept it. The fake records kwargs and returns a `_FakeResponse(status_code=...)` or raises `WebPushException` with a synthetic .response attribute. Captured as MEM362.
  - notify._push() lazy-imports `from app.core import push_dispatch` inside the function body — keeps notify importable even if pywebpush's transitive deps fail and lets `monkeypatch.setattr(push_dispatch, 'dispatch_push', fake)` actually intercept the call. A module-level import would bind a stale reference. MEM363.
  - dispatch_push() commits the entire fan-out — per-row state changes via session.add() AND prune deletes — in a single session.commit() at end of function. Sibling-device updates must land atomically; staggered commits would let the next dispatch read partially-updated state. MEM364.
  - PUSH_DEFAULTS team-default = False for every NotificationKind. Pushes are opt-in: a user must explicitly toggle push=True via the preferences route before the channel fans out, even if in-app is on. Avoids surprising-on-first-event UX and keeps S03 demo's flow honest (user must consent).
  - notify.dispatched log gained a `push=<bool>` field; the line emits whenever EITHER channel produces an effect (in_app row inserted OR push delivered ≥1). Slice's grep gate looks for `push=true` to confirm per-event fan-out happened. MEM365.
duration: 
verification_result: passed
completed_at: 2026-04-28T11:48:05.898Z
blocker_discovered: false
---

# T02: Add pywebpush dispatcher with VAPID-signed fan-out + 410/5xx self-pruning, wire notify() push channel, redaction-only logging.

**Add pywebpush dispatcher with VAPID-signed fan-out + 410/5xx self-pruning, wire notify() push channel, redaction-only logging.**

## What Happened

Stood up the M005/S03 Web Push send-and-prune engine. New module backend/app/core/push_dispatch.py exposes dispatch_push(session, *, user_id, kind, title, body, url, icon=None) -> int (count of accepted deliveries). It loads the configured VAPID private key via decrypt_setting (translates SystemSettingDecryptError → INFO `push.vapid_decrypt_failed key=vapid_private_key` ERROR + return 0), SELECTs every PushSubscription row for the user, and for each row builds a 5-key JSON payload {title, body, url, kind, icon?} and calls pywebpush.webpush(subscription_info, data, vapid_private_key, vapid_claims={'sub':'mailto:operator@perpetuity.invalid'}, ttl=3600). The response branch sets last_status_code, resets consecutive_failures to 0, bumps last_seen_at, and emits `push.dispatch.delivered`. The WebPushException branch consults exc.response.status_code: 410 → schedule the row for delete + INFO `push.dispatch.pruned_410`; 500-599 → bump consecutive_failures, log WARNING `push.dispatch.consecutive_failure count=<n>` (or `push.dispatch.pruned_max_failures` + delete when count reaches 5); 4xx-other → log ERROR `push.dispatch.send_failed status_code=<n>` without prune. Non-WebPushException errors log ERROR `push.dispatch.send_failed cause=<class>` and never prune. Every log line that names an endpoint uses the 8-hex-char sha256 prefix via _endpoint_hash(); the raw URL is treated as a bearer-style secret (never logged). All per-row state changes share a single session.commit() at end of fan-out so sibling-device updates land atomically (MEM364).

In backend/app/core/notify.py I replaced the _push_stub with a real two-stage flow: _resolve_push() mirrors _resolve_in_app but reads the `push` column (PUSH_DEFAULTS map seeds False for every kind today — opt-in by user); _render_push() switches on NotificationKind to synthesize (title, body, url, icon) — workflow_run_failed → 'Workflow failed'/payload.message/`/runs/<id>`, team_invite_accepted → 'Team invite accepted'/payload.team_name/'/teams', project_created → 'Project created'/payload.project_name/'/projects', system + fall-through → 'Notification'/payload.message/'/'. The new _push() wrapper resolves the pref and (when true) lazy-imports app.core.push_dispatch and dispatches with the redacted payload — lazy import is intentional so monkeypatch.setattr(push_dispatch, 'dispatch_push', fake) takes effect from tests (MEM363) and so notify stays import-safe even if pywebpush's tree fails to load. _push() catches every exception locally and emits `notify.push_failed user_id=… kind=… cause=<class> stage=resolve|dispatch` so the in-app channel never depends on push success. The notify.dispatched log line gained a `push=<bool>` field so the slice's grep gate (MEM365) can confirm per-event channel routing.

Tests: tests/api/routes/test_push_dispatch.py adds 8 dispatcher tests using a monkeypatch helper _patch_webpush() that intercepts the `webpush` symbol inside push_dispatch (pywebpush uses `requests`, not httpx — respx can't reach it; MEM362). Coverage: signs/posts with VAPID + correct subscription_info; 201 resets failures and bumps last_seen_at (was: failures=3, status=503); 410 prunes the row + emits the pruned_410 log; sequential 500s warn at counts 1..4 then prune at 5; multi-device fanout (two subscriptions, both delivered); the slice's redaction gate — caplog never carries the raw endpoint URL substring, only the 8-hex-char sha256 prefix; corrupted Fernet ciphertext → returns 0 + emits `push.vapid_decrypt_failed key=vapid_private_key` ERROR; non-WebPushException (RuntimeError) → ERROR log + NO prune. tests/api/routes/test_notifications.py gained 2 integration tests: test_push_channel_routes_to_dispatcher seeds a user + push=True pref + 2 PushSubscription rows + monkeypatches dispatch_push, calls notify(), and asserts dispatch_push received user_id/kind/title='Notification'/body='hello world'/url='/' as synthesized by _render_push. test_push_channel_off_skips_dispatcher seeds push=False + 1 sub, calls notify(), and asserts the in-app row landed AND dispatch_push was NOT called. The cleanup fixture also wipes PushSubscription so the test is isolated from sibling specs.

The two ERROR logs that fire from this slice (`push.vapid_decrypt_failed`, `push.dispatch.send_failed`) plus the WARNING surfaces (`push.dispatch.consecutive_failure`, `push.dispatch.pruned_max_failures`) plus the INFO surfaces (`push.dispatch.start`, `push.dispatch.delivered`, `push.dispatch.pruned_410`) match the slice's Observability Impact taxonomy verbatim; no new redaction holes — the redaction unit test proves it.

## Verification

From /Users/josh/code/perpetuity/backend with POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app:

1. uv run pytest tests/api/routes/test_push_dispatch.py tests/api/routes/test_notifications.py::test_push_channel_routes_to_dispatcher tests/api/routes/test_notifications.py::test_push_channel_off_skips_dispatcher -x — 10/10 passed in 0.48s.
2. uv run pytest tests/api/routes/test_push_dispatch.py -k 'redaction or endpoint_hash' -x — 1/1 (the redaction gate) passed; raw endpoint URL never appears in caplog.
3. uv run pytest tests/api/routes/test_notifications.py -x — full S02 file 26/26 passed in 1.24s; the notify() change is additive.

Negative coverage built into the dispatcher tests: 410 prune path, sequential 5xx prune-at-5 path, non-WebPushException exception path (no prune), corrupted Fernet ciphertext path (return 0 + ERROR log), redaction gate (raw endpoint URL substring forbidden in any caplog line).

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app uv run pytest tests/api/routes/test_push_dispatch.py tests/api/routes/test_notifications.py::test_push_channel_routes_to_dispatcher tests/api/routes/test_notifications.py::test_push_channel_off_skips_dispatcher -x` | 0 | ✅ pass (10 passed in 0.48s) | 480ms |
| 2 | `POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app uv run pytest tests/api/routes/test_push_dispatch.py -k 'redaction or endpoint_hash' -x` | 0 | ✅ pass (1 passed; raw endpoint never in caplog) | 100ms |
| 3 | `POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app uv run pytest tests/api/routes/test_notifications.py -x` | 0 | ✅ pass (26/26 — S02 still green) | 1240ms |

## Deviations

The slice plan suggested respx.mock for pywebpush stubbing; switched to monkeypatching `push_dispatch.webpush` directly because pywebpush 1.14 transports over `requests`, not httpx, and respx is httpx-only (would silently never intercept). The contract the dispatcher tests verify is unchanged — webpush() is invoked once per subscription with the right kwargs, and the response/exception branches drive the prune+counter logic — only the interception layer differs.

Added a 4th status-code branch (`push.dispatch.send_failed status_code=<n>`) for non-410, non-5xx WebPushException cases (e.g. 4xx malformed-payload). The plan's Failure Modes only specified 410, 5xx, and "any other exception"; 4xx-via-WebPushException needed an explicit branch to avoid silent classification as 5xx. No prune on this path — the operator decides via inspection.

## Known Issues

- Test file uses raw module-level imports of `app.api.routes.admin._generate_vapid_keypair` from inside the seed helper. Working but the underscore-prefixed import is technically reaching past a private boundary; if admin.py ever splits the keypair generator into a separate module the test imports must follow.
- The PUSH_DEFAULTS map duplicates the seven NotificationKind values; future kinds added to the enum without updating PUSH_DEFAULTS will silently default to False (the .get(kind, False) fallback). The DEFAULTS map for in_app has the same pattern — accepted convention.
- Pre-existing logger-disable issue (MEM359) still affects caplog after migration tests run in the same session; mitigated locally with the autouse `_reenable_loggers` fixture in test_push_dispatch.py.

## Files Created/Modified

- `backend/app/core/push_dispatch.py`
- `backend/app/core/notify.py`
- `backend/tests/api/routes/test_push_dispatch.py`
- `backend/tests/api/routes/test_notifications.py`
