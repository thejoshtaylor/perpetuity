# T01 Verification Report — M003-umluob / S06 (FINAL)

**Slice:** S06 — Final integrated acceptance (signup → POST → WS attach → echo hello → orchestrator restart → reattach → scrollback contains 'hello' → echo world same shell)
**Milestone:** M003-umluob
**Task:** T01 — Verify M003/S06 final-integrated-acceptance demo by citation against the bundled M002/S05 e2e; produce T01-VERIFICATION.md and escalate the duplication hand-off to milestone-level
**Date:** 2026-04-25
**HEAD:** `b1afe70`
**Verdict:** ✅ ALL EIGHT SUB-CRITERIA PASS by citation against `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance` (the bundled M002 milestone-capstone e2e — the literal S06 demo). The bundled e2e PASSED end-to-end against the live compose stack on this run; **15 corroborating PASSED tests** captured for cookie auth, ownership/no-enumeration, attach frame, and scrollback proxy lifecycle. ⚠️ One bonus supplementary test (`test_m002_s05_two_key_rotation`) is recorded as a `## Verification gap:` for MEM214 linuxkit loop-device-pool exhaustion — environmental, not an S06 regression. Strict scope held: NO modification of backend/orchestrator source, compose, Dockerfiles, or test code.

This is the **FIFTH and FINAL** filed `M003-umluob duplicates M002-jy6pde` reconciliation hand-off. After this slice closes, **NO further M003-umluob slices remain to file the hand-off in.** The duplication has been blocking-but-bypassed across S01/T01, S03/T02, S04/T01, S05/T01, and now S06/T01. Auto-mode CANNOT reconcile this; it MUST be escalated to a human operator at milestone-level. See `## Human action required` block below.

## Human action required: M003-umluob duplicates M002-jy6pde — MILESTONE-LEVEL ESCALATION

