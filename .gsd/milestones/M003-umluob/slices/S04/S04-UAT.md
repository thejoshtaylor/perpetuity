# S04: Tmux session model + Redis registry + reattach across orchestrator restart (verification-only over M002/S04 + M002/S05) — UAT

**Milestone:** M003-umluob
**Written:** 2026-04-25T15:11:35.565Z

## UAT — M003-umluob / S04: Tmux session model + Redis registry + reattach across orchestrator restart

This is a **verification-only** slice. The slice's deliverable is `.gsd/milestones/M003-umluob/slices/S04/tasks/T01-VERIFICATION.md` plus the cited test runs against the live compose stack. UAT below re-runs each cited test independently and asserts the verification artifact has the required structure.

### Preconditions

- Working directory: `/Users/josh/code/perpetuity` at HEAD `b1afe70` or later (working tree clean before starting).
- `.env` populated with `POSTGRES_PASSWORD`, `POSTGRES_USER`, `POSTGRES_DB`, `REDIS_PASSWORD=changethis` (MEM111).
- Compose stack: `db` (postgres:18 on host port 55432, MEM021), `redis`, and `orchestrator` services up and healthy.
- Required images present locally: `orchestrator:latest`, `perpetuity/workspace:latest`, `perpetuity/workspace:test` (the `perpetuity/workspace:test` image is required by the orchestrator integration tests per MEM141 — `docker pull perpetuity/workspace:test || docker tag perpetuity/workspace:latest perpetuity/workspace:test`).
- Linuxkit loop-device pool not exhausted (`docker volume prune` + restart Docker Desktop if `losetup` complains about device pool — see MEM210).
- Backend env: project `.venv` resolved by `uv run pytest` from `backend/` (MEM041).
- Orchestrator env: `orchestrator/.venv/bin/pytest`.

### Test 1 — Verification artifact structure (slice-plan grep gate)

**Goal:** Confirm `T01-VERIFICATION.md` satisfies all four grep-gate clauses.

```bash
F=.gsd/milestones/M003-umluob/slices/S04/tasks/T01-VERIFICATION.md
test -f $F && \
  [ "$(grep -c '^## Criterion:' $F)" -ge 4 ] && \
  grep -q 'M003-umluob duplicates M002-jy6pde' $F && \
  [ "$(grep -c 'PASSED' $F)" -ge 4 ] && \
  [ -z "$(git status --porcelain | grep -v '^.. .gsd/' || true)" ] && \
  echo "GATE PASS exit=0"
```

**Expected:** `GATE PASS exit=0`. Final counts: 4 `## Criterion:` sections (one per sub-criterion: tmux pty ownership / Redis source-of-truth / scrollback restoration / R008 sibling-skip); ≥1 occurrence of the duplication hand-off string; 13 PASSED lines (≥4 required); zero non-`.gsd/` git changes.

### Test 2 — Literal S04 demo via the bundled M002/S05 acceptance e2e (the headline test)

**Goal:** Prove the literal S04 demo end-to-end against the live compose stack — POST creates tmux session → WS-style stream pipes `echo hello` → restart orchestrator → reconnect with same `session_id` → scrollback contains `hello` → `echo world` on same shell with same PID.

```bash
cd backend
uv run pytest -m e2e tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance -v
```

**Expected outcome:**
- Exit code: `0`
- Output contains: `tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance PASSED`
- Output contains: `1 passed` (typically in ~25-35s)
- This run alone covers all four S04 sub-criteria.

**Edge cases the test covers:**
- Same shell PID before/after `docker restart <ephemeral_orchestrator>` (proves tmux owns the pty, not the orchestrator process).
- Same `session_id` UUID routes to correct `(container_id, tmux_session)` after orchestrator boot (proves Redis is the only durability surface — no in-memory cache survives the process death).
- `'hello' in scrollback` after restart (proves D017 `scrollback_max_bytes` capture-pane is delivered to the new attach frame).
- `'world'` reaches the same bash via second exec attach (proves no orphaned-state guard returned `""`).

### Test 3 — Multi-tmux + scrollback proxy (corroborating)

**Goal:** Prove the multi-tmux Redis user_sessions index correctly drives multi-session list/scrollback proxy through the backend.

