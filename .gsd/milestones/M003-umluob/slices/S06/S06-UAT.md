# S06: Final integrated acceptance — UAT

**Milestone:** M003-umluob
**Written:** 2026-04-25T21:23:51.033Z

# S06 — Final Integrated Acceptance UAT

## Goal
Prove the S06 headline demo (signup → cookie-authed WS attach → echo hello → close → orchestrator restart → reattach same session_id → scrollback contains 'hello' → echo world in same shell) end-to-end against the real compose stack, and prove the FIFTH and FINAL M003-umluob ≡ M002-jy6pde reconciliation hand-off is filed as a milestone-level escalation.

## Preconditions
- Working tree clean at HEAD on `main` (b1afe70 or later).
- Docker Desktop running with linuxkit loop-device pool intact: `docker exec <orchestrator_container> losetup -a | wc -l` reports < 47. If near 47, restart Docker Desktop before proceeding (per MEM214).
- Compose stack healthy: `docker compose ps` shows `perpetuity-db-1`, `perpetuity-redis-1`, `perpetuity-orchestrator-1` all healthy.
- `perpetuity/workspace:test` image is built per MEM141: `docker build -f orchestrator/tests/fixtures/Dockerfile.test -t perpetuity/workspace:test orchestrator/workspace-image/`.
- `.env` file is present at repo root with the orchestrator API key, Redis password, and Postgres credentials.

## Test Cases

### TC-1: Slice-plan verification gate passes
**Steps:**
1. From repo root, run:
   ```
   test -f .gsd/milestones/M003-umluob/slices/S06/tasks/T01-VERIFICATION.md \
     && [ "$(grep -c '^## Criterion:' .gsd/milestones/M003-umluob/slices/S06/tasks/T01-VERIFICATION.md)" -ge 6 ] \
     && grep -q 'M003-umluob duplicates M002-jy6pde' .gsd/milestones/M003-umluob/slices/S06/tasks/T01-VERIFICATION.md \
     && grep -q 'gsd_complete_milestone\|gsd_reassess_roadmap' .gsd/milestones/M003-umluob/slices/S06/tasks/T01-VERIFICATION.md \
     && [ "$(grep -c 'PASSED' .gsd/milestones/M003-umluob/slices/S06/tasks/T01-VERIFICATION.md)" -ge 6 ] \
     && [ -z "$(git status --porcelain | grep -v '^.. .gsd/' || true)" ] \
     && echo GATE_PASS
   ```

**Expected:**
- Output `GATE_PASS`. Exit code 0.

### TC-2: Bundled S06 e2e runs end-to-end against the live compose stack (load-bearing)
**Steps:**
1. From `backend/`:
   ```
   set -a && . ../.env && set +a
   POSTGRES_PORT=5432 uv run pytest -m e2e \
     tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance \
     -v --tb=short
   ```

