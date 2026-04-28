---
estimated_steps: 17
estimated_files: 1
skills_used: []
---

# T01: Produce M003/S02 citation-by-test verification report

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

## Inputs

- ``orchestrator/orchestrator/volumes.py``
- ``orchestrator/orchestrator/volume_store.py``
- ``backend/app/api/routes/admin.py``
- ``backend/app/models.py``
- ``docker-compose.yml``
- ``backend/app/alembic/versions/s04_workspace_volume.py``
- ``backend/app/alembic/versions/s05_system_settings.py``
- ``backend/tests/integration/test_m002_s02_volume_cap_e2e.py``
- ``backend/tests/integration/test_m002_s03_settings_e2e.py``
- ``backend/tests/migrations/test_s04_migration.py``
- ``backend/tests/migrations/test_s05_migration.py``
- ``orchestrator/tests/integration/test_volumes.py``

## Expected Output

- ``.gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md``

## Verification

test -f .gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md && [ "$(grep -c '^## Criterion:' .gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md)" -ge 7 ] && grep -q 'M003-umluob duplicates M002-jy6pde' .gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md && grep -q 'nano_cpus=1_000_000_000' .gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md && [ "$(grep -c 'PASSED' .gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md)" -ge 7 ]

## Observability Impact

No new runtime observability surfaces introduced. The verification report is itself the inspection surface for this slice's outcome — every `## Criterion:` section names a source file path with line range and a test id with verbatim PASS line, so a future agent can re-run any individual citation without re-deriving the proof tree. Existing M002 observability keys (`volume_provisioned`, `volume_mounted`, `system_setting_updated`, `system_setting_shrink_warnings_emitted`, `volume_size_gb_resolved`) are preserved unchanged. Failure-path discipline carried forward: report stays UUID-only (MEM134); cited PASS lines from pytest are already redacted by default.
