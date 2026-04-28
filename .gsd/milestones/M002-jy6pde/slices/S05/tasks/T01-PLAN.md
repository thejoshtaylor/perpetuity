---
estimated_steps: 14
estimated_files: 1
skills_used: []
---

# T01: Add bundled M002 final acceptance e2e (durability + reaper + ownership + redaction)

Land the milestone-capstone e2e at `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py` that bundles every M002 headline guarantee into one ordered flow against the real compose stack (db + redis + orchestrator + workspace image, no mocks below the backend HTTP boundary). Strategy mirrors the proven S01/S04 sibling-backend approach (MEM117) plus the live-orchestrator-swap pattern (MEM149) used by S04 to dial REAPER_INTERVAL_SECONDS=1. Flow:

1. Boot prerequisites — sibling backend container via existing `backend_url` fixture; ephemeral orchestrator (REAPER_INTERVAL_SECONDS=1) via the S04 swap pattern (autouse fixture restoring compose orchestrator on teardown); autouse skip-guard probing `backend:latest` for the T03 scrollback route + the s05 alembic revision (MEM173/MEM186/MEM162); autouse `_wipe_idle_timeout_setting` cleanup before AND after (MEM161).
2. Sign up alice (RFC 2606 example.com per MEM131) + log in admin@example.com (already system_admin from compose's initial_data).
3. Admin PUT `/api/v1/admin/settings/idle_timeout_seconds` to 600 (prep window per MEM175 — keep generous so the WS/HTTP round-trips and orchestrator restart don't race the reaper on a 1-second tick).
4. Alice POST `/api/v1/sessions` once → sid_a. Open WS to `/api/v1/ws/terminal/{sid_a}` with explicit Cookie header (MEM133 — aconnect_ws does NOT inherit cookie jar). Send `echo hello\n`. Drain data frames, ANSI-strip per MEM132, assert `hello` lands in a `data` frame within 10s. Send `echo $$\n`, capture pid_before via the same standalone-digit-run regex used in S01.
5. `docker compose restart orchestrator`. **Critical** — when restarting compose's orchestrator while an ephemeral one is masquerading on the same DNS alias, the restart only restarts the compose service. Per MEM149/MEM188 the ephemeral orchestrator owns the `orchestrator` DNS alias for the duration of the test. Resolution: T01's restart subtest uses the EPHEMERAL orchestrator's container name with `docker restart <ephemeral_name>` (NOT `docker compose restart`) so the durability path is exercised against the same container the backend is already talking to. Wait for the ephemeral orchestrator's `/v1/health` to return `image_present=true` from inside the sibling backend (probe shape from S04's `_wait_for_orch_running`).
6. Reconnect WS to the SAME sid_a. Assert first frame is `attach` and decoded scrollback contains `hello` (proves tmux survived the orchestrator restart). Send `echo $$\n`, drain until pid_before substring matches (proves the shell PID is identical — D012/MEM092). Send `echo world\n`, drain until `world` lands.
7. Ownership / no-enumeration sub-test (independent of sid_a). Sign up bob (mid-test, fresh user). With bob's cookies open WS to `/api/v1/ws/terminal/{sid_a}` → expect close 1008 reason='session_not_owned'. With bob's cookies open WS to `/api/v1/ws/terminal/{never_existed_uuid}` → expect close 1008 reason='session_not_owned'. Capture the close-code AND close-reason for both and assert byte-equal. Capture the 404 body shape for the parallel DELETE assertion: `DELETE /api/v1/sessions/{sid_a}` as bob and `DELETE /api/v1/sessions/{never_existed_uuid}` as bob — both 404, body must match byte-for-byte.
8. DELETE sid_a as alice → 200. Assert `GET /api/v1/sessions?team_id=<alice_team>` returns []. Workspace container should still be alive immediately after DELETE because the reaper hasn't ticked since the last tmux session died.
9. Admin PUT `idle_timeout_seconds` down to 3 (per MEM175 two-phase pattern — dial it down right before the reap-wait sleep). Sleep `idle_timeout (3s) + reaper_interval (1s) + 2s buffer = 6s`. Poll-with-deadline up to +10s for `docker ps` (filtered on alice's user_id+team_id labels) to be empty AND `GET /api/v1/sessions` to return empty (matches the S04 reap-poll shape). Assert workspace_volume row still exists in Postgres (`SELECT id FROM workspace_volume WHERE user_id=...` returns the original UUID — D015/R006 invariant: volumes outlive containers).
10. Smoke-check observability: grep the captured ephemeral-orchestrator + sibling-backend logs for the M002 taxonomy keys (`image_pull_ok`, `session_created`, `session_attached`, `session_detached`, `attach_registered`, `attach_unregistered`, `reaper_started`, `reaper_tick`, `reaper_killed_session`, `reaper_reaped_container`, `idle_timeout_seconds_resolved`, `session_scrollback_proxied`). Each must appear at least once.
11. Milestone-wide redaction sweep: capture `docker logs <ephemeral_orchestrator> + <sibling_backend>` (NOT `docker compose logs` — the ephemeral orchestrator isn't compose-managed, and `docker compose logs orchestrator` would hit the restored compose orchestrator after teardown; capture happens BEFORE teardown). Assert zero substring matches for `alice_email`, `alice_full_name`, `bob_email`, `bob_full_name` in the combined blob.

Do NOT introduce any new helpers in `backend/tests/integration/conftest.py` unless reused by T02. Self-contained helpers (`_b64enc`, `_b64dec`, `_strip_ansi`, `_drain_data`, `_input_frame`, `_signup_login`, `_login_only`, `_personal_team_id`, `_create_session_raw`, `_delete_session`, `_list_session_ids`, `_psql_one`, `_user_id_from_db`, `_capture_compose_logs`, `_read_dotenv_value`, `_ensure_host_workspaces_shared`, `_boot_ephemeral_orchestrator`, `_wait_for_orch_running`, `_restore_compose_orchestrator`) are copy-paste from `test_m002_s04_e2e.py` — they are intentionally module-local to keep slice e2es independently runnable, per the established M002 e2e pattern. Use the exact same printf-substitution sentinel approach (MEM142/MEM150) where the test waits for shell output: `printf 'EN%sOK_%s\n' D <token>` so the literal `ENDOK_<token>` only appears once shell stdout flushes, not in the input echo.

Duration target: ≤120s wall-clock on a warm compose stack (ephemeral orchestrator boot ~5s, restart subtest ~5s, reap wait ~6s, plus HTTP/WS round-trips). Budget allows generous slack for cold boot.

## Inputs

- ``backend/tests/integration/test_m002_s04_e2e.py` — copy module-local helpers (boot ephemeral orchestrator, autouse fixtures, printf sentinel pattern, log capture)`
- ``backend/tests/integration/test_m002_s01_e2e.py` — copy `_strip_ansi`, `_drain_data`, `_compose_restart`, `_wait_orch_healthy` shapes; T01 adapts the restart subtest to target the ephemeral orchestrator container directly`
- ``backend/tests/integration/conftest.py` — uses existing `backend_url`, `compose_stack_up`, `_e2e_env_check` fixtures unchanged`
- ``orchestrator/orchestrator/auth.py` — read-only reference to confirm 1008 close shape on auth fail (no code changes)`
- ``backend/app/api/routes/sessions.py` — read-only reference to confirm cross-user WS close shape and 404 body shape`
- ``orchestrator/orchestrator/reaper.py` — read-only reference to confirm log key names asserted in step 10`
- ``backend/app/api/routes/admin.py` — read-only reference to confirm idle_timeout_seconds validator + PUT shape (S04/T02)`

## Expected Output

- ``backend/tests/integration/test_m002_s05_full_acceptance_e2e.py` — new file containing the bundled acceptance test, module-local helpers, and three autouse fixtures (skip-guard for backend image, skip-guard for s05 alembic, idle_timeout_seconds wipe before/after)`

## Verification

cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s05_full_acceptance_e2e.py -v

## Observability Impact

Asserts existing M002 observability taxonomy fires during the bundled run (no new log keys). Captures ephemeral-orchestrator + sibling-backend logs via `docker logs` BEFORE teardown so the redaction sweep has the right blob to grep. Step-numbered assertion messages make any failure self-diagnosing without a debugger attach.
