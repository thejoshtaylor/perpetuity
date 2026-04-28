---
id: T05
parent: S01
milestone: M002-jy6pde
key_files:
  - backend/app/api/routes/sessions.py
  - backend/app/api/team_access.py
  - backend/app/api/ws_protocol.py
  - backend/app/api/routes/teams.py
  - backend/app/api/main.py
  - backend/app/core/config.py
  - backend/pyproject.toml
  - backend/tests/api/routes/test_sessions.py
  - orchestrator/orchestrator/routes_sessions.py
key_decisions:
  - Backend WS bridge is a verbatim text-frame proxy: backend never decodes/re-encodes JSON payloads, and the orchestrator's close code+reason are mirrored 1:1 onto the browser WS — keeps the locked frame protocol contract (MEM097) a single schema across all three legs.
  - Existence-enumeration prevention codified at the router layer: identical close shape (1008 'session_not_owned') for both 'session does not exist' and 'session exists but caller does not own it', and identical 404 with the same body for the same two cases on DELETE.
  - Lifted team-access guards to `app/api/team_access.py` rather than re-implementing them in sessions.py — membership is a security boundary, duplicating it would be a correctness hazard. teams.py re-exports the helpers under their original underscored names so prior call sites are untouched.
  - Added orchestrator endpoint `GET /v1/sessions/by-id/{session_id}` instead of forcing the backend to enumerate the list endpoint with (user_id, team_id) it doesn't know yet. Backend-driven need but a tiny non-controversial addition that keeps the orchestrator authoritative on storage shape and the backend authoritative on policy (D016).
  - Defense-in-depth on GET /sessions: backend re-filters the orchestrator's response by `user_id == caller.id` even though the orchestrator already filters server-side — a router bug should never be enough to leak another user's session.
duration: 
verification_result: untested
completed_at: 2026-04-25T09:52:18.565Z
blocker_discovered: false
---

# T05: Wire backend sessions router and cookie-authed WS bridge proxy with ownership enforcement and no-enumeration error shape

**Wire backend sessions router and cookie-authed WS bridge proxy with ownership enforcement and no-enumeration error shape**

## What Happened

T05 ships the public-facing M002 surface: three HTTP routes (`POST/GET/DELETE /api/v1/sessions`) plus the cookie-authed WS bridge `/api/v1/ws/terminal/{session_id}`. All four endpoints land in a new `backend/app/api/routes/sessions.py` registered on `api_router` in `backend/app/api/main.py`.

`POST /api/v1/sessions` validates `team_id` from the JSON body, gates on team membership via the lifted `assert_caller_is_team_member` helper, generates a fresh `session_id = uuid4()`, and forwards `{session_id, user_id, team_id}` to the orchestrator's `POST /v1/sessions` with an `X-Orchestrator-Key` header. Orchestrator HTTP/connect failures map to 503 with a stable `orchestrator_unavailable` log line.

`GET /api/v1/sessions` lists the caller's sessions by passing `user_id=<caller>` (and optional `team_id`) to the orchestrator's existing list endpoint. Defense-in-depth: even after the orchestrator filters server-side, the backend re-filters the response to strip any row whose `user_id` doesn't match the caller, so a router bug can never leak another user's session.

`DELETE /api/v1/sessions/{sid}` enforces ownership before forwarding. The same 404 body is returned whether the record is missing OR exists-but-not-owned — codified existence-enumeration prevention per the slice CONTEXT error-handling rule.

The WS bridge `/api/v1/ws/terminal/{session_id}` is the demo-critical leg of the user→browser→backend→orchestrator chain. Lifecycle: (1) cookie auth via `get_current_user_ws` (close-before-accept on auth fail per MEM081/MEM022); (2) ownership check via the new orchestrator endpoint `GET /v1/sessions/by-id/{sid}` — missing OR not owned → close `1008 session_not_owned` with identical close shape (no enumeration); (3) on accept, open `aconnect_ws(orch_url + ?key=<API_KEY>)` from `httpx_ws`, then race two coroutines that proxy text frames verbatim in both directions; (4) the orchestrator's close code+reason are mirrored 1:1 onto the browser WS so the locked frame protocol contract from `app.api.ws_protocol` (a verbatim copy of `orchestrator.protocol`, T05 plan) stays a single schema across all three legs. Orchestrator WS upgrade failure → close `1011 orchestrator_unavailable`.

Two supporting changes landed alongside the router:

1. `backend/app/api/team_access.py` lifts `_assert_caller_is_team_admin` and `_assert_caller_is_team_member` out of `routes/teams.py` so the new sessions router can import the membership guard without circular routes-to-routes dependency. `routes/teams.py` re-exports both names via aliased imports — every existing call site keeps working unchanged. (T05 plan: "refactor to a shared helper if needed".)

2. `orchestrator/orchestrator/routes_sessions.py` gains `GET /v1/sessions/by-id/{session_id}` returning the raw Redis record or 404. The pre-existing list endpoint required both `user_id` AND `team_id` query params, but the WS bridge needs an O(1) ownership-check lookup at a point where it knows neither — only the sid from the URL. Adding by-id keeps the orchestrator authoritative on storage and the backend authoritative on policy (D016).

Config: `ORCHESTRATOR_BASE_URL` (default `http://orchestrator:8001`) and `ORCHESTRATOR_API_KEY` (default `changethis` — same placeholder pattern as other secrets) added to `backend/app/core/config.py`. Compose already exports both env vars to the backend service (no compose change needed). Dependencies `httpx-ws<1.0,>=0.6.0` and `websockets<14.0,>=12.0` added to `backend/pyproject.toml`.