**Expected:**
- `tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance PASSED [100%]`.
- Final summary line: `1 passed, 3 warnings in <duration>s` (≈31s baseline).
- Internally exercises (per the test's step-numbered structure): step 1 alice signup + personal team; step 2 backend POST /api/v1/sessions provisions a real container with volume; step 3 cookie-authed WS attach + echo hello + 'hello' in data frame; step 4 clean WS close; step 5 `docker restart <ephemeral_orchestrator>` + /healthz wait per MEM193; step 6 reattach to same session_id, decode attach frame's scrollback (base64), assert 'hello' present; step 7 'echo world\n' input, assert 'world' in next data frame from same shell; step 8 cross-owner 1008 byte-equal to never-existed UUID per MEM113.

### TC-3: Supporting backend tests PASSED (cookie auth + scrollback proxy + no-enumeration)
**Steps:**
1. From `backend/`:
   ```
   set -a && . ../.env && set +a
   POSTGRES_PORT=5432 uv run pytest \
     tests/api/routes/test_ws_auth.py \
     tests/api/routes/test_sessions.py::test_b_create_session_without_cookie_returns_401 \
     tests/api/routes/test_sessions.py::test_c_create_session_for_other_team_returns_403 \
     tests/api/routes/test_sessions.py::test_e_ws_without_cookie_closes_1008_missing_cookie \
     tests/api/routes/test_sessions.py::test_h_ws_for_never_existed_sid_closes_1008_session_not_owned \
     tests/api/routes/test_sessions.py::test_scrollback_owner_returns_200_with_orchestrator_text \
     tests/api/routes/test_sessions.py::test_scrollback_owner_with_empty_scrollback_returns_200_empty_string \
     tests/api/routes/test_sessions.py::test_scrollback_missing_session_returns_404_byte_equal_to_non_owner \
     tests/api/routes/test_sessions.py::test_scrollback_unauthenticated_returns_401 -v
   ```

**Expected:**
- 14 PASSED in <10s (≈8.57s baseline).
- Provides supplementary proof for criteria 1 (cookie auth), 4 (clean disconnect surfaces), and validates no-enumeration discipline (1008 byte-equal across never-existed and not-owned).

### TC-4: T01-VERIFICATION.md contains the milestone-level escalation block
**Steps:**
1. From repo root:
   ```
   grep -A 5 '^## Human action required: M003-umluob duplicates M002-jy6pde' \
     .gsd/milestones/M003-umluob/slices/S06/tasks/T01-VERIFICATION.md
   ```
2. Confirm the block contains:
   - The literal string `M003-umluob duplicates M002-jy6pde`.
   - The phrase `FIFTH and FINAL` (case-insensitive — locks the escalation level).
   - Both `gsd_complete_milestone` and `gsd_reassess_roadmap` named explicitly.

**Expected:**
- All three substrings present in or near the block.
- Block clearly states auto-mode CANNOT continue M003 productively beyond this point.

### TC-5: Strict scope held — zero source/compose/Dockerfile/test changes
**Steps:**
1. From repo root: `git status --porcelain | grep -v '^.. .gsd/' || echo "(empty)"`

**Expected:**
- Output is empty (or `(empty)` literal).
- Confirms verification-only scope: no backend/orchestrator/compose/Dockerfile/test code modified.

### TC-6 (Edge case): MEM214 environmental flake is recorded honestly, NOT masked
**Steps:**
1. Read T01-VERIFICATION.md and locate the `## Verification gap:` section.

**Expected:**
- A `## Verification gap:` section exists naming MEM214 (or MEM210) linuxkit loop-pool exhaustion.
- Includes verbatim pytest failure output for the bonus two-key rotation test.
- Includes pre-flight + post-bundled `losetup -a | wc -l` probe results documenting 46→47 of 47.
- States explicitly that this is environmental, NOT an S06 code regression.
- Confirms that the bundled e2e itself was unaffected (ran first while one slot was still free).

### TC-7 (Bonus, optional): Two-key rotation supplementary proof when loop pool is fresh
**Preconditions:** Restart Docker Desktop to free the loop pool (`docker exec <orchestrator_container> losetup -a | wc -l` < 40).

**Steps:**
1. From `backend/`:
   ```
   set -a && . ../.env && set +a
   POSTGRES_PORT=5432 uv run pytest -m e2e \
     tests/integration/test_m002_s05_two_key_rotation_e2e.py -v --tb=short
   ```

**Expected:**
- All steps PASSED (operationally proves the `ORCHESTRATOR_API_KEY_PREVIOUS` rotation path used during restart per MEM096).
- Bonus credit only — the bundled e2e in TC-2 is the load-bearing proof.

## Pass Criteria
TC-1 through TC-5 all PASS. TC-6 confirms honest disclosure of any environmental flake. TC-7 is optional bonus credit.

## Human Action Required (Out of Scope for This UAT)
M003-umluob is now in an auto-mode-terminal state. The next productive move is human-decided:
- **RECOMMENDED:** Call `gsd_complete_milestone` to close M003-umluob as already-delivered (every S0X demo is byte-for-byte covered by tests on main).
- **Alternative:** Call `gsd_reassess_roadmap` to replan M003-umluob toward R009-R012 Projects/GitHub scope per PROJECT.md.
