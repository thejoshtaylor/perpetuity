# S04: Idle reaper + multi-session per container + sessions REST surface + scrollback endpoint — UAT

**Milestone:** M002-jy6pde
**Written:** 2026-04-25T13:29:48.301Z

# S04 UAT — Idle reaper + multi-session per container + sessions REST surface + scrollback endpoint

## Scope

Validates that S04's deliverables behave correctly end-to-end against the live compose stack (real Postgres + Redis + orchestrator + Docker daemon). The slice's automated demo-truth proof is `backend/tests/integration/test_m002_s04_e2e.py::test_m002_s04_full_demo` — this UAT is the human-runnable equivalent for an operator stepping through the same demo.

## Preconditions

- Compose stack up and healthy: `docker compose ps` shows `db healthy`, `redis healthy`, `orchestrator healthy`.
- Backend image is freshly built with the T03 scrollback route + T02 admin validator: `docker compose build backend` (the stale-image autouse skip-guard catches this in the automated test; humans should run the build explicitly).
- Orchestrator image is freshly built with the T01 AttachMap + T02 reaper: `docker compose build orchestrator && docker compose up -d --force-recreate orchestrator`.
- Reaper interval set to 1s for fast feedback. The compose default is 30s; for the UAT, swap to 1s using the S02 live-orchestrator-swap pattern (or set `REAPER_INTERVAL_SECONDS=1` in a one-off `docker run` against the network alias `orchestrator`).
- A seeded admin account (admin@example.com / changethis or per `.env`) exists with role=system_admin.
- The `system_settings` table is wiped of any pre-existing `idle_timeout_seconds` row (compose's `app-db-data` volume persists across runs — MEM161): `docker compose exec db psql -U postgres -d app -c "DELETE FROM system_settings WHERE key='idle_timeout_seconds';"`.

## Test Cases

### TC01 — Admin sets a long idle_timeout_seconds up-front so prep doesn't race the reaper

**Steps:**
1. Log in as admin@example.com via `POST /api/v1/login/access-token` (or the cookie-auth login).
2. `PUT /api/v1/admin/settings/idle_timeout_seconds` with body `{"value": 600}`.

**Expected:** 200 with response body containing the new value (600), no `warnings` (idle_timeout_seconds is not a partial-apply key), and `previous_value_present=false` in the backend log if this is a fresh DB.

**Edge cases:**
- Same PUT a second time → 200 with `previous_value_present=true` in the log.
- PUT with `{"value": true}` → 422 `invalid_value_for_key` (bool rejected explicitly).
- PUT with `{"value": 0}` or `{"value": 86401}` → 422 `invalid_value_for_key` (out of range).
- PUT with `{"value": "120"}` → 422 `invalid_value_for_key` (string rejected, must be int).
- Boundary: `{"value": 86400}` → 200; `{"value": 1}` → 200.
- PUT to an unknown key (e.g. `idle_timeout_secondz`) → 422 `unknown_setting_key` (typo-proof).
- PUT as a non-admin user → 403; PUT without a session cookie → 401.

### TC02 — Multi-session-per-container filesystem sharing (R008)

**Steps:**
1. Sign up `alice@example.com` (any password; M001 endpoints).
2. As alice, `POST /api/v1/sessions` twice (no body, or with `{}` — orchestrator infers personal team from membership). Capture `sid_a` and `sid_b` from the responses.
3. WS-attach to sid_a at `/api/v1/ws/terminal/{sid_a}` with the session cookie. Wait for the `attach` frame.
4. Send a client `input` frame with `bytes=base64("echo 'a' > /workspaces/<alice_team_id>/marker.txt && cat /workspaces/<alice_team_id>/marker.txt\n")`. Wait for a `data` frame containing `'a'` (ANSI-strip first).
5. Close the sid_a WS.
6. WS-attach to sid_b at `/api/v1/ws/terminal/{sid_b}`. Wait for `attach`.
7. Send `input` frame with `bytes=base64("ls /workspaces/<alice_team_id>/ && cat /workspaces/<alice_team_id>/marker.txt\n")`. Wait for `data` frame.

**Expected:** sid_a and sid_b are distinct UUIDs. The orchestrator response for the second POST has `created==False` (same container reused — MEM120). The sid_b data frame contains `marker.txt` in the `ls` output AND `'a'` in the `cat` output — proves R008 multi-tmux/single-container filesystem sharing.

**Edge cases:**
- `docker ps --filter label=user_id=<alice_uuid> --filter label=team_id=<alice_team_uuid>` returns exactly 1 container row (single container, two tmux sessions inside).
- `docker compose exec orchestrator-1 sh -c 'docker exec <container_id> tmux ls'` would return 2 sessions, named after sid_a and sid_b.
- Orchestrator log contains two distinct `attach_registered count=1` lines (one per session_id) and two corresponding `attach_unregistered count=0` lines after the WS closes.

### TC03 — GET /api/v1/sessions returns both live sessions

**Steps:**
1. Continuing from TC02 (sid_a and sid_b live in alice's personal team), call `GET /api/v1/sessions?team_id=<alice_team_id>` as alice.

**Expected:** 200 with `data: [{...}, {...}]` containing two records, set of ids equal to `{sid_a, sid_b}`, both with alice's user_id.

**Edge cases:**
- `?team_id=` is required — without it, the response is 503 (pre-existing backend bug per MEM174). Document this constraint in the UAT result.
- Calling as a different user (e.g. bob) returns the bob's sessions, not alice's — no cross-user leak.

### TC04 — GET /api/v1/sessions/{sid}/scrollback (T03 happy path)

**Steps:**
1. As alice, `GET /api/v1/sessions/{sid_a}/scrollback`.

**Expected:** 200 with body `{"session_id": "<sid_a>", "scrollback": "<utf-8 string>"}` where the scrollback contains the marker echo from TC02. Backend log emits `session_scrollback_proxied session_id=<sid_a> user_id=<alice_uuid> bytes=<n>` (UUIDs only, byte length only — no scrollback content in the log).

**Edge cases:**
- GET without auth → 401.
- GET with a never-existed UUID (e.g. `00000000-0000-0000-0000-000000000000`) → 404 `{"detail": "Session not found"}`.
- Stop the orchestrator (`docker compose stop orchestrator`) and retry → 503 `{"detail": "orchestrator_unavailable"}`. Restart orchestrator before continuing.

### TC05 — No-enumeration ownership: bob cannot tell whether sid_a exists

**Steps:**
1. Sign up `bob@example.com`.
2. As bob, `GET /api/v1/sessions/{sid_a}/scrollback`.
3. As bob, `GET /api/v1/sessions/00000000-0000-0000-0000-000000000000/scrollback`.

**Expected:** Both return 404 with `r.content` byte-equal to `{"detail":"Session not found"}`. Bob cannot distinguish "exists but not yours" from "does not exist" (MEM113/MEM123).

### TC06 — DELETE one session leaves the sibling and the container alive

**Steps:**
1. As alice, `DELETE /api/v1/sessions/{sid_a}`.
2. As alice, `GET /api/v1/sessions?team_id=<alice_team_id>`.
3. Run `docker ps --filter label=user_id=<alice_uuid> --filter label=team_id=<alice_team_id>`.

**Expected:** DELETE returns 200. GET returns exactly 1 session (sid_b). docker ps still shows the workspace container (only the tmux session for sid_a was killed; sid_b is still attached to the container).

**Edge cases:**
- A second DELETE for sid_a returns 404 (already gone, no-enumeration shape).
- DELETE for a sid that bob owns returns 404 to alice (no cross-user enumeration).

### TC07 — Reaper kills the surviving idle session and reaps the container

**Steps:**
1. As admin, `PUT /api/v1/admin/settings/idle_timeout_seconds` with `{"value": 3}` (two-phase PUT pattern — MEM175).
2. Wait 6 seconds (3s timeout + 1s reaper interval + 2s slack).
3. As alice, `GET /api/v1/sessions?team_id=<alice_team_id>`.
4. Run `docker ps --filter label=user_id=<alice_uuid> --filter label=team_id=<alice_team_id>`.
5. `docker compose logs orchestrator --since=10s | grep -E '^.*reaper_(killed_session|reaped_container)'`.

**Expected:** GET returns empty `data: []`. docker ps returns zero rows (container reaped via stop+delete). Logs show at least one `reaper_killed_session session_id=<sid_b> reason=idle_no_attach` line and one `reaper_reaped_container container_id=<short> user_id=<alice_uuid> team_id=<alice_team_id> reason=last_session_killed` line.

**Edge cases:**
- The `workspace_volume` Postgres row for alice's personal team is UNCHANGED — verify with `docker compose exec db psql -U postgres -d app -c "SELECT id, user_id, team_id, size_gb FROM workspace_volume WHERE user_id='<alice_uuid>';"`. Row still present.
- The underlying `.img` file on the host is UNCHANGED — verify with `ls -la /var/lib/perpetuity/vols/` (the file with alice's volume_id UUID is still there).

### TC08 — Re-provisioning after reap remounts the existing volume

**Steps:**
1. As alice, `POST /api/v1/sessions` (any team_id from her membership). Capture `sid_c`.
2. WS-attach to sid_c at `/api/v1/ws/terminal/{sid_c}`.
3. Send `input` frame with `bytes=base64("cat /workspaces/<alice_team_id>/marker.txt\n")`. Wait for `data` frame.
4. Run `docker ps --filter label=user_id=<alice_uuid> --filter label=team_id=<alice_team_id>`.

**Expected:** sid_c is a NEW UUID. The data frame contains `'a'` (ANSI-strip first) — proves the workspace_volume row + .img persisted across the reap, and the new container remounted the existing volume (D015 invariant). docker ps now shows exactly 1 container row again (a fresh container with the same labels but a different container_id).

### TC09 — Log redaction sweep (MEM134)

**Steps:**
1. `docker compose logs orchestrator --since=<test_start_iso> | grep -E '(alice@example\\.com|bob@example\\.com|alice|bob)' | wc -l`.
2. `docker compose logs backend --since=<test_start_iso> | grep -E '(alice@example\\.com|bob@example\\.com|alice|bob)' | wc -l`.

**Expected:** zero lines from each grep across all reaper/attach/scrollback log lines that this UAT exercised. Use the actual signed-up users' email addresses and full_names for the grep — they MUST NOT appear anywhere in the orchestrator or backend logs after S04's new code paths fire.

### TC10 — Cleanup

**Steps:**
1. `docker compose exec db psql -U postgres -d app -c "DELETE FROM system_settings WHERE key='idle_timeout_seconds';"` (per MEM161).
2. Restore the compose orchestrator if you swapped it for `REAPER_INTERVAL_SECONDS=1`: `docker compose up -d --force-recreate orchestrator`.

**Expected:** `system_settings` table no longer has the `idle_timeout_seconds` row. The compose-default orchestrator is running again.

## Sign-off

- All ten test cases pass.
- Edge-case assertions hold (no-enumeration, validator gates, log redaction, volume persistence).
- The automated `test_m002_s04_full_demo` is the canonical regression bar — re-run via `cd backend && POSTGRES_PORT=5432 REAPER_INTERVAL_SECONDS=1 uv run pytest -m e2e tests/integration/test_m002_s04_e2e.py -v` (~19 s).
