---
verdict: needs-attention
remediation_round: 0
---

# Milestone Validation: M002-jy6pde

## Success Criteria Checklist
## Success Criteria

- [x] **Signed-up user can WS to /api/v1/ws/terminal/<new_session_id>, run `echo hello`, see `hello`** — S01 T06 e2e (`test_m002_s01_e2e.py`) PASSED 19.16s; ANSI-stripped data frames assert `hello\r\n`.
- [x] **`docker compose restart orchestrator`; reconnect SAME session_id; see prior scrollback in attach frame; same shell PID** — S01 T06 (pid_before == pid_after, "hello" in scrollback) and re-validated by S05 T01 `test_m002_s05_full_acceptance_e2e.py` against an ephemeral orchestrator.
- [x] **system_admin PUT /api/v1/admin/settings/workspace_volume_size_gb shrink → 200 with warnings; affected rows keep old cap; new uses new cap** — S03 T04 e2e step (3) admin PUT 4→1 GiB, alice unchanged + warnings emitted; step (4) bob fresh signup gets 1 GiB; step (5) ENOSPC at 1 GiB.
- [x] **Multiple WS sessions for same (user, team) attach to single shared container as distinct tmux sessions; share filesystem** — S04 T04 e2e (`test_m002_s04_e2e.py`): orchestrator returns `created==True` then `False` for sid_a/sid_b in same container; sid_a writes marker, sid_b reads via `cat` (R008 validated).
- [x] **GET /api/v1/sessions returns caller's live sessions; DELETE kills the tmux session** — S04 T04 e2e steps (GET returns {sid_a,sid_b}; DELETE sid_a leaves sid_b active); S05 T01 capstone exercises both.
- [x] **Idle reaper kills containers ONLY when both Redis last_activity exceeds idle timeout AND active-WS map confirms no live attach** — S04 T02 reaper integration (9/9 tests covering kill-idle-no-attach, skip-attached, skip-non-idle); S05 T01 verifies end-to-end (DELETE → idle_timeout=3 → `docker ps` empty).
- [x] **Orchestrator pulls perpetuity/workspace:latest once on startup; pull failure is a boot blocker** — S01 T02 (3 image-pull integration tests); orchestrator emits `image_pull_failed` and exits non-zero on pull failure.
- [x] **Sensitive log lines emit UUIDs only — never email or full name** — Every slice e2e ends with a `docker compose logs orchestrator backend` redaction sweep that grep-fails on email/full_name. S01/S02/S03/S04/S05 all confirm zero matches.
- [x] **Full backend + orchestrator integration suite passes against real Postgres + Redis + Docker; ≤ 60s wall clock** — S01 19.16s, S02 17.87s, S03 9.37s, S04 19.87s, S05 capstone 46s combined. Each slice well under 60s budget.
- [x] **Migrations for system_settings and workspace_volume round-trip cleanly up/down (M001 MEM016 lock-hazard pattern preserved)** — `test_s04_migration.py` (4/4) for workspace_volume; `test_s05_migration.py` (3/3) for system_settings. Both use the M001 lock-hazard alembic discipline.

**All 10 success criteria are satisfied with concrete passing-test evidence.**

## Slice Delivery Audit
## Slice Delivery Audit

| Slice | Status | SUMMARY.md | Assessment | Notes |
|-------|--------|------------|------------|-------|
| S01 | complete | ✅ present, frontmatter `verification_result: passed` | passed via slice-level e2e (T06) | WS frame protocol locked here; one self-flagged known limitation: `test_resize_succeeds` accepts `200 or 500` pending S04 — see cross-slice gap below. |
| S02 | complete | ✅ present | passed (T01 migration 4/4 + T04 e2e ENOSPC + neighbor isolation) | Loopback ext4 cap; orchestrator escalated to `privileged: true` (LOOP_SET_FD EPERM under SYS_ADMIN). |
| S03 | complete | ✅ present | passed (17 admin_settings tests + T04 e2e shrink + warnings + fresh-signup new cap) | system_settings table + per-key validators + D015 partial-apply shrink. |
| S04 | complete | ✅ present | passed (9/9 reaper tests + T04 e2e two-WS share + DELETE + reap) | AttachMap + two-phase D018 reaper + scrollback proxy + idle_timeout_seconds validator. |
| S05 | complete | ✅ present | passed (T01 full acceptance e2e PASSED, T02 two-key rotation e2e PASSED) | Bundled capstone: durability + reaper + ownership + redaction sweep + key rotation. |

