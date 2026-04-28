# S02: Loopback-ext4 hard-cap volumes + per-container resource limits

**Goal:** Replace the plain-dir bind-mount under each (user, team) workspace container with a per-volume sparse loopback-ext4 .img file whose ext4 size is the kernel-enforced hard cap, backed by a new `workspace_volume` Postgres row that owns the per-volume effective `size_gb`. New volumes use a hardcoded default of 4 GB (S03 swaps that for `system_settings.workspace_volume_size_gb`). Per-container resource limits (mem_limit=2g, pids_limit=512, nano_cpus=1.0) carry over from S01 and are explicitly re-verified on the spawned container.
**Demo:** Provision a workspace with size_gb=1; attach a shell; run `dd if=/dev/zero of=/workspaces/<u>/<t>/big bs=1M count=1100` and observe ENOSPC at ~1 GB. Other workspaces' .img files are untouched and their writes still succeed. The workspace_volume row matches what's on disk.

## Must-Haves

- Provision a workspace with size_gb=1 → attach a shell → `dd if=/dev/zero of=/workspaces/<team>/big bs=1M count=1100` returns ENOSPC at ~1 GB without disturbing the host or any other workspace's .img file. The matching `workspace_volume` Postgres row has `size_gb=1` and `img_path` pointing at the on-disk file. A second workspace provisioned in parallel is unaffected by the first's ENOSPC and has an independent .img file. The orchestrator inspects the spawned container and sees Memory=2GB, PidsLimit=512, NanoCpus=1_000_000_000.

## Proof Level

- This slice proves: - This slice proves: integration (real Postgres + real Docker daemon + real loopback/mount syscalls inside the orchestrator container)
- Real runtime required: yes
- Human/UAT required: no

## Integration Closure

- Upstream surfaces consumed: `orchestrator/orchestrator/sessions.py::provision_container` (S01-locked), `orchestrator/orchestrator/main.py` lifespan + exception handlers, `orchestrator/orchestrator/config.py`, `backend/app/models.py` (Team/User), `backend/app/alembic/versions/s03_team_invites.py` (last alembic revision; S02 chains as `s04_workspace_volume`), `backend/app/core/db.py` engine, `docker-compose.yml` orchestrator service.
- New wiring introduced in this slice: `orchestrator/orchestrator/volumes.py` (new module — losetup/mount/umount/detach helpers, `ensure_volume(user_id, team_id) -> VolumeRecord`), `provision_container` invokes `ensure_volume` instead of `_ensure_workspace_dir`, orchestrator now opens an asyncpg connection at lifespan to read/write `workspace_volume`, compose adds `/var/lib/perpetuity/vols:/var/lib/perpetuity/vols` bind, alembic `s04_workspace_volume` migration creates the table.
- What remains before the milestone is truly usable end-to-end: S03 (system_settings API + dynamic default cap + partial-apply shrink rule), S04 (idle reaper + multi-tmux REST surface), S05 (final integrated acceptance + two-key rotation test). After S02, hard-cap is enforced but the cap is hardcoded; S03 makes it admin-configurable.

## Verification

- Runtime signals: structured INFO `volume_provisioned volume_id=<uuid> user_id=<uuid> team_id=<uuid> size_gb=N img_path=<path>`, INFO `volume_mounted volume_id=<uuid> loop=/dev/loopN mount=/var/lib/perpetuity/workspaces/<u>/<t>`, WARNING `volume_already_mounted volume_id=<uuid>` (idempotent re-provision), ERROR `volume_provision_failed reason=<truncate|mkfs|losetup|mount> volume_id=<uuid>`. All emit UUIDs only — never email/full_name/team slug (MEM134).
- Inspection surfaces: `SELECT id, user_id, team_id, size_gb, img_path FROM workspace_volume` — DB is the source of truth for the effective per-volume cap. On the orchestrator host: `losetup -a` and `mount | grep /var/lib/perpetuity/workspaces` show what is actually mounted right now. `stat /var/lib/perpetuity/vols/<uuid>.img` proves the .img file's apparent vs allocated size.
- Failure visibility: VolumeProvisionFailed exceptions carry the failing step (truncate|mkfs|losetup|mount) in the reason field; orchestrator's exception handler returns 500 with `{detail: "volume_provision_failed", reason: "..."}` so the failing step is visible from the backend's POST /api/v1/sessions response. ENOSPC inside the workspace surfaces as a normal write error from the user's `dd` — this is the contract, not a failure mode.
- Redaction constraints: never log host filesystem paths that include user email or team slug; the .img path is uuid-keyed so it's safe by construction. Do not log `losetup -a` output verbatim — it can include neighbor volumes' paths.

