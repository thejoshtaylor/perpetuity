# S02: Loopback volumes + system_settings + admin API

**Goal:** Verify by citation-by-test that all M003/S02 success criteria (loopback ext4 volumes, system_settings + admin GET/PUT API, partial-apply shrink, kernel-enforced ENOSPC, role-gated 403 for non-system-admin, MEM016 autouse session-release in s04 alembic test) are already met by M002-jy6pde-shipped code on main. Produce a single proof-by-citation verification report (T01-VERIFICATION.md) keyed per criterion with verbatim PASS lines from the existing test suite. No new code, no compose/orchestrator/backend/alembic source changes — strict verification + documentation scope, mirroring the M003/S01 verification-slice pattern (MEM200/MEM201).
**Demo:** Integration test against real Docker + real Postgres: orchestrator boots with loopback volume support; POST /v1/sessions creates a 10GB loopback ext4 volume bind-mounted at /workspaces/<user_id>/<team_id>/; writing past 10GB inside the container returns ENOSPC. Backend admin user PUTs workspace_volume_size_gb=20; the next provision (or restart of warm container) triggers resize2fs and the volume is now 20GB. Shrink preview endpoint surfaces overflow; shrink with overflow returns 4xx warning. Non-system-admin PUT returns 403. MEM016 autouse fixture released the session-scoped DB lock before s04 alembic migration ran.

## Must-Haves

- T01-VERIFICATION.md exists at `.gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md` with one `## Criterion: <N>` section per M003/S02 success criterion (≥7 criteria covered: loopback ext4 volumes via volumes.py, system_settings table + admin API, kernel ENOSPC enforcement, dynamic resize on cap raise, partial-apply shrink warnings on cap lower, non-system-admin 403, MEM016 autouse session-release in test_s04_migration.py).
- Each criterion section contains at least one verbatim PASS line from a live test run (file path + test id + status) plus a static citation (file path + line range) against the source-of-truth file (volumes.py, volume_store.py, admin.py, models.py, docker-compose.yml, alembic versions/, or the test files).
- Report includes a "Human Action Required" note repeating MEM202: M003-umluob still duplicates M002-jy6pde and a human owner must reconcile (close as delivered, or `gsd_replan_slice` toward R009–R012 Projects/GitHub) before subsequent M003 slices proceed.
- Report includes a "Known Accepted Divergences" note carrying forward the `nano_cpus=1_000_000_000` (1.0 vCPU) vs spec's `2_000_000_000` (2.0 vCPU) drift (MEM203) — recorded, not failing.
- Total PASS-line count across the report is ≥7 (one per criterion minimum).
- Zero modifications to `orchestrator/orchestrator/*`, `backend/app/api/routes/admin.py`, `backend/app/models.py`, `docker-compose.yml`, `backend/app/alembic/versions/s04_workspace_volume.py`, or `backend/app/alembic/versions/s05_system_settings.py`.
- Verification command (slice stopping condition):
- ```
- test -f .gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md && \
- grep -c '^## Criterion:' .gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md | awk '$1>=7{exit 0} {exit 1}' && \
- grep -q 'M003-umluob duplicates M002-jy6pde' .gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md && \
- grep -q 'nano_cpus=1_000_000_000' .gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md && \
- test "$(grep -c 'PASSED' .gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md)" -ge 7
- ```

## Proof Level

- This slice proves: - This slice proves: integration (verifies that the M002-shipped integration boundaries — admin API ↔ Postgres ↔ orchestrator ↔ loopback ext4 — still satisfy the M003/S02 contract).
- Real runtime required: yes (the cited integration tests run against real Postgres + real Docker daemon + real orchestrator container).
- Human/UAT required: no (the verification artifact is the deliverable; auto-mode produces it directly from existing test output).

## Integration Closure

- Upstream surfaces consumed (read-only — no writes): `orchestrator/orchestrator/volumes.py`, `orchestrator/orchestrator/volume_store.py`, `backend/app/api/routes/admin.py`, `backend/app/models.py` (SystemSetting, SystemSettingPut, SystemSettingShrinkWarning, SystemSettingPutResponse, WorkspaceVolume), `docker-compose.yml` (orchestrator privileged + workspace-mount-init sidecar), `backend/app/alembic/versions/s04_workspace_volume.py`, `backend/app/alembic/versions/s05_system_settings.py`, `backend/tests/integration/test_m002_s02_volume_cap_e2e.py`, `backend/tests/integration/test_m002_s03_settings_e2e.py`, `backend/tests/migrations/test_s04_migration.py`, `backend/tests/migrations/test_s05_migration.py`, `orchestrator/tests/integration/test_volumes.py`.
- New wiring introduced in this slice: none — strict verification + documentation scope.
- What remains before M003 is truly usable end-to-end: human-owner reconciliation of M003-umluob vs M002-jy6pde scope (MEM202). Once reconciled, downstream M003 slices (S03 idle reaper, S04 tmux + Redis, S05 WS bridge, S06 final acceptance) either close as already-delivered or get `gsd_replan_slice`'d toward R009–R012 Projects/GitHub.

## Verification