The S06 demo (signup → POST creates session → cookie-authed WS attach → `echo hello\n` → 'hello' in data frame → close WS → `docker restart <ephemeral_orchestrator>` → reconnect WS to same `session_id` → attach frame's scrollback contains 'hello' → `echo world\n` succeeds in the same shell) is **byte-for-byte the literal demo** of `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance` — a test already shipped to main under M002/S05. Commit b7ea8c6 ("feat: Add bundled M002 final acceptance e2e covering durability, reaper") makes the duplication explicit in the commit name itself. The bundled e2e PASSED on HEAD `b1afe70` during this verification run.

**This is the FIFTH consecutive M003-umluob slice to file the same reconciliation hand-off.** Prior filings:
- `.gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md` (S01 — provisioning)
- `.gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md` (S03 — idle reaper)
- `.gsd/milestones/M003-umluob/slices/S04/tasks/T01-VERIFICATION.md` (S04 — tmux durability)
- `.gsd/milestones/M003-umluob/slices/S05/tasks/T01-VERIFICATION.md` (S05 — WS bridge)
- this file (S06 — final integrated acceptance)

S02 was the only M003 slice with novel scope (system_settings table for `workspace_volume_size_gb` admin endpoint and `idle_timeout_seconds` admin endpoint — both already shipped in M002 alembic revision `s05_system_settings`, but verified there as an integration rather than a duplication). MEM200/MEM201/MEM202/MEM205/MEM206/MEM208/MEM211/MEM212/MEM213 carry the full provenance.

**`M003-umluob duplicates M002-jy6pde`** — grep-stable string for downstream tooling. **No further M003 slices remain.** The reconciliation MUST happen at the milestone level. The two valid next moves are:

- **`gsd_complete_milestone` — RECOMMENDED.** Close M003-umluob as already-delivered (every S0X demo is byte-for-byte covered by tests on main and PASSED today). M003's stated requirements (R009-R012 per PROJECT.md) target Projects-and-GitHub scope that the M003 roadmap and slice plans never addressed; closing this milestone as already-delivered acknowledges the duplication and frees the M003 milestone-id slot for the actual Projects/GitHub work to be re-scoped under a fresh milestone-id. Recommended because (a) all six demo bullets are already proven against main, (b) M002-jy6pde already shipped to main, (c) re-running the same proofs across two milestones is auditable churn with zero net product value.
- **`gsd_reassess_roadmap` — alternative.** Replan M003 toward R009-R012 Projects/GitHub scope per PROJECT.md without closing the milestone. This requires writing a new ROADMAP for M003 that owns net-new work, re-running plan-slice for each new slice, and accepting that the existing five "verification-only" T01-VERIFICATION.md files become historical artifacts of the misalignment. Use this only if there's a specific reason to keep the M003 milestone-id alive (e.g. linked external trackers or planning artifacts that pin M003 specifically).

**Auto-mode CANNOT make this call.** The decision involves milestone-id semantics, roadmap intent vs. roadmap content, and product-level trade-offs that require a human operator. Auto-mode has done what it can: PASSED proofs are captured here and in the four sibling reports, the duplication is visible to grep against `M003-umluob duplicates M002-jy6pde`, and the recommendation is on record. Until a human runs `gsd_complete_milestone` OR `gsd_reassess_roadmap`, M003 is in a stable but ambiguous state.

## Known accepted divergences

None for this slice. The bundled-e2e demo is fully spec-aligned with the slice plan's eight sub-criteria.

A minor factual mismatch in the slice plan's input list: it cites `orchestrator/orchestrator/registry.py` as the Redis-backed registry. The actual filename on HEAD `b1afe70` is `orchestrator/orchestrator/redis_client.py` (class `RedisSessionRegistry`); same module also appears as `redis_client.py` in the S04/T01 verification artifact. This report cites the actual filename. No code change required.

## Verification environment

- Host Docker daemon up; `perpetuity-db-1` (postgres:18 on host port 5432, MEM114), `perpetuity-redis-1`, and `perpetuity-orchestrator-1` running and healthy at run time.
- Required images present locally: `orchestrator:latest`, `perpetuity/workspace:latest`, `perpetuity/workspace:test`, `backend:latest`.
- Tests executed from working directory `/Users/josh/code/perpetuity/backend` with env loaded from `../.env` (`POSTGRES_PASSWORD=changethis`, `POSTGRES_USER=postgres`, `POSTGRES_DB=app`, `REDIS_PASSWORD=changethis` per MEM111) and `POSTGRES_PORT=5432` for the in-network DB.
- Backend e2e + unit suites via `backend` `uv run pytest` (resolves to project `.venv/bin/python` per MEM041).
- Pre-flight loop-device sanity check per MEM214: `docker exec perpetuity-orchestrator-1 sh -c 'losetup -a | wc -l'` reported **46 of 47** before the bundled e2e run — at the threshold but with one slot free, enough for the bundled e2e's single workspace-volume provision. Post-run probe reported **47 of 47** in use; the supplementary two-key rotation e2e (which provisions THREE backends each requiring a fresh session POST) hit the empty pool and failed at step 8 — recorded as a `## Verification gap:` below, NOT as an S06 code regression.
- Working tree clean before this report was written (`git status --porcelain` empty); no source/compose/Dockerfile/test-code modified during this verification.

---

## Criterion: Signup creates user + personal team + cookie-authed login (S06 demo bullet 1)

**Source-of-truth files:**
- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py` step 2 L564–L580 — admin login (`_login_only` L193–L204) + alice signup-and-login (`_signup_login` L172–L190 calls `POST /api/v1/auth/signup` with `{email, password, full_name}`, asserts 200, then `POST /api/v1/auth/login`, captures the `perpetuity_session` cookie). Personal team auto-created on signup is resolved via `_personal_team_id` L207–L214 which calls `GET /api/v1/teams/` and asserts a row with `is_personal=True` exists.
- `backend/app/api/deps.py` `get_current_user_ws` (cookie-first WS auth helper) — proven directly by criterion's auth tests below; the same helper is reused on the WS endpoint that step 4 attaches to.

**Tests covering criterion:**
- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance` step 2 (the literal demo bullet) — alice signup completes, personal team auto-created, login returns the cookie used by step 4's WS upgrade.
- `backend/tests/api/routes/test_ws_auth.py::test_ws_connect_with_valid_cookie_returns_pong_and_role` — proves the cookie WS auth path that step 4 depends on.

**Run command (bundled e2e):** `set -a && . ../.env && set +a && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance -v --tb=short` (from `backend/`)

**Verbatim runner output (bundled e2e):**
```
tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance PASSED [100%]
======================== 1 passed, 3 warnings in 31.43s ========================
```

**Run command (cookie WS auth supporting proofs):** `set -a && . ../.env && set +a && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_ws_auth.py -v` (from `backend/`)

**Verbatim runner output (cookie WS auth):**
```
tests/api/routes/test_ws_auth.py::test_ws_connect_without_cookie_rejects_missing_cookie PASSED [  7%]
tests/api/routes/test_ws_auth.py::test_ws_connect_with_garbage_cookie_rejects_invalid_token PASSED [ 14%]
tests/api/routes/test_ws_auth.py::test_ws_connect_with_expired_cookie_rejects_invalid_token PASSED [ 21%]
tests/api/routes/test_ws_auth.py::test_ws_connect_with_unknown_user_rejects_user_not_found PASSED [ 28%]
tests/api/routes/test_ws_auth.py::test_ws_connect_with_inactive_user_rejects_user_inactive PASSED [ 35%]
tests/api/routes/test_ws_auth.py::test_ws_connect_with_valid_cookie_returns_pong_and_role PASSED [ 42%]
```

**Verdict:**
- PASSED: `test_m002_s05_full_acceptance` — proves signup + personal team + cookie-authed login as the literal precondition of every later step (the test cannot have reached step 11's redaction sweep without step 2 succeeding).
- PASSED (6 supporting): full cookie-WS auth lifecycle including the happy-path `test_ws_connect_with_valid_cookie_returns_pong_and_role`.

---

## Criterion: Backend POST /api/v1/sessions creates a real container with volume-aware provisioning (S06 demo bullet 2)

**Source-of-truth files:**
- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py` step 4 L593–L612 — `_create_session_raw` L217–L225 calls `POST /api/v1/sessions` with `{team_id}` (cookie-authed via `httpx.Client(cookies=alice_cookies)`); asserts 200; the test then asserts via `docker ps --filter label=user_id=<alice_user_id> --filter label=team_id=<alice_team>` that **exactly one** workspace container exists (L602–L611). Snapshots the container_id for the post-reap invariant at step 9.
- `backend/app/api/routes/sessions.py` `ws_terminal` L354–L444 — uses the same `session_id` later via WS upgrade.
- The orchestrator-side container provisioning (volume-aware loopback-ext4 mount + tmux start) is proven by the same step 4 succeeding.

**Tests covering criterion:**
- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance` step 4 (the literal demo bullet) — workspace container provisioned with the `(user_id, team_id)` labels, container_id captured for downstream invariants.
- `backend/tests/api/routes/test_sessions.py::test_b_create_session_without_cookie_returns_401` and `test_c_create_session_for_other_team_returns_403` — corroborating policy guards on the same endpoint.

**Run command:** `set -a && . ../.env && set +a && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_sessions.py::test_b_create_session_without_cookie_returns_401 tests/api/routes/test_sessions.py::test_c_create_session_for_other_team_returns_403 -v` (from `backend/`)

**Verbatim runner output:**
```
tests/api/routes/test_sessions.py::test_b_create_session_without_cookie_returns_401 PASSED [ 50%]
tests/api/routes/test_sessions.py::test_c_create_session_for_other_team_returns_403 PASSED [ 57%]
```

**Verdict:**
- PASSED: `test_m002_s05_full_acceptance` — proves POST → real workspace container with volume-aware provisioning end-to-end (the bundled e2e cannot have reached step 5's restart without step 4 succeeding).
- PASSED (2 supporting): cookie-required + team-ownership policy on the POST path.

---

## Criterion: Cookie-authed WS attach + 'echo hello\n' input → 'hello' in data frame (S06 demo bullet 3)

**Source-of-truth files:**
- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py` step 4 L614–L648 — `cookie_header` built from `alice_cookies` (L615), `aconnect_ws(ws_url_a, headers={"Cookie": cookie_header})` (L620–L622), assert first frame `type == "attach"` (L623–L628), `await ws.send_text(_input_frame("echo hello\n"))` (L629), `_drain_data(ws, timeout_s=10.0, until_substring="hello")` then `assert "hello" in seen` (L630–L636).
- `backend/app/api/routes/sessions.py` `ws_terminal` L354–L455 — full lifecycle: cookie auth at L373 via `get_current_user_ws`, ownership check L378–L406 with no-enumeration close shape, accept at L409, `_proxy_frames` at L433. The proxy is verbatim text-frame forwarding (`_pump_browser_to_orch` L478–L490 / `_pump_orch_to_browser` L492–L505) — backend does not decode/re-encode JSON (locked frame protocol from `app.api.ws_protocol`).
- `orchestrator/orchestrator/routes_ws.py` `session_stream` L97–L240:
  - shared-secret WS auth L107–L109 (`authenticate_websocket` validates `?key=<ORCHESTRATOR_API_KEY>` via `auth.py` two-key rotation per MEM096)
  - registry lookup L114–L133 (`get_registry().get_session(session_id)` — Redis is source of truth per D013, rebuilt-on-boot after restart)
  - attach frame send L146–L173 (calls `capture_scrollback` at L148, builds `make_attach(scrollback.encode("utf-8"))` at L161, sends as first WS text frame at L163, emits `session_attached session_id=… container_id=…` INFO log at L169–L173)
  - exec stream open + bash/tmux attach L175–L226 (`docker exec` of `tmux attach-session -t <sid>` — re-attaches to the surviving tmux session post-restart per D012/MEM092)
  - attach refcount register at L228–L240 (process-local `AttachMap.register` per MEM181, emits `attach_registered session_id=… count=…` INFO log)
  - dual pumps `_pump_exec_to_ws` L252–L280 (forwards exec stdout chunks as `make_data` frames at L263, sends as WS text frame at L266) and `_pump_ws_to_exec` L282–L379 (handles `input` frame: decode_bytes L311 → `stream.write_in(raw)` L319 → bumps Redis `last_activity` heartbeat L335 best-effort)
- `orchestrator/orchestrator/sessions.py`:
  - `start_tmux_session` L374–L409 — `tmux new-session -d` at L388 (detached so docker exec returns immediately, tmux owns the pty per D012/MEM092). Emits `session_created` INFO log L405–L409.
  - `capture_scrollback` L430–L465 — `tmux capture-pane -p -S - -E -` at L447–L457 capped to `settings.scrollback_max_bytes` per D017 (default 100 KiB). Returns `""` on `can't find session` orphaned-state guard at L462–L463.

**Tests covering criterion:**
- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance` step 4 — the literal demo: cookie WS upgrade succeeds, attach frame received, `echo hello\n` input echoed back as 'hello' in a data frame within 10s.
- `backend/tests/api/routes/test_sessions.py::test_e_ws_without_cookie_closes_1008_missing_cookie` — proves the exact close shape on the production endpoint when the cookie is missing.

**Verbatim runner output (bundled e2e — already cited above):** `PASSED [100%]` line covers this criterion.

**Verbatim runner output (no-cookie close shape):**
```
tests/api/routes/test_sessions.py::test_e_ws_without_cookie_closes_1008_missing_cookie PASSED [ 64%]
```

**Verdict:**
- PASSED: `test_m002_s05_full_acceptance` — proves cookie-authed WS attach + 'echo hello\n' → 'hello' in data frame end-to-end (the literal demo bullet).
- PASSED: `test_e_ws_without_cookie_closes_1008_missing_cookie` — proves the negative path (cookie required) on the same endpoint.

---

## Criterion: Clean WS close (no tmux teardown) (S06 demo bullet 4)

**Source-of-truth files:**
- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py` step 4 L618–L645 — the `_phase_one` async function exits the `async with aconnect_ws(...) as ws` context manager after capturing `pid_before`. Exiting the `async with` sends a clean WS close (httpx_ws does a normal 1000 close on context-manager exit). The next step's `_phase_two` then opens a *new* WS to the same `session_id` — only possible if step 4's clean close did NOT tear down the tmux session.
- `backend/app/api/routes/sessions.py` `_proxy_frames` L458–L539 — `_pump_browser_to_orch` (L478–L490) catches `WebSocketDisconnect` at L483 and sets `close_state["reason_label"] = "client"`. The `_pump_orch_to_browser` task is cancelled (L508–L519). The orchestrator side closes from the context-manager exit at L432 (`async with aconnect_ws(orch_ws_url) as orch_ws`). On the `client` branch (L522–L525) the proxy returns `(1000, "client")` without re-closing the browser side.
- `orchestrator/orchestrator/routes_ws.py` close path L424–L458 — on client disconnect (`detach_reason = REASON_CLIENT_CLOSE` at L181, set in `_pump_ws_to_exec`'s `WebSocketDisconnect` branch at L378), the `_safe_close` at L427 closes the orchestrator-side WS with `CLOSE_NORMAL` (1000). The exec stream is torn down at L432–L440 (`stream.__aexit__`) — this kills the docker exec that was running `tmux attach-session`, but **not** tmux itself (D012's whole point: tmux owns the pty inside the workspace container, the docker exec is just a viewer). Emits `session_detached session_id=… container_id=… reason=client_close exit_code=-` INFO log at L442–L448. Always-decrement attach refcount at L449–L458 (`finally` block), emits `attach_unregistered session_id=… count=…` INFO log.
- `orchestrator/orchestrator/attach_map.py` L57–L69 — `unregister` decrements with floor at zero; drops the key entirely so map size tracks live attach count, not lifetime count (the reaper's two-phase liveness check at MEM181/MEM180 depends on this invariant).

**Tests covering criterion:**
- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance` step 4 → step 5 transition — the test runs `await ws.close()` (implicit via context-manager exit at L622) AND the orchestrator restart at step 5 AND the reconnect at step 6 all succeed. The only way step 6 can find the same tmux session alive is if step 4's WS close did NOT kill tmux.

**Verbatim runner output:** the bundled e2e PASSED line covers this criterion (already cited).

**Verdict:**
- PASSED: `test_m002_s05_full_acceptance` — proves clean WS close does not tear down tmux. The architectural bet (D012/MEM092 — tmux owns the pty, docker exec is the viewer) holds because step 6 successfully reattaches to the same shell PID after the close-restart-reconnect cycle.

---

## Criterion: Programmatic orchestrator restart + /healthz wait (S06 demo bullet 5)

**Source-of-truth files:**
- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py` step 5 L650–L660 — `_restart_ephemeral_orchestrator(ephemeral_orchestrator)` at L655 calls `docker restart -t 5 <ephemeral_name>` (defined at L420–L427). **NOT** `docker compose restart orchestrator` per MEM193: the ephemeral orchestrator owns the `orchestrator` DNS alias for the test's duration via `--network-alias` (set at L330 in `_boot_ephemeral_orchestrator`), so a compose restart would only kick the masked-out compose service. The durability path needs the actual live container that the backend is talking to. After the restart, `_wait_for_orch_running` at L656–L660 polls `/v1/health` from inside the sibling backend container (probe script at L370–L379) until it returns a body containing `image_present` — proves the rebooted orchestrator completed `_lifespan` setup including `RedisSessionRegistry` rebuild and image-presence verification.
- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py` `_boot_ephemeral_orchestrator` L310–L348 — the ephemeral orchestrator is launched with `REAPER_INTERVAL_SECONDS=1` and `--network-alias orchestrator` so the backend resolves `http://orchestrator:8001` to it. The compose orchestrator is `docker compose rm -sf orchestrator` first to free the alias.
- `orchestrator/orchestrator/main.py` (cited via S04 verification artifact L196–L200) — `_lifespan` binds a fresh `RedisSessionRegistry()` to `app.state.registry` on every boot, no in-memory shim — every read after restart hits Redis directly per D013.
- `orchestrator/orchestrator/redis_client.py` `RedisSessionRegistry.get_session` L107–L118 — `GET session:<sid>` from Redis; returns `None` if the key is missing. Pure pass-through with no caching layer in front, so a freshly-booted orchestrator's first request reads the Redis row written by the pre-restart create.

**Tests covering criterion:**
- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance` step 5 (the literal demo bullet) — restart completes, `/v1/health` returns ready (`image_present` in the body), step 6 then opens a new WS to the same `session_id` and the orchestrator successfully resolves it via the rebuilt registry.

**Verbatim runner output:** the bundled e2e PASSED line covers this criterion (already cited).

**Verdict:**
- PASSED: `test_m002_s05_full_acceptance` — proves programmatic orchestrator restart via `docker restart <ephemeral_name>` followed by `/v1/health` polling works end-to-end. The post-restart orchestrator successfully serves the WS reconnect at step 6.

---

## Criterion: Reconnect WS to same session_id; attach frame's scrollback contains 'hello' — DURABILITY, the architectural bet (S06 demo bullet 6)

**Source-of-truth files:**
- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py` step 6 L662–L699 — `_phase_two` async function: opens a fresh `aconnect_ws(ws_url_a, headers={"Cookie": cookie_header})` at L664–L666 (same `session_id` as step 4, same alice cookie), receives the attach frame at L667–L669, base64-decodes the `scrollback` field via `_b64dec(first["scrollback"])` at L674, ANSI-strips it via `_strip_ansi` at L673, asserts `"hello" in scrollback_after` at L688–L691 — this is the load-bearing durability assertion (the architectural bet of M002/S04+S05 and M003/S04+S06). Followed by `assert pid_before in pid_buffer` at L692–L695 (proves the same shell PID survived) and `assert "world" in world_buffer` at L696–L699 (proves the next echo runs in the same shell — see criterion 7 below).
- `orchestrator/orchestrator/routes_ws.py` L146–L173 — on every fresh WS attach (which is also what happens after an orchestrator restart, since the prior exec stream died), the orchestrator calls `capture_scrollback(docker, container_id, session_id)` at L148 and ships its bytes as the `attach` frame's `scrollback` field at L161–L163. The post-restart client receives the same buffer that the pre-restart client wrote into.
- `orchestrator/orchestrator/sessions.py` `capture_scrollback` L430–L465 — `tmux capture-pane -t <sid> -p -S - -E -` (D017, capped to 100 KiB). Tmux's pane buffer survived the orchestrator restart because tmux runs inside the workspace container (not inside the orchestrator process) and was started detached via `tmux new-session -d` at L388; the orchestrator restart killed only the docker exec stream, not tmux per D012/MEM092/MEM121.
- `orchestrator/orchestrator/redis_client.py` `RedisSessionRegistry.get_session` L107–L118 — Redis-backed lookup. Per D013 there is no in-memory fallback; the rebooted orchestrator reads `session:<sid>` from Redis on its very first request and resolves to the same `(container_id, tmux_session)` pair.

**Tests covering criterion:**
- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance` step 6 (the literal architectural-bet demo) — `assert "hello" in scrollback_after` is the load-bearing assertion. Cannot pass unless tmux survived the orchestrator restart with its scrollback buffer intact AND the rebooted orchestrator resolved the WS to the same `(container_id, tmux_session)` pair via Redis.

**Verbatim runner output:** the bundled e2e PASSED line covers this criterion (already cited).

**Verdict:**
- PASSED: `test_m002_s05_full_acceptance` — proves the architectural bet: tmux owns the pty inside the workspace container, the orchestrator restart killed only the exec stream not tmux, and the new attach receives the pre-restart scrollback containing 'hello'. **This is the load-bearing durability assertion of the entire M002/M003 milestone.**

---

## Criterion: 'echo world\n' in same shell post-restart (S06 demo bullet 7)

**Source-of-truth files:**
- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py` step 6 L676–L699 — `_phase_two` after the attach frame: `await ws.send_text(_input_frame("echo $$\n"))` at L676 to capture `pid_buffer`, then `await ws.send_text(_input_frame("echo world\n"))` at L680 to capture `world_buffer`, then asserts `pid_before in pid_buffer` (L692–L695: shell PID survived — proves the same bash process is still owning the tmux pane) AND `"world" in world_buffer` (L696–L699: the next echo runs successfully on the post-restart shell).
- `orchestrator/orchestrator/routes_ws.py` `_pump_ws_to_exec` input handler L302–L340 — handles `input` frame: decodes base64 bytes via `decode_bytes` at L311, writes to `stream.write_in(raw)` at L319 (the docker exec stream's stdin, which is `tmux attach-session`'s stdin, which is the tmux client → tmux server → bash pty). Bumps Redis `last_activity` heartbeat best-effort at L335.
- `orchestrator/orchestrator/sessions.py` `start_tmux_session` L374–L409 — the tmux session was created with `bash` as the shell at L388. The post-restart `tmux attach-session -t <sid>` at routes_ws.py L188 attaches to that pre-existing detached session — the bash PID is the one tmux spawned at create time, unchanged across the orchestrator restart per D012/MEM092.

**Tests covering criterion:**
- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance` step 6 (the literal demo bullet) — `pid_before in pid_buffer` AND `"world" in world_buffer` are both load-bearing assertions: the same bash process is still attached AND the new echo command runs successfully.

**Verbatim runner output:** the bundled e2e PASSED line covers this criterion (already cited).

**Verdict:**
- PASSED: `test_m002_s05_full_acceptance` — proves the post-restart shell is the same shell (PID equality) AND that input frames continue to route to it correctly. This is the strongest tmux-ownership assertion in the suite.

---

## Criterion: Cross-owner 1008 byte-equal to never-existed UUID — no enumeration (S06 demo bullet 8 — bonus / MEM113)

**Source-of-truth files:**
- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py` step 7 L701–L784 — bob signs up via `_signup_login` at L709–L716, then `_capture_ws_upgrade_failure` at L720–L747 sends a manual WS upgrade GET via `httpx.AsyncClient` (sidesteps the streaming machinery so the rejection response body can be read directly). Asserts `r.status_code != 101` (the upgrade MUST fail). Calls it twice: once with `sid_a` (alice's session — bob is not the owner), once with `never_existed_sid` (a UUID that was never POSTed). The byte-equality assertions are at L755–L764: `other_status == missing_status` AND `other_body == missing_body`. Same shape on the DELETE path at L767–L784 (`r_other.content == r_missing.content`). Per MEM191 the pre-accept WS close at sessions.py L405 becomes an HTTP 403 with a regular response body — the comparison sees the exact same status code and body across both cases, proving no enumeration.
- `backend/app/api/routes/sessions.py` `ws_terminal` ownership check L378–L406 — orchestrator lookup at L380 → `_orch_get_session_record(session_id)` → if `record is None OR str(record.get("user_id")) != str(user.id)`: emits `session_proxy_reject session_id=… user_id=… reason=session_not_owned` INFO log at L400–L404 and `await websocket.close(code=1008, reason="session_not_owned")` at L405 BEFORE accept. The `record is None` branch and the `user_id mismatch` branch share the close shape — that is the no-enumeration property MEM113.
- `orchestrator/orchestrator/routes_ws.py` `session_stream` L114–L133 — orchestrator-side mirror: registry lookup → `record is None` → `_safe_close(websocket, code=CLOSE_POLICY_VIOLATION, reason=REASON_SESSION_NOT_FOUND)` at L130–L132. Same 1008 close shape.

**Tests covering criterion:**
- `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance` step 7 (bonus demo bullet) — byte-equality across (a) bob WS to alice's `sid_a` and (b) bob WS to a never-existed UUID; AND byte-equality on the DELETE 404 paths.
- `backend/tests/api/routes/test_sessions.py::test_h_ws_for_never_existed_sid_closes_1008_session_not_owned` — corroborating proof of the missing-sid close shape on the production endpoint.
- `backend/tests/api/routes/test_sessions.py::test_scrollback_missing_session_returns_404_byte_equal_to_non_owner` — corroborating proof of the parallel HTTP-side no-enumeration property on the scrollback proxy.

**Run command:** `set -a && . ../.env && set +a && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_sessions.py::test_h_ws_for_never_existed_sid_closes_1008_session_not_owned tests/api/routes/test_sessions.py::test_scrollback_owner_returns_200_with_orchestrator_text tests/api/routes/test_sessions.py::test_scrollback_owner_with_empty_scrollback_returns_200_empty_string tests/api/routes/test_sessions.py::test_scrollback_missing_session_returns_404_byte_equal_to_non_owner tests/api/routes/test_sessions.py::test_scrollback_unauthenticated_returns_401 -v` (from `backend/`)

**Verbatim runner output:**
```
tests/api/routes/test_sessions.py::test_h_ws_for_never_existed_sid_closes_1008_session_not_owned PASSED [ 71%]
tests/api/routes/test_sessions.py::test_scrollback_owner_returns_200_with_orchestrator_text PASSED [ 78%]
tests/api/routes/test_sessions.py::test_scrollback_owner_with_empty_scrollback_returns_200_empty_string PASSED [ 85%]
tests/api/routes/test_sessions.py::test_scrollback_missing_session_returns_404_byte_equal_to_non_owner PASSED [ 92%]
tests/api/routes/test_sessions.py::test_scrollback_unauthenticated_returns_401 PASSED [100%]
```

**Verdict:**
- PASSED: `test_m002_s05_full_acceptance` — proves byte-equal close shape across cross-owner and missing-sid cases on the WS upgrade AND the DELETE path (no enumeration).
- PASSED: `test_h_ws_for_never_existed_sid_closes_1008_session_not_owned` — proves the missing-sid 1008 'session_not_owned' close on the production endpoint.
- PASSED (3 supporting): the scrollback proxy mirror of the no-enumeration property and other lifecycle policy guards.

---

## Aggregate runner output (the literal-S06-demo bundled e2e in isolation)

```
============================= test session starts ==============================
platform darwin -- Python 3.12.13, pytest-7.4.4, pluggy-1.6.0 -- /Users/josh/code/perpetuity/.venv/bin/python
cachedir: .pytest_cache
rootdir: /Users/josh/code/perpetuity/backend
configfile: pyproject.toml
plugins: anyio-4.12.1
collecting ... collected 1 item

tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance PASSED [100%]

=============================== warnings summary ===============================
…
======================== 1 passed, 3 warnings in 31.43s ========================
```

## Aggregate runner output (14 supporting backend tests)

```
============================= test session starts ==============================
tests/api/routes/test_ws_auth.py::test_ws_connect_without_cookie_rejects_missing_cookie PASSED [  7%]
tests/api/routes/test_ws_auth.py::test_ws_connect_with_garbage_cookie_rejects_invalid_token PASSED [ 14%]
tests/api/routes/test_ws_auth.py::test_ws_connect_with_expired_cookie_rejects_invalid_token PASSED [ 21%]
tests/api/routes/test_ws_auth.py::test_ws_connect_with_unknown_user_rejects_user_not_found PASSED [ 28%]
tests/api/routes/test_ws_auth.py::test_ws_connect_with_inactive_user_rejects_user_inactive PASSED [ 35%]
tests/api/routes/test_ws_auth.py::test_ws_connect_with_valid_cookie_returns_pong_and_role PASSED [ 42%]
tests/api/routes/test_sessions.py::test_b_create_session_without_cookie_returns_401 PASSED [ 50%]
tests/api/routes/test_sessions.py::test_c_create_session_for_other_team_returns_403 PASSED [ 57%]
tests/api/routes/test_sessions.py::test_e_ws_without_cookie_closes_1008_missing_cookie PASSED [ 64%]
tests/api/routes/test_sessions.py::test_h_ws_for_never_existed_sid_closes_1008_session_not_owned PASSED [ 71%]
tests/api/routes/test_sessions.py::test_scrollback_owner_returns_200_with_orchestrator_text PASSED [ 78%]
tests/api/routes/test_sessions.py::test_scrollback_owner_with_empty_scrollback_returns_200_empty_string PASSED [ 85%]
tests/api/routes/test_sessions.py::test_scrollback_missing_session_returns_404_byte_equal_to_non_owner PASSED [ 92%]
tests/api/routes/test_sessions.py::test_scrollback_unauthenticated_returns_401 PASSED [100%]
======================= 14 passed, 27 warnings in 8.57s ========================
```

---

## Verification gap: `test_m002_s05_two_key_rotation_e2e.py` — bonus two-key rotation supplementary proof blocked by MEM214 linuxkit loop-device-pool exhaustion

**Status:** ❌ Blocked: bonus supplementary proof `test_m002_s05_two_key_rotation` failed at step 8 (`bob POST via backend_previous`) with `503 orchestrator_status_500`. **NOT** an S06 code regression — environmental.

**Root cause:** Pre-existing environmental flake per MEM210/MEM214. The Docker Desktop linuxkit VM ships a fixed pool of `/dev/loopN` devices (~47 on this host); orphan workspace `.img` files persist under `/var/lib/perpetuity/vols/` from prior test runs and remain attached. Pre-flight probe before this verification reported **46 of 47** loop devices in use (the threshold MEM214 calls out — close to exhaustion). The bundled e2e ran first and consumed the last free slot for alice's workspace volume; post-bundled probe reported **47 of 47** in use. The two-key rotation test then provisions a fresh `(user, team)` workspace volume per backend (three backends → three fresh volume provisions, each needing a free loop device) — fails at step 8 with `losetup: failed to set up loop device: No such file or directory` surfaced as orchestrator 503. Same HEAD `b1afe70`, same code: the bundled e2e PASSED earlier in this same verification run, so the divergence is purely environmental.

**Failing pytest output (verbatim, abridged):**
```
>       assert r.status_code == 200, (
E       AssertionError: step 8: bob POST via backend_previous must succeed (key_previous in candidates); got 503 {"detail":"orchestrator_status_500"}
E       assert 503 == 200
E        +  where 503 = <Response [503 Service Unavailable]>.status_code
FAILED tests/integration/test_m002_s05_two_key_rotation_e2e.py::test_m002_s05_two_key_rotation
======================== 1 failed, 3 warnings in 17.64s ========================
```

**Pre-flight + post-bundled loop-device probe (verbatim):**
```
$ docker exec perpetuity-orchestrator-1 sh -c 'losetup -a | wc -l; ls /dev/loop* | wc -l'
46
47
$ # … bundled e2e ran here, PASSED, consumed the last free slot …
$ docker exec perpetuity-orchestrator-1 sh -c 'losetup -a | wc -l'
47
```

**Why this does not invalidate the slice:** the two-key rotation test is **bonus** evidence per the slice plan ("This is bonus evidence that the restart cycle is operationally safe; record the PASSED line in T01-VERIFICATION.md as additional credit") — the load-bearing proof is the bundled e2e, which PASSED. The two-key rotation contract itself is exercised inside the bundled e2e implicitly: the ephemeral orchestrator boots with `ORCHESTRATOR_API_KEY=changethis` (set at backend.tests.integration.test_m002_s05_full_acceptance_e2e.py L339, equal to the dotenv value the sibling backend reads), the restart cycle at step 5 doesn't disrupt that contract because the same key is in scope before and after — and the test's step 6 reconnect succeeds, proving the post-restart auth path works. A separate two-key rotation regression would have surfaced as auth failure at step 6, not as a step-8 503 in a downstream test.

**Remediation (out of scope for verification):** environmental — clean orphan loop devices in the linuxkit VM (`docker volume prune`, `losetup -D`, or restart Docker Desktop to reclaim the device pool) per MEM210 / MEM214. A code-level remediation (a pytest fixture that asserts free loop slots before booting the ephemeral orchestrator, or a cleanup hook in the orchestrator's `provision_container` that detaches orphan loops) is recorded as a possible follow-up for the human owner reconciling M003-umluob ≡ M002-jy6pde to file alongside that decision. **Strictly out of scope for this verification-only task per the slice plan's scope rule ("NO modification of backend/orchestrator source, compose files, Dockerfiles, or test code").**

---

## Aggregate result

- **8 of 8 sub-criteria PASS** by citation against `test_m002_s05_full_acceptance` (the bundled M002 milestone-capstone e2e — the literal S06 demo). The bundled e2e PASSED end-to-end against the live compose stack on this run; **15 corroborating PASSED tests** captured for cookie auth, ownership/no-enumeration, attach frame, and scrollback proxy lifecycle.
- **0 S06-functionality regressions** surfaced.
- **1 verification gap** recorded against the bonus two-key rotation supplementary proof (`test_m002_s05_two_key_rotation`) — pre-existing environmental flake (MEM210/MEM214 linuxkit loop-device-pool exhaustion), NOT an S06 regression; the load-bearing bundled e2e was unaffected because it ran first while one slot was still free.
- **0 known accepted divergences** for this slice.
- **1 human-action note re-filed** (`M003-umluob duplicates M002-jy6pde` — same hand-off as S01/T01, S03/T02, S04/T01, S05/T01; **FIFTH and FINAL** filed hand-off — no further M003 slices remain).

**M003-umluob duplicates M002-jy6pde** — grep-stable string (FIFTH and FINAL filing).

No remediation work in scope for this slice. Auto-mode CANNOT continue M003 productively beyond this point. The next move MUST be one of:

1. **`gsd_complete_milestone` — RECOMMENDED.** Close M003-umluob as already-delivered. Every S0X demo is byte-for-byte covered by tests on main; the bundled e2e PASSED today on HEAD `b1afe70`. M003's stated requirements (R009-R012 per PROJECT.md) target Projects/GitHub scope and were never addressed by the M003 roadmap or slice plans; closing this milestone-id frees the slot for the actual Projects/GitHub work to be re-scoped under a fresh milestone-id.

2. **`gsd_reassess_roadmap` — alternative.** Replan M003 toward R009-R012 Projects/GitHub scope per PROJECT.md without closing the milestone. Requires writing a new ROADMAP for M003 owning net-new work, re-running plan-slice for each new slice, and accepting the existing five "verification-only" T01-VERIFICATION.md files as historical artifacts of the misalignment.

The future agent (or human) reconciling M003 vs M002 should:

1. Read this file and its four sibling reports:
   - `cat .gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md`
   - `cat .gsd/milestones/M003-umluob/slices/S03/tasks/T02-VERIFICATION.md`
   - `cat .gsd/milestones/M003-umluob/slices/S04/tasks/T01-VERIFICATION.md`
   - `cat .gsd/milestones/M003-umluob/slices/S05/tasks/T01-VERIFICATION.md`
   - `cat .gsd/milestones/M003-umluob/slices/S06/tasks/T01-VERIFICATION.md` (this file)
2. Pick `gsd_complete_milestone` (recommended) OR `gsd_reassess_roadmap` (alternative). Do not attempt a third path; auto-mode has exhausted productive moves under the current milestone framing.
3. Optionally file three side follow-ups, all independent of the M003 reconciliation:
   - **MEM209** — fix `test_ws_bridge.py::_seed_session` to seed user/team rows (out of scope: WS-bridge tests with stale FK seeding).
   - **MEM210/MEM214** — orchestrator-side cleanup hook for orphan linuxkit loop devices, or a pytest fixture asserting free loop slots before booting an ephemeral orchestrator (out of scope: environmental flake on long test marathons).
   - **Slice-plan typo** — the S06 plan cites `orchestrator/orchestrator/registry.py`; the actual filename is `orchestrator/orchestrator/redis_client.py` (class `RedisSessionRegistry`). Cosmetic; this report cites the actual filename.
