# S02: Loopback volumes + system_settings + admin API (verification-only) — UAT

**Milestone:** M003-umluob
**Written:** 2026-04-25T14:37:32.391Z

# S02 UAT — Loopback volumes + system_settings + admin API (verification-only)

## Premise

This slice is verification-only. The deliverable is a citation-by-test report (`.gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md`) proving M002-jy6pde-shipped code already satisfies every M003/S02 success criterion. UAT here exercises the cited integration paths against the real compose stack to confirm the report's claims hold *now*, not just when M002 shipped.

## Preconditions

- Repository at HEAD of `main` after S02 completion.
- Docker Desktop running. Compose stack started: `docker compose up -d db redis orchestrator workspace-mount-init` and all containers `healthy`.
- `.env` loaded; `POSTGRES_PORT=5432` override for backend pytest runs (MEM135 — the running `perpetuity-db-1` container publishes 5432, not the .env-pinned 55432).
- `uv` available; `cd backend` for backend test runs (MEM041/MEM195).
- Admin user is the seeded superuser (`first_superuser` from settings); a non-admin user is created on demand by the e2e tests.

## Test 1 — Verification report integrity (slice stopping condition)

**Goal:** Confirm the report exists, has all required structure, and the literal stopping-condition shell command exits 0.

Steps:
1. From project root, run:
   ```
   test -f .gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md && \
     [ "$(grep -c '^## Criterion:' .gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md)" -ge 7 ] && \
     grep -q 'M003-umluob duplicates M002-jy6pde' .gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md && \
     grep -q 'nano_cpus=1_000_000_000' .gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md && \
     [ "$(grep -c 'PASSED' .gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md)" -ge 7 ]
   ```
2. Echo the exit code (`echo $?`).

Expected: exit 0. Criterion sections ≥ 7. PASSED-line count ≥ 7. Both required markers (MEM202 and MEM203 strings) present.

## Test 2 — Migration suite (cited PASS lines)

**Goal:** Confirm the s04 + s05 migration tests cited under Criteria 3, 4, 5, 7 still pass.

Steps:
1. `cd backend && POSTGRES_PORT=5432 uv run pytest tests/migrations/test_s04_migration.py tests/migrations/test_s05_migration.py -v`

Expected: `7 passed` in <2s. Specifically the `release_db_session` autouse in `test_s04_migration.py` runs and the s04/s05 tests upgrade-then-rollback successfully.

## Test 3 — E2E volume cap (Criterion 1, 2 — kernel-level ENOSPC)

**Goal:** Confirm orchestrator provisions a real loopback ext4 volume and the kernel returns ENOSPC past the cap.

Steps:
1. `cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s02_volume_cap_e2e.py::test_phase_a_alice_1gib_volume_cap_enforced -v`
2. While running (or after, via `docker compose logs orchestrator | grep volume_`), confirm `volume_provisioned`, `volume_image_allocated`, `volume_mounted` log keys appear.

Expected: 1 passed. The dd-into-ENOSPC assertion fires; no test infrastructure ENOSPC bypass.

## Test 4 — E2E admin settings (Criteria 3, 4, 5, 6 — admin API + grow-on-next-provision + shrink warnings + non-admin 403)

**Goal:** Confirm the admin PUT handler grows volumes on next provision, returns shrink warnings on cap-down, and 403s for non-admins.

Steps:
1. `cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s03_settings_e2e.py -v`

Expected: 1 passed. Sub-asserts that exercise:
- Superuser PUT `/api/v1/admin/settings` for `workspace_volume_size_gb` succeeds.
- Subsequent provision picks up the new value.
- PUT to a smaller value returns 200 with non-empty `warnings: [...]` listing affected rows; existing rows keep old `size_gb`.
- Non-system-admin PUT returns 403.

## Test 5 — Source isolation

**Goal:** Confirm no in-scope source was modified by S02.

Steps:
1. `git status --short orchestrator/ backend/app/ docker-compose.yml backend/app/alembic/versions/`
2. `git diff --stat orchestrator/ backend/app/ docker-compose.yml backend/app/alembic/versions/`

Expected: empty output for both — only the verification report and slice artifacts under `.gsd/milestones/M003-umluob/slices/S02/` should have changed at this point.

## Edge cases

- **Compose stack not up:** All e2e tests are gated `@pytest.mark.e2e` and will skip with `compose stack not running` rather than fail. Re-bring it up and re-run — do not modify tests to mask skips.
- **Wrong Postgres port:** Without `POSTGRES_PORT=5432` override, backend tests fail to connect (MEM135). Symptom: `connection refused` on 55432. Fix is environment-only.
- **system_settings empty default-row case (MEM204):** The s05 migration intentionally creates `system_settings` empty. The first PUT seeds the row; until then, orchestrator falls back to `default_volume_size_gb=4` from settings, not the spec's 10. Tests do an explicit PUT before asserting cap behavior — do not add a seed row to "fix" this without owner reconciliation.

## Failure handling

If any of Tests 1–5 fail:
1. Capture the full failing output into the report's `## Verification limitations` block (do not delete passing PASS-line citations).
2. Do **not** modify orchestrator/backend/alembic/compose source — that is out of scope for this slice. File a follow-up under the Human Action Required block instead.
3. Re-run `gsd_complete_slice` only after the report's stopping condition exits 0 again.

## Sign-off

Slice is signed off when:
- Test 1 (stopping condition) exits 0.
- Test 2 (migration suite) reports `7 passed`.
- Test 3 (volume cap e2e) reports `1 passed`.
- Test 4 (admin settings e2e) reports `1 passed`.
- Test 5 (source isolation) reports clean.
- Human owner has acknowledged the MEM202 reconciliation note in the Human Action Required block.