## Tasks

- [x] **T01: Add workspace_volume Postgres table + SQLModel + s04 alembic migration** `est:1.5h`
  Land the persistence shape D014 calls for: a `workspace_volume` row per (user, team) recording the effective per-volume size_gb and the host img_path. This task is Postgres-only — no orchestrator changes — so it can be reviewed and tested via the existing migration-test pattern (MEM016/MEM025) before the loopback machinery in T02/T03 references the schema.

Schema:
  - `id` UUID PK (default uuid4)
  - `user_id` UUID NOT NULL, FK user.id ON DELETE CASCADE
  - `team_id` UUID NOT NULL, FK team.id ON DELETE CASCADE
  - `size_gb` INTEGER NOT NULL (effective per-volume cap; 1..256 range enforced at app level)
  - `img_path` VARCHAR(512) NOT NULL UNIQUE — the on-disk .img file path; uniqueness is the canonical 'one volume per file' invariant
  - `created_at` TIMESTAMPTZ default now()
  - UniqueConstraint(user_id, team_id) NAMED `uq_workspace_volume_user_team` — exactly one volume per (user, team) is the D004/MEM004 invariant
  - Index `ix_workspace_volume_user_id`, `ix_workspace_volume_team_id` for the orchestrator's lookup-by-(user,team) call

Migration discipline (MEM016): the autouse `db` fixture holds an AccessShareLock; the migration test must release+dispose engine before alembic, then dispose again on restore. Copy the pattern from `backend/tests/migrations/test_s01_migration.py::_release_autouse_db_session` and `_restore_head_after`. Migration file name is `s04_workspace_volume.py` per the M001 series convention; revision id `s04_workspace_volume`, down_revision `s03_team_invites`. Downgrade drops the table and both indexes by name (MEM025: explicit names so downgrade can drop them deterministically).

Model: add `WorkspaceVolume(SQLModel, table=True)` to `backend/app/models.py` with the same fields. No public Pydantic shape needed yet — S03 owns the admin API surface; the orchestrator reads via raw SQL through asyncpg in T03 so this model exists for ORM use from backend test code only.
  - Files: `backend/app/alembic/versions/s04_workspace_volume.py`, `backend/app/models.py`, `backend/tests/migrations/test_s04_migration.py`
  - Verify: cd backend && uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head && uv run pytest tests/migrations/test_s04_migration.py -v

- [x] **T02: Implement loopback-ext4 volume manager (truncate + mkfs + losetup + mount)** `est:3h`
  Create `orchestrator/orchestrator/volumes.py` — a self-contained host-side module that allocates and mounts a per-(user, team) loopback-ext4 volume. Pure subprocess plumbing, no Docker calls, no Postgres calls. T03 wires this into `provision_container` and into the lifespan-opened pg connection.

