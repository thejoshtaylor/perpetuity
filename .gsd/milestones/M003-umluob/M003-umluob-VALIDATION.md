---
verdict: needs-attention
remediation_round: 0
---

# Milestone Validation: M003-umluob

## Success Criteria Checklist
### Acceptance Criteria

- [x] Orchestrator service exists in docker-compose.yml; sole `/var/run/docker.sock` mount; CAP_SYS_ADMIN | S01/T01-VERIFICATION static cite (compose L71/L102/L122) + 5 lifecycle tests + auth tests PASS
- [x] Redis 7-alpine service password-authed via shared secret, internal-only | Cited in S01/S04 verification (registry tests, REDIS_PASSWORD usage); covered by M002-shipped compose
- [x] perpetuity/workspace:latest builds + pulled on startup with hard-fail | `test_image_pull_ok_for_existing_image` + `test_image_pull_failed_exits_nonzero` PASS (S01)
- [x] Per-(user,team) provisioning with labels, deterministic name, quota=1, mem/cpu/pids limits | `test_create_session_provisions_container_and_tmux`, `test_second_session_reuses_container_multi_tmux`, `test_list_sessions_filters_by_user_team` PASS (S01); accepted divergence MEM203: `nano_cpus=1.0` vs spec 2.0
- [x] Loopback ext4 volumes; ENOSPC on cap; resize2fs grow; shrink-warning surface | `test_m002_s02_volume_cap_e2e` PASS, `test_m002_s03_settings_e2e` PASS (S02); accepted divergence MEM204: 4 GiB seed default vs spec 10 GiB
- [x] system_settings table + admin GET/PUT API gated by system_admin | `test_s05_migration` + admin PUT/403-non-admin assertions in `test_m002_s03_settings_e2e` PASS (S02)
- [x] Tmux-inside-container session model; pty survives WS disconnect AND orchestrator restart; ≥100KB scrollback restored | `test_m002_s05_full_acceptance` PASS in 30.12s + 31.43s (S04, S06); same shell PID before/after restart asserted
- [x] Backend WS `/api/v1/ws/terminal/{session_id}` cookie-authed; proxies to orchestrator shared-secret WS; JSON frames | `test_ws_auth.py` 6/6, `test_sessions.py` policy 4/4, scrollback 8/8, `test_ws_attach_map` 2/2 PASS (S05)
- [x] Idle reaper kills tmux + container; volume persists; two-phase check vs active WS | `test_reaper_*` 4/4 PASS (S03); MEM210 environmental flake on `test_reaper_skips_attached_session` (passed earlier same HEAD)
- [x] Final integrated acceptance: signup → WS → echo hello → restart orchestrator → reconnect → scrollback intact → echo world same shell | `test_m002_s05_full_acceptance` PASS 31.43s (S06) covers all 8 sub-criteria bundled
- [x] Full backend test suite green; M001 patterns preserved (cookie-auth, MEM016 autouse, MEM017) | `test_s04_migration` MEM016 release verified (S02); 22 backend tests PASS (S05)

### Caveats
- All 6 slices were verification-only over already-shipped M002-jy6pde code; M003-umluob ≡ M002-jy6pde duplication hand-off filed (MEM200/201/202/205/208/211/213/216).
- CONTEXT.md is a blocker placeholder (depth-verification gate failed); roadmap success criteria sourced from M003-umluob-ROADMAP.md.
- Accepted divergences: MEM203 (1.0 vCPU vs 2.0 spec), MEM204 (4 GiB volume default vs 10 GiB spec) — recorded, not failing.

## Slice Delivery Audit
All 6 slices have SUMMARY.md files with `verification_result: passed` recorded:

| Slice | SUMMARY | Verdict | Notes |
|---|---|---|---|
| S01 | present | passed | Verification-only; T01-VERIFICATION.md with 16 PASS lines for 7 success criteria |
| S02 | present | passed | Verification-only; volumes + system_settings live cited; MEM016 autouse confirmed |
| S03 | present | passed | Verification-only; idle reaper 4/4 PASS, two-phase check verified; MEM210 env flake noted |
| S04 | present | passed | Verification-only; tmux pty + Redis durability + scrollback restore proven via bundled e2e |
| S05 | present | passed | Verification-only; backend WS bridge 22 tests PASS; ownership/disconnect race covered |
| S06 | present | passed | Bundled headline e2e `test_m002_s05_full_acceptance` PASS 31.43s on commit b7ea8c6 |