**All five slices have a SUMMARY.md and a passing assessment. No missing artifacts. One minor cross-slice handoff gap (S01→S04 resize hardening — captured in cross-slice integration finding) but it's non-blocking.**

## Cross-Slice Integration
## Reviewer B — Cross-Slice Integration

| Boundary | Producer (slice + evidence) | Consumer (slice + evidence) | Status |
|---|---|---|---|
| Orchestrator HTTP `POST /v1/sessions` | S01 provides | S02 surfaces 500 volume_provision_failed; S04/S05 e2es exercise | OK |
| Orchestrator HTTP `GET /v1/sessions` | S01 provides | S04/S05 e2es exercise (with documented MEM174 team_id caveat) | OK |
| Orchestrator HTTP `DELETE /v1/sessions/{id}` | S01 provides | S04/S05 e2es exercise | OK |
| Orchestrator `POST /v1/sessions/{id}/scrollback` | S01 provides 100 KB hard-cap | S04 backend GET scrollback proxies orchestrator POST | OK |
| Orchestrator `POST /v1/sessions/{id}/resize` | S01 provides | Roadmap promised "S01 minimum, hardened S04" — S04 SUMMARY does not document any resize hardening; S01 known-limitation flags `test_resize_succeeds` as accepting `200 or 500` pending S04 | **GAP (minor — non-blocking)** |
| Orchestrator WS `/v1/sessions/{id}/stream` | S01 provides locked frame protocol | S04 instruments routes_ws.py with attach_map; S05 T01/T02 exercise | OK |
| Orchestrator → Docker socket; CAP_SYS_ADMIN→privileged in S02 | S01 establishes (D005); S02 escalates to `privileged: true` (LOOP_SET_FD EPERM) | S02–S05 consume; S04 reaper uses Docker | OK |
| Orchestrator → Redis (session registry) | S01 provides set/get/update_last_activity | S04 adds `scan_session_keys()` for reaper; S05 confirms | OK |
| Orchestrator → Postgres `workspace_volume` | S02 provides table + ensure_volume_for | S03 partial-apply shrink reads; S04 reaper preserves rows (D015); S05 T01 asserts persistence | OK |
| Orchestrator → Postgres `system_settings` | S03 provides + `_resolve_default_size_gb` | S04 mirrors with `_resolve_idle_timeout_seconds`; S04 admin adds idle_timeout_seconds validator | OK |
| Orchestrator → Postgres `team_member` (ownership) | Backend-side guard `assert_caller_is_team_member` (S01) | S04/S05 reuse | OK (boundary lives at backend layer rather than orchestrator) |
| Backend `POST /api/v1/sessions` | S01 provides | S02 surfaces volume_provision_failed; S03/S04/S05 e2es exercise | OK |
| Backend `GET /api/v1/sessions` | S01 provides | S04/S05 e2es exercise | OK |
| Backend `DELETE /api/v1/sessions/{id}` | S01 provides | S04/S05 e2es exercise | OK |
| Backend WS `/api/v1/ws/terminal/{session_id}` | S01 provides verbatim text-frame proxy | S04 e2e attaches; S05 T01 durability + T02 rotation WS path | OK |
| Backend `GET /api/v1/sessions/{id}/scrollback` | S04 provides | S05 T01 consumes via `session_scrollback_proxied` log | OK |
| Backend `GET/PUT /api/v1/admin/settings[/{key}]` | S03 provides + _VALIDATORS registry | S04 adds `idle_timeout_seconds` validator; S04/S05 e2es PUT it | OK |
| Backend → orchestrator shared secret (two-key) | S01 introduces `X-Orchestrator-Key` HTTP + `?key=` WS, constant-time compare | S05 T02 hardens with full rotation acceptance test (current/previous/wrong sibling backends, HTTP+WS) | OK |
| Postgres `system_settings` table | S03 via `s05_system_settings` alembic | S04 reads idle_timeout_seconds; S05 T01 implicit via reaper | OK |
| Postgres `workspace_volume` table | S02 via `s04_workspace_volume` alembic | S03 partial-apply shrink; S04 reaper preserves; S05 T01 asserts | OK |
| Compose `redis` + `orchestrator` services | S01 provides | S02 modifies orchestrator (privileged, mount-init sidecar); S03/S04/S05 consume | OK |
| Workspace base image `perpetuity/workspace:latest` + `:test` | S01 provides | S02–S05 consume via provision_container | OK |
| WS frame protocol (locked at S01) | S01 provides locked TypedDicts | S02/S03/S04/S05 SUMMARIES all explicitly confirm "frame protocol UNCHANGED (S01 lock preserved)" | OK |
| Observability log keys / UUID-only | S01 establishes taxonomy + redaction sweep | Every slice's e2e re-runs sweep — zero email/full_name matches | OK |

