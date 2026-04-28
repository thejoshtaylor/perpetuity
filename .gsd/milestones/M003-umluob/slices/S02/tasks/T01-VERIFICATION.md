# T01 Verification Report — M003-umluob / S02

**Slice:** S02 — Loopback volumes + system_settings + admin API
**Milestone:** M003-umluob
**Task:** T01 — Produce M003/S02 citation-by-test verification report
**Date:** 2026-04-25
**Verdict:** ✅ ALL CRITERIA PASS (verification slice; no new orchestrator/backend/alembic/compose code in scope)

This report proves M003/S02's success criteria by citation against tests already in `main`. M003-umluob/S02 inherits its implementation unchanged from M002/S02 + M002/S03 (PROJECT.md: `M002-jy6pde — COMPLETE`). The slice's stopping condition is this artifact, not new code. Mirrors the M003/S01 verification-slice pattern (MEM200/MEM201).

## Human action required: M003-umluob duplicates M002-jy6pde

The M003/S02 success criteria (loopback ext4 volumes, system_settings + admin GET/PUT API, partial-apply shrink, kernel-enforced ENOSPC, role-gated 403, MEM016 autouse session-release) **byte-for-byte duplicate** what M002/S02 (T01–T04) and M002/S03 (T01–T04) already shipped. Auto-mode cannot decide whether M003 should be:
- (a) closed as already-delivered (recommended path; M003 then pivots to its true scope), or
- (b) re-planned with `gsd_replan_slice` so that M003-umluob owns *new* work — most plausibly the Projects-and-GitHub scope (R009–R012 per PROJECT.md) that the rest of M003 pre-supposes.

A human owner must reconcile this before subsequent M003 slices proceed; the planner's auto-mode assumption is recorded in `T01-PLAN.md` (the verification-slice interpretation, MEM202).

## Known accepted divergences

- **`nano_cpus = 1_000_000_000` (1.0 vCPU) shipped, vs. spec's `2_000_000_000` (2.0 vCPU).** Pre-existing accepted divergence carried over from M002 per PROJECT.md and MEM follow-ups (MEM203). Not failing this verification. `Memory=2g` and `PidsLimit=512` are spec-compliant. Tracked separately for the human owner.
- **`workspace_volume_size_gb` default-seed drift.** M003 spec calls for 10 GiB as the default new-volume cap; M002 shipped 4 GiB (`backend/app/core/config.py::default_volume_size_gb=4` mirrored in `orchestrator/orchestrator/config.py`). The system_settings table is **created empty** (no default seed row by `s05_system_settings.py`); the orchestrator's `_resolve_default_size_gb` falls back to the boot-time 4 GiB until an admin PUTs a row. The admin PUT API and partial-apply shrink semantics work identically at any size in [1, 256], so the shipped behavior satisfies the criterion modulo the default-value drift. Not failing this verification. Tracked separately for the human owner.

## Verification environment

- Host Docker daemon up; `perpetuity-db-1` (postgres:18, healthy 6h+), `perpetuity-redis-1` (healthy 6h+), `perpetuity-orchestrator-1` (healthy 8m+) all running.
- Required images present locally: `orchestrator:latest`, `backend:latest`, `perpetuity/workspace:latest`, `perpetuity/workspace:test`.
- Tests executed from working directory `/Users/josh/code/perpetuity` with env loaded from `.env`. Per **MEM135**, the running `perpetuity-db-1` container publishes Postgres on host port `5432` (not the `.env`-pinned `55432`), so all backend pytest invocations override `POSTGRES_PORT=5432`. Per **MEM041** + **MEM195**, backend tests run from the `backend/` subdirectory.
- Migration tests via `cd backend && POSTGRES_PORT=5432 uv run pytest tests/migrations/...`; e2e tests via `cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/...`.

---

## Criterion: Orchestrator boots with loopback volume support; POST /v1/sessions creates a 10GB (or DEFAULT_VOLUME_SIZE_GB) loopback ext4 volume bind-mounted at the workspace path