```bash
cd backend
uv run pytest -m e2e tests/integration/test_m002_s04_e2e.py::test_m002_s04_full_demo -v
```

**Expected outcome:**
- Exit code: `0`
- Output contains: `tests/integration/test_m002_s04_e2e.py::test_m002_s04_full_demo PASSED`
- Typically completes in ~15-25s.

### Test 4 — R008 sibling-skip (orchestrator-side)

**Goal:** Prove `kill_tmux_session` kills only the named tmux session and the container survives if other tmux sessions remain.

```bash
cd orchestrator
.venv/bin/pytest tests/integration/test_reaper.py::test_reaper_keeps_container_with_surviving_session -v
```

**Expected outcome:**
- Exit code: `0`
- Output contains: `tests/integration/test_reaper.py::test_reaper_keeps_container_with_surviving_session PASSED`
- Typically completes in ~5-10s.

### Test 5 — Inspection surfaces match cited code

**Goal:** Confirm Redis registry is reachable, tmux owns the pty, and orchestrator structured logs carry the cited keys.

```bash
# Redis registry source-of-truth (D013):
docker exec perpetuity-redis-1 redis-cli -a "$REDIS_PASSWORD" KEYS 'session:*'
# Expected: zero or more session:<uuid> rows; each is a Redis hash readable via GET.

# Tmux ownership of pty (after running test 2 once with PYTEST_KEEP_CONTAINER):
docker ps --filter label=user_id --format '{{.Names}}'
# Pick one container name → docker exec <ws-container> tmux ls
# Expected: at least one tmux session line (proves tmux is the pty owner inside the workspace container).

# Structured log keys exercised by cited tests:
docker compose logs orchestrator | grep -E 'session_attached|session_detached|session_created|attach_registered|attach_unregistered'
# Expected: each key appears at least once after running test 2.
```

### Test 6 — Verification gap is recorded, not hidden

**Goal:** Confirm the report honestly documents the pre-existing `test_ws_bridge.py::_seed_session` test seeding bug rather than papering over it. (This is a quality check on the report itself.)

```bash
F=.gsd/milestones/M003-umluob/slices/S04/tasks/T01-VERIFICATION.md
grep -q '## Verification gap: orchestrator/tests/integration/test_ws_bridge.py' $F && \
  grep -q 'workspace_volume_store_unavailable: ForeignKeyViolationError' $F && \
  grep -q 'pre-existing test seeding bug' $F && \
  echo "GAP RECORDED HONESTLY"
```

**Expected:** `GAP RECORDED HONESTLY`. Confirms the failure is filed as a verification gap with root cause (FK was wired at a4de0d1 after the test was committed at bfc9cc6) and a concrete remediation pointer (port `_create_pg_user_team` from sibling test files).

### Acceptance

✅ **All six tests pass.** Slice S04 is verified by citation against shipped M002/S04 + M002/S05 code. The literal S04 demo end-to-end is the bundled `test_m002_s05_full_acceptance` step (a) DURABILITY which PASSED in 30.12s.

❌ **If Test 1 fails:** the verification artifact is structurally broken — re-render T01-VERIFICATION.md or re-run T01.

❌ **If Test 2 fails:** this is a real S04 regression (the literal demo doesn't pass). Stop the slice, surface the failure as a Verification gap, and re-plan toward repair before continuing.

❌ **If Test 3 or Test 4 fails on the same HEAD where Test 2 passes:** record as a corroborating-test regression but do not block S04 (the literal demo proof is Test 2). Investigate independently.

⚠️ **If Test 5's `tmux ls` line is empty after a passing Test 2:** the test's container teardown likely ran. Re-run Test 2 with `PYTEST_KEEP_CONTAINER=1` (if supported) or run a fresh `POST /v1/sessions` against the orchestrator and re-inspect.

### Human action still required (does NOT block this UAT passing)

The M003-umluob ≡ M002-jy6pde duplication hand-off is now filed in three verification reports (S01/T01, S03/T02, S04/T01) plus six memories (MEM200/201/202/205/208/211). A human owner must decide between (a) closing M003 as already-delivered (recommended) or (b) re-planning toward R009–R012 Projects/GitHub scope before S05 and S06 can be trusted to deliver net-new work. This UAT verifying S04's verification artifact is independent of that decision.
