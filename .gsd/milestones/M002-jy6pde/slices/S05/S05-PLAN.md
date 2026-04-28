# S05: Operational hardening + final integrated acceptance + two-key rotation

**Goal:** Prove M002 is operationally complete by bundling the four headline guarantees into integrated e2e tests against the real compose stack: (a) signup → POST session → WS echo → orchestrator restart → reconnect same session_id with stable shell PID and prior scrollback → echo on the same shell → DELETE → wait idle_timeout_seconds → assert container reaped via docker ps; (b) two-key rotation — orchestrator booted with both ORCHESTRATOR_API_KEY and ORCHESTRATOR_API_KEY_PREVIOUS set, two backends sending different keys both succeed; (c) ownership + no-enumeration — user B WS to user A's session_id and to a never-existed session_id both close 1008/session_not_owned with byte-identical close shape; (d) milestone-wide log redaction sweep — zero email/full_name leaks across all M002 surfaces in the captured run.
**Demo:** Acceptance test: signup → POST /api/v1/sessions → WS attach → `echo hello` → restart orchestrator → reconnect same session_id → observe `hello` in scrollback → `echo world` in the same shell (`echo $$` PID stable) → DELETE the session → wait idle_timeout_seconds → assert container reaped via `docker ps`. Two-key rotation test: orchestrator with both keys set, two requests with different keys, both succeed. Ownership test: user B WS to user A's session_id and to a never-existed session_id both close 1008/session_not_owned identically. Regression test: log scan finds zero email/name leaks across all M002 log lines.

## Must-Haves