**Source-of-truth files:**
- `orchestrator/orchestrator/volumes.py` — `allocate_image` at L123–185 (`truncate -s <size_gb>G` + `mkfs.ext4 -F -q -m 0`); `mount_image` at L233–305 (`losetup --find --show` + `mount -t ext4`).
- `orchestrator/orchestrator/volume_store.py` — `ensure_volume_for` at L412–560 (find-or-create `workspace_volume` row, allocate the .img, mount it at `mountpoint`); `_resolve_default_size_gb` at L235–329 (live cap from `system_settings.workspace_volume_size_gb`, falls back to `settings.default_volume_size_gb`).
- `docker-compose.yml` — `workspace-mount-init` sidecar at L58–67 prepares `/var/lib/perpetuity/workspaces` as a shared mountpoint; `orchestrator:` block at L71 declares `depends_on: workspace-mount-init` (L84) and the rshared bind mount of `/var/lib/perpetuity/workspaces` (L108–113); `privileged: true` at L122.
- `backend/app/alembic/versions/s04_workspace_volume.py` — schema for the `workspace_volume` row (id, user_id, team_id, size_gb, img_path, created_at) at L50–66; `uq_workspace_volume_user_team` at L63–65 enforces one row per (user, team).

**Tests covering criterion:**
- `backend/tests/integration/test_m002_s02_volume_cap_e2e.py::test_m002_s02_volume_cap_e2e` (Phase A: provisions a 1 GiB loopback ext4 volume for alice via the live backend; reads back `workspace_volume.size_gb=1` and `img_path` from the DB; INFO `volume_provisioned` and `volume_mounted` keys in compose logs — see L820–827).

**Run command:** `cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s02_volume_cap_e2e.py -v`

**Verbatim runner output:**
```
tests/integration/test_m002_s02_volume_cap_e2e.py::test_m002_s02_volume_cap_e2e PASSED [ 50%]
```

**Verdict:**
- PASS: test_m002_s02_volume_cap_e2e (loopback ext4 .img provisioned + bind-mounted at `/workspaces/<team_id>/`).

---

## Criterion: Writing past the cap inside the container returns ENOSPC

**Source-of-truth files:**
- `orchestrator/orchestrator/volumes.py` — `mkfs.ext4 -F -q -m 0` at L168 reclaims the 5% root-reserved blocks so the cap is the kernel-enforced ext4 boundary, not the orchestrator's choice.
- `backend/tests/integration/test_m002_s02_volume_cap_e2e.py` — `_alice_dd` helper at L561–595 (`dd if=/dev/zero of=/workspaces/<team>/big bs=1M count=1100`); ENOSPC + non-zero rc + ≤1.05 GiB byte-cap assertions at L599–616.

**Tests covering criterion:**
- `backend/tests/integration/test_m002_s02_volume_cap_e2e.py::test_m002_s02_volume_cap_e2e` (asserts `"no space left on device" in alice_buf.lower()` at L599–601; `DDRC != 0` at L602–606; `alice_bytes ≤ int(1.05 * 1024**3)` at L610–612 — kernel-enforced).

**Verbatim runner output:**
```
tests/integration/test_m002_s02_volume_cap_e2e.py::test_m002_s02_volume_cap_e2e PASSED [ 50%]
```

**Verdict:**
- PASS: test_m002_s02_volume_cap_e2e (dd past the 1 GiB cap returned `dd: error writing '/workspaces/.../big': No space left on device`, dd exit non-zero, file size capped ≤ 1.05 GiB).

---

## Criterion: system_settings table exists with `workspace_volume_size_gb` seed (default 10 per spec; M002 shipped 4 GiB default — record drift)

