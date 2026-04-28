---
estimated_steps: 26
estimated_files: 2
skills_used: []
---

# T01: Verify S04 demo by citation against shipped M002 code; produce T01-VERIFICATION.md

Verification-only task following the pattern locked by M003/S01/T01 (MEM200/MEM201) and M003/S03/T02 (MEM205). The slice's single demo (POST creates tmux session → WS-style interface pipes 'echo hello' → restart orchestrator → reconnect same session_id → scrollback contains 'hello' → 'echo world' on same shell) is byte-for-byte the demo of `test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance` step (a) DURABILITY. Plus orchestrator-internal `test_ws_bridge.py::test_disconnect_reconnect_preserves_scrollback` proves the same shell + scrollback survives a WS disconnect+reconnect even without a full process restart. Plus M002/S04's bundled e2e `test_m002_s04_full_demo` covers the multi-tmux + scrollback proxy.

Citations to wire in T01-VERIFICATION.md (read-only, do not modify):
  * Tmux session model: `orchestrator/orchestrator/sessions.py` start_tmux_session L374-409, capture_scrollback L430-465, kill_tmux_session L468-488, resize_tmux_session L491-526, list_tmux_sessions L412-427.
  * Redis registry as durable source of truth (D013, no in-memory fallback): `orchestrator/orchestrator/redis_client.py` RedisSessionRegistry L51-265 (set_session L83-105, get_session L107-118, scan_session_keys L176-230, list_sessions L232-265).
  * Lifespan rebuilds state from Redis on boot (D013): `orchestrator/orchestrator/main.py` _lifespan L146-252 — registry binding L198-200; teardown order L240-252 (MEM170: stop_reaper FIRST, then registry/pool/docker).
  * HTTP routes: `orchestrator/orchestrator/routes_sessions.py` create_session L88-138, get_session_by_id L155-174, delete_session L177-226, get_scrollback L229-264, resize_session L267-303.
  * WS-style interface (the slice description's 'WS-style interface'): `orchestrator/orchestrator/routes_ws.py` session_stream L97-458 — auth L107-109, registry lookup L114-133, attach frame L146-173, exec stream L175-226, attach refcount L228-240, dual pumps L242-394, teardown L429-458.
  * MEM092/MEM121 architecture: tmux owns pty inside workspace container; orchestrator restart kills exec stream but tmux keeps shell + scrollback alive.
  * MEM181: process-local AttachMap empties on restart (correct — restart drops every WS attach because the exec stream dies; D018 two-phase check works correctly post-restart).

Test runs (LIVE, against the real compose stack — no mocks below the backend HTTP boundary; matches the verification mode used by S01/T01 and S03/T02):
  1. Boot the compose stack: `docker compose up -d db redis orchestrator` (and ensure `perpetuity/workspace:test` is built per MEM141).
  2. Run from `orchestrator/`: `.venv/bin/pytest tests/integration/test_ws_bridge.py::test_disconnect_reconnect_preserves_scrollback -v` — proves scrollback survives a WS disconnect+reconnect cycle (the within-process durability proof).
  3. Run from `backend/` (MEM041): `uv run pytest -m e2e tests/integration/test_m002_s04_e2e.py::test_m002_s04_full_demo -v` — proves multi-tmux/single-container, list, scrollback proxy.
  4. Run from `backend/` (MEM041): `uv run pytest -m e2e tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance -v` — proves DURABILITY (echo hello → restart orchestrator → reconnect same session_id → scrollback intact → echo world on same shell). This is the literal S04 demo.
  5. Capture verbatim PASSED lines from each run into T01-VERIFICATION.md.

T01-VERIFICATION.md MUST contain:
  * One `## Criterion:` section per S04 success-criterion bullet (the slice has one demo bullet, but split it into the four observable sub-criteria the cited tests prove: tmux ownership of pty; Redis registry as source-of-truth across restart; scrollback ≥100KB restored on reattach; same shell PID before/after restart).
  * ≥4 verbatim PASSED lines from live test runs (one per sub-criterion minimum).
  * File-and-line citations into the source modules listed above.
  * A top-level `## Human action required: M003-umluob duplicates M002-jy6pde` block re-stating the same reconciliation hand-off filed by S01/T01 and S03/T02 — keep the wording grep-stable: include the literal string `M003-umluob duplicates M002-jy6pde` so the slice-plan grep gate passes.
  * Optional: a list of any accepted divergences (recorded but not failing) — the nano_cpus=1.0 vCPU divergence (MEM203) is a provisioning concern from S01, not a tmux/reattach concern; do not record it here.

Strict scope:
  * NO modification of orchestrator source, compose files, Dockerfiles, or test code.
  * NO new alembic migrations, no new endpoints, no new test files in /orchestrator/tests/ or /backend/tests/.
  * `git status --porcelain` after the task should show only `.gsd/milestones/M003-umluob/slices/S04/tasks/T01-*` files (and the engine-written summary/UAT for the slice).

If a cited test fails when re-run on HEAD, surface the failure as a real verification gap — write a `## Verification gap:` section in T01-VERIFICATION.md, include the failing pytest output, and stop the slice. Do NOT modify the test or the orchestrator to make it pass; that's a human-action call (re-plan toward true M003 scope or close M003 as undelivered).

## Inputs

- ``orchestrator/orchestrator/sessions.py``
- ``orchestrator/orchestrator/redis_client.py``
- ``orchestrator/orchestrator/main.py``
- ``orchestrator/orchestrator/routes_sessions.py``
- ``orchestrator/orchestrator/routes_ws.py``
- ``orchestrator/tests/integration/test_ws_bridge.py``
- ``backend/tests/integration/test_m002_s04_e2e.py``
- ``backend/tests/integration/test_m002_s05_full_acceptance_e2e.py``

## Expected Output

- ``.gsd/milestones/M003-umluob/slices/S04/tasks/T01-VERIFICATION.md``
- ``.gsd/milestones/M003-umluob/slices/S04/tasks/T01-SUMMARY.md``

## Verification

test -f .gsd/milestones/M003-umluob/slices/S04/tasks/T01-VERIFICATION.md && [ "$(grep -c '^## Criterion:' .gsd/milestones/M003-umluob/slices/S04/tasks/T01-VERIFICATION.md)" -ge 4 ] && grep -q 'M003-umluob duplicates M002-jy6pde' .gsd/milestones/M003-umluob/slices/S04/tasks/T01-VERIFICATION.md && [ "$(grep -c 'PASSED' .gsd/milestones/M003-umluob/slices/S04/tasks/T01-VERIFICATION.md)" -ge 4 ] && [ -z "$(git status --porcelain | grep -v '^.. .gsd/' || true)" ]

## Observability Impact

- Signals added/changed: none (verification-only).
- How a future agent inspects this: open T01-VERIFICATION.md; `grep '^## Criterion:'` lists the sub-criteria; `grep PASSED` shows the verbatim live-test evidence; `grep 'M003-umluob duplicates M002-jy6pde'` surfaces the unresolved human-action hand-off.
- Failure state exposed: a `## Verification gap:` section appears in T01-VERIFICATION.md if any cited test fails on HEAD — that is the on-disk durable signal that S04's demo no longer holds and the slice must NOT be marked complete.
