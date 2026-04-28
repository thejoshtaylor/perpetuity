---
id: S02
parent: M003-umluob
milestone: M003-umluob
provides:
  - ["citation-by-test verification artifact for M003/S02 (T01-VERIFICATION.md)", "live-tested confirmation that M002-jy6pde code still satisfies all 7 M003/S02 success criteria", "documented MEM204 default-seed drift for workspace_volume_size_gb (4 GiB shipped vs 10 GiB spec)", "human-action-required block surfacing the M003-vs-M002 duplication (MEM202)"]
requires:
  - slice: M002-jy6pde shipped: orchestrator/orchestrator/volumes.py, volume_store.py, backend/app/api/routes/admin.py, backend/app/models.py, docker-compose.yml, backend/app/alembic/versions/s04_workspace_volume.py + s05_system_settings.py, integration tests test_m002_s02_volume_cap_e2e.py + test_m002_s03_settings_e2e.py + test_s04_migration.py + test_s05_migration.py
    provides: 
  - slice: MEM135: POSTGRES_PORT=5432 override for backend pytest runs (not .env-pinned 55432)
    provides: 
  - slice: MEM041/MEM195: backend tests run from backend/ directory
    provides: 
  - slice: Compose stack up (db/redis/orchestrator) before live runs
    provides: 
affects:
  - [".gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md"]
key_files:
  - [".gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md", ".gsd/milestones/M003-umluob/slices/S02/tasks/T01-SUMMARY.md"]
key_decisions:
  - ["Treated M003/S02 as a verification-only slice mirroring M003/S01 (MEM200/MEM201) — same authority basis. Citation-by-test report is the deliverable; no source modified.", "Recorded `workspace_volume_size_gb` default-seed drift (M002 ships 4 GiB via `settings.default_volume_size_gb` boot-time fallback; M003 spec calls for 10 GiB; s05 migration creates system_settings empty by design) as a Known Accepted Divergence (MEM204) rather than failing verification — the API/migration are correct under M002 contract; only M003 spec disagreement.", "Used POSTGRES_PORT=5432 override for all backend pytest runs per MEM135 (running perpetuity-db-1 publishes host port 5432, not the .env-pinned 55432).", "Carried forward MEM203 (`nano_cpus=1_000_000_000` = 1.0 vCPU vs spec's 2.0 vCPU) from M002 unchanged."]
patterns_established:
  - ["Verification-only slice pattern: when a milestone duplicates an already-shipped milestone, discharge each success criterion via static citation (file:line) + verbatim PASS line from the existing live test suite, surface human-reconciliation blocker explicitly, and capture spec-vs-shipped drifts as Known Accepted Divergences without failing the slice.", "Stopping-condition shell as the primary slice gate: a single literal command in the slice plan that greps for criterion-section count, required marker strings, and minimum PASSED-line count — exits 0 ⇒ slice mechanically verifiable."]
observability_surfaces:
  - ["Existing M002 INFO log keys preserved (cited in report): volume_provisioned, volume_mounted, volume_image_allocated, volume_unmounted, volume_size_gb_resolved, system_setting_updated, system_setting_shrink_warnings_emitted, system_settings_listed, pg_pool_opened, loop_devices_ready", "Existing WARNING log key preserved: system_settings_lookup_failed", "Inspection surfaces (unchanged): `docker compose logs orchestrator | grep volume_`, `docker compose exec db psql -c 'SELECT * FROM system_settings'`, `docker compose exec orchestrator ls /var/lib/perpetuity/vols/`", "The verification report itself is now an inspection surface — each `## Criterion:` section names file path, line range, and test id"]
