# S05: Operational hardening + final integrated acceptance + two-key rotation — UAT

**Milestone:** M002-jy6pde
**Written:** 2026-04-25T14:01:49.334Z

# S05 UAT — Operational hardening + final integrated acceptance + two-key rotation

## Preconditions

- Docker compose stack up: `db` (Postgres 18, port 5432), `redis:7-alpine`, `orchestrator` (orchestrator:latest, port 8001 exposed only on the compose network).
- `backend:latest` image built with the s05 alembic revision applied.
- `perpetuity/workspace:latest` and `perpetuity/workspace:test` images present locally.
- POSTGRES_PORT=5432 exported (or prefix invocation).
- Working directory: `backend/`.
- Auto-mode reaper interval: orchestrator default. Tests dial REAPER_INTERVAL_SECONDS=1 via the ephemeral-orchestrator swap.

## Test Case 1 — Bundled M002 acceptance e2e (T01)

**Goal:** Prove every M002 headline guarantee in one ordered flow against the real compose stack.

### Steps

1. From `backend/`, run:
   ```
   POSTGRES_PORT=5432 uv run pytest -m e2e \
     tests/integration/test_m002_s05_full_acceptance_e2e.py -v
   ```
   **Expected:** Test collects 1 item, runs `test_m002_s05_full_acceptance`, exits 0 within ≤120s.