Public surface (async functions; orchestrator is asyncio-first):
  - `async def allocate_image(volume_id: str, size_gb: int, vols_dir: str = '/var/lib/perpetuity/vols') -> str` — returns absolute img_path. Steps: `os.makedirs(vols_dir, mode=0o700, exist_ok=True)`, then `subprocess.run(['truncate', '-s', f'{size_gb}G', img_path])` (sparse file — instant, no disk consumed yet), then `subprocess.run(['mkfs.ext4', '-F', '-q', '-m', '0', img_path])` (-m 0 reclaims the 5% root-reserved blocks; this volume is single-user). Idempotent: if img_path already exists with non-zero size, re-mkfs only if `mkfs_check=True` (default False — we trust existing files).
  - `async def mount_image(img_path: str, mountpoint: str) -> str` — returns the loop device assigned (e.g. `/dev/loop3`). Steps: `os.makedirs(mountpoint, mode=0o700, exist_ok=True)`, `subprocess.run(['losetup', '--find', '--show', img_path])` returns the loop device on stdout, then `subprocess.run(['mount', '-t', 'ext4', loop_dev, mountpoint])`. Idempotent: if `mountpoint` is already a mountpoint per `os.path.ismount(mountpoint)`, skip and return the loop device by parsing `losetup -j <img_path>` output.
  - `async def unmount_image(mountpoint: str) -> None` — `umount` + `losetup -d <loop_dev>`. Best-effort; logs a WARNING but does not raise if already unmounted (idempotent shutdown path for tests).
  - Custom exception `VolumeProvisionFailed(reason: str, step: str)` (subclass of `OrchestratorError`) where `step` is one of `truncate|mkfs|losetup|mount|umount`. Mapped to 500 by a new exception handler T03 registers in `main.py`.

Every `subprocess.run` uses `check=True, capture_output=True, text=True, timeout=30` and re-raises subprocess failures as `VolumeProvisionFailed(step=..., reason=stderr_first_line)`. Use `asyncio.to_thread` to call subprocess from async code so the event loop stays responsive (mkfs of a 4 GB ext4 typically completes in <500 ms but the test variant uses 1 GB; mount/losetup are <100 ms).

Why a separate module: keeps `sessions.py` focused on container/tmux concerns (D012/D018 boundary) and lets T02 ship with its own unit + integration tests before T03 touches `provision_container`. Tests run inside the orchestrator container (where SYS_ADMIN is granted per MEM101) — running them on the bare host would require root.

Test plan: `orchestrator/tests/integration/test_volumes.py` runs INSIDE the live compose orchestrator container (the only place SYS_ADMIN is available). Per-test scratch dirs under `/tmp/perpetuity-test-vols/<uuid>/` (vols + mountpoint), torn down by a fixture that always calls `unmount_image` first. Cases: (1) allocate+mount round-trip — `losetup -j` shows the .img associated with a /dev/loopN; the mount appears in `/proc/mounts`. (2) Hard-cap honored — write 1100 MB into a 1-GB volume; subprocess call returns non-zero with ENOSPC in stderr. (3) Idempotent re-mount — calling `mount_image` twice returns the same loop device. (4) Step-tagged failures — mkfs.ext4 on a path with a non-existent parent raises VolumeProvisionFailed(step='mkfs').
  - Files: `orchestrator/orchestrator/volumes.py`, `orchestrator/orchestrator/errors.py`, `orchestrator/tests/integration/test_volumes.py`
  - Verify: docker compose exec orchestrator /app/.venv/bin/pytest tests/integration/test_volumes.py -v

- [x] **T03: Wire volume manager into provision_container + add asyncpg lookup of workspace_volume** `est:4h`
  Connect T01's persistence and T02's host-side mount machinery into the existing `provision_container` flow so a fresh (user, team) provision: (1) finds-or-creates a `workspace_volume` Postgres row, (2) ensures the .img file is allocated and mounted at `<workspace_root>/<user_id>/<team_id>/`, (3) bind-mounts that mountpoint into the workspace container at `/workspaces/<team_id>/` exactly as before — so the locked S01 in-container path stays unchanged.

