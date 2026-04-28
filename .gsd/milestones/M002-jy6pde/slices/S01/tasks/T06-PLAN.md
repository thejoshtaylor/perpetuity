---
estimated_steps: 1
estimated_files: 5
skills_used: []
---

# T06: End-to-end integration: echo hello + orchestrator-restart proof + log redaction sweep

Final integrated acceptance test that stitches every prior task together against the real compose stack. Create `backend/tests/integration/test_m002_s01_e2e.py` (new directory `backend/tests/integration/` if absent) — runs against the running compose stack, NOT TestClient. Test flow: (1) `pytest` fixture spins up `docker compose up -d db redis orchestrator backend` (or the suite assumes the user ran it; document both paths in module docstring — auto-mode ASSUMPTION: fixture handles startup). (2) Use `httpx` to register a fresh user (POST /api/v1/auth/signup), log in (cookies returned), `POST /api/v1/sessions` for their personal team → got `session_id`. (3) Open WS to `wss://localhost:.../api/v1/ws/terminal/{sid}` with cookies (use `httpx_ws`). (4) Receive `{type:'attach',scrollback:''}` (or shell-prompt content). (5) Send `{type:'input', bytes: base64('echo hello\n')}`. (6) Read frames until a `{type:'data'}` decoded payload contains the substring `hello`; assert this happens within 10s. (7) Capture the current shell PID by sending `echo $$\n` and parsing the response data frames; record as `pid_before`. (8) Close the WS. (9) Run `docker compose restart orchestrator` from the test (subprocess); wait for orchestrator healthcheck to flip green (poll `docker compose ps` until orchestrator is healthy or 30s timeout). (10) Open a NEW WS to the SAME `session_id` with same cookies. (11) Receive `{type:'attach', scrollback: <s>}`; assert decoded `s` contains `hello` (proves the prior shell output survived the orchestrator restart). (12) Send `echo $$\n`; assert the response data contains `pid_before` (proves it's the same shell process — the strict bar from the success criteria). (13) Send `echo world\n`; assert response contains `world`. (14) `DELETE /api/v1/sessions/{sid}` → 200. ALSO add a log-redaction sweep: capture orchestrator + backend logs from steps (1)-(13) via `docker compose logs orchestrator backend > /tmp/m002_s01.log`; assert the seeded user's email and full_name do NOT appear in the log file (regression guard for the UUID-only logging discipline). The test MUST be opt-in via `pytest -m e2e` and skipped if `DOCKER_HOST` or the daemon is not reachable so it never breaks unit-only runs. Add `e2e` marker to `backend/pyproject.toml` and document the run command in `orchestrator/README.md` (created in this task — minimal). ASSUMPTION (auto-mode): test publishes backend on a deterministic host port (8000 or via override) for httpx to reach — uses the default compose configuration; if port 8000 is taken (per MEM046) the test reads `BACKEND_TEST_URL` env and falls back to `http://localhost:8000`.

## Inputs

- ``backend/app/api/routes/sessions.py``
- ``backend/app/api/main.py``
- ``backend/app/api/deps.py``
- ``orchestrator/orchestrator/main.py``
- ``orchestrator/orchestrator/routes_ws.py``
- ``docker-compose.yml``
- ``backend/tests/conftest.py``

## Expected Output

- ``backend/tests/integration/__init__.py``
- ``backend/tests/integration/conftest.py``
- ``backend/tests/integration/test_m002_s01_e2e.py``
- ``backend/pyproject.toml` (modified — adds e2e marker)`
- ``orchestrator/README.md``

## Verification

Run `cd backend && uv run pytest -m e2e tests/integration/test_m002_s01_e2e.py -v` against a live compose stack — must pass. Specifically the orchestrator-restart subtest (steps 9–12) must demonstrate `pid_before == pid_after` AND `'hello' in scrollback_after_restart`. The log redaction subtest (step 14 of test, separate from session DELETE) must report zero matches for the seeded email/full_name in the captured log file. Suite total wall-clock should be ≤ 60s per the milestone success criteria (image is `perpetuity/workspace:test`; container reuse across subtests not required since this is one user). If any single sub-assertion fails, the test reports which step failed (use `assert ..., f'step N: ...'` everywhere). Smoke check: `docker compose logs orchestrator | grep -E 'image_pull_ok|session_created|session_attached'` shows all three INFO lines after a successful run.

## Observability Impact

This task does not introduce new log keys but VERIFIES the taxonomy from T01–T05. The log-redaction sweep is the canonical regression guard for the M002 'UUIDs only in logs' invariant — every future M002 slice's task plan should reference this sweep. Failure modes captured: if orchestrator never reaches healthy after restart, test fails with explicit `step 9: orchestrator did not become healthy within 30s`; if `pid_before != pid_after`, test fails with `step 12: shell PID changed across orchestrator restart — tmux durability broken`. Negative test (already implicit): test 6 of T05 verifies non-owner WS rejection — not re-tested here since it's contract-level coverage in T05. Load profile: single-user, ~10 frames; nothing stressful — the proof is qualitative (does it survive a restart?) not quantitative.