drill_down_paths:
  - [".gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md (the citation-by-test report — primary drill-down)", "backend/tests/integration/test_m002_s02_volume_cap_e2e.py (cited under Criteria 1, 2)", "backend/tests/integration/test_m002_s03_settings_e2e.py (cited under Criteria 4, 5, 6)", "backend/tests/migrations/test_s04_migration.py (cited under Criterion 7 — MEM016 autouse)", "backend/tests/migrations/test_s05_migration.py (cited under Criterion 3)", "orchestrator/orchestrator/volumes.py + volume_store.py (cited under Criterion 1)", "backend/app/api/routes/admin.py (cited under Criteria 4, 5, 6)", "backend/app/alembic/versions/s05_system_settings.py (cited under Criterion 3)", "backend/app/models.py (cited under Criteria 4, 5)"]
duration: ""
verification_result: passed
completed_at: 2026-04-25T14:37:32.390Z
blocker_discovered: false
---

# S02: Loopback volumes + system_settings + admin API (verification-only)

**Citation-by-test verification report proves all 7 M003/S02 success criteria are already satisfied by M002-jy6pde-shipped code on main; no source modified.**

## What Happened

M003/S02 was executed as a verification-only slice mirroring M003/S01 (MEM200/MEM201) — the same authority basis. Premise: M003-umluob byte-for-byte duplicates the already-shipped M002-jy6pde milestone (MEM202), so every M003/S02 success criterion can be discharged by citing M002-shipped source plus a verbatim PASS line from the existing live test suite. No new code, no compose/orchestrator/backend/alembic source changes.

The single deliverable is `.gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md`, a citation-by-test report with one `## Criterion:` section per success criterion (7 total): (1) orchestrator boots with loopback volume support and POST /v1/sessions creates a loopback ext4 volume bind-mounted at /workspaces/<user>/<team>/ (cites volumes.py + volume_store.py + test_m002_s02_volume_cap_e2e.py); (2) writing past the cap returns ENOSPC (cites the dd-into-ENOSPC assertion); (3) system_settings table exists for workspace_volume_size_gb (cites s05_system_settings.py migration + test_s05_migration.py); (4) admin PUT picks up new value on next provision (cites admin.py PUT handler + the PUT-then-bob-1GiB-cap flow in test_m002_s03_settings_e2e.py); (5) partial-apply shrink returns 200 with `warnings: [...]` and existing rows keep old size_gb per D015 (cites _compute_shrink_warnings in admin.py + warnings-non-empty assertion); (6) non-system-admin PUT returns 403 (cites router-level Depends(get_current_active_superuser) + non-admin assertion); (7) MEM016 autouse fixture releases the session-scoped DB lock before the s04 migration runs (cites the release_db_session autouse in test_s04_migration.py).

Each criterion section pairs a static citation (file path + line range against source-of-truth files) with a verbatim PASS line from a live test run. Total cited PASSED lines: 13 (≥7 required). Live runs from the project root with .env loaded, backend tests run from backend/ per MEM041/MEM195, with POSTGRES_PORT=5432 override per MEM135 (the running perpetuity-db-1 container publishes 5432 on the host, not the .env-pinned 55432). Compose stack already up (db/redis/orchestrator healthy) before the runs.

Two required call-outs are carried forward in the report:
- Human Action Required (MEM202): M003-umluob duplicates M002-jy6pde — a human owner must reconcile (close M003 as delivered, or `gsd_replan_slice` toward R009–R012 Projects/GitHub) before subsequent M003 slices proceed.
- Known Accepted Divergences: (a) `nano_cpus=1_000_000_000` (1.0 vCPU) vs spec's 2.0 vCPU per MEM203 — carried over from M002, recorded not failing; (b) NEW: `workspace_volume_size_gb` default-seed drift — M002 ships 4 GiB default via `settings.default_volume_size_gb` boot-time fallback, M003 spec calls for 10 GiB, the s05 migration creates system_settings empty by design (captured as MEM204).

The slice's stopping-condition shell command was executed and exited 0: criteria=7, passed=13, both required markers present.</narrative>
<parameter name="verification">All slice-level verification gates pass:

