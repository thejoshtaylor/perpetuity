---
estimated_steps: 32
estimated_files: 2
skills_used: []
---

# T01: Verify S05 demo by citation against shipped M002 code; produce T01-VERIFICATION.md

Verification-only task following the pattern locked by M003/S01/T01 (MEM200/MEM201), M003/S03/T02 (MEM205), and M003/S04/T01 (MEM206/MEM208) — fourth in a row. The S05 demo (browser WS to /api/v1/ws/terminal/<sid> with auth cookie → attach frame with scrollback → input echo round-trip → resize/SIGWINCH → disconnect-race cleanup with tmux survival → cross-owner 1008 close) is byte-for-byte covered by tests already shipped under M002/S04 and M002/S05. This slice is therefore verification-only: zero orchestrator/backend source, compose, Dockerfile, or test-code modifications. Auto-mode cannot decide whether to close M003 as already-delivered or replan toward Projects/GitHub scope (R009-R012); the duplication hand-off MUST be re-filed.

Citations to wire in T01-VERIFICATION.md (read-only, do not modify):
  * Backend cookie-authed WS proxy: `backend/app/api/routes/sessions.py` ws_terminal L354-L444 — get_current_user_ws cookie check L373; orchestrator-down close 1011 L389/L393; ownership check L394-L405 with identical 1008 close shape ('session_not_owned') for both 'session does not exist' AND 'session exists but not owned' (MEM113 existence-enumeration prevention); pre-accept close BEFORE accept() per MEM022; accept L409; _proxy_frames L478-L535 (dual pumps, browser→orch L478-L490, orch→browser L492-L505, code+reason 1:1 mirror L527-L535).
  * Backend WS auth helper: `backend/app/api/deps.py::get_current_user_ws` — cookie-first via `websocket.cookies.get(SESSION_COOKIE_NAME)` (MEM018/MEM067), pre-accept close on auth failure (MEM022).
  * Orchestrator WS-side: `orchestrator/orchestrator/routes_ws.py` session_stream L97-L458 — shared-secret two-key auth via `?key=` query string L107-L109 (MEM105/MEM096); registry lookup L114-L133 (1008 'session_not_found' on miss); attach frame L146-L173 (scrollback delivered, base64-encoded, capped to D017's `scrollback_max_bytes`); exec stream L175-L226; attach refcount L228-L240 + L453-L458 (MEM181 process-local AttachMap); dual pumps L242-L394; resize handler L341-L370 (`ws_malformed_resize`/`tmux_resize_failed` log keys); teardown L429-L458.
  * Orchestrator tmux resize: `orchestrator/orchestrator/sessions.py` resize_tmux_session L491-L526 — D017 last-writer-wins via `tmux refresh-client -C cols,rows`; non-existent-session yields harmless `can't find session`.
  * Orchestrator attach map: `orchestrator/orchestrator/attach_map.py` — process-local refcount keyed by session_id; D018 two-phase liveness check upstream of reaper kill consults this map; restart correctly drops every attach (MEM181).
  * Architecture refs: MEM092/MEM121 (tmux owns pty inside workspace container; orchestrator restart kills exec stream but tmux keeps shell + scrollback alive); MEM096 (two-key shared-secret rotation); MEM105 (orchestrator WS auth via `?key=` query string); MEM113 (identical close shape across 'doesn't exist' and 'exists but not owned'); MEM191 (httpx_ws upgrade rejection capture pattern); MEM193 (use `docker restart <ephemeral_name>` not `docker compose restart`).

Covered S05 demo elements and which test proves each (load-bearing assertions only):
  1. **Cookie-authed browser WS upgrade** → `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance` step 4 (alice signup → personal team → POST session → WS attach with cookies — `cookie_header = '; '.join(f'{n}={v}' for n,v in alice_cookies.items())`). Also `backend/tests/integration/test_m002_s01_e2e.py` (cookie WS attach + echo round-trip).
  2. **Attach frame with scrollback (empty for fresh session, populated on reattach)** → `orchestrator/tests/integration/test_ws_bridge.py::test_attach_frame_then_echo_roundtrip` (first frame is `attach`, scrollback decodes; input round-trip yields `data` frame containing 'hello'). Plus `test_disconnect_reconnect_preserves_scrollback` for the populated case.
  3. **Input frame → data frame echo round-trip** → `test_attach_frame_then_echo_roundtrip` ('echo hello\n' input → 'hello' in data frame within 5s).
  4. **Resize / SIGWINCH no-error** → `test_resize_frame_does_not_error` (send `{type:'resize',cols:120,rows:40}`, assert WS stays open, assert subsequent input still works).
  5. **Disconnect race — tmux survives WS close** → `test_disconnect_reconnect_preserves_scrollback` (echo hello → close WS → reconnect same sid → scrollback contains 'hello'). Plus `orchestrator/tests/integration/test_ws_attach_map.py::test_ws_close_emits_attach_unregistered` (clean attach_unregistered emission on close).
  6. **Cross-owner 1008 'session_not_owned' (no enumeration)** → `test_m002_s05_full_acceptance` step 7 (bob signs up; bob WS to alice's sid_a returns HTTP 403 byte-equal to bob WS to never-existed UUID; bob DELETE alice's sid → 404 with byte-equal body; backend's pre-accept `websocket.close(1008,'session_not_owned')` becomes HTTP 403 per MEM191).

Test runs (LIVE, against the real compose stack — matches verification mode of S01/T01, S03/T02, S04/T01):
  1. Boot the stack: `docker compose up -d db redis orchestrator` and ensure `perpetuity/workspace:test` is built per MEM141 (`docker build -f orchestrator/tests/fixtures/Dockerfile.test -t perpetuity/workspace:test orchestrator/workspace-image/`).
  2. From `backend/`: `POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance -v` — covers criteria 1, 2 (reattach), 5, 6 in one bundled run.
  3. From `orchestrator/`: `.venv/bin/pytest tests/integration/test_ws_bridge.py::test_attach_frame_then_echo_roundtrip tests/integration/test_ws_bridge.py::test_resize_frame_does_not_error tests/integration/test_ws_bridge.py::test_disconnect_reconnect_preserves_scrollback -v` — covers criteria 2 (fresh), 3, 4, 5 directly at the orchestrator boundary. NOTE per MEM209: `test_disconnect_reconnect_preserves_scrollback` may 503 on HEAD due to a pre-existing `_seed_session` FK seeding bug (committed before workspace_volume FK was wired at a4de0d1). If that trips, criterion 5 (within-process reconnect) is still proven by `test_m002_s05_full_acceptance` step 6's reconnect-after-restart flow (which transitively proves the simpler within-process reconnect case) — record as a Verification gap, do NOT modify the test.
  4. From `orchestrator/`: `.venv/bin/pytest tests/integration/test_ws_attach_map.py::test_ws_close_emits_attach_unregistered -v` — proves disconnect race cleanup at the attach-refcount layer.
  5. Capture verbatim PASSED lines from each run into T01-VERIFICATION.md (≥5 across all criteria).

T01-VERIFICATION.md MUST contain:
  * One `## Criterion:` section per S05 sub-criterion (≥5 sections covering the six demo elements; cookie auth + attach-frame can share a section since the same test proves both, OR split — either is fine as long as the count is ≥5).
  * ≥5 verbatim PASSED lines from live test runs.
  * File-and-line citations into the source modules listed above (`backend/app/api/routes/sessions.py`, `backend/app/api/deps.py`, `orchestrator/orchestrator/routes_ws.py`, `orchestrator/orchestrator/sessions.py`, `orchestrator/orchestrator/attach_map.py`).
  * A top-level `## Human action required: M003-umluob duplicates M002-jy6pde` block re-stating the same reconciliation hand-off filed by S01/T01, S03/T02, S04/T01 — keep the wording grep-stable: include the literal string `M003-umluob duplicates M002-jy6pde`. State this is the FOURTH filed hand-off and that the only remaining M003 slice is S06 (likely also verification-only).
  * Optional `## Verification gap:` sections honestly recording any non-blocking failures (e.g. MEM209 `_seed_session` FK seeding bug if it trips again, or MEM210 linuxkit loop-device-pool flake on unrelated reaper tests). Do NOT modify the test or source to make a citation pass — verification-only scope is strict.

Strict scope:
  * NO modification of backend/orchestrator source, compose files, Dockerfiles, or test code.
  * NO new alembic migrations, no new endpoints, no new test files in `/orchestrator/tests/` or `/backend/tests/`.
  * `git status --porcelain` after the task should show only `.gsd/milestones/M003-umluob/slices/S05/tasks/T01-*` files (and the engine-written summary/UAT for the slice).

If a cited test fails when re-run on HEAD, surface the failure as a real verification gap — write a `## Verification gap:` section in T01-VERIFICATION.md, include the failing pytest output, and stop the slice. Do NOT modify the test or the source to make it pass; that's a human-action call (re-plan toward true M003 scope or close M003 as undelivered).

## Inputs

- ``backend/app/api/routes/sessions.py``
- ``backend/app/api/deps.py``
- ``orchestrator/orchestrator/routes_ws.py``
- ``orchestrator/orchestrator/sessions.py``
- ``orchestrator/orchestrator/attach_map.py``
- ``orchestrator/tests/integration/test_ws_bridge.py``
- ``orchestrator/tests/integration/test_ws_attach_map.py``
- ``backend/tests/integration/test_m002_s05_full_acceptance_e2e.py``
- ``backend/tests/integration/test_m002_s01_e2e.py``

## Expected Output

- ``.gsd/milestones/M003-umluob/slices/S05/tasks/T01-VERIFICATION.md``
- ``.gsd/milestones/M003-umluob/slices/S05/tasks/T01-SUMMARY.md``

## Verification

test -f .gsd/milestones/M003-umluob/slices/S05/tasks/T01-VERIFICATION.md && [ "$(grep -c '^## Criterion:' .gsd/milestones/M003-umluob/slices/S05/tasks/T01-VERIFICATION.md)" -ge 5 ] && grep -q 'M003-umluob duplicates M002-jy6pde' .gsd/milestones/M003-umluob/slices/S05/tasks/T01-VERIFICATION.md && [ "$(grep -c 'PASSED' .gsd/milestones/M003-umluob/slices/S05/tasks/T01-VERIFICATION.md)" -ge 5 ] && [ -z "$(git status --porcelain | grep -v '^.. .gsd/' || true)" ]

## Observability Impact

- Signals added/changed: none (verification-only).
- How a future agent inspects this: open T01-VERIFICATION.md; `grep '^## Criterion:'` lists the sub-criteria; `grep PASSED` shows verbatim live-test evidence; `grep 'M003-umluob duplicates M002-jy6pde'` surfaces the unresolved human-action hand-off (now the FOURTH filing).
- Failure state exposed: a `## Verification gap:` section appears in T01-VERIFICATION.md if any cited test fails on HEAD — that is the on-disk durable signal that S05's demo no longer holds and the slice must NOT be marked complete. Pre-existing `_seed_session` FK seeding bug (MEM209) is the most likely trip wire; `test_m002_s05_full_acceptance` (which uses signup-driven user/team creation) is the load-bearing fallback proof if so.