2. The test internally executes the following sub-steps; each carries a step-numbered assertion message:
   - **Setup:** sibling backend booted; ephemeral orchestrator (REAPER_INTERVAL_SECONDS=1) swapped onto the `orchestrator` DNS alias; autouse `_wipe_idle_timeout_setting` runs before AND after.
   - **Step A:** Sign up alice@example.com (RFC 2606); admin@example.com logs in (system_admin from compose initial_data).
   - **Step B:** Admin PUT `/api/v1/admin/settings/idle_timeout_seconds` = 600 (prep window).
   - **Step C:** Alice POST `/api/v1/sessions` → 200 with `sid_a`; open WS to `/api/v1/ws/terminal/{sid_a}` with explicit Cookie header (aconnect_ws does NOT inherit cookie jar). Send `echo hello\n`. **Expected:** `hello` lands in a `data` frame within 10s after ANSI strip. Capture `pid_before` via `echo $$\n`.
   - **Step D:** `docker restart <ephemeral_orchestrator_container_name>` (NOT `docker compose restart` — the ephemeral one owns the DNS alias). Probe ephemeral `/v1/health` from inside the sibling backend until `image_present=true`.
   - **Step E:** Reconnect WS to SAME `sid_a`. **Expected:** First frame is `attach`, scrollback contains `hello` (tmux survived restart). Send `echo $$\n` → drain until `pid_before` substring matches (D012/MEM092 — shell PID stable). Send `echo world\n` → `world` lands in data frame.
   - **Step F:** Sign up bob mid-test. With bob's cookies WS to `/api/v1/ws/terminal/{sid_a}` → close 1008 reason='session_not_owned'. With bob's cookies WS to `/api/v1/ws/terminal/{never_existed_uuid}` → close 1008 reason='session_not_owned'. **Expected:** close-code AND close-reason byte-equal across the two cases. DELETE `/api/v1/sessions/{sid_a}` as bob → 404; DELETE `/api/v1/sessions/{never_existed_uuid}` as bob → 404. **Expected:** 404 body byte-equal.
   - **Step G:** DELETE `sid_a` as alice → 200. `GET /api/v1/sessions?team_id=<alice_team>` → `[]`. Workspace container still alive (reaper hasn't ticked since last tmux died).
   - **Step H:** Admin PUT `idle_timeout_seconds` = 3 (two-phase pattern, MEM175). Sleep 6s (idle_timeout 3s + reaper_interval 1s + 2s buffer). Poll-with-deadline up to +10s for `docker ps` (filtered on alice's user_id+team_id labels) empty AND `GET /api/v1/sessions` empty.
   - **Step I:** Assert `SELECT id FROM workspace_volume WHERE user_id=<alice_uid>` returns the original UUID (D015/R006 — volumes outlive containers).
   - **Step J:** Grep captured `docker logs <ephemeral_orchestrator>` + `<sibling_backend>` for the M002 taxonomy keys: `image_pull_ok`, `session_created`, `session_attached`, `session_detached`, `attach_registered`, `attach_unregistered`, `reaper_started`, `reaper_tick`, `reaper_killed_session`, `reaper_reaped_container`, `idle_timeout_seconds_resolved`, `session_scrollback_proxied`. **Expected:** each appears at least once.
   - **Step K:** Milestone-wide redaction sweep across the captured combined log blob. **Expected:** zero substring matches for `alice_email`, `alice_full_name`, `bob_email`, `bob_full_name`.

3. Teardown (always runs via `request.addfinalizer`): `_restore_compose_orchestrator` brings compose's orchestrator back; alice's container reaped if still alive.

### Expected Result

PASSED in ≤120s wall-clock. If any sub-step fails, the step-numbered assertion message identifies exactly which guarantee broke (e.g. "step 8: shell PID changed across orchestrator restart — tmux durability broken").

## Test Case 2 — Two-key rotation e2e (T02)

**Goal:** Prove an orchestrator booted with both ORCHESTRATOR_API_KEY and ORCHESTRATOR_API_KEY_PREVIOUS accepts requests signed with EITHER key on both HTTP and WS paths.

### Steps

1. From `backend/`, run:
   ```
   POSTGRES_PORT=5432 uv run pytest -m e2e \
     tests/integration/test_m002_s05_two_key_rotation_e2e.py -v
   ```
   **Expected:** Test collects 1 item, runs `test_m002_s05_two_key_rotation`, exits 0 within ≤120s.

2. Internal sub-steps:
   - **Setup:** Generate two distinct random keys via `secrets.token_urlsafe(32)` — `key_current` and `key_previous`. Stop compose orchestrator (`docker compose rm -sf orchestrator`). Boot ephemeral orchestrator with BOTH keys set, on `--network perpetuity_default --network-alias orchestrator`. Probe `/v1/health` from inside the ephemeral container itself via `docker exec ... python3 -c "import urllib.request; ..."` (MEM198) until `image_present=true`.
   - **Step A:** Boot `backend_current` on a fresh host port with `ORCHESTRATOR_API_KEY=key_current`, `ORCHESTRATOR_API_KEY_PREVIOUS=`. Wait for `/api/v1/utils/health-check/` 200.
   - **Step B:** Boot `backend_previous` on a fresh host port with `ORCHESTRATOR_API_KEY=key_previous`, `ORCHESTRATOR_API_KEY_PREVIOUS=`. Wait for health 200.
   - **Step C:** Sign up alice on `backend_current`. Sign up bob on `backend_previous` (different user — proves the test isn't reusing one backend twice).
   - **Step D:** Alice POST `/api/v1/sessions` via `backend_current` → **Expected: 200**, returns `sid_a`. (Backend → orchestrator with `X-Orchestrator-Key: key_current`; `_key_matches` accepts active key.) DELETE `sid_a` to clean up.
   - **Step E:** Bob POST `/api/v1/sessions` via `backend_previous` → **Expected: 200**, returns `sid_b`. (Backend → orchestrator with `X-Orchestrator-Key: key_previous`; `_key_matches` accepts previous key from candidates list.) DELETE `sid_b`.
   - **Step F (WS path):** Alice POST again on `backend_current` → `sid_a2`. Open WS to `ws://localhost:<port_current>/api/v1/ws/terminal/{sid_a2}` with alice's cookies; backend's WS-bridge proxies with `?key=key_current`. **Expected: `attach` frame received.** DELETE `sid_a2`.
   - **Step G (WS path):** Bob POST again on `backend_previous` → `sid_b2`. Open WS to `ws://localhost:<port_previous>/api/v1/ws/terminal/{sid_b2}` with bob's cookies; backend proxies with `?key=key_previous`. **Expected: `attach` frame received.** DELETE `sid_b2`.
   - **Step H (negative):** Boot a third sibling backend `backend_wrong` with a third random key. Alice POST `/api/v1/sessions` via `backend_wrong` → **Expected: 503** (orchestrator returns 401 with `orchestrator_http_unauthorized` log line carrying `key_prefix=<first 4 chars>...`; backend surfaces orchestrator_unavailable). The exact body shape is read from `routes/sessions.py` rather than hardcoded.
   - **Step I:** Capture `docker logs` for ephemeral_orchestrator + all three sibling backends. Assert no email or full_name leaks.

3. Teardown via `request.addfinalizer`: `docker rm -f` all three sibling backends + ephemeral orchestrator; `docker compose up -d orchestrator` to restore; reap any lingering alice/bob workspace containers.

### Expected Result

PASSED in ≤120s wall-clock. If `key_previous` is rejected on either HTTP or WS path, the rotation contract is broken.

## Test Case 3 — Combined run

**Goal:** Confirm both tests run together cleanly with no cross-test interference.

### Steps

1. From `backend/`, run:
   ```
   POSTGRES_PORT=5432 uv run pytest -m e2e \
     tests/integration/test_m002_s05_full_acceptance_e2e.py \
     tests/integration/test_m002_s05_two_key_rotation_e2e.py -v
   ```

### Expected Result

`2 passed` in ≤180s combined wall-clock (observed: 46s on a warm compose stack). Both ephemeral-orchestrator swaps tear down cleanly via their fixture finalizers; the compose orchestrator is restored after each.

## Edge Cases / Negative Tests

- **Skip-guard fires on missing alembic revision:** if `backend:latest` doesn't have the s05 revision, both tests skip with instructions to run `docker compose build backend`.
- **Skip-guard fires on missing scrollback route (T01 only):** if `backend:latest` doesn't expose `GET /api/v1/sessions/{sid}/scrollback`, T01 skips per MEM173/MEM186.
- **Cookie-jar omission on WS:** the explicit `Cookie:` header on aconnect_ws is mandatory (MEM133) — omitting it would return a server close 4401 unauthenticated, not 1008. Both tests pass cookies explicitly.
- **`docker compose restart orchestrator` instead of `docker restart <ephemeral>` in T01:** would restart a service that isn't masquerading on the DNS alias and the test would falsely pass without exercising the durability path. T01 uses the ephemeral container name explicitly (MEM196).
- **Reaper race in T01:** idle_timeout starts at 600s precisely so the WS round-trips and orchestrator restart can't race the 1s reaper tick. Only after step G is the value lowered to 3s for the reap-wait sleep.

## Pass Criteria

- All three test cases above produce `PASSED`.
- Combined wall-clock ≤180s.
- Zero email/full_name leaks in any captured log blob.
- All M002 observability taxonomy keys fire during T01.
- No new code paths landed in `backend/app/` or `orchestrator/orchestrator/` (verification-only slice).