**Verdict: NEEDS-ATTENTION** — 22 of 23 boundaries honored with explicit producer/consumer evidence. One minor gap: roadmap promised `POST /v1/sessions/{id}/resize` would be "hardened S04," but S04 SUMMARY does not document any resize hardening (S01 known-limitation flags `test_resize_succeeds` as still accepting `200 or 500` pending S04). Functionally non-blocking — resize works as of S01.

## Requirement Coverage
## Reviewer A — Requirements Coverage

| Requirement | Status | Evidence (slice + key bullet) |
|---|---|---|
| R005 — Per-(user,team) Docker container with dedicated mounted volume `/workspaces/<u>/<t>/`, isolated | COVERED | S01: container-per-(user,team) with labels `user_id=`/`team_id=`/`perpetuity.managed=true`. S02: replaced bind with kernel-enforced loopback-ext4 hard cap; T04 proves alice 1 GiB ENOSPC + bob neighbor isolation. S05/T01 validates row+volume persistence. |
| R006 — On-demand spin-up, idle timeout shutdown, volumes persist, remount on next provision | COVERED | S04 e2e (`test_m002_s04_full_demo`): two-phase D018 reaper kills idle session + reaps container after `idle_timeout_seconds=3`; workspace_volume row + .img persist; third POST re-provisions and remounts; marker `cat /workspaces/<team_id>/marker.txt` survives reap. |
| R007 — `/ws/terminal/{session_id}` relays I/O via docker exec to pty in user container | COVERED | S01/T06 echo round-trip + `docker compose restart orchestrator` durability with stable shell PID. S04: two distinct WS sessions per `/api/v1/ws/terminal/{session_id}` attach to distinct tmux sessions. S05/T01 reconnect-after-restart with prior scrollback. |
| R008 — Multiple terminal windows for same team workspace, distinct ptys, shared filesystem | COVERED | S04 e2e: alice opens sid_a + sid_b; orchestrator `created==True` then `False` (same container), distinct tmux sessions, marker written via sid_a read via sid_b. |
| R042 — Pty sessions outlive WS via tmux-inside-container; reattach restores ≥100KB scrollback, survives orchestrator restart | COVERED (mapped to M003 in REQUIREMENTS.md but actually delivered here) | S01/T06: `docker compose restart orchestrator` mid-test, reconnect SAME sid, `pid_before==pid_after`, `'hello' in scrollback`. Orchestrator-side 100KB hard-cap (D017). S05/T01 re-validates with `docker restart` ephemeral orchestrator. |
| R043 — Orchestrator runs as separate compose container, sole Docker socket access, shared-secret HTTP+WS API | COVERED | S01: orchestrator is the only non-traefik service mounting `/var/run/docker.sock` (D005); two-key auth via `X-Orchestrator-Key` HTTP and `?key=` WS, constant-time compare. S05/T02 two-key rotation e2e: backend with wrong key gets 503, valid keys accepted on HTTP + WS. |
| R044 — Per-container limits (mem_limit=2g, pids_limit=512, cpus=2) + per-volume hard size cap via loopback ext4; cap value in `system_settings` | **PARTIAL** | S01: HostConfig Memory=2GB, PidsLimit=512, **NanoCpus=1.0 (cpus=1, not cpus=2 as spec text states)**. S02: loopback-ext4 .img enforces ENOSPC at kernel; `docker inspect` validates limits. S03: cap source-of-truth swapped to `system_settings.workspace_volume_size_gb` with D015 partial-apply shrink + warnings payload. **Spec drift: requirement says cpus=2, implementation uses NanoCpus=1.0.** |
| R045 — `system_settings` table + GET/PUT `/api/v1/admin/settings` API gated by system_admin; ships `workspace_volume_size_gb` key | COVERED | S03/T01-T04: `s05_system_settings` alembic revision, generic key/JSONB table, three admin endpoints with `_VALIDATORS` registry (reject-by-default), `workspace_volume_size_gb` validator, non-admin 403, unknown key 422. |
| Idle reaper as standalone capability | COVERED | S04/T02: `orchestrator/reaper.py` two-phase liveness (Redis idle + AttachMap.is_attached), label-collision re-check, `idle_timeout_seconds` validator [1,86400], log keys `reaper_started`, `reaper_tick`, `reaper_killed_session`, `reaper_reaped_container`. |
| Two-key rotation acceptance | COVERED | S05/T02: ephemeral orchestrator with both keys; three sibling backends (current/previous/wrong); HTTP + WS paths accept both rotation keys; wrong key surfaces 503; constant-time compare iterates all candidates without short-circuit. |
| Observability / UUID-only logging discipline | COVERED | All slices: log keys taxonomy enforced; MEM134 redaction sweep across `docker compose logs orchestrator backend` returns zero email/full_name matches in every slice e2e. Auth log lines emit only `key_prefix=<first 4 chars>...`. |

