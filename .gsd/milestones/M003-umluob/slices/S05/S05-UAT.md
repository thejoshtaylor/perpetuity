# S05: Cookie-authed WS bridge (browser → backend → orchestrator → tmux) — UAT

**Milestone:** M003-umluob
**Written:** 2026-04-25T15:32:16.159Z

# UAT — M003-umluob / S05 — Cookie-authed WS bridge (verification-by-citation)

**Scope:** This is a verification-only slice. There is no new functionality to UAT; UAT here is "re-run the cited tests on a clean compose stack and confirm the artifact is intact + the four hand-off conditions still hold."

## Preconditions

1. Working tree at HEAD b1afe70 (or a descendant with no source/compose/test changes since).
2. Docker daemon running. Required images present:
   ```
   docker images | grep -E '(orchestrator|backend|perpetuity/workspace).*latest|perpetuity/workspace.*test'
   ```
   Expect: `orchestrator:latest`, `backend:latest`, `perpetuity/workspace:latest`, `perpetuity/workspace:test`.
3. Compose stack up: `docker compose up -d db redis orchestrator` and `perpetuity-db-1` healthy.
4. Loop-device pool not exhausted (otherwise MEM210 will block volume-provisioning tests):
   ```
   docker exec $(docker compose ps -q orchestrator) losetup -a | wc -l
   ```
   Expect: <40. If ≥45, run `docker desktop restart` or clean orphan .img mounts before proceeding.
5. Env loaded from `.env` (POSTGRES_PASSWORD/POSTGRES_USER/POSTGRES_DB/REDIS_PASSWORD per MEM111).

## Test cases

### TC-1 — Slice gate command returns GATE_PASS

**Steps:**
1. From `/Users/josh/code/perpetuity`, run:
   ```bash
   test -f .gsd/milestones/M003-umluob/slices/S05/tasks/T01-VERIFICATION.md && \
   echo "criteria=$(grep -c '^## Criterion:' .gsd/milestones/M003-umluob/slices/S05/tasks/T01-VERIFICATION.md)" && \
   echo "passed=$(grep -c 'PASSED' .gsd/milestones/M003-umluob/slices/S05/tasks/T01-VERIFICATION.md)" && \
   grep -q 'M003-umluob duplicates M002-jy6pde' .gsd/milestones/M003-umluob/slices/S05/tasks/T01-VERIFICATION.md && echo "handoff=present" && \
   echo "non_gsd_changes=$(git status --porcelain | grep -v '^.. .gsd/' | wc -l)"
   ```

**Expected:**
- `criteria=6` (≥5 required)
- `passed=55` (≥5 required)
- `handoff=present`
- `non_gsd_changes=0`

### TC-2 — Cookie auth on WS bridge (criterion 1)

**Steps:**
1. From `backend/`:
   ```bash
   POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_ws_auth.py -v
   ```

**Expected:** 6/6 PASSED — every `get_current_user_ws` branch (missing/malformed/expired cookie, unknown/inactive user, valid happy path) returns the correct close shape pre-accept.

### TC-3 — Backend scrollback proxy carries attach-frame contents (criterion 2)

**Steps:**
1. From `backend/`:
   ```bash
   POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_sessions.py::test_scrollback_owner_returns_200_with_orchestrator_text tests/api/routes/test_sessions.py::test_scrollback_owner_with_empty_scrollback_returns_200_empty_string tests/api/routes/test_sessions.py::test_scrollback_missing_session_returns_404_byte_equal_to_non_owner tests/api/routes/test_sessions.py::test_scrollback_non_owner_returns_404_session_not_found tests/api/routes/test_sessions.py::test_scrollback_unauthenticated_returns_401 tests/api/routes/test_sessions.py::test_scrollback_orchestrator_unreachable_on_lookup_returns_503 tests/api/routes/test_sessions.py::test_scrollback_orchestrator_unreachable_on_fetch_returns_503 tests/api/routes/test_sessions.py::test_scrollback_logs_bytes_only_not_content -v
   ```

**Expected:** 8/8 PASSED — proxy delivers content, empty-scrollback round-trips as empty string, missing/non-owner return byte-equal 404s (no enumeration), 401 on missing cookie, 503 on orchestrator down at lookup AND fetch, log-shape sanitized.

### TC-4 — Attach frame + WS upgrade against real workspace container (criteria 2 + 3)

