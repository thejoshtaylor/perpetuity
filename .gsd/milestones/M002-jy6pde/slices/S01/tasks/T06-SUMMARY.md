---
id: T06
parent: S01
milestone: M002-jy6pde
key_files:
  - backend/tests/integration/__init__.py
  - backend/tests/integration/conftest.py
  - backend/tests/integration/test_m002_s01_e2e.py
  - backend/pyproject.toml
  - orchestrator/README.md
key_decisions:
  - E2E test reaches the backend by spawning a sibling `backend:latest` container on `perpetuity_default` with a published host port — NOT by using the compose `backend` service (which has no `ports:` block and would be untestable from the host). This isolates the test runner from the compose backend service and lets `docker compose restart orchestrator` exercise the durability path without disturbing the test's HTTP client.
  - The integration suite's conftest overrides the unit-suite's session-scoped autouse `db` and `client` fixtures with no-ops via pytest's name-based override resolution. Without the override, the unit harness connects to localhost:55432 (the host-side mapping per MEM021/MEM114) before the e2e test runs and fails on connection refused.
  - Test domain switched from `.local` to `example.com` (RFC 2606) because `email_validator` flags `.local`/`.localhost` as special-use TLDs and rejects them with a 422.
  - Cookie propagation to the WS upgrade uses an explicit `Cookie:` request header on `aconnect_ws` rather than installing cookies on the underlying `httpx.Client` — `aconnect_ws` doesn't inherit jar state from the surrounding stream context, so the explicit header is the only path that survives the upgrade.
  - ANSI escape stripping (`_strip_ansi`) is applied to all decoded data frames before substring assertions because the workspace image runs an interactive bash inside tmux — color codes and cursor moves bracket the literal `hello` we're hunting for. Strip CSI and OSC sequences only; leave plain UTF-8 alone.
duration: 
verification_result: passed
completed_at: 2026-04-25T10:07:37.248Z
blocker_discovered: false
---

# T06: Land M002/S01 e2e acceptance test proving echo round-trip, orchestrator-restart shell durability (same PID + scrollback), and UUID-only log redaction against the live compose stack

**Land M002/S01 e2e acceptance test proving echo round-trip, orchestrator-restart shell durability (same PID + scrollback), and UUID-only log redaction against the live compose stack**

## What Happened

Built the canonical end-to-end test for slice S01 in `backend/tests/integration/test_m002_s01_e2e.py`, gated by the `e2e` pytest marker (added to `backend/pyproject.toml`) so unit-only runs are unaffected. The test runs against the real compose stack — no TestClient, no aiodocker stubs — and exercises every prior task in the slice through the public surface.

Strategy for reaching the backend: the compose `backend` service has no published host port, so the test fixture (`backend_url` in `backend/tests/integration/conftest.py`) spawns a sibling `backend:latest` container on the `perpetuity_default` network with `-p <free_port>:8000`. The container runs the same `prestart.sh` (alembic + initial_data) and then `fastapi run`, with `POSTGRES_SERVER=db POSTGRES_PORT=5432 ORCHESTRATOR_BASE_URL=http://orchestrator:8001 REDIS_HOST=redis` so it reaches every supporting service over compose DNS. That topology is the key: `docker compose restart orchestrator` mid-test breaks the live WS upgrade path without touching the test backend, which is exactly what the durability proof needs.

The test flow mirrors the slice plan exactly: signup with `example.com` (RFC 2606 reserved-for-test domain — `email_validator` rejects `.local`/`.localhost`), login to capture the cookie jar, POST /api/v1/sessions on the user's personal team, attach via `httpx_ws.aconnect_ws` with the session cookie in a manual `Cookie:` header, observe `{type:"attach"}`, send `echo hello\n`, drain `data` frames until `hello` appears (ANSI-stripped because the workspace shell is interactive). Capture the shell PID with `echo $$`, close the WS, restart the orchestrator via `docker compose restart orchestrator`, poll `docker compose ps` until orchestrator is healthy (30s timeout, fails with explicit `step 9: orchestrator did not become healthy within 30s`), reattach to the SAME `session_id`, assert `hello` survives in the new attach frame's scrollback, assert `echo $$` returns the recorded `pid_before` (`step 12: shell PID changed across orchestrator restart — tmux durability broken`), echo `world` round-trip, then `DELETE /api/v1/sessions/<sid>`.

The log-redaction sweep captures `docker compose logs orchestrator backend` at the end of the run, writes it to `/tmp/m002_s01.log`, and asserts the seeded user's email and `full_name` appear ZERO times — the regression guard for the M002 "UUIDs only in logs" invariant. The test also smoke-checks that the three required INFO keys (`image_pull_ok`, `session_created`, `session_attached`) fired during the run.

The integration conftest overrides the unit-suite's session-scoped autouse `db` fixture (and `client`) with no-ops to prevent the unit harness from connecting to localhost:55432 and holding an AccessShareLock — both are wrong for tests that exercise a separate backend process over HTTP.

During execution I hit one real issue and one cosmetic one: (1) email_validator rejected the `.local` test domain — switched to `example.com`. (2) The running orchestrator container was an older build that did NOT have the `/v1/sessions/by-id/{sid}` endpoint T05 added — every WS upgrade rejected with 1008 because backend's `_orch_get_session_record` got a 404 even though the session record was clearly in Redis. Rebuilding the orchestrator image and force-recreating the container fixed it. Captured this gotcha as MEM116 because it will bite again on every M002 slice that adds orchestrator routes. Captured the ephemeral-backend-on-compose-network pattern as MEM117 and the integration-fixture-override convention as MEM118.

Final test pass: `1 passed in 19.25s` — well under the slice plan's 60s wall-clock budget. Smoke check: `docker compose logs orchestrator | grep -E 'image_pull_ok|session_created|session_attached'` shows all three INFO lines after a green run.

## Verification

Ran `cd backend && uv run pytest -m e2e tests/integration/test_m002_s01_e2e.py -v` against a live compose stack (`db`, `redis`, `orchestrator` healthy). The test passed in 19.25s, exercising all 14 numbered steps from the task plan including the orchestrator-restart shell-durability subtest (steps 9–12: pid_before == pid_after AND `'hello' in scrollback_after_restart`) and the log-redaction sweep (step 14: zero matches for the seeded email/full_name in `docker compose logs orchestrator backend`). Also verified: (a) `pytest -m "not e2e" tests/integration/` deselects the test (1 deselected, 0 errors); (b) `SKIP_INTEGRATION=1 pytest -m e2e ...` skips cleanly (1 skipped); (c) `docker compose logs orchestrator | grep -E 'image_pull_ok|session_created|session_attached'` shows all three observability INFO keys.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && uv run pytest -m e2e tests/integration/test_m002_s01_e2e.py -v` | 0 | ✅ pass | 19250ms |
| 2 | `uv run pytest -m "not e2e" tests/integration/` | 5 | ✅ pass (1 deselected, 0 errors) | 200ms |
| 3 | `SKIP_INTEGRATION=1 uv run pytest -m e2e tests/integration/test_m002_s01_e2e.py -v` | 0 | ✅ pass (1 skipped) | 680ms |
| 4 | `docker compose logs orchestrator | grep -E 'image_pull_ok|session_created|session_attached'` | 0 | ✅ pass (all 3 INFO keys present) | 250ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `backend/tests/integration/__init__.py`
- `backend/tests/integration/conftest.py`
- `backend/tests/integration/test_m002_s01_e2e.py`
- `backend/pyproject.toml`
- `orchestrator/README.md`