**Verdict: NEEDS-ATTENTION** — coverage is strong end-to-end, but **R044 has a spec drift: requirement text says `cpus=2`, implementation uses `NanoCpus=1.0` (= 1 CPU)**. Asserted via `docker inspect` in S02/T04. Worth a follow-up decision (update R044 spec to cpus=1, or raise the limit).

## Verification Class Compliance
## Verification Classes

| Class | Planned Check | Evidence (slice + summary bullet) | Verdict |
|---|---|---|---|
| Contract | Orchestrator HTTP/WS endpoints return correct shapes/status | S01 SUMMARY: 12 sessions-lifecycle tests + 9 WS-bridge tests + 11 backend-router tests | covered |
| Contract | settings API CRUD round-trips with per-key validators | S03 SUMMARY: 17 admin_settings tests; S04 SUMMARY: +9 idle_timeout tests = 26 total | covered |
| Contract | workspace_volume table writes correct | S02 SUMMARY: T01 migration tests (4/4); T04 e2e asserts row size_gb + img_path | covered |
| Contract | Structured error codes (image_pull_failed, disk_full, name_conflict, volume_mount_failed) | S01 (image_pull_failed); S02 (VolumeProvisionFailed step=truncate/mkfs/losetup/mount); disk_full surfaced as ENOSPC by kernel; name_conflict implicit via label-collision guard | covered |
| Contract | Backend public endpoints match OpenAPI shapes | S01 SUMMARY (sessions router); S03 (admin/settings); S04 (GET scrollback) | covered |
| Integration | E2E provision→tmux→attach→input→output→detach→reattach→scrollback→DELETE, no mocks | S01 T06 e2e PASSED 19.16s | covered |
| Integration | Two-key shared-secret rotation HTTP+WS | S05 T02 e2e PASSED (parameterized sibling backends with key_current + key_previous + wrong) | covered |
| Integration | Cross-service: admin PUT settings → POST /api/v1/sessions → workspace_volume.size_gb + .img file size match | S03 T04 e2e step (4) bob fresh 1 GiB + step (5) df=1G, dd ENOSPC | covered |
| Operational | `docker compose restart orchestrator` doesn't kill live tmux; reattach shows scrollback | S01 T06 + S05 T01 | covered |
| Operational | Idle reaper two-phase check (Redis last_activity + active-WS map) | S04 T02 (9/9 reaper tests including kill-idle-no-attach, skip-attached, skip-non-idle); S05 T01 verified end-to-end | covered |
| Operational | Docker daemon unreachable → 503 | S01 SUMMARY: backend integration tests cover orchestrator-down → 503/1011 | covered |
| Operational | Redis unreachable → 503 | S01 SUMMARY: 8 redis-client tests including RedisUnavailable on unreachable port | covered |
| Operational | WS cookie auth fail → 1008 | S01 SUMMARY: backend WS uses M001 ws_auth_reject pattern; tested in 11 backend integration tests | covered |
| Operational | Orchestrator WS shared-secret fail → 1008 unauthorized | S01 SUMMARY: 9 WS-bridge tests include "bad key 1008"; S05 T02 negative case | covered |
| Operational | Session ownership violation → 1008 session_not_owned (no enumeration) | S01 codified at router; S05 T01 explicit byte-identical close test for missing-vs-not-owned | covered |
| Operational | Container-exists-but-tmux-gone → 410 | S01 SUMMARY references the contract; not explicitly listed as a dedicated test in any slice SUMMARY — coverage implicit via shell-exit emit-exit-frame test | **partial** |
| Operational | Resize for unattached → 404 no-op | S01 SUMMARY: sessions-lifecycle tests include resize 404-for-never-existed-sid (strict); known-limitation note acknowledges happy-path accepts 200 or 500 pending S04 hardening | partial |
| UAT | Manual flow: signup → wscat → attach+scrollback → echo hello → restart orchestrator → reconnect → scrollback contains hello → echo $$ stable | S01 UAT.md + S05 UAT.md present and align with automated S05 T01 demo | covered |
| UAT | Promote system_admin → PUT workspace_volume_size_gb=1 → second fresh user → row size_gb=1 + .img ~1GB | S03 UAT.md present; backed by automated S03 T04 e2e | covered |