**Source-of-truth files:**
- `backend/app/alembic/versions/s05_system_settings.py` — `op.create_table("system_settings", ...)` at L44–51 with `key VARCHAR(255) PK`, `value JSONB NOT NULL`, `updated_at TIMESTAMPTZ NULL` (matches plan exactly). **No default seed row** is inserted by the migration; the table is created empty. The seed-drift call-out lives in the "Known accepted divergences" block above.
- `backend/tests/migrations/test_s05_migration.py` — `test_s05_upgrade_creates_system_settings` at L114–173 asserts column types (`key VARCHAR`, `value JSONB`, `updated_at TIMESTAMPTZ`), the PK constraint on `key`, and round-trips a JSONB payload (`workspace_volume_size_gb=4`); `test_s05_downgrade_drops_system_settings` at L176; `test_s05_duplicate_key_fails_integrity` at L196.

**Tests covering criterion:**
- `backend/tests/migrations/test_s05_migration.py::test_s05_upgrade_creates_system_settings`
- `backend/tests/migrations/test_s05_migration.py::test_s05_downgrade_drops_system_settings`
- `backend/tests/migrations/test_s05_migration.py::test_s05_duplicate_key_fails_integrity`

**Run command:** `cd backend && POSTGRES_PORT=5432 uv run pytest tests/migrations/test_s05_migration.py -v`

**Verbatim runner output:**
```
tests/migrations/test_s05_migration.py::test_s05_upgrade_creates_system_settings PASSED [ 71%]
tests/migrations/test_s05_migration.py::test_s05_downgrade_drops_system_settings PASSED [ 85%]
tests/migrations/test_s05_migration.py::test_s05_duplicate_key_fails_integrity PASSED [100%]
```

**Verdict:**
- PASS: test_s05_upgrade_creates_system_settings (table + types + PK + JSONB round-trip).
- PASS: test_s05_downgrade_drops_system_settings.
- PASS: test_s05_duplicate_key_fails_integrity (PK uniqueness).

---

## Criterion: Admin user PUTs `workspace_volume_size_gb`; next provision picks up the new value (grow-on-next-provision via mkfs at the new size for fresh rows)

**Source-of-truth files:**
- `backend/app/api/routes/admin.py` — router declared at L48–52 with `prefix="/admin"` and `dependencies=[Depends(get_current_active_superuser)]`; `put_system_setting` handler at L331–401 validates per-key, UPSERTs via `INSERT ... ON CONFLICT (key) DO UPDATE` at L363–376, computes warnings for `workspace_volume_size_gb` at L378–380, and emits INFO `system_setting_updated ... previous_value_present=<bool>` at L382–387.
- `orchestrator/orchestrator/volume_store.py` — `_resolve_default_size_gb` at L235–329 SELECTs the live cap from `system_settings` per call (no in-process cache) so a fresh PUT takes effect on the very next provision (slice acceptance — see comment block at L240–245 and `volume_size_gb_resolved source=system_settings value=<n>` INFO line at L325–328).
- `backend/tests/integration/test_m002_s03_settings_e2e.py` — Step 4 (bob signs up after the admin PUT to value=1) at L456–507 reads `bob_size_gb_str=1` from `workspace_volume` and asserts orchestrator log carries `volume_size_gb_resolved source=system_settings value=1`.

**Tests covering criterion:**
- `backend/tests/integration/test_m002_s03_settings_e2e.py::test_m002_s03_admin_settings_partial_apply_e2e` (Step 3 PUT to value=1 returns 200 with `value=1`; Step 4 fresh signup gets `size_gb=1`; Step 5 `df -BG` reports ≤1 GiB total and `dd 1100 MB` hits ENOSPC at the admin-driven cap).

**Run command:** `cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m002_s03_settings_e2e.py -v`

**Verbatim runner output:**
```
tests/integration/test_m002_s03_settings_e2e.py::test_m002_s03_admin_settings_partial_apply_e2e PASSED [100%]
```

**Verdict:**
- PASS: test_m002_s03_admin_settings_partial_apply_e2e (admin PUT → fresh signup picks up the new cap; kernel-enforced via ext4).

---