Observability per the slice taxonomy: INFO `session_proxy_open user_id=<uuid> session_id=<uuid>` on WS attach success, INFO `session_proxy_close session_id=<uuid> reason=<client|orch|exit> code=<int>` on WS teardown (always emitted via `try/finally`), WARNING `orchestrator_unavailable url=<base>` on connect failure. The `test_logs_emit_uuid_only_no_email_or_full_name` test seeds a user with a `FullNameSentinel-<random>` full_name and asserts the captured log records contain neither the email nor the sentinel — only the user_id UUID.

ASSUMPTION (auto-mode): backend uses `httpx_ws.aconnect_ws` for the orchestrator WS client (per the task plan's stated assumption) — installed cleanly via `uv sync`.

## Verification

All 11 integration tests in `backend/tests/api/routes/test_sessions.py` pass against the live compose stack (Postgres on 5432 + ephemeral orchestrator container on the `perpetuity_default` network with the live redis service):

(a) `test_a_create_session_for_personal_team_returns_200` — signed-up user A POSTs with their personal team_id → 200 returns `{session_id, team_id, created_at}`; orchestrator `GET /v1/sessions/by-id/<sid>` returns a record with `user_id == A.id`.
(b) `test_b_create_session_without_cookie_returns_401` — POST without cookie → 401.
(c) `test_c_create_session_for_other_team_returns_403` — user A tries to create a session for user B's personal team → 403.
(d) `test_d_list_sessions_returns_callers_session` — after create, GET /sessions returns the session and every row's user_id matches the caller.
(e) `test_e_ws_without_cookie_closes_1008_missing_cookie` — WS attach without cookie → close(1008, 'missing_cookie').
(f) `test_f_ws_with_own_session_attaches_and_round_trips` — attach yields `attach` frame first; sending `echo hi-t05\n` returns a `data` frame whose b64-decoded payload contains `hi-t05` within 10s.
(g) `test_g_ws_with_other_users_session_closes_1008_session_not_owned` — user B attaching to A's sid → close(1008, 'session_not_owned').
(h) `test_h_ws_for_never_existed_sid_closes_1008_session_not_owned` — bogus uuid → identical close to (g).
(i) `test_i_delete_then_ws_attach_closes_1008_session_not_owned` — DELETE owner then re-attach → 1008 (record gone, identical shape).
(j) `test_j_orchestrator_down_returns_503_and_1011` — `docker stop` the test orchestrator → POST 503, WS attach → close(1011, 'orchestrator_unavailable'); `docker start` then retry → 200.
(k) `test_logs_emit_uuid_only_no_email_or_full_name` — captured log records contain the user_id UUID and exclude email + full_name sentinel.

No regressions in the rest of the backend test suite (123 pre-existing tests still pass). Orchestrator unit tests still pass (11/11) and a smoke run of the orchestrator WS-bridge integration tests confirms my routes_sessions.py addition didn't break T04. Ruff lint clean on every touched file in both backend and orchestrator.</verification>
<parameter name="verificationEvidence">[{"command": "POSTGRES_PORT=5432 POSTGRES_SERVER=localhost uv run pytest backend/tests/api/routes/test_sessions.py -v", "exitCode": 0, "verdict": "✅ pass (11/11)", "durationMs": 19350}, {"command": "POSTGRES_PORT=5432 POSTGRES_SERVER=localhost uv run pytest backend/tests/api/ --ignore=backend/tests/api/routes/test_sessions.py -q", "exitCode": 0, "verdict": "✅ pass (123/123 — no regressions from team_access refactor)", "durationMs": 5370}, {"command": "uv run pytest orchestrator/tests/unit -q", "exitCode": 0, "verdict": "✅ pass (11/11)", "durationMs": 270}, {"command": "uv run pytest orchestrator/tests/integration/test_ws_bridge.py::test_unknown_session_id_closes_1008 orchestrator/tests/integration/test_ws_bridge.py::test_attach_frame_then_echo_roundtrip -q", "exitCode": 0, "verdict": "✅ pass (2/2 — T04 still works after by-id route added)", "durationMs": 2180}, {"command": "ruff check backend/app/api/routes/sessions.py backend/app/api/team_access.py backend/app/api/ws_protocol.py backend/app/api/routes/teams.py backend/app/api/main.py backend/app/core/config.py backend/tests/api/routes/test_sessions.py orchestrator/orchestrator/routes_sessions.py", "exitCode": 0, "verdict": "✅ pass (all checks passed)", "durationMs": 200}, {"command": "docker build -t orchestrator:latest -f orchestrator/Dockerfile .", "exitCode": 0, "verdict": "✅ pass (rebuilt with by-id endpoint)", "durationMs": 1100}]

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| — | No verification commands discovered | — | — | — |

## Deviations

Added `GET /v1/sessions/by-id/{session_id}` to the orchestrator. The task plan said "fetch orchestrator session record" but the existing list endpoint requires both `user_id` AND `team_id` as required query params — the backend doesn't know team_id at the point it needs to enforce ownership (the WS path only has sid + caller cookie). The cleanest fix is the by-id lookup; alternative was to relax `team_id` to optional on the list endpoint, which would have changed the public orchestrator surface more than necessary. Documented in the route's docstring as a T05-driven addition.

## Known Issues

None.

## Files Created/Modified

- `backend/app/api/routes/sessions.py`
- `backend/app/api/team_access.py`
- `backend/app/api/ws_protocol.py`
- `backend/app/api/routes/teams.py`
- `backend/app/api/main.py`
- `backend/app/core/config.py`
- `backend/pyproject.toml`
- `backend/tests/api/routes/test_sessions.py`
- `orchestrator/orchestrator/routes_sessions.py`
