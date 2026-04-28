---
estimated_steps: 1
estimated_files: 6
skills_used: []
---

# T05: Backend session router + WS bridge proxy + ownership enforcement

Wire the public-facing API. New file `backend/app/api/routes/sessions.py` exposes: (1) `POST /api/v1/sessions` body `{team_id}` (cookie-authed via `CurrentUser`) — validates that the caller is a member of `team_id` (use the existing `_assert_caller_is_team_member` from `teams.py` — refactor to a shared helper if needed); generates a fresh `session_id = uuid4()`; calls orchestrator `POST /v1/sessions` with shared secret (env `ORCHESTRATOR_API_KEY`, base URL `ORCHESTRATOR_BASE_URL=http://orchestrator:8001`); on orchestrator success returns `{session_id, team_id, created_at}`. (2) `GET /api/v1/sessions` — calls orchestrator `GET /v1/sessions?user_id=<caller>&team_id=<implied or query>`; returns sessions belonging to the caller. (3) `DELETE /api/v1/sessions/{session_id}` — verifies ownership by reading orchestrator session record (orchestrator `GET` includes user_id); if `record.user_id != caller.id` close 404 with the same shape as not-found (no existence enumeration). Then call orchestrator `DELETE`. (4) WS `/api/v1/ws/terminal/{session_id}` (cookie-authed via `get_current_user_ws` — reuses M001 pattern; MUST close-before-accept on auth fail per MEM081). On accept: ownership check — fetch orchestrator session record; if missing OR `record.user_id != user.id` close 1008 reason=`session_not_owned` (identical close for both — prevents existence enumeration per CONTEXT error-handling). Then open client WS to orchestrator `WS /v1/sessions/{sid}/stream?key=<ORCHESTRATOR_API_KEY>` and proxy frames in both directions verbatim — backend does NOT decode/re-encode payloads; it forwards JSON text frames as-is. Orchestrator close → backend close with mapped code. Orchestrator unreachable on connect → close 1011 reason=`orchestrator_unavailable`. Add `ORCHESTRATOR_API_KEY` and `ORCHESTRATOR_BASE_URL` settings to `backend/app/core/config.py` (required at boot in non-test envs; tests can override). Register the new router in `backend/app/api/main.py`. Copy `orchestrator/orchestrator/protocol.py` into `backend/app/api/ws_protocol.py` for type sharing — short-term duplication is fine; a later milestone can extract a shared package. ASSUMPTION (auto-mode): backend uses `httpx_ws` for the orchestrator WS client (added to `backend/pyproject.toml`).

## Inputs

- ``backend/app/api/routes/ws.py``
- ``backend/app/api/routes/teams.py``
- ``backend/app/api/deps.py``
- ``backend/app/core/config.py``
- ``orchestrator/orchestrator/protocol.py``
- ``orchestrator/orchestrator/routes_sessions.py``
- ``orchestrator/orchestrator/routes_ws.py``

## Expected Output

- ``backend/app/api/routes/sessions.py``
- ``backend/app/api/main.py` (modified — registers sessions router)`
- ``backend/app/api/ws_protocol.py``
- ``backend/app/core/config.py` (modified — adds ORCHESTRATOR_* settings)`
- ``backend/pyproject.toml` (modified — adds httpx_ws dependency)`
- ``backend/tests/api/routes/test_sessions.py``

## Verification

Integration `backend/tests/api/routes/test_sessions.py` (uses TestClient + the real orchestrator container — conftest adds an orchestrator fixture analogous to the existing real-Postgres pattern): (a) signed-in user A `POST /api/v1/sessions` with team_id of their personal team → 200 returns session_id; orchestrator `GET` shows the session. (b) Cookie missing → 401. (c) Cookie valid but team_id is a team A is not a member of → 403. (d) `GET /api/v1/sessions` returns A's session. (e) WS `/api/v1/ws/terminal/<sid>` no cookie → close(1008, reason='missing_cookie'). (f) WS valid cookie + valid sid (own session) → first frame is `{type:'attach',...}`; round-trip `echo hi` → frame contains `hi`. (g) WS user B's cookie attaching to user A's sid → close(1008, reason='session_not_owned'). (h) WS attaching to a never-existed sid → close(1008, reason='session_not_owned') — IDENTICAL close to (g) (no enumeration). (i) `DELETE /api/v1/sessions/{sid}` as owner → 200; subsequent WS attach → 1008 session_not_owned (because record gone — same close as ownership violation, satisfying the no-enumeration rule). (j) Stop the orchestrator container mid-test then `POST /api/v1/sessions` → 503; restart, retry → 200. WS attach with orchestrator down → close(1011, reason='orchestrator_unavailable').

## Observability Impact

Backend INFO `session_proxy_open user_id=<uuid> session_id=<uuid>` on WS attach success. Backend INFO `session_proxy_close session_id=<uuid> reason=client|orch|exit code=<int>`. Backend WARNING `orchestrator_unavailable url=<base>` on connect failure. Failure modes: orchestrator WS rejects with 1008 → propagate same close code/reason to client; orchestrator HTTP returns 503 → backend returns 503; ownership-check returns 404 from orchestrator (record gone) → backend treats as 1008 session_not_owned (no enumeration). Negative tests covered in (b),(c),(e),(g),(h),(j) above. Logs: NEVER emit `current_user.email` or `full_name` — only `current_user.id` UUID. The existing `ws_auth_reject reason=...` taxonomy from M001 is preserved unchanged.
