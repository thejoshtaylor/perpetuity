---
estimated_steps: 35
estimated_files: 2
skills_used: []
---

# T01: Verify S06 final-integrated-acceptance demo by citation against the bundled M002/S05 e2e; produce T01-VERIFICATION.md and escalate the duplication hand-off to milestone-level

Verification-only task following the pattern locked across M003/S01/T01 (MEM200/MEM201), M003/S03/T02 (MEM205), M003/S04/T01 (MEM206/MEM208), and M003/S05/T01 (MEM212/MEM213) — FIFTH and FINAL in a row. The S06 demo (signup creates user + personal team; backend POST creates session via orchestrator with volume-aware provisioning; client opens WS to /api/v1/ws/terminal/<sid> with auth cookie; sends 'echo hello\n' input; receives data frame containing 'hello'; closes WS; programmatically `docker restart <ephemeral_name>` per MEM193; waits for orchestrator /healthz; opens new WS to same session_id; attach frame's scrollback decodes and contains 'hello'; sends 'echo world\n'; receives data frame containing 'world' from the SAME shell — proving tmux owns the pty inside the workspace container and survived the orchestrator restart per MEM092/MEM121) is byte-for-byte the literal demo of `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance` (commit b7ea8c6 is even named 'Add bundled M002 final acceptance e2e covering durability, reaper'). This slice is therefore verification-only: zero orchestrator/backend source, compose, Dockerfile, or test-code modifications.

The milestone-level escalation: this is the FIFTH consecutive verification-only slice. Auto-mode CANNOT decide whether to close M003 as already-delivered or replan toward Projects/GitHub scope (R009-R012 per PROJECT.md). T01-VERIFICATION.md MUST contain a top-level escalation block naming the two valid next moves: `gsd_complete_milestone` (close as already-delivered — RECOMMENDED) OR `gsd_reassess_roadmap` (replan toward R009-R012). After this slice closes, no further M003-umluob slices remain to file the hand-off in.

Citations to wire in T01-VERIFICATION.md (read-only, do not modify):
  * The bundled e2e itself: `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance` — cite the test's step-numbered structure with line ranges. Locate via `grep -n 'step' backend/tests/integration/test_m002_s05_full_acceptance_e2e.py` to find each step block. Step 1 alice signup + personal team; step 2 POST /api/v1/sessions; step 3 cookie-authed WS attach + echo hello + assert 'hello' in data frame; step 4 close WS cleanly; step 5 `docker restart <ephemeral_orchestrator_name>` (NOT `docker compose restart` per MEM193) and wait for /healthz; step 6 reconnect WS to same sid and decode attach frame's scrollback (base64) and assert 'hello' in it; step 7 'echo world\n' input and assert 'world' in next data frame; step 8 bob ownership check producing HTTP 403 (per MEM191 — Starlette pre-accept close becomes HTTP 403) byte-equal to bob WS to never-existed UUID (MEM113 enumeration prevention).
  * Backend cookie-authed WS proxy: `backend/app/api/routes/sessions.py` ws_terminal L354-L444 (cookie auth via get_current_user_ws L373; ownership check L394-L405 with identical 1008 'session_not_owned' close shape for both 'doesn't exist' AND 'exists but not owned' per MEM113; pre-accept close BEFORE accept() per MEM022; accept L409; _proxy_frames L478-L535 dual pumps with 1:1 close-code+reason mirror).
  * Orchestrator WS-side: `orchestrator/orchestrator/routes_ws.py::session_stream` L97-L458 — key auth L107-L109 (two-key rotation per MEM096 — proven by `test_m002_s05_two_key_rotation_e2e.py`); registry lookup L114-L133 (rebuilt from Redis after restart per D016); attach frame L146-L173 (scrollback delivered base64-encoded, capped to D017's scrollback_max_bytes); exec stream rebuild L175-L226 (re-attaches to surviving tmux session post-restart); attach refcount L228-L240 + L453-L458 (MEM181 process-local AttachMap); dual pumps L242-L394; teardown L429-L458.
  * Orchestrator scrollback delivery: `orchestrator/orchestrator/sessions.py` `attach_to_tmux_session` and `capture_pane_scrollback` — D017 tmux capture-pane invocation; MEM092/MEM121 (tmux owns the pty inside the workspace container; orchestrator restart kills only the exec stream, not tmux).
  * Orchestrator session registry: `orchestrator/orchestrator/registry.py` — Redis-backed `session:<id>` hash; MEM096 (rebuilt on boot from Redis per D016 — Redis is source of truth for session state); MEM170 (lifespan teardown order: stop_reaper FIRST so a restart doesn't trip the in-flight reaper).
  * Orchestrator attach map: `orchestrator/orchestrator/attach_map.py` — process-local refcount, drops every attach on restart (MEM181); D018 two-phase liveness check upstream of reaper consults this map.

Covered S06 demo elements and which test proves each (load-bearing assertions only):
  1. **Signup creates user + personal team + cookie-authed login** → `test_m002_s05_full_acceptance` step 1 (alice signup + personal team auto-created).
  2. **Backend POST /api/v1/sessions creates a real container with volume-aware provisioning** → step 2 (POST returns 200, container_id and session_id present in response).
  3. **Cookie-authed WS attach + 'echo hello\n' input → 'hello' in data frame** → step 3 (cookie_header built from alice_cookies; WS receives data frame containing 'hello' within timeout).
  4. **Clean WS close (no tmux teardown)** → step 4 (`await websocket.close()`).
  5. **Programmatic orchestrator restart + /healthz wait** → step 5 (`docker restart <ephemeral_orchestrator_name>` per MEM193; poll /healthz until 200).
  6. **Reconnect WS to same session_id; attach frame's scrollback contains 'hello' (DURABILITY — the architectural bet)** → step 6 (reconnect, base64-decode attach frame's `scrollback` field, assert b'hello' in decoded bytes).
  7. **'echo world\n' in same shell post-restart** → step 7 (assert 'world' in next data frame, proving tmux + shell PID survived).
  8. **(Bonus, recorded if step 8 is in the bundled e2e) Cross-owner 1008 byte-equal to never-existed UUID 1008 (MEM113 enumeration prevention)** → step 8.

Live test run (against the real compose stack — matches verification mode of S01/T01, S03/T02, S04/T01, S05/T01):
  1. Boot the stack: `docker compose up -d db redis orchestrator` and ensure `perpetuity/workspace:test` is built per MEM141 (`docker build -f orchestrator/tests/fixtures/Dockerfile.test -t perpetuity/workspace:test orchestrator/workspace-image/`).
  2. Pre-flight loop-device sanity check per MEM214: `docker exec <orchestrator_container> losetup -a | wc -l` — if close to 47, the linuxkit pool is exhausted; record as a Verification gap and run alternative-proof tests instead of masking. Do NOT silently retry.
  3. From `backend/`: `POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance -v --tb=short` — covers all 6+ S06 sub-criteria in one bundled run. Capture the verbatim PASSED line.
  4. If MEM210 trips and the bundled e2e fails with `losetup: failed to set up loop device`, record the verbatim pytest output as `## Verification gap: MEM210 — linuxkit /dev/loopN pool exhaustion (environmental)` AND run alternative-proof tests for every affected criterion: `tests/integration/test_m002_s01_e2e.py` (cookie auth + WS attach + echo round-trip — covers criteria 1, 3), `orchestrator/tests/integration/test_ws_attach_map.py` (clean disconnect — covers criterion 4), `orchestrator/tests/integration/test_reaper.py` (registry survives restart — covers criterion 5 partial). Capture PASSED lines for each.
  5. Two-key rotation supplementary proof: `POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s05_two_key_rotation_e2e.py -v` — proves the `ORCHESTRATOR_API_KEY_PREVIOUS` rotation path used during restart per MEM096. This is bonus evidence that the restart cycle is operationally safe; record the PASSED line in T01-VERIFICATION.md as additional credit.

T01-VERIFICATION.md MUST contain:
  * One `## Criterion:` section per S06 sub-criterion (≥6 sections covering the demo elements above).
  * ≥6 verbatim PASSED lines from live test runs.
  * File-and-line citations into `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py` (the test that IS the demo), `backend/app/api/routes/sessions.py`, `orchestrator/orchestrator/routes_ws.py`, `orchestrator/orchestrator/sessions.py`, `orchestrator/orchestrator/registry.py`, `orchestrator/orchestrator/attach_map.py`.
  * A top-level `## Human action required: M003-umluob duplicates M002-jy6pde` block re-stating the same reconciliation hand-off filed by S01/T01, S03/T02, S04/T01, S05/T01 — keep the wording grep-stable: include the literal string `M003-umluob duplicates M002-jy6pde`. ESCALATE to milestone-level: state this is the FIFTH and FINAL filed hand-off (no further M003 slices remain), name the two valid next moves explicitly — `gsd_complete_milestone` (close as already-delivered — RECOMMENDED, since every S0X demo is byte-for-byte covered by tests on main) OR `gsd_reassess_roadmap` (replan M003 toward R009-R012 Projects/GitHub scope per PROJECT.md). State that auto-mode CANNOT make this call.
  * Optional `## Verification gap:` sections honestly recording any non-blocking environmental failures (e.g. MEM210 linuxkit loop-pool exhaustion if it trips). Do NOT modify the test or source to make a citation pass — verification-only scope is strict.

Strict scope (enforced mechanically by the verify command):
  * NO modification of backend/orchestrator source, compose files, Dockerfiles, or test code.
  * NO new alembic migrations, no new endpoints, no new test files in `/orchestrator/tests/` or `/backend/tests/`.
  * `git status --porcelain` after the task should show only `.gsd/milestones/M003-umluob/slices/S06/tasks/T01-*` files (and the engine-written summary/UAT for the slice).

If the bundled e2e fails when re-run on HEAD AND the failure is a real S06-functionality regression (NOT MEM210 environmental), surface the failure as a real verification gap — write a `## Verification gap:` section in T01-VERIFICATION.md, include the failing pytest output, and stop the slice. Do NOT modify the test or the source to make it pass; that's the human-action call (re-plan toward true M003 scope or close M003 as undelivered).

## Inputs

- ``backend/tests/integration/test_m002_s05_full_acceptance_e2e.py` — the bundled e2e that IS the literal S06 demo; cite step structure and line ranges (read-only)`
- ``backend/tests/integration/test_m002_s05_two_key_rotation_e2e.py` — bonus two-key rotation proof for the orchestrator restart cycle per MEM096 (read-only)`
- ``backend/app/api/routes/sessions.py` — cookie-authed WS proxy (ws_terminal + _proxy_frames); cite L354-L444 + L478-L535 (read-only)`
- ``backend/app/api/deps.py` — `get_current_user_ws` cookie-first WS auth helper (MEM018/MEM067/MEM022) (read-only)`
- ``orchestrator/orchestrator/routes_ws.py` — session_stream + attach frame + restart-survival path; cite L97-L458 (read-only)`
- ``orchestrator/orchestrator/sessions.py` — tmux capture-pane scrollback delivery + attach_to_tmux_session (read-only)`
- ``orchestrator/orchestrator/registry.py` — Redis-backed session:<id> registry rebuilt on boot per D016 (read-only)`
- ``orchestrator/orchestrator/attach_map.py` — process-local AttachMap dropped on restart per MEM181 (read-only)`
- ``.gsd/milestones/M003-umluob/M003-umluob-ROADMAP.md` — slice descriptions and success criteria (read-only)`
- ``.gsd/milestones/M003-umluob/slices/S05/S05-PLAN.md` — pattern reference for verification-only slice structure (read-only)`
- ``.gsd/milestones/M003-umluob/slices/S05/tasks/T01-VERIFICATION.md` — pattern reference for citation-by-test report shape (read-only)`

## Expected Output

- ``.gsd/milestones/M003-umluob/slices/S06/tasks/T01-VERIFICATION.md` — citation-by-test verification report with ≥6 `## Criterion:` sections, ≥6 verbatim PASSED lines, file:line citations into the bundled e2e and production code paths, top-level milestone-level escalation block (literal grep-stable `M003-umluob duplicates M002-jy6pde` string + explicit naming of `gsd_complete_milestone` or `gsd_reassess_roadmap` as the only two valid next moves)`
- ``.gsd/milestones/M003-umluob/slices/S06/tasks/T01-SUMMARY.md` — task summary recording verification-only scope, verification_result based on bundled e2e outcome (passed if PASSED, partial+gap if MEM210 trips), and FIFTH/FINAL duplication hand-off filed`

## Verification

test -f .gsd/milestones/M003-umluob/slices/S06/tasks/T01-VERIFICATION.md && [ "$(grep -c '^## Criterion:' .gsd/milestones/M003-umluob/slices/S06/tasks/T01-VERIFICATION.md)" -ge 6 ] && grep -q 'M003-umluob duplicates M002-jy6pde' .gsd/milestones/M003-umluob/slices/S06/tasks/T01-VERIFICATION.md && grep -q 'gsd_complete_milestone\|gsd_reassess_roadmap' .gsd/milestones/M003-umluob/slices/S06/tasks/T01-VERIFICATION.md && [ "$(grep -c 'PASSED' .gsd/milestones/M003-umluob/slices/S06/tasks/T01-VERIFICATION.md)" -ge 6 ] && [ -z "$(git status --porcelain | grep -v '^.. .gsd/' || true)" ]

## Observability Impact

No new observability added — strict verification-only scope. Existing INFO log keys exercised by the bundled e2e: `session_proxy_open`, `session_proxy_reject`, `attach_registered`, `attach_unregistered`, `session_scrollback_proxied`, `session_created`, `session_attached`, plus orchestrator boot logs `orchestrator_starting`/`orchestrator_ready` (proves restart cycle completed end-to-end). Existing close codes: 1008 (`session_not_owned`/`session_not_found`/`missing_cookie`), 1011 (`orchestrator_unavailable`), 1000 (clean exit). Inspection: `docker compose logs backend orchestrator --since <ts>` (per MEM160) carries the structured restart-cycle log lines; `docker exec perpetuity-redis-1 redis-cli -a $REDIS_PASSWORD HGETALL session:<id>` shows registry state surviving the restart; `docker exec <ws-container> tmux ls` proves the tmux session and its shell PID survived the orchestrator restart (the headline architectural bet of M002/S04+S05 and M003/S04+S06).
