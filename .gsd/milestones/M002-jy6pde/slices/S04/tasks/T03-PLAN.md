---
estimated_steps: 11
estimated_files: 2
skills_used: []
---

# T03: Add backend public GET /api/v1/sessions/{session_id}/scrollback proxy with no-enumeration ownership check

Add `GET /api/v1/sessions/{session_id}/scrollback` to `backend/app/api/routes/sessions.py`. Proxies the orchestrator's existing `POST /v1/sessions/{sid}/scrollback` endpoint (note the orchestrator side stays POST per S01 plan; the backend exposes GET because it is a read in the public API surface — modelling the public verb as POST would force the FE into a POST-for-read. Internal POST→backend GET asymmetry is acceptable; the orchestrator endpoint stays unchanged.).

Response shape: `{session_id: str, scrollback: str}` where `scrollback` is the same UTF-8 string the orchestrator returns (NOT base64 — the orchestrator's `POST /v1/sessions/{sid}/scrollback` returns `{scrollback: <str>}` directly per S01 routes_sessions.py; the WS attach frame is the only place that base64-encodes scrollback per the locked frame protocol).

Ownership: reuses `_orch_get_session_record(session_id)` from sessions.py — same no-enumeration rule as DELETE: missing record OR record.user_id != current_user.id → 404 with `{detail: 'Session not found'}`. The two cases must be indistinguishable to the caller (MEM113/MEM123). Orchestrator unreachable → 503 via `_orch_unavailable_503`.

Observability: INFO `session_scrollback_proxied session_id=<uuid> user_id=<uuid> bytes=<n>` on success. UUIDs only (MEM134). Do NOT log the scrollback content itself (could be sensitive, e.g. echoed secrets) — only the byte length.

Must-haves:
  - The route is registered on the existing `router` in `backend/app/api/routes/sessions.py` so it inherits the `/api/v1/sessions` prefix from `main.py::api_router.include_router`.
  - Orchestrator timeout 30s, connect 3s — reuse `_ORCH_TIMEOUT`.
  - The orchestrator-side endpoint is POST /v1/sessions/{sid}/scrollback (no body); call it with `httpx.AsyncClient.post(... )` and an empty JSON body or no body — match the orchestrator's existing signature exactly (it currently takes `session_id` as a path param and no body; tests in S01 call it with `c.post(url, headers=...)` with no json kwarg — replicate that).
  - Add unit/integration test cases in `backend/tests/api/routes/test_sessions.py` (extending the S01 file): owner GET 200 with the orchestrator-returned scrollback, owner GET when orchestrator returns empty string → 200 with empty, non-owner GET → 404, missing-session GET → 404 (same body as non-owner — assert byte-equal response body), unauthenticated GET → 401, orchestrator unreachable on lookup → 503, orchestrator unreachable on scrollback fetch → 503.
  - These tests use the existing `monkeypatch_orch` style fixture pattern from the S01 test file (no real orchestrator needed at the unit-test layer — the integration check is in T04).

Also add a tiny safety-net assert in this route: if the orchestrator's POST scrollback response is missing the `scrollback` key, treat as orchestrator anomaly → 503 `orchestrator_unavailable` rather than crash with KeyError. This guards against future orchestrator-side schema drift surfacing as a 500 to the user.

## Inputs

- ``backend/app/api/routes/sessions.py``
- ``backend/app/api/team_access.py``
- ``backend/tests/api/routes/test_sessions.py``
- ``orchestrator/orchestrator/routes_sessions.py``

## Expected Output

- ``backend/app/api/routes/sessions.py``
- ``backend/tests/api/routes/test_sessions.py``

## Verification

cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_sessions.py -v && cd backend && uv run ruff check app/api/routes/sessions.py tests/api/routes/test_sessions.py

## Observability Impact

Adds INFO `session_scrollback_proxied session_id=<uuid> user_id=<uuid> bytes=<n>` on success and reuses the existing WARNING `orchestrator_unavailable url=<base> detail=<str>` on the 503 path (no new error key required). The byte length is the only data leak surface; the scrollback content is never logged. UUID-only per MEM134.