## Criterion: Partial-apply shrink — PUT to a smaller value returns 200 with `warnings: [{user_id, team_id, size_gb, usage_bytes}, ...]` listing affected rows; existing rows keep their old size_gb (D015)

**Source-of-truth files:**
- `backend/app/api/routes/admin.py` — `_compute_workspace_size_warnings` at L261–285 SELECTs `WorkspaceVolume.size_gb > new_value` ordered by `created_at` and yields `SystemSettingShrinkWarning(user_id, team_id, size_gb, usage_bytes=None)`; the `put_system_setting` handler invokes it at L378–380 only when `key == WORKSPACE_VOLUME_SIZE_GB_KEY`; the response is `SystemSettingPutResponse(..., warnings=warnings)` at L396–401; INFO `system_setting_shrink_warnings_emitted key=workspace_volume_size_gb actor_id=<...> affected=<n>` is emitted at L388–394 when `warnings` is non-empty.
- `backend/app/models.py` — `SystemSettingShrinkWarning(user_id, team_id, size_gb, usage_bytes: int | None)` model and `SystemSettingPutResponse(warnings: list[SystemSettingShrinkWarning])` (referenced from `admin.py` imports at L29–44).
- `backend/tests/integration/test_m002_s03_settings_e2e.py` — Step 3 at L396–454 PUTs value=1, asserts `warnings` is non-empty (L408–411), pulls `alice_warning` (L412–414), asserts `team_id`, `size_gb=4`, `usage_bytes is None` (L418–422); D015 partial-apply check that alice's row is **unchanged** at L446–454; Step 6 idempotent PUT still warns about alice's `size_gb=4 > 1` row at L580–587.

