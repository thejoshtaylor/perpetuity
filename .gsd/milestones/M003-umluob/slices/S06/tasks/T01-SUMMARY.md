---
id: T01
parent: S06
milestone: M003-umluob
key_files:
  - .gsd/milestones/M003-umluob/slices/S06/tasks/T01-VERIFICATION.md
key_decisions:
  - Verification-only slice — no source/compose/Dockerfile/test-code changes, per slice plan strict scope rule mechanically enforced by the verify command
  - Cited backend/tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance as the literal S06 demo (commit b7ea8c6 named 'Add bundled M002 final acceptance e2e')
  - Filed FIFTH and FINAL M003-umluob ≡ M002-jy6pde reconciliation hand-off as MILESTONE-LEVEL escalation: explicitly named gsd_complete_milestone (RECOMMENDED) or gsd_reassess_roadmap (alternative) as the only two valid next moves
  - Honored MEM214 honestly: bonus two-key rotation test blocked by post-bundled loop-pool exhaustion (47/47), recorded as Verification gap with verbatim pytest output and pre-flight/post-bundled probe evidence — did NOT modify test or source to mask the environmental flake
  - Cited orchestrator/orchestrator/redis_client.py (actual filename) instead of orchestrator/orchestrator/registry.py (slice-plan typo) — recorded as a minor factual mismatch in the report's Known accepted divergences
duration: 
verification_result: mixed
completed_at: 2026-04-25T21:19:25.923Z
blocker_discovered: false
---

# T01: Verify S06 final-integrated-acceptance demo by citation against the bundled M002/S05 e2e and escalate the M003-umluob ≡ M002-jy6pde duplication hand-off to milestone-level (FIFTH and FINAL filing).

**Verify S06 final-integrated-acceptance demo by citation against the bundled M002/S05 e2e and escalate the M003-umluob ≡ M002-jy6pde duplication hand-off to milestone-level (FIFTH and FINAL filing).**

## What Happened

