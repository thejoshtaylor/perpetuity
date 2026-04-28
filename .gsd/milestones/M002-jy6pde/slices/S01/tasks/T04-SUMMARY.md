---
id: T04
parent: S01
milestone: M002-jy6pde
key_files:
  - orchestrator/orchestrator/protocol.py
  - orchestrator/orchestrator/routes_ws.py
  - orchestrator/orchestrator/main.py
  - orchestrator/tests/integration/test_ws_bridge.py
key_decisions:
  - Locked the WS frame protocol at the codified shape from MEM097 in `orchestrator/orchestrator/protocol.py` — TypedDicts + close-code constants + b64 helpers — as the canonical home; T05 imports a copy. Locking now means downstream slices cannot widen/narrow frame fields without explicit migration.
  - Used `?key=<secret>` query-param auth on the WS hop (matches T02's documented strategy) rather than Sec-WebSocket-Protocol subprotocol, because the second leg (FastAPI ↔ orchestrator) is wired with `websockets`/`httpx_ws` where query strings attach trivially. Documented as switchable in auth.py.
  - Heartbeat Redis `last_activity` on every `input` frame, not on every `data` frame — a passive viewer (no input) shouldn't keep a session alive; the reaper's idle metric is genuine user interactivity. Redis blips during heartbeat are logged WARNING but non-fatal: the next input retries.
  - Pump architecture races two coroutines with `asyncio.wait(FIRST_COMPLETED)` and cancels the loser, rather than complex state machine. The race outcome (exec_eof / exec_error / client_close) drives the close code/reason, with a single point of teardown. This is simpler than per-pump cleanup and safer against double-close.
  - On WS client disconnect, the orchestrator closes only the exec stream — never the tmux session. This is D012/MEM092's entire purpose: tmux owns the pty so the shell survives every orchestrator-side disconnect. The exit-frame path is reserved for actual shell exit (exec EOF).
duration: 
verification_result: passed
completed_at: 2026-04-25T09:32:47.971Z
blocker_discovered: false
---

# T04: Wire orchestrator WS bridge: cookie-less, shared-secret-authed bidirectional bytes between WebSocket and tmux exec stream with locked JSON+base64 frame protocol

**Wire orchestrator WS bridge: cookie-less, shared-secret-authed bidirectional bytes between WebSocket and tmux exec stream with locked JSON+base64 frame protocol**

## What Happened

T04 ships the orchestrator-side WS endpoint that streams bytes between an authenticated WS client and a running tmux session inside the (user, team) workspace container — the live byte pipe S01's whole architecture is built around. Three new files, one wiring edit, and a 9-test integration suite.

`orchestrator/orchestrator/protocol.py` is the **canonical home** of the WS frame schemas (MEM097): TypedDicts for every server frame (`attach`, `data`, `exit`, `detach`, `error`) and client frame (`input`, `resize`), plus close-code constants and reason strings. Byte payloads (`scrollback`, `data.bytes`, `input.bytes`) are base64-encoded over JSON UTF-8 — the only way to round-trip arbitrary binary, ANSI escapes, and chunk-split multi-byte chars cleanly. The ~33% size overhead is documented and accepted. Frame shape is locked at end of S01 per MEM097; T05 (backend bridge) will get a copy of this module.

`orchestrator/orchestrator/routes_ws.py` is the WS endpoint itself: `WS /v1/sessions/{session_id}/stream?key=<secret>`. Lifecycle: (1) `authenticate_websocket()` close-before-accept on bad key (1008 'unauthorized'), (2) Redis lookup → close 1008 'session_not_found' if missing, (3) `capture_scrollback` → send `attach` frame (≤ 100 KB hard cap from T03), (4) open `tmux attach-session -t <sid>` exec with stdin+stdout+stderr+tty=True, (5) race two pumps via `asyncio.wait(FIRST_COMPLETED)` — exec→WS pumps `read_out()` chunks as `data` frames; WS→exec routes `input` (decode b64, `write_in`, heartbeat Redis `last_activity`) and `resize` (call T03's `resize_tmux_session`), ignoring unknown frame types for forward-compat, (6) on exec EOF inspect ExitCode and send `exit` frame + close 1000, (7) on client disconnect cancel pumps but **never** kill the tmux session (D012/MEM092 — the entire point of putting tmux between docker exec and the shell). Failure-mode handling per the task plan: malformed JSON → 1003, exec stream error → log WARNING `docker_exec_stream_error` + close 1011, Redis blip on heartbeat is non-fatal (logged, retried next frame).

`main.py` gets a one-line wire-in: `app.include_router(ws_router)`. The shared-secret HTTP middleware already skips WS upgrades (T02 carried that branch), so the `?key=` flow doesn't double-authenticate.

The aiodocker exec API is well-suited but has sharp edges captured in MEM110: `start(detach=False)` returns a Stream object **not** an awaitable, so it must be entered via `await stream.__aenter__()` to do the HTTP-upgrade dance; with tty=True stdout/stderr merge on stream 1; the upgraded TCP socket needs explicit `__aexit__` cleanup. The pumps both use `WebSocketState.CONNECTED` checks before send to avoid RuntimeError on close-after-close, and a `_safe_close` helper centralizes the duplicate-close guard.

Integration suite at `orchestrator/tests/integration/test_ws_bridge.py` boots a fresh ephemeral orchestrator (same fixture pattern as the T03 suite) for each test against the live compose Redis, drives the WS via the modern `websockets.asyncio.client` API, and covers all 7 verification cases from the task plan plus the two negative cases from Q7 — 9 tests total. Each test seeds a session through the T03 HTTP API first, then exercises the WS frame protocol end-to-end. The disconnect+reconnect test specifically proves tmux survives the WS close (the orchestrator-restart variant lives in T06).

One gotcha worth surfacing (captured as MEM111): the compose `.env` REDIS_PASSWORD is `changethis` not `changeme`. The T03 test uses `changeme` as fallback and apparently passes only when REDIS_PASSWORD is set in the env at invocation. T04's fixture parses `<repo>/.env` to discover the actual password and falls back to `changethis`, fixing the latent issue.

Slice observability: every required INFO line ships — `session_attached session_id=<uuid> container_id=<12chars>` on WS open, `session_detached session_id=<uuid> container_id=<12chars> reason=<...> exit_code=<n>` on close. WARNINGs cover orphaned tmux, exec stream errors, malformed frames, unknown frame types, and Redis heartbeat blips. Every identifier in the log line is a UUID — never email or full_name. The observability_log_lines test asserts both INFO lines appear and that the seeded user UUID is present.

ASSUMPTIONS made (per task plan auto-mode latitude): WS message size limit is FastAPI's default ~1 MB, well above the 100 KB scrollback cap. Base64 overhead of ~33% on `data` payloads is accepted as the cost of clean binary round-trip.

## Verification

All 9 integration tests in `orchestrator/tests/integration/test_ws_bridge.py` pass against the live compose Redis + Docker stack (12.56s wall-clock). Each test boots an isolated ephemeral orchestrator container so Redis state is owned by the test:

(a) test_attach_frame_then_echo_roundtrip — first frame is `attach`; sending `echo hello\n` yields a `data` frame whose b64-decoded payload contains `hello` within 5s.
(b) test_resize_frame_does_not_error — `{type:resize,cols:120,rows:40}` accepted; subsequent `input` still round-trips.
(c) test_disconnect_reconnect_preserves_scrollback — disconnect mid-session, reconnect to SAME sid, second `attach` frame's scrollback decodes to UTF-8 containing the prior `hello-d-test`. Proves tmux survived WS close.
(d) test_bad_key_closes_1008_unauthorized — connect with `?key=not-the-key` → 401/403 on the upgrade (close-before-accept).
(e) test_unknown_session_id_closes_1008 — bogus session_id → ConnectionClosed code=1008 reason='session_not_found'.
(f) test_shell_exit_emits_exit_frame_and_closes_1000 — `input: exit\n` → server emits `{type:exit,code:int}` then closes 1000.
(g) test_malformed_json_closes_1003 — `not-json{{{` → close 1003 reason='malformed_frame'.
(h) test_unknown_frame_type_is_ignored — `{type:telepathy,...}` is logged and ignored; subsequent `input` still works.
(i) test_observability_log_lines — both `session_attached` and `session_detached` INFO lines appear in container logs, with the user UUID present.

All 11 pre-existing unit tests (`tests/unit/`) still pass — no regressions. New modules import cleanly.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `uv run pytest tests/integration/test_ws_bridge.py -v` | 0 | ✅ pass (9/9) | 12560ms |
| 2 | `uv run pytest tests/unit -v` | 0 | ✅ pass (11/11 — no regressions) | 270ms |
| 3 | `docker build -t orchestrator:latest -f orchestrator/Dockerfile .` | 0 | ✅ pass (image rebuilt with protocol.py + routes_ws.py) | 1500ms |
| 4 | `python -c 'import orchestrator.protocol, orchestrator.routes_ws'` | 0 | ✅ pass (modules import cleanly) | 200ms |

## Deviations

Added a small helper `_env_redis_password()` to the integration test to read REDIS_PASSWORD from `<repo>/.env` rather than hardcode `changeme`. The pre-existing T03 test fixture defaults to `changeme` (the example placeholder, but compose actually uses `changethis`) which would 503 against the live stack. T04 picks the real value up automatically. Documented as MEM111. T03's fixture has the same latent issue but lies outside this task's scope.

## Known Issues

None. Two `DeprecationWarning`s from `websockets` 16.0 about `ConnectionClosed.code`/`reason` being deprecated in favor of `Protocol.close_code`/`close_reason` — non-blocking; will be fixed in a future test refactor.

## Files Created/Modified

- `orchestrator/orchestrator/protocol.py`
- `orchestrator/orchestrator/routes_ws.py`
- `orchestrator/orchestrator/main.py`
- `orchestrator/tests/integration/test_ws_bridge.py`