**Verdict: NEEDS-ATTENTION** — 17 of 19 verification rows fully covered. Two minor partials: (1) explicit "container-exists-but-tmux-gone → 410" test not surfaced as a dedicated test in any slice SUMMARY (contract is declared in S01 but evidence is implicit via shell-exit pathway); (2) resize happy-path test still accepts `200 or 500` per S01's own known-limitation note pending an S04 hardening pass that didn't materialize.


## Verdict Rationale
All three parallel reviewers returned NEEDS-ATTENTION with non-blocking gaps: (A) R044 spec text says `cpus=2` but implementation uses `NanoCpus=1.0` (cpus=1) — spec drift, not a missing capability; (B) roadmap promised S04 would harden the resize endpoint but S04 SUMMARY doesn't document resize work (S01's known-limitation note re: `test_resize_succeeds` accepting `200 or 500` remains outstanding); (C) the "container-exists-but-tmux-gone → 410" contract is declared in S01 but no dedicated test is surfaced in any SUMMARY (coverage implicit via shell-exit pathway). All 10 success criteria are satisfied with concrete passing-test evidence, all 5 slices have SUMMARY+passing assessment, the S05 capstone e2es PASSED in 46s combined, and redaction sweeps confirm zero PII leaks across every slice. The gaps are documentation-and-test-evidence shortfalls rather than functional defects, which fits `needs-attention` rather than `needs-remediation`.
