---
id: T03
parent: S04
milestone: M002-jy6pde
key_files:
  - backend/app/api/routes/sessions.py
  - backend/tests/api/routes/test_sessions.py
key_decisions:
  - Public verb is GET (read in the public API surface) even though the orchestrator endpoint stays POST per S01's locked frame plan — modelling the public verb as POST would force the FE into POST-for-read. The internal POST→backend GET asymmetry is documented in the route docstring.
  - Response returns the orchestrator's raw UTF-8 scrollback string, NOT base64. Only the WS attach frame base64-encodes scrollback per the locked frame protocol (MEM097). Re-encoding here would create two contracts for one piece of data.
  - Added a schema-drift safety net (missing/non-string scrollback key → 503) so future orchestrator changes surface as a known 503 rather than a 500 KeyError trace. Costs nothing on the happy path.
  - Built a per-test `_FakeAsyncClient` pattern instead of pulling in respx — keeps the dev dependency surface unchanged and is reusable for any future route that proxies the orchestrator. Captured as MEM172.
duration: 
verification_result: passed
completed_at: 2026-04-25T12:55:39.206Z
blocker_discovered: false
---

# T03: Add backend GET /api/v1/sessions/{session_id}/scrollback proxy with two-step ownership lookup, no-enumeration 404, 503 fallthrough, and schema-drift safety net

**Add backend GET /api/v1/sessions/{session_id}/scrollback proxy with two-step ownership lookup, no-enumeration 404, 503 fallthrough, and schema-drift safety net**

## What Happened

Implemented `GET /api/v1/sessions/{session_id}/scrollback` in `backend/app/api/routes/sessions.py` as a thin proxy in front of the orchestrator's existing `POST /v1/sessions/{sid}/scrollback`. The route reuses `_orch_get_session_record` for ownership, then issues an empty-body POST to the orchestrator with `_ORCH_TIMEOUT` (30s/3s connect) and `_orch_headers()`. Response shape is `{session_id: str, scrollback: str}` — the raw UTF-8 string from the orchestrator, NOT base64 (only the WS attach frame base64-encodes scrollback per the locked S01 frame protocol).

The error matrix mirrors DELETE per MEM113/MEM123: missing record OR record owned by another user both return identical 404 `{"detail": "Session not found"}` so the caller cannot enumerate session existence. Orchestrator unreachable on either lookup or fetch surfaces as 503 `{"detail": "orchestrator_unavailable"}` via `_orch_unavailable_503`. Added a schema-drift safety net: if the orchestrator's POST scrollback response is not a dict, lacks the `scrollback` key, or returns a non-string value, we raise 503 instead of crashing with KeyError — guards against future orchestrator-side schema changes surfacing as a 500 to the user.

Observability: emits `INFO session_scrollback_proxied session_id=<uuid> user_id=<uuid> bytes=<n>` on success. Byte length is the only data-leak surface — the scrollback content is NEVER logged (could be echoed secrets). UUIDs only per MEM134. Reuses the existing WARNING `orchestrator_unavailable url=<base> detail=<str>` line on the 503 path; no new error keys.

Test infrastructure: the plan referenced a "monkeypatch_orch style fixture pattern from the S01 test file" but no such fixture exists in `backend/tests/api/routes/test_sessions.py` (S01's tests boot a real orchestrator container). Built a `_FakeAsyncClient` + `_install_fake_orch` helper in the same file that monkeypatches `app.api.routes.sessions.httpx.AsyncClient` so each test scripts orchestrator responses for the lookup and fetch independently. Captured this as MEM172 for downstream reuse.

Added 9 unit tests (covers all 7 plan must-haves plus schema-drift safety and observability):
  1. owner GET → 200 with the orchestrator-returned scrollback
  2. owner GET when scrollback is empty string → 200 with empty
  3. non-owner GET → 404 with `Session not found`
  4. missing-session GET (orch returns 404) byte-equal to non-owner GET response
  5. unauthenticated GET → 401
  6. orchestrator unreachable on lookup → 503
  7. orchestrator unreachable on scrollback fetch → 503
  8. orchestrator response missing `scrollback` key → 503 (schema-drift safety net)
  9. log line includes `bytes=<n>` but never the scrollback content (sensitive-content rule)

Note on prelude verification failures: the gate's first failure (`docker compose exec orchestrator pytest tests/integration/test_reaper.py`) is T02 territory — it requires `docker compose build orchestrator && up -d --force-recreate` per MEM116/MEM126; the running compose container is a stale image. The second failure (`pytest tests/api/routes/test_admin_settings.py` exit 4) was a path mismatch — the file lives at `backend/tests/api/routes/test_admin_settings.py` and the gate ran from repo root. Neither touches T03 code paths or files. The T03 verification commands in the plan (`backend && pytest tests/api/routes/test_sessions.py -v && ruff check ...`) both pass cleanly.

## Verification

Ran the task plan's verification commands from `backend/`:

  1. `SKIP_INTEGRATION=1 POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_sessions.py -v` → 9 passed (the new T03 unit tests), 11 skipped (S01 integration tests that need docker — expected when SKIP_INTEGRATION=1). Without SKIP_INTEGRATION the integration suite would still run; the unit tests are docker-free by design (per the plan: "no real orchestrator needed at the unit-test layer — the integration check is in T04").
  2. `uv run ruff check app/api/routes/sessions.py tests/api/routes/test_sessions.py` → All checks passed (after one round-trip to drop a forward-reference quote on `_FakeAsyncClient`).

The 9 new tests cover every error path defined by the plan: ownership 200, empty 200, non-owner 404, missing 404 byte-equal-to-non-owner, unauth 401, lookup-503, fetch-503, schema-drift-503, and the observability rule (logs bytes but never scrollback content). The byte-equal assertion in test (4) is the no-enumeration proof — both 404 paths produce identical `r.content`.

Slice-level verification: this is an intermediate task in S04; the slice's full end-to-end check is gated to T04 which exercises the route against a live orchestrator inside the compose stack.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && SKIP_INTEGRATION=1 POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_sessions.py -v` | 0 | ✅ pass | 590ms |
| 2 | `cd backend && uv run ruff check app/api/routes/sessions.py tests/api/routes/test_sessions.py` | 0 | ✅ pass | 200ms |

## Deviations

The task plan referenced a `monkeypatch_orch` style fixture pattern from the S01 test file, but no such fixture exists — S01's `test_sessions.py` boots a real orchestrator container for every test. Followed the spirit of the plan (unit-layer tests that do not require a real orchestrator) by introducing a small `_FakeAsyncClient` + `_install_fake_orch` helper in the same file. The integration check remains scoped to T04 per the plan.

## Known Issues

None for T03 itself. The two prelude verification commands the gate ran are out-of-scope for this task: the reaper integration test (T02 territory) needs `docker compose build orchestrator && up -d --force-recreate orchestrator` to refresh the stale image per MEM116/MEM126, and the admin_settings pytest invocation needs to be run from `backend/` (the test file is at `backend/tests/api/routes/test_admin_settings.py`, not the repo root). T03 source paths are untouched by both.

## Files Created/Modified

- `backend/app/api/routes/sessions.py`
- `backend/tests/api/routes/test_sessions.py`