- Runtime signals: no new keys introduced. Existing M002 INFO keys preserved and cited in the verification report: `volume_provisioned`, `volume_mounted`, `volume_image_allocated`, `volume_unmounted`, `volume_size_gb_resolved`, `system_setting_updated`, `system_setting_shrink_warnings_emitted`, `system_settings_listed`, `pg_pool_opened`, `loop_devices_ready`. WARNING keys preserved: `system_settings_lookup_failed`.
- Inspection surfaces: existing CLI surfaces only — `docker compose logs orchestrator | grep volume_`, `docker compose exec db psql ... -c 'SELECT * FROM system_settings'`, `docker compose exec orchestrator ls /var/lib/perpetuity/vols/`, `cd backend && uv run pytest tests/integration/test_m002_s02_volume_cap_e2e.py tests/integration/test_m002_s03_settings_e2e.py`.
- Failure visibility: report itself is the inspection surface — each `## Criterion:` section names the source file, line range, and test id that proves the criterion. `VolumeProvisionFailed.step` and `.reason` shape carried forward from M002 unchanged.
- Redaction constraints: report stays UUID-only (MEM134); no email, full_name, team slug, or scrollback content quoted. Cited test outputs only show test ids, file paths, and PASS/FAIL — already redacted by pytest's default reporter.

## Tasks

- [x] **T01: Produce M003/S02 citation-by-test verification report** `est:45m`
  Auto-mode verification slice mirroring M003/S01 (MEM201). Goal: prove every M003/S02 success criterion is already met by M002-shipped code on main. Output: a single artifact `T01-VERIFICATION.md` at `.gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md` with one `## Criterion:` section per M003/S02 success criterion, each containing (a) a static citation (file path + line range) against source-of-truth files and (b) a verbatim PASS line from a live test run.

M003/S02 criteria to cover (one section each):
  1. Orchestrator boots with loopback volume support; POST /v1/sessions creates a 10GB (or DEFAULT_VOLUME_SIZE_GB) loopback ext4 volume bind-mounted at workspace path — cite `orchestrator/orchestrator/volumes.py` (allocate_image / mount_image), `orchestrator/orchestrator/volume_store.py` (ensure_volume_for), and `backend/tests/integration/test_m002_s02_volume_cap_e2e.py::test_phase_a_alice_1gib_volume_cap_enforced`.
  2. Writing past the cap inside the container returns ENOSPC — cite the dd-into-ENOSPC assertion in `test_m002_s02_volume_cap_e2e.py`.
  3. system_settings table exists with `workspace_volume_size_gb` seed (default 10 per spec; M002 shipped 4 GiB default — record drift) — cite `backend/app/alembic/versions/s05_system_settings.py` and `backend/tests/migrations/test_s05_migration.py`.
  4. Admin user PUTs `workspace_volume_size_gb`; next provision picks up the new value (grow-on-next-provision via mkfs at the new size for fresh rows) — cite `backend/app/api/routes/admin.py` PUT handler and `backend/tests/integration/test_m002_s03_settings_e2e.py` PUT-then-bob-1GiB-cap flow.
  5. Partial-apply shrink: PUT to a smaller value returns 200 with `warnings: [{user_id, team_id, size_gb, usage_bytes}, ...]` listing affected rows; existing rows keep their old size_gb (D015) — cite `_compute_shrink_warnings`/`SystemSettingShrinkWarning` in `admin.py` + the warnings-non-empty assertion in `test_m002_s03_settings_e2e.py`.
  6. Non-system-admin PUT returns 403 — cite the router-level `dependencies=[Depends(get_current_active_superuser)]` in `admin.py` plus the non-admin 403 assertion in `test_m002_s03_settings_e2e.py`.
  7. MEM016 autouse fixture released the session-scoped DB lock before the s04 alembic migration ran — cite the `release_db_session` autouse in `backend/tests/migrations/test_s04_migration.py` (lines 53+) and the test's PASS line.

Report must also include:
  - 'Human Action Required' block repeating MEM202 verbatim: M003-umluob duplicates M002-jy6pde; human owner must reconcile (close M003 as delivered, or replan toward R009–R012) before subsequent M003 slices proceed.
  - 'Known Accepted Divergences' block carrying forward `nano_cpus=1_000_000_000` (1.0 vCPU) vs spec's `2_000_000_000` (2.0 vCPU) per MEM203, plus the `workspace_volume_size_gb` default-seed drift (M002 shipped 4 GiB default; M003 spec calls for 10) — record both, do NOT fail verification on either.

Do NOT modify any source under `orchestrator/`, `backend/app/`, `docker-compose.yml`, or `backend/app/alembic/versions/`. The only file written by this task is the verification report itself.

Assumptions documented in the report:
  - Auto-mode treats this slice as verification-only, mirroring M003/S01 — same authority basis as MEM201.
  - Live test runs are executed from the project root with `.env` loaded (POSTGRES_PORT=55432 per MEM021); backend tests run from `backend/` (MEM041); compose stack must be up (`docker compose up -d db redis orchestrator workspace-mount-init`) before live runs.
  - On any test failure during live runs, capture the full failing output into the report's 'Verification Limitations' block — do NOT attempt to fix the underlying source (out of scope).
  - Files: ``.gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md``
  - Verify: test -f .gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md && [ "$(grep -c '^## Criterion:' .gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md)" -ge 7 ] && grep -q 'M003-umluob duplicates M002-jy6pde' .gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md && grep -q 'nano_cpus=1_000_000_000' .gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md && [ "$(grep -c 'PASSED' .gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md)" -ge 7 ]

## Files Likely Touched

- `.gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md`