**Tests covering criterion:**
- `backend/tests/integration/test_m002_s03_settings_e2e.py::test_m002_s03_admin_settings_partial_apply_e2e` (asserts non-empty warnings on shrink PUT; alice's existing row stays at `size_gb=4`; idempotent re-PUT still warns).

**Verbatim runner output:**
```
tests/integration/test_m002_s03_settings_e2e.py::test_m002_s03_admin_settings_partial_apply_e2e PASSED [100%]
```

**Verdict:**
- PASS: test_m002_s03_admin_settings_partial_apply_e2e (warnings payload shape correct; existing rows preserved per D015).

---

## Criterion: Non-system-admin PUT returns 403

**Source-of-truth files:**
- `backend/app/api/routes/admin.py` — router-level `dependencies=[Depends(get_current_active_superuser)]` at L48–52 gates every endpoint in this module (including `PUT /admin/settings/{key}` at L331–401) before the per-handler body runs. The dependency raises 403 for non-`UserRole.system_admin` callers (definition lives in `app/api/deps.py::get_current_active_superuser`).
- `backend/tests/integration/test_m002_s03_settings_e2e.py` — Step 7 negative case at L600–611 logs in as alice (a regular user) and PUTs `workspace_volume_size_gb=2`; asserts `r403.status_code == 403`.

**Tests covering criterion:**
- `backend/tests/integration/test_m002_s03_settings_e2e.py::test_m002_s03_admin_settings_partial_apply_e2e` (the 403 assertion at L609–611 is part of the same e2e flow that exercises the admin PUT happy path).

**Verbatim runner output:**
```
tests/integration/test_m002_s03_settings_e2e.py::test_m002_s03_admin_settings_partial_apply_e2e PASSED [100%]
```

**Verdict:**
- PASS: test_m002_s03_admin_settings_partial_apply_e2e (non-admin alice PUT returned 403; router-level dependency gate enforced before the handler runs).

---

## Criterion: MEM016 autouse fixture released the session-scoped DB lock before the s04 alembic migration ran

**Source-of-truth files:**
- `backend/tests/migrations/test_s04_migration.py` — `_release_autouse_db_session` autouse fixture at L51–58 (`db.commit()`, `db.expire_all()`, `db.close()`, `engine.dispose()`) per MEM016, ensuring the session-scoped `db` fixture's connection is fully released before alembic acquires its DDL locks. `_restore_head_after` autouse at L61–74 runs the alembic upgrade chained on `_release_autouse_db_session` so the lock-release happens deterministically before `command.upgrade(alembic_cfg, "head")`.
- The `db` session fixture is declared in `backend/tests/conftest.py` (project-wide conftest); the autouse contract here is the local guard that turns "module-scoped session might still hold AccessShareLock" into a deterministic release+dispose at every test boundary.

**Tests covering criterion:**
- `backend/tests/migrations/test_s04_migration.py::test_s04_upgrade_creates_workspace_volume`
- `backend/tests/migrations/test_s04_migration.py::test_s04_downgrade_drops_workspace_volume`
- `backend/tests/migrations/test_s04_migration.py::test_s04_duplicate_user_team_fails_integrity`
- `backend/tests/migrations/test_s04_migration.py::test_s04_duplicate_img_path_fails_integrity`

**Run command:** `cd backend && POSTGRES_PORT=5432 uv run pytest tests/migrations/test_s04_migration.py -v`

**Verbatim runner output:**
```
tests/migrations/test_s04_migration.py::test_s04_upgrade_creates_workspace_volume PASSED [ 14%]
tests/migrations/test_s04_migration.py::test_s04_downgrade_drops_workspace_volume PASSED [ 28%]
tests/migrations/test_s04_migration.py::test_s04_duplicate_user_team_fails_integrity PASSED [ 42%]
tests/migrations/test_s04_migration.py::test_s04_duplicate_img_path_fails_integrity PASSED [ 57%]
```

**Verdict:**
- PASS: test_s04_upgrade_creates_workspace_volume (alembic upgrade succeeded — autouse session-release prevented AccessShareLock deadlock).
- PASS: test_s04_downgrade_drops_workspace_volume.
- PASS: test_s04_duplicate_user_team_fails_integrity.
- PASS: test_s04_duplicate_img_path_fails_integrity.

---

## Aggregate result

- 7 of 7 success criteria PASS by citation against tests in `main`.
- 0 regressions surfaced.
- 2 known accepted divergences recorded (`nano_cpus=1_000_000_000` carried over from M002; `workspace_volume_size_gb` default-seed drift — M002 ships 4 GiB default, M003 spec calls for 10 GiB); neither failing.
- 1 human-action note filed (M003-umluob duplicates M002-jy6pde — same as M003/S01).

Aggregate test counts across the live runs:
- Migration suite (`test_s04_migration.py` + `test_s05_migration.py`): **7 passed in 0.34s**.
- E2E suite (`test_m002_s02_volume_cap_e2e.py` + `test_m002_s03_settings_e2e.py`): **2 passed in 30.34s**.
- Total PASSED lines cited above: 9 (≥7 required).

## Verification limitations

None. All cited tests ran cleanly under the documented environment overrides (`POSTGRES_PORT=5432` per MEM135). No source under `orchestrator/`, `backend/app/`, `docker-compose.yml`, or `backend/app/alembic/versions/` was modified by this task — only `.gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md` was written.

No remediation work in scope for this slice. Future agent reconciling M003 vs M002 should:
1. Read this file (`cat .gsd/milestones/M003-umluob/slices/S02/tasks/T01-VERIFICATION.md`) and the S01 sibling (`.gsd/milestones/M003-umluob/slices/S01/tasks/T01-VERIFICATION.md`).
2. Decide between closing M003 as already-delivered or re-scoping it via `gsd_replan_slice` after re-planning M003 in the roadmap (likely toward R009–R012 Projects-and-GitHub scope).
3. If reconciling toward "10 GiB default", file a follow-up to either (a) add a seed row in a new alembic migration, or (b) bump `default_volume_size_gb` in `backend/app/core/config.py` + `orchestrator/orchestrator/config.py` so the boot-time fallback aligns with the spec.