- ## Success Criteria
- T01's bundled acceptance e2e (`backend/tests/integration/test_m002_s05_full_acceptance_e2e.py`) passes against the real compose stack, covering signup → session → restart → durability → reaper-reap → ownership/no-enumeration → log redaction in one ordered flow.
- T02's two-key rotation e2e (`backend/tests/integration/test_m002_s05_two_key_rotation_e2e.py`) proves the orchestrator (booted with both ORCHESTRATOR_API_KEY and ORCHESTRATOR_API_KEY_PREVIOUS set) accepts HTTP and WS calls signed with EITHER key — same endpoint, same surface, two distinct sibling-backend containers each carrying one of the two keys.
- Both tests run with `cd backend && uv run pytest -m e2e tests/integration/test_m002_s05_*.py -v` and complete in ≤180 s combined wall-clock on a warm compose stack.
- Combined log-redaction sweep across both tests confirms zero matches for any seeded email or full_name in `docker compose logs` for backend + orchestrator.
- All M002 observability taxonomy keys (`session_created`, `session_attached`, `session_detached`, `image_pull_ok`, `container_provisioned`, `attach_registered`, `attach_unregistered`, `reaper_started`, `reaper_killed_session`, `reaper_reaped_container`) fire at least once during the bundled acceptance run.
- ## Threat Surface
- **Abuse**: cross-user WS attach (user B → user A's session_id) and missing-session WS attach must close 1008 session_not_owned with byte-identical shape — any divergence is an existence-enumeration leak. Tests assert byte-equal close-code/reason across the two cases.
- **Data exposure**: structured logs (backend + orchestrator) must never contain user emails, full_names, or team slugs across the entire M002 surface. The milestone-wide redaction sweep is the regression net.
- **Input trust**: shared-secret keys arrive on every backend↔orchestrator hop. Two-key rotation must accept both candidates with constant-time compare; this slice does not modify the auth code path but proves the rotation acceptance contract end-to-end.
- ## Requirement Impact
- **Requirements touched**: R005 (per-(user, team) container with dedicated mounted volume) — re-verified by the bundled acceptance test (signup → provision → durability → reap → re-provision implicitly through reaper). No requirements rescoped or invalidated.
- **Re-verify**: end-to-end durability (S01 contract), idle reaper (S04 contract), no-enumeration (S01 router contract), UUID-only logging (M002 milestone-wide observability discipline).
- **Decisions revisited**: D016 (two-key rotation) — promoted from "code path exists" to "rotation acceptance proven end-to-end with two backends carrying different keys". No decisions revised or invalidated.
- ## Proof Level
- This slice proves: final-assembly
- Real runtime required: yes
- Human/UAT required: no
- ## Observability / Diagnostics
- Runtime signals: existing M002 observability taxonomy (S01/S04 keys above) — this slice adds no new log keys; it asserts the taxonomy fired during the bundled run.
- Inspection surfaces: `docker compose logs orchestrator backend` (the e2e tests capture and grep this), `docker ps --filter label=user_id=<uuid> --filter label=team_id=<uuid>` for live containers, `SELECT * FROM workspace_volume` for volume persistence.
- Failure visibility: each step in the bundled e2e carries a step-numbered assertion message identifying exactly which guarantee broke (e.g. "step 8: shell PID changed across orchestrator restart — tmux durability broken"). Two-key rotation test surfaces 401 from orchestrator with `key_prefix=` log line if either key is rejected.
- Redaction constraints: emails, full_names, team slugs MUST NOT appear in any captured log. The milestone-wide sweep is the regression net.
- ## Integration Closure
- Upstream surfaces consumed: backend `POST/GET/DELETE /api/v1/sessions` + `WS /api/v1/ws/terminal/{sid}` + `GET /api/v1/sessions/{sid}/scrollback` + admin settings PUT (S01/S04); orchestrator two-key auth (S01/T02 `auth.py`); idle reaper (S04/T02); workspace_volume + system_settings (S02/S03).
- New wiring introduced in this slice: NONE — S05 is verification-only. No new code paths land in `backend/app/` or `orchestrator/orchestrator/`. Two new e2e test files plus any helpers they need under `backend/tests/integration/`.
- What remains before the milestone is truly usable end-to-end: NOTHING — once T01 and T02 pass, M002's success criteria from the roadmap are demonstrably met by automated tests.

## Proof Level

- This slice proves: final-assembly

## Integration Closure

Upstream: backend session/admin routes (S01/S03/S04), orchestrator auth.py two-key plumbing (S01/T02), idle reaper (S04/T02), workspace_volume + system_settings (S02/S03). New wiring: none — verification-only slice. Closes M002: nothing remains once both e2e tests are green.

## Verification

- No new observability keys introduced. The slice asserts the existing M002 taxonomy fires during the bundled acceptance run and that emails/full_names never appear in any captured log. Two-key rotation test surfaces existing `orchestrator_http_unauthorized` / `orchestrator_ws_unauthorized` log keys via the negative-case branch (wrong key returns 401/1008 with `key_prefix=<first 4 chars>...`).

## Tasks

- [x] **T01: Add bundled M002 final acceptance e2e (durability + reaper + ownership + redaction)** `est:3h`
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
  - Files: `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py`
  - Verify: cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s05_full_acceptance_e2e.py -v

- [x] **T02: Add two-key rotation e2e proving both ORCHESTRATOR_API_KEY and _PREVIOUS are accepted** `est:3h`
  Land `backend/tests/integration/test_m002_s05_two_key_rotation_e2e.py` proving the rotation acceptance contract end-to-end. The auth code path was already unit-tested in S01/T02, but no integration test runs an orchestrator with both keys set and proves that two distinct backends, each carrying a different key, both succeed against the same orchestrator endpoint.

Approach — reuse the live-orchestrator-swap pattern from S04 (MEM149) plus a custom sibling-backend boot. The sibling-backend `backend_url` fixture in `conftest.py` is hard-wired to the dotenv `ORCHESTRATOR_API_KEY` (line 301-302) and explicitly empties `ORCHESTRATOR_API_KEY_PREVIOUS` (line 324). T02 needs two backends with different keys — neither matches that fixture's shape. Solution: T02 ships its own `_boot_sibling_backend(api_key=...)` helper that takes the key as an argument and calls `docker run` with the matching env; same shape as the existing fixture, just parameterized by key.

Flow:
1. Autouse skip-guard: probe `backend:latest` for the s05 alembic revision presence (MEM162) and skip with `docker compose build backend` instructions on miss. Probe orchestrator image presence + workspace image presence (cheap re-checks beyond the conftest autouse) so the test gives a useful skip when the orchestrator hasn't been rebuilt for S05.
2. Generate two distinct random API keys (use `secrets.token_urlsafe(32)` — no need to read .env's value here; the test owns both halves of the secret). Call them `key_current` and `key_previous`.
3. Stop the compose orchestrator (`docker compose rm -sf orchestrator`) and boot an ephemeral orchestrator with BOTH `ORCHESTRATOR_API_KEY=key_current` AND `ORCHESTRATOR_API_KEY_PREVIOUS=key_previous` set, on `--network perpetuity_default --network-alias orchestrator`. Reuse the rest of the S04 swap shape (privileged, vol mounts, REDIS/DATABASE_URL). REAPER_INTERVAL_SECONDS can stay at the orchestrator default (no reaper interaction in this test).
4. Probe the ephemeral orchestrator from inside compose's `db` container (or a throwaway `docker run --network perpetuity_default --rm curlimages/curl curl -sf http://orchestrator:8001/v1/health`) until `image_present` is true. The probe path needs SOME container on the compose network; using the seeded compose db container as the probe host avoids spawning a throwaway curl image.
5. Boot TWO sibling backends on the same compose network — `backend_current` (env `ORCHESTRATOR_API_KEY=key_current`, `ORCHESTRATOR_API_KEY_PREVIOUS=`) and `backend_previous` (env `ORCHESTRATOR_API_KEY=key_previous`, `ORCHESTRATOR_API_KEY_PREVIOUS=`). Each gets its own host port and waits for `/api/v1/utils/health-check/` to respond 200. Critical: the compose `prestart` already ran during compose's initial bring-up (alembic migrations applied to the shared db), so the second backend's `bash scripts/prestart.sh` is a no-op upgrade — but we still run prestart for both to keep the boot shape identical to the existing fixture and avoid shape drift.
6. Sign up alice on `backend_current`. Sign up bob on `backend_previous` (different user — proves the test isn't accidentally reusing one backend twice). Use the standard signup helpers from S04.
7. Alice POST `/api/v1/sessions` via `backend_current` → 200 with sid_a. (HTTP path: backend_current → orchestrator with `X-Orchestrator-Key: key_current`. Orchestrator's `_key_matches` accepts because key_current is the active key.) Tear down sid_a with DELETE.
8. Bob POST `/api/v1/sessions` via `backend_previous` → 200 with sid_b. (HTTP path: backend_previous → orchestrator with `X-Orchestrator-Key: key_previous`. Orchestrator's `_key_matches` accepts because key_previous is in the candidates list.) Tear down sid_b with DELETE.
9. WS path proof — alice WS-attach to sid_a is no longer feasible since sid_a was deleted in step 7. Reprovision: alice POST again via `backend_current` → sid_a2. Open WS to `ws://localhost:<port_current>/api/v1/ws/terminal/{sid_a2}` with alice's cookies. The backend's WS-bridge code in `routes/sessions.py` proxies to orchestrator with `?key=key_current` query string. Assert `attach` frame received. Close. Same flow for bob's `sid_b2` against `backend_previous` (which proxies with `?key=key_previous`). Assert `attach` frame received. Close. Both DELETEs to clean up.
10. Negative case — boot a THIRD ephemeral sibling backend `backend_wrong` with `ORCHESTRATOR_API_KEY=<random_third_key>`. POST `/api/v1/sessions` as alice via `backend_wrong` → expect 503 with `{detail: "orchestrator_unavailable"}` or whatever shape the backend surfaces when orchestrator returns 401 (read `routes/sessions.py` to confirm the actual shape — do NOT hardcode the body shape; assert status == 503 OR the orchestrator-unauthorized branch the backend chose to surface). The orchestrator's `orchestrator_http_unauthorized` log line should appear in the ephemeral orchestrator's `docker logs` with `key_prefix=<first 4 chars>...`.
11. Log redaction sweep — same shape as T01 + S04. Capture `docker logs` for ephemeral_orchestrator + all three sibling backends; assert no email or full_name leaks.

Teardown (use `request.addfinalizer` so it runs even on assertion failure): `docker rm -f` all three sibling backends + the ephemeral orchestrator; `docker compose up -d orchestrator` to restore the compose orchestrator (use the existing `_restore_compose_orchestrator` pattern). Reap any workspace containers that ended up labelled to alice/bob. Same shape as S04's teardown.

Duration target: ≤120s wall-clock on warm compose. Boot of the ephemeral orchestrator + 3 sibling backends is the dominant cost (~30-45s); the actual auth assertions are ~5s of HTTP/WS round-trips.

Do NOT touch `backend/tests/integration/conftest.py` — keep T02's parameterized backend boot module-local. The conftest's `backend_url` fixture stays as-is for every other M002 e2e (S01/S02/S03/S04), which all use the dotenv ORCHESTRATOR_API_KEY value.
  - Files: `backend/tests/integration/test_m002_s05_two_key_rotation_e2e.py`
  - Verify: cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s05_two_key_rotation_e2e.py -v

## Files Likely Touched

- backend/tests/integration/test_m002_s05_full_acceptance_e2e.py
- backend/tests/integration/test_m002_s05_two_key_rotation_e2e.py