FIFTH and FINAL verification-only slice in M003-umluob, locking the pattern established across S01/T01 (MEM200/201), S03/T02 (MEM205), S04/T01 (MEM206/208), and S05/T01 (MEM212/213). The S06 demo (signup → backend POST creates session → cookie-authed WS attach → 'echo hello\n' → 'hello' in data frame → close WS → docker restart ephemeral orchestrator → /healthz wait → reconnect WS to same session_id → attach frame's scrollback contains 'hello' → 'echo world\n' succeeds in same shell → bonus cross-owner 1008 byte-equal MEM113) is the literal demo of `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance` — a test already shipped to main (commit b7ea8c6).

Wrote `.gsd/milestones/M003-umluob/slices/S06/tasks/T01-VERIFICATION.md` (citation-by-test report). Eight `## Criterion:` sections cover all S06 demo bullets with file:line citations into the bundled e2e (step 2 L564–L580 signup, step 4 L593–L648 POST + WS attach + echo hello, step 5 L650–L660 docker restart + healthz wait, step 6 L662–L699 reattach scrollback durability + echo world, step 7 L701–L784 cross-owner 1008 byte-equal) and into the production code paths it exercises (backend/app/api/routes/sessions.py ws_terminal L354–L444 cookie auth + ownership check + accept + proxy_frames; backend/app/api/routes/sessions.py _proxy_frames L458–L539 dual pumps with 1:1 close-code+reason mirror; orchestrator/orchestrator/routes_ws.py session_stream L97–L458 with auth L107–L109, registry lookup L114–L133, attach frame send L146–L173, exec stream open L175–L226, attach refcount register L228–L240 + finally unregister L449–L458, dual pumps L252–L379; orchestrator/orchestrator/sessions.py start_tmux_session L374–L409 + capture_scrollback L430–L465 + kill_tmux_session L468–L488; orchestrator/orchestrator/redis_client.py RedisSessionRegistry L51–L118 — note: actual filename is redis_client.py, NOT registry.py as the slice plan input list incorrectly stated; orchestrator/orchestrator/attach_map.py L38–L77 process-local refcount per MEM181).

Live test run on the real compose stack: pre-flight loop-device probe per MEM214 reported 46 of 47 in use (at threshold but with one free slot, enough for the bundled e2e's single workspace-volume provision). The bundled e2e PASSED end-to-end in 31.43s (verbatim PASSED line captured). Post-run probe reported 47 of 47 in use; the bonus supplementary `test_m002_s05_two_key_rotation` then failed at step 8 (bob POST via backend_previous → 503 orchestrator_status_500) because it provisions three fresh workspace volumes (one per backend) with no free loop slots — recorded as a Verification gap NOT an S06 regression. Honored MEM214's escape clause: did NOT modify the test or source to mask the environmental flake. Captured 14 supplementary PASSED tests for cookie auth (test_ws_auth.py — 6), no-cookie/team-ownership policy (test_sessions.py — 3), and scrollback proxy lifecycle (test_sessions.py — 5).

Filed the FIFTH and FINAL `M003-umluob duplicates M002-jy6pde` reconciliation hand-off as a milestone-level escalation block at the top of T01-VERIFICATION.md. Auto-mode CANNOT continue M003 productively beyond this point — explicitly named the two valid next moves: `gsd_complete_milestone` (RECOMMENDED, close as already-delivered since every S0X demo is byte-for-byte covered by tests on main) or `gsd_reassess_roadmap` (alternative, replan toward R009-R012 Projects/GitHub scope per PROJECT.md). Captured MEM216 to record the milestone-level escalation state.

Slice-plan verification gate ran from repo root and all conditions PASS:
- Criterion sections: 8 (≥6 required) ✅
- PASSED lines: 57 (≥6 required) ✅
- Duplication string `M003-umluob duplicates M002-jy6pde`: 6 occurrences (≥1 required) ✅
- Both `gsd_complete_milestone` AND `gsd_reassess_roadmap` named: ✅
- Strict scope (no non-.gsd modifications): empty git status outside .gsd/ ✅

Strict scope held: zero modifications to backend/, orchestrator/, docker-compose.yml, Dockerfiles, or any test code. Working tree clean at HEAD b1afe70 before this report; the only changes are inside `.gsd/milestones/M003-umluob/slices/S06/`.

## Verification

Slice plan's verification gate ran from repo root: `test -f .gsd/.../T01-VERIFICATION.md && [ "$(grep -c '^## Criterion:' file)" -ge 6 ] && grep -q 'M003-umluob duplicates M002-jy6pde' file && grep -q 'gsd_complete_milestone\|gsd_reassess_roadmap' file && [ "$(grep -c 'PASSED' file)" -ge 6 ] && [ -z "$(git status --porcelain | grep -v '^.. .gsd/' || true)" ]` — GATE PASS.

Live test execution against the live compose stack (perpetuity-db-1 + perpetuity-redis-1 + perpetuity-orchestrator-1 healthy, all required images present): `set -a && . ../.env && set +a && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance -v --tb=short` returned `tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance PASSED [100%]` and `1 passed, 3 warnings in 31.43s` — covers all 8 S06 sub-criteria in one bundled run. Supporting backend tests: `set -a && . ../.env && set +a && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_ws_auth.py tests/api/routes/test_sessions.py::{test_b,test_c,test_e,test_h,test_scrollback_*} -v` returned 14 PASSED in 8.57s. Verbatim PASSED lines for both runs captured in T01-VERIFICATION.md.

Verification gap recorded honestly: bonus `test_m002_s05_two_key_rotation` failed at step 8 with 503 orchestrator_status_500 due to MEM214 linuxkit loop-device-pool exhaustion (47/47 in use post-bundled-run); environmental flake, not an S06 code regression. Documented in `## Verification gap:` section with verbatim pytest output and pre-flight/post-bundled loop-probe evidence.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `test -f .gsd/milestones/M003-umluob/slices/S06/tasks/T01-VERIFICATION.md && [ "$(grep -c '^## Criterion:' .gsd/milestones/M003-umluob/slices/S06/tasks/T01-VERIFICATION.md)" -ge 6 ] && grep -q 'M003-umluob duplicates M002-jy6pde' .gsd/milestones/M003-umluob/slices/S06/tasks/T01-VERIFICATION.md && grep -q 'gsd_complete_milestone\|gsd_reassess_roadmap' .gsd/milestones/M003-umluob/slices/S06/tasks/T01-VERIFICATION.md && [ "$(grep -c 'PASSED' .gsd/milestones/M003-umluob/slices/S06/tasks/T01-VERIFICATION.md)" -ge 6 ] && [ -z "$(git status --porcelain | grep -v '^.. .gsd/' || true)" ]` | 0 | ✅ pass | 200ms |
| 2 | `set -a && . ../.env && set +a && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance -v --tb=short` | 0 | ✅ pass (1 passed in 31.43s — covers all 8 S06 sub-criteria) | 31430ms |
| 3 | `set -a && . ../.env && set +a && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_ws_auth.py tests/api/routes/test_sessions.py::test_b_create_session_without_cookie_returns_401 tests/api/routes/test_sessions.py::test_c_create_session_for_other_team_returns_403 tests/api/routes/test_sessions.py::test_e_ws_without_cookie_closes_1008_missing_cookie tests/api/routes/test_sessions.py::test_h_ws_for_never_existed_sid_closes_1008_session_not_owned tests/api/routes/test_sessions.py::test_scrollback_owner_returns_200_with_orchestrator_text tests/api/routes/test_sessions.py::test_scrollback_owner_with_empty_scrollback_returns_200_empty_string tests/api/routes/test_sessions.py::test_scrollback_missing_session_returns_404_byte_equal_to_non_owner tests/api/routes/test_sessions.py::test_scrollback_unauthenticated_returns_401 -v` | 0 | ✅ pass (14 passed in 8.57s — supporting cookie auth + scrollback proxy + no-enumeration proofs) | 8570ms |
| 4 | `set -a && . ../.env && set +a && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s05_two_key_rotation_e2e.py -v --tb=short` | 1 | ❌ fail (MEM214 linuxkit loop-pool exhaustion — environmental, NOT S06 regression; recorded as Verification gap) | 17640ms |

## Deviations

Slice plan's input list cited `orchestrator/orchestrator/registry.py` as the Redis-backed registry; the actual filename on HEAD b1afe70 is `orchestrator/orchestrator/redis_client.py` (class `RedisSessionRegistry`, same module also referenced this way in the S04/T01 verification artifact). Cited the actual filename in T01-VERIFICATION.md and recorded the typo as a follow-up note for the human owner reconciling M003. No code change required; cosmetic only.

Bonus two-key rotation supplementary proof (`test_m002_s05_two_key_rotation`) recorded as a Verification gap rather than a PASSED bonus citation because MEM214 linuxkit loop-pool exhaustion (47/47 in use post-bundled-run) blocked it at step 8. The slice plan explicitly anticipated this with the MEM214 escape clause ("if MEM210 trips and the bundled e2e fails… record verbatim pytest output as a Verification gap section AND find alternative-proof PASSED tests"); the bundled e2e itself was unaffected because it ran first while one slot was still free, so the load-bearing proof for all 8 sub-criteria still PASSED.

## Known Issues

M003-umluob is now in a stable but auto-mode-terminal state. Five verification-only slices have filed the same `M003-umluob duplicates M002-jy6pde` reconciliation hand-off; no further M003 slices remain to file it in. The next productive move requires human intervention — `gsd_complete_milestone` (RECOMMENDED) or `gsd_reassess_roadmap` (alternative). Auto-mode advancing into a sixth slice would be a regression in this state machine.

Three independent follow-ups exist that the human owner reconciling M003 should consider filing as side issues (independent of the M003 reconciliation itself): (1) MEM209 — `_seed_session` FK seeding gap in `orchestrator/tests/integration/test_ws_bridge.py`; (2) MEM210/MEM214 — orchestrator-side cleanup hook for orphan linuxkit loop devices, or a pytest fixture asserting free loop slots before booting an ephemeral orchestrator; (3) slice-plan typo — references to `orchestrator/orchestrator/registry.py` should read `orchestrator/orchestrator/redis_client.py`.

## Files Created/Modified

- `.gsd/milestones/M003-umluob/slices/S06/tasks/T01-VERIFICATION.md`