1. Stopping-condition shell command (project root): `test -f .gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md && [ "$(grep -c '^## Criterion:' …)" -ge 7 ] && grep -q 'M003-umluob duplicates M002-jy6pde' … && grep -q 'nano_cpus=1_000_000_000' … && [ "$(grep -c 'PASSED' …)" -ge 7 ]` → exit 0. Output: criteria=7, PASSED count=13, MEM202 marker present, MEM203 marker present.

2. Live test runs (cd backend && POSTGRES_PORT=5432 uv run pytest):
   - Migration suite: test_s04_migration.py + test_s05_migration.py → 7 passed in 0.34s.
   - E2E suite: test_m002_s02_volume_cap_e2e.py + test_m002_s03_settings_e2e.py → 2 passed in 30.34s.
   - Total cited PASSED lines in report: 13 (≥7 required).

3. Source isolation: zero modifications to orchestrator/orchestrator/*, backend/app/api/routes/admin.py, backend/app/models.py, docker-compose.yml, backend/app/alembic/versions/s04_workspace_volume.py, or backend/app/alembic/versions/s05_system_settings.py. Confirmed by `git status` showing the only file written is the report itself.

4. Observability surfaces preserved: existing M002 INFO keys (volume_provisioned, volume_mounted, volume_image_allocated, volume_unmounted, volume_size_gb_resolved, system_setting_updated, system_setting_shrink_warnings_emitted, system_settings_listed, pg_pool_opened, loop_devices_ready) and WARNING key (system_settings_lookup_failed) are still asserted by the cited e2e tests on compose log content (test_m002_s02_volume_cap_e2e.py L820–827; test_m002_s03_settings_e2e.py L670–678).

5. Redaction discipline (MEM134): report stays UUID-only — no email, full_name, team slug, or scrollback content quoted; cited test outputs only show test ids, file paths, and PASS/FAIL.

## Verification

All slice-level verification gates pass — see the verification field on this completion record. Stopping condition exits 0 (criteria=7, PASSED=13, MEM202 + MEM203 markers present); migration suite 7/7 in 0.34s; e2e suite 2/2 in 30.34s; zero in-scope source modifications.

## Requirements Advanced

None.

## Requirements Validated

None.

## New Requirements Surfaced

None.

## Requirements Invalidated or Re-scoped

None.

## Operational Readiness

None.

## Deviations

"None — strict verification + documentation scope. Stopping-condition shell exited 0 on first run; live tests passed on first run."

## Known Limitations

"Slice is verification-only — it does not advance any net-new capability. The Human Action Required block in T01-VERIFICATION.md (MEM202) carries forward unresolved: M003-umluob byte-for-byte duplicates M002-jy6pde, and a human owner must reconcile (close M003 as delivered, or `gsd_replan_slice` toward R009–R012 Projects/GitHub) before subsequent M003 slices (S03 idle reaper, S04 tmux + Redis, S05 WS bridge, S06 final acceptance) proceed. Until that reconciliation lands, downstream M003 slices either close as already-delivered or get replanned. Additionally, MEM204 (workspace_volume_size_gb default-seed drift: 4 GiB shipped vs 10 GiB spec) needs follow-up if M003 is reconciled toward 10 GiB-default — either add a seed-row migration or bump default_volume_size_gb in backend+orchestrator config."

## Follow-ups

"1) Human owner reconciles M003-umluob vs M002-jy6pde scope (MEM202) — most likely close M003-umluob as delivered and replan a fresh M003 toward R009–R012 (Projects + GitHub). 2) If reconciled toward '10 GiB default', resolve MEM204 by either adding a seed-row alembic migration for system_settings (`workspace_volume_size_gb=10`) or bumping `default_volume_size_gb` in backend+orchestrator config. 3) Once reconciled, downstream M003 slices S03–S06 either close as already-delivered (cite the same M002 e2e tests) or get `gsd_replan_slice`'d toward the new scope."

## Files Created/Modified

- `.gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md` — New: citation-by-test verification report — 7 criterion sections, 13 cited PASSED lines, Human Action Required (MEM202) + Known Accepted Divergences (MEM203 + MEM204) blocks.