No slice has outstanding follow-ups blocking validation. All slices uniformly escalate the milestone-level reconciliation (M003 ≡ M002 duplication) as a hand-off requiring human action — auto-mode-terminal by design.

## Cross-Slice Integration
## Reviewer B — Cross-Slice Integration

All 10 boundaries from the M003 ROADMAP Boundary Map are honored by citation:

| Boundary | Producer | Consumer | Status |
|---|---|---|---|
| Browser ↔ Backend WS `/api/v1/ws/terminal/{id}` (cookie auth, JSON frames) | S05 (`sessions.py::ws_terminal` L354–444 + `_proxy_frames` L458–539; 22 tests PASS) | S06 bundled e2e exercises end-to-end | HONORED |
| Backend ↔ Orchestrator HTTP (`X-Orchestrator-Key`) | S01 (auth tests + two-key rotation), S02 (admin shrink-preview) | S04 (`test_m002_s04_full_demo` PASS), S06 bundled e2e | HONORED |
| Backend ↔ Orchestrator WS `/v1/sessions/{id}/stream` (shared-secret) | S05 (`routes_ws.py::session_stream` L97–458 + AttachMap; orchestrator-side tests PASS) | S05/S06 backend `_proxy_frames` consumes via bundled e2e | HONORED |
| Orchestrator ↔ Docker daemon (sole socket holder) | S01 (compose L102 + provisioning tests PASS) | S03/S04 reaper + bundled e2e using real Docker | HONORED |
| Orchestrator ↔ Redis (session registry hash) | S04 (`redis_client.py::RedisSessionRegistry` L51–265; durability proven) | S03 reaper + S06 bundled e2e (same session_id after restart) | HONORED |
| Orchestrator ↔ Postgres (read-only system_settings) | S02 (`_resolve_idle_timeout_seconds` + `volume_size_gb` reads) | S02 (`test_m002_s03_settings_e2e` PUT-then-provision PASS) | HONORED |
| Backend ↔ Postgres (admin GET/PUT settings) | S02 (`admin.py` PUT + 403 assertion) | S02 (`test_m002_s03_settings_e2e` PASS) | HONORED |
| Orchestrator ↔ host filesystem (loopback `.img`) | S02 (`volumes.py` + ENOSPC test PASS) | S03 (.img survives reap; bundled e2e PASS) | HONORED |
| Container internals: tmux pty + bind mount | S04 (`sessions.py` L374–526; same-shell-PID asserted) | S06 bundled e2e (echo hello → restart → scrollback → echo world same shell) | HONORED |
| Privilege boundary (orchestrator-only Docker/CAP_SYS_ADMIN) | S01 (compose L71/L102/L122) | S05/S06 (backend never touches Docker) | HONORED |

S06 headline test `test_m002_s05_full_acceptance` PASSED 31.43s on HEAD b1afe70 (commit b7ea8c6) covering all 8 sub-criteria in one bundled run.

**Verdict: PASS** — all boundaries honored.

## Requirement Coverage
## Reviewer A — Requirements Coverage

