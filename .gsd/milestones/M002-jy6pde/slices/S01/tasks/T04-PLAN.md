---
estimated_steps: 1
estimated_files: 4
skills_used: []
---

# T04: Orchestrator WS bridge: bidirectional bytes between WS and tmux exec

Implement the orchestrator-side WS endpoint that streams bytes between an authenticated client and a running tmux session. In `orchestrator/orchestrator/routes_ws.py`: `WS /v1/sessions/{session_id}/stream` — auth via shared secret (T02 query-param pattern). On connect: (1) read Redis session record; if missing → close 1008 reason=`session_not_found`. (2) Send `{type:'attach', scrollback: <100KB-capped>}` JSON frame to client. (3) Open a `docker exec` stream to `tmux attach-session -t <session_id>` with stdin+stdout streams (`aiodocker.containers.Container.exec` with `stdin=True, tty=True`); spawn two asyncio tasks — one pumps bytes from exec stdout → WS as `{type:'data', bytes:<base64-or-utf8?>}` frames (use base64 for binary safety; locked here for the lifetime of the protocol), one pumps WS client frames → exec stdin (only `{type:'input'}` and `{type:'resize'}`; resize calls the orchestrator resize logic from T03). (4) On exec stream close (shell exits) → send `{type:'exit', code:<n>}` and close WS 1000. (5) On WS client disconnect → cancel pumps but do NOT kill tmux session (the whole point of D012 — tmux stays alive). (6) Update Redis `last_activity` on every input frame received (this is the heartbeat S04's reaper depends on). Frame protocol locked here — JSON-encoded UTF-8, `data` and `input` payloads are base64-encoded raw bytes (handles binary, locale, escape sequences cleanly). Add a `protocol.py` module that exports the typed frame schemas (TypedDict or pydantic) — both backend and orchestrator import from it eventually, but for S01 the canonical home is `orchestrator/orchestrator/protocol.py` and the backend gets a copy in T05. ASSUMPTION (auto-mode): WS message size limit is 1 MB on the FastAPI side; 100 KB scrollback frames are well under. ASSUMPTION: base64-encoding `bytes` payloads adds ~33% overhead but is the only way to round-trip binary safely in JSON — accepted.

## Inputs

- ``orchestrator/orchestrator/sessions.py``
- ``orchestrator/orchestrator/routes_sessions.py``
- ``orchestrator/orchestrator/auth.py``
- ``orchestrator/orchestrator/redis_client.py``

## Expected Output

- ``orchestrator/orchestrator/routes_ws.py``
- ``orchestrator/orchestrator/protocol.py``
- ``orchestrator/orchestrator/main.py` (modified — registers ws route)`
- ``orchestrator/tests/integration/test_ws_bridge.py``

## Verification

Integration `test_ws_bridge.py` (real Docker + real Redis): (a) seed a session via `POST /v1/sessions`; open WS to `/v1/sessions/{sid}/stream?key=<correct>`; assert first frame is `{type:'attach', scrollback:''}` (or shell prompt). (b) Send `{type:'input', bytes: base64('echo hello\n')}`; await `{type:'data',...}` frames until decoded payload contains `hello`; assert it does within 5s. (c) Send `{type:'resize', cols:120, rows:40}`; await any data frame OR a 200ms quiet period; assert no error log. (d) Disconnect WS; reconnect; assert second `attach` frame's scrollback decodes to UTF-8 containing `hello` (proves tmux survived browser disconnect — precursor to the orchestrator-restart proof in T06). (e) Bad key → close(1008, reason='unauthorized'). (f) Non-existent session_id → close(1008, reason='session_not_found'). (g) Send `{type:'input', bytes: base64('exit\n')}` followed by enough time for shell exit; assert client receives `{type:'exit', code:<int>}` then close(1000).

## Observability Impact

INFO `session_attached session_id=<uuid> container_id=<uuid>` on WS open. INFO `session_detached session_id=<uuid> reason=client_close|exec_eof|orchestrator_shutdown`. ERROR `orchestrator_ws_unauthorized key_prefix=<4chars>...`. Failure modes: aiodocker exec stream raises mid-stream → log WARNING `docker_exec_stream_error session_id=<uuid> err=<class>`, close WS 1011. Load profile: small payloads typical (<1KB per frame); large bursts on `cat large_file` — pumps must `await` between chunks or they'll deadlock the event loop. Negative tests: malformed JSON frame → close 1003 (unsupported data); unknown frame `type` → ignored (log WARNING).