Changes:
  1. `orchestrator/orchestrator/volume_store.py` — new tiny module owning the asyncpg connection pool and the two SQL operations `get_volume(user_id, team_id) -> dict | None` and `create_volume(user_id, team_id, size_gb, img_path) -> dict`. Pool opened at lifespan startup, closed at shutdown. Raises `WorkspaceVolumeStoreUnavailable` (subclass of OrchestratorError) on connection error → 503.
  2. `orchestrator/orchestrator/config.py` — add `database_url: str` (read from env `DATABASE_URL`, default to the compose-internal `postgresql://postgres:<pwd>@db:5432/app` shape; tests pass an override) and `default_volume_size_gb: int = 4` (S03 will replace this with a system_settings lookup; D015 says per-row size_gb is the source of truth, so this default ONLY governs new-row creation).
  3. `orchestrator/orchestrator/sessions.py::provision_container` — replace the call to `_ensure_workspace_dir(host_workspace)` with a call into a new helper `ensure_volume_for(pg, user_id, team_id) -> VolumeRecord` that lives in `volume_store.py` (the helper composes get_volume → create_volume + volumes.allocate_image + volumes.mount_image). Bind-mount source becomes the mountpoint `<workspace_root>/<user>/<team>` (same path as before, but now backed by ext4 inside a loopback file). Container destination stays `/workspaces/<team_id>/`. The container_id flow is unchanged — only the bind-mount source backing differs.
  4. `orchestrator/orchestrator/main.py` — open `app.state.pg` (asyncpg pool) at lifespan; close on shutdown. Register a new exception handler for `VolumeProvisionFailed` → 500 `{detail:'volume_provision_failed', step, reason}` (replaces the T03-placeholder VolumeMountFailed handler shape; keep VolumeMountFailed handler too for backward compat — the loopback path can still fail at the os.makedirs step inside `volumes.allocate_image`).
  5. `docker-compose.yml` — add `/var/lib/perpetuity/vols:/var/lib/perpetuity/vols` bind to the orchestrator service so .img files survive orchestrator restarts (the workspace bind for mountpoints already exists from S01). No backend or compose-network changes.
  6. `orchestrator/tests/integration/test_sessions_lifecycle.py` — extend (do not rewrite) the existing T03 tests: the existing `test_provision_creates_container` now must also assert (a) a workspace_volume row exists for (user_id, team_id), (b) `losetup -a` inside orchestrator shows a loop attached to that row's img_path, (c) `mount | grep <mountpoint>` shows ext4. Container resource-limit re-verification: extend the existing provision test to inspect the spawned container with `docker inspect` and assert `HostConfig.Memory == 2 * 1024**3`, `HostConfig.PidsLimit == 512`, `HostConfig.NanoCpus == 1_000_000_000`.
  7. ENOSPC integration check: `test_volume_hard_cap_enospc` — provision a session with `default_volume_size_gb` overridden to 1, exec a `dd if=/dev/zero of=/workspaces/<t>/big bs=1M count=1100` inside the workspace container, assert the dd command exits non-zero with `No space left on device` in stderr AND that exactly ~1 GB was written (use `stat -c %s /workspaces/<t>/big` and assert ~1 GB ± 5%).

Idempotency: a re-provision with the same (user_id, team_id) MUST find the existing workspace_volume row, MUST find the .img already mounted, MUST NOT mkfs.ext4 again (would zero the user's data — guarded by allocate_image's `mkfs_check=False` default). Test: `test_provision_idempotent_volume` calls provision twice and asserts the row's id is unchanged AND the .img inode is unchanged AND a sentinel file written between provisions still exists.
  - Files: `orchestrator/orchestrator/volume_store.py`, `orchestrator/orchestrator/sessions.py`, `orchestrator/orchestrator/main.py`, `orchestrator/orchestrator/config.py`, `orchestrator/orchestrator/errors.py`, `docker-compose.yml`, `orchestrator/tests/integration/test_sessions_lifecycle.py`
  - Verify: docker compose build orchestrator && docker compose up -d --force-recreate orchestrator && docker compose exec orchestrator /app/.venv/bin/pytest tests/integration/test_sessions_lifecycle.py tests/integration/test_volumes.py -v

- [x] **T04: End-to-end loopback hard-cap acceptance test (the slice demo)** `est:2.5h`
  Land the integration test that proves the slice success criteria verbatim. Reuses the e2e harness pattern from `backend/tests/integration/test_m002_s01_e2e.py` (sibling backend container on `perpetuity_default`, ephemeral orchestrator, real Postgres + real Redis + real Docker daemon — no mocks).