**Steps:**
1. From `orchestrator/`:
   ```bash
   .venv/bin/pytest tests/integration/test_ws_attach_map.py::test_ws_attach_emits_attach_registered -v
   ```

**Expected:** 1/1 PASSED — first frame on connect is type `attach`; `attach_registered session_id=<sid> count=1` lands within 5s.

### TC-5 — Disconnect-race cleanup at attach-refcount layer (criterion 5)

**Steps:**
1. From `orchestrator/`:
   ```bash
   .venv/bin/pytest tests/integration/test_ws_attach_map.py::test_ws_close_emits_attach_unregistered -v
   ```

**Expected:** 1/1 PASSED — `attach_unregistered session_id=<sid> count=0` lands within 2s of WS close.

### TC-6 — Cross-owner 1008 no-enumeration (criterion 6)

**Steps:**
1. From `backend/`:
   ```bash
   POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_sessions.py::test_h_ws_for_never_existed_sid_closes_1008_session_not_owned tests/api/routes/test_sessions.py::test_b_create_session_without_cookie_returns_401 tests/api/routes/test_sessions.py::test_c_create_session_for_other_team_returns_403 tests/api/routes/test_sessions.py::test_e_ws_without_cookie_closes_1008_missing_cookie -v
   ```
2. From `orchestrator/`:
   ```bash
   .venv/bin/pytest tests/integration/test_ws_bridge.py::test_unknown_session_id_closes_1008 -v
   ```

**Expected:** 4/4 PASSED on backend (never-existed-sid byte-equals not-yours close shape; auth/team-ownership policy on POST twin); 1/1 PASSED on orchestrator (orchestrator-side 1008 mirror).

### TC-7 — Bundled e2e (load-bearing; conditionally blocked by MEM210)

**Steps:**
1. From `backend/`:
   ```bash
   POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance -v
   ```

**Expected:** PASSED if loop-device pool has free slots. If FAILED with `503 orchestrator_status_500` and `losetup: failed to set up loop device`, this is MEM210 (linuxkit pool exhaustion), NOT an S05 code regression. The same HEAD b1afe70 PASSED this test in S04/T01 earlier today. Remediation: `docker desktop restart` or clean orphan workspace .img mounts, then re-run. Documented as a Verification gap in T01-VERIFICATION.md.

### TC-8 — Hand-off block is grep-stable

**Steps:**
1. ```bash
   grep -A 5 'Human action required: M003-umluob duplicates M002-jy6pde' .gsd/milestones/M003-umluob/slices/S05/tasks/T01-VERIFICATION.md
   ```

**Expected:** A markdown block stating this is the FOURTH filed hand-off (after S01/T01, S03/T02, S04/T01) and naming the two remediation options: (a) close M003 as already-delivered, or (b) `gsd_replan_slice` toward Projects/GitHub scope (R009–R012). The literal string `M003-umluob duplicates M002-jy6pde` MUST appear at least once.

### TC-9 — Edge case: re-running on a successor commit

**Steps:**
1. Verify HEAD has no source/compose/test changes vs b1afe70:
   ```bash
   git diff b1afe70..HEAD -- backend/ orchestrator/ docker-compose.yml
   ```

**Expected:** Empty diff. If the diff is non-empty, the cited file:line numbers in T01-VERIFICATION.md may have drifted — re-verify each citation against current line numbers before relying on this report. Verification-by-citation only holds while the cited code is byte-stable.

## Pass criteria

- TC-1 returns GATE_PASS (slice plan's gate command).
- TC-2 through TC-6 all return their expected PASSED counts.
- TC-7 either PASSES, OR fails with the MEM210 signature AND the failure is recorded as a Verification gap in T01-VERIFICATION.md (already done; do not modify the artifact to mask it).
- TC-8 confirms the hand-off block is intact.
- TC-9 confirms the citations are still valid against the current HEAD.

## Human action required (post-UAT)

Regardless of UAT outcome, the FOURTH duplication hand-off in T01-VERIFICATION.md is now standing. Before S06 proceeds, a human owner must reconcile M003-umluob: either close it as already-delivered (recommended; M003 then pivots to its true Projects/GitHub scope per R009–R012) or `gsd_replan_slice` toward net-new work. See MEM213 for the locked verification-only pattern across S01/S03/S04/S05.