| Requirement | Status | Evidence |
|---|---|---|
| R042 — Pty sessions outlive WS via tmux-inside-container, ≥100KB scrollback restored on reattach, survives orchestrator restart | COVERED | S04/S05/S06 cite `test_m002_s05_full_acceptance` PASS 31.43s end-to-end; same shell PID before/after restart |
| R043 — Orchestrator holds sole Docker socket, shared-secret-authed HTTP+WS API | COVERED | S01 cites docker-compose L71/L102/L122; auth tests 10 PASS, two-key rotation tests PASS |
| R044 — Per-container resource limits + per-volume hard size cap via loopback ext4, sysadmin-adjustable | COVERED | S02 cites `test_m002_s02_volume_cap_e2e.py` (ENOSPC) + `test_m002_s03_settings_e2e.py` (PUT raises cap, next provision uses new value) PASS |
| R045 — system_settings table + GET/PUT /api/v1/admin/settings gated by role==system_admin, workspace_volume_size_gb seeded | COVERED | S02 cites s05_system_settings.py migration + admin.py PUT handler + test_s05_migration.py PASS; non-admin PUT 403 verified; MEM204 records 4 GiB vs 10 GiB seed drift (non-blocking) |
| R005-R008 (M002 carry, idle reaper, multi-tmux) | COVERED | S03 cites 4 reaper tests PASS + bundled M002/S05 acceptance PASS 30.46s |
| R009 — Projects live at team level, link to GitHub repo (M003/S01 mapping) | MISSING | All 6 M003 SUMMARYs explicitly state "Requirements Advanced: None" — M003 actual scope was terminal infra (verifying M002), not Projects/GitHub |
| R010 — Repo cloned into team workspace per project (M003/S02 mapping) | MISSING | No slice touched repo cloning |
| R011 — GitHub webhooks for push/PR/tag events (M003/S03 mapping) | MISSING | No slice touched webhook handling |
| R012 — Team admins configure GitHub connections (M003/S01 mapping) | MISSING | No slice touched GitHub OAuth/PAT |

**Verdict: NEEDS-ATTENTION** — Terminal-infra requirements (R042–R045 + R005–R008 carry) are fully covered. R009–R012 are mapped to M003 slices in REQUIREMENTS.md but have zero evidence; M003's actual delivered scope was terminal infrastructure (verification-only over M002-jy6pde). This is a milestone scope-mapping mismatch, not a verification gap on the delivered scope.

## Verification Class Compliance
### Verification Classes

| Class | Planned Check | Evidence | Verdict |
|---|---|---|---|
| Contract | Each slice ships integration tests against real compose stack, no mocking | All 6 slices cite live compose-stack runs (orchestrator + backend integration suites); zero mocked Docker per S01–S06 SUMMARYs | PASS |
| Integration | S06 dedicated end-to-end pytest | `backend/tests/integration/test_m002_s05_full_acceptance_e2e.py::test_m002_s05_full_acceptance` PASS 31.43s, 8/8 sub-criteria bundled (S06) | PASS |
| Operational | INFO/WARNING/ERROR log keys, /healthz, UUID-only IDs in logs | INFO keys (session_proxy_open, attach_registered, orchestrator_starting/ready, volume_provisioned, reaper_*), WARNING keys (system_settings_lookup_failed, reaper_tick_failed), close codes 1008/1011/1000 cited across S01–S06; /healthz wait in bundled e2e; UUID-only redaction (MEM134) preserved | PASS |
| UAT | No human UAT — S06 integration test is the literal user | S06 bundled e2e PASS end-to-end (signup → WS → echo → restart → reconnect → scrollback → echo world) | PASS |

All 4 verification classes covered.


## Verdict Rationale
All 11 milestone success criteria PASS by citation against live compose-stack runs (`test_m002_s05_full_acceptance` PASS 31.43s on HEAD b1afe70 covers all 8 headline sub-criteria in one bundled run). All 10 ROADMAP boundaries honored. All 4 verification classes (Contract/Integration/Operational/UAT) covered. Two accepted divergences recorded but non-blocking (MEM203: 1.0 vCPU vs 2.0 spec; MEM204: 4 GiB vs 10 GiB volume default seed). The verdict is needs-attention rather than pass because REQUIREMENTS.md maps R009–R012 (Projects/GitHub scope) to M003 slices but no M003 slice advanced them — M003's actual delivered scope was terminal infrastructure verification over already-shipped M002-jy6pde code. This is a known milestone scope-mapping mismatch escalated by every slice (MEM200/202/205/208/211/216) requiring human reconciliation between (a) closing M003 as delivered (recommended; M003 then pivots) and (b) replanning M003 toward R009–R012. Auto-mode cannot make this call. The verdict is not needs-remediation because no remediation slice would resolve the mismatch — only a human-driven reassess_roadmap or complete_milestone call can.