File: `backend/tests/integration/test_m002_s02_volume_cap_e2e.py`. Marked with the `e2e` pytest marker. Skipped if Docker unreachable (same fixture pattern as S01's e2e).

Flow:
  1. Sign up TWO fresh users via M001 endpoints (alice + bob, both at example.com per MEM131 — `email_validator` rejects .local).
  2. POST /api/v1/sessions for alice with `size_gb_override=1` (a test-only env override on the orchestrator, exposed as `TEST_DEFAULT_VOLUME_SIZE_GB` and consumed in T03's settings). Capture alice's session_id and container_id from the response.
  3. WS-attach as alice (cookie-authed, RFC-2606 example.com address, explicit Cookie: header per MEM133). Send `dd if=/dev/zero of=/workspaces/<team>/big bs=1M count=1100\n` and read until the prompt comes back. Assert the dd output contains `No space left on device` AND `stat -c %s /workspaces/<team>/big` returns a value ≤ 1.05 * 1024^3 (≤ ~1.05 GB).
  4. POST /api/v1/sessions for bob (different team, default size_gb=4). Run `df -BG /workspaces/<team>` inside bob's container; assert the reported total is 4 GB and `Use%` is single-digit. Run `ls /workspaces/<team>/` — must NOT see alice's `big` file (independent .img per (user, team)).
  5. Query Postgres directly through the test backend's session: `SELECT size_gb, img_path FROM workspace_volume WHERE user_id=<alice.id> AND team_id=<alice.personal_team>` — assert size_gb=1 AND img_path matches the orchestrator's `/var/lib/perpetuity/vols/<volume_id>.img` shape.
  6. Run `docker inspect <alice_container_id>` from the test (via subprocess against compose's docker socket — the test runs from the host) and assert `HostConfig.Memory == 2147483648`, `HostConfig.PidsLimit == 512`, `HostConfig.NanoCpus == 1000000000`.
  7. Log redaction sweep (mirrors S01's T06): `docker compose logs orchestrator backend | grep -E '<alice.email>|<alice.full_name>|<bob.email>|<bob.full_name>'` — assert ZERO matches across orchestrator and backend logs (MEM134).
  8. Cleanup: DELETE both sessions; orchestrator should unmount alice's volume on session-tear-down ONLY if no live sessions remain on the container — but per S01's lifecycle, container reaping is S04's job, so for now the volume stays mounted and the test does not assert tear-down of the .img. The orchestrator process (next test run) finds the existing row+.img and reuses both — that's the idempotency path covered in T03 unit tests, asserted indirectly here by the test fixture's label-scoped cleanup not touching the volumes.

Wall-clock budget: ≤ 60 s per the milestone success criterion. The 1-GB mkfs.ext4 is the slowest single step (~500 ms); dd of 1100 MB is bounded by ENOSPC and exits within ~3 s. Total expected ≈ 25-35 s, comfortably under budget.

This test is the demo-truth statement: 'a workspace with size_gb=1 honors a kernel-enforced hard cap, neighbors are isolated, the workspace_volume row matches disk, container resource limits hold, and observability logs do not leak PII.' If every prior task is complete and this test passes, the slice goal is true.
  - Files: `backend/tests/integration/test_m002_s02_volume_cap_e2e.py`, `backend/tests/integration/conftest.py`
  - Verify: cd backend && uv run pytest -m e2e tests/integration/test_m002_s02_volume_cap_e2e.py -v

## Files Likely Touched

- backend/app/alembic/versions/s04_workspace_volume.py
- backend/app/models.py
- backend/tests/migrations/test_s04_migration.py
- orchestrator/orchestrator/volumes.py
- orchestrator/orchestrator/errors.py
- orchestrator/tests/integration/test_volumes.py
- orchestrator/orchestrator/volume_store.py
- orchestrator/orchestrator/sessions.py
- orchestrator/orchestrator/main.py
- orchestrator/orchestrator/config.py
- docker-compose.yml
- orchestrator/tests/integration/test_sessions_lifecycle.py
- backend/tests/integration/test_m002_s02_volume_cap_e2e.py
- backend/tests/integration/conftest.py
