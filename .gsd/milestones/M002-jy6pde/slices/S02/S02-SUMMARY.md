---
id: S02
parent: M002-jy6pde
milestone: M002-jy6pde
provides:
  - ["workspace_volume Postgres table (s04 alembic revision, fully reversible)", "orchestrator/orchestrator/volumes.py — async loopback-ext4 manager (allocate/mount/unmount, idempotent, step-tagged failures)", "orchestrator/orchestrator/volume_store.py — asyncpg pool + ensure_volume_for find-or-create-allocate-mount composer", "VolumeProvisionFailed(step, reason) → 500 {detail, step, reason} HTTP contract", "WorkspaceVolumeStoreUnavailable → 503 {detail, reason} HTTP contract", "Per-(user, team) kernel-enforced size cap via ext4 hard cap on a sparse .img file", "Per-container resource limits re-verified on every spawn: Memory=2 GiB, PidsLimit=512, NanoCpus=1e9", "Compose: orchestrator privileged: true + /var/lib/perpetuity/{vols,workspaces} binds (workspaces :rshared) + workspace-mount-init sidecar + _ensure_loop_device_nodes boot helper"]
requires:
  - slice: M002/S01
    provides: orchestrator service + provision_container + label-scoped container lookup + WS frame protocol — S02 hooks into provision_container without changing the locked S01 in-container path or frame protocol
  - slice: M001/S04 (alembic head s03_team_invites)
    provides: Alembic head before s04_workspace_volume chains on it
affects:
  - ["backend/api: POST /api/v1/sessions now surfaces 500 volume_provision_failed and 503 workspace_volume_store_unavailable from the orchestrator", "orchestrator: now requires privileged: true + asyncpg + e2fsprogs in the image", "compose: workspaces bind requires :rshared propagation; new workspace-mount-init sidecar must run before orchestrator boot; new /var/lib/perpetuity/vols 1:1 bind", "Postgres schema: workspace_volume table (s04 alembic head)"]
key_files:
  - ["backend/app/alembic/versions/s04_workspace_volume.py", "backend/app/models.py", "backend/tests/migrations/test_s04_migration.py", "backend/tests/integration/test_m002_s02_volume_cap_e2e.py", "orchestrator/orchestrator/volumes.py", "orchestrator/orchestrator/volume_store.py", "orchestrator/orchestrator/sessions.py", "orchestrator/orchestrator/main.py", "orchestrator/orchestrator/config.py", "orchestrator/orchestrator/errors.py", "orchestrator/orchestrator/routes_sessions.py", "orchestrator/Dockerfile", "orchestrator/pyproject.toml", "orchestrator/tests/integration/test_volumes.py", "orchestrator/tests/integration/test_sessions_lifecycle.py", "docker-compose.yml"]
key_decisions:
  - ["workspace_volume row + .img file together are the source of truth for per-(user, team) effective volume cap (D015); mkfs_check defaults to False so re-provisions never zero user data (MEM144)", "Orchestrator runs privileged: true on Docker Desktop / linuxkit (SYS_ADMIN alone is insufficient — LOOP_SET_FD EPERM); workspace containers stay unprivileged; /var/lib/perpetuity/workspaces is converted to a shared mountpoint by a workspace-mount-init sidecar and bound :rshared so loopback mounts propagate back to dockerd (MEM136, MEM138, MEM139, MEM145)", "_ensure_loop_device_nodes(count=32) at orchestrator boot mknods /dev/loopN beyond linuxkit's default 8 (MEM140, MEM151)", "VolumeProvisionFailed exposes both .step (closed enum) and .reason (200-char first-line stderr) so handlers emit {detail, step, reason} without re-parsing; reason hard-capped to prevent neighbor uuid-keyed path leakage from losetup output (MEM134, MEM146)", "Concurrent-create race on workspace_volume(user_id, team_id) handled via asyncpg.UniqueViolationError catch + refetch (MEM148)", "DEFAULT_VOLUME_SIZE_GB is boot-time orchestrator env (no per-request override); S03 will swap the hardcoded default for a system_settings.workspace_volume_size_gb lookup", "Asyncpg pool is best-effort at orchestrator boot — warn + serve, not exit — so health/auth keep working in transient pg windows; POST /v1/sessions surfaces 503 workspace_volume_store_unavailable until pg is reachable", "Backend image bakes alembic versions; new revisions require docker compose build backend before e2e tests work (MEM147)", "Live-orchestrator swap pattern for e2e tests requiring different orchestrator config (MEM149)", "WS-piped shell test sentinels MUST be built via printf string-substitution so they do not appear in tmux input echo (MEM150)"]
patterns_established:
  - ["Concurrent-create on a per-(user, team) singleton: catch asyncpg.UniqueViolationError on INSERT, refetch the winner — the unique constraint is the canonical tie-break", "Idempotent host-side resource provisioning: allocate is mkfs-once-by-default (mkfs_check=False); mount is ismount-guarded; unmount is no-op-on-unmounted — re-provision after host reboot or container restart converges without zeroing user data", "Step-tagged subprocess failure surface: VolumeProvisionFailed(step, reason) where step is a closed enum and reason is the first-non-empty stderr line truncated to 200 chars (PII/neighbor-leakage safe by construction)", "Best-effort lifespan resource open: warn + serve on Postgres unreachable rather than crash-loop, so health/auth keep working in transient-pg windows", "Live-orchestrator swap pattern for e2e tests that need different orchestrator config: compose rm + ephemeral docker run with --network-alias + test env, then compose up to restore (sibling backend resolves DNS at request time, swap is invisible)", "WS-piped shell test sentinels constructed via printf string-substitution (e.g. `printf 'EN%sOK_%s' D <uuid>` → ENDOK_<uuid>) so the literal substring is not present in the typed input that tmux echoes back"]
observability_surfaces:
  - ["INFO volume_provisioned volume_id=<uuid> user_id=<uuid> team_id=<uuid> size_gb=N img_path=<uuid-keyed>", "INFO volume_mounted volume_id=<uuid> loop=/dev/loopN mount=<uuid-keyed>", "INFO volume_image_allocated volume_id=<uuid> img_path=<uuid-keyed> size_gb=N", "INFO volume_unmounted mount=<uuid-keyed>", "INFO volume_reused volume_id=<uuid>", "INFO pg_pool_opened size=5", "INFO loop_devices_ready count=32", "WARNING volume_already_mounted volume_id=<uuid> (idempotent re-provision)", "WARNING pg_pool_open_failed (best-effort orchestrator boot)", "ERROR volume_provision_failed step=<truncate|mkfs|losetup|mount> volume_id=<uuid> reason=<≤200 char first-line stderr>", "DB inspection: SELECT id, user_id, team_id, size_gb, img_path FROM workspace_volume", "Host inspection: losetup -a inside orchestrator (binds .img → /dev/loopN); mount | grep /var/lib/perpetuity/workspaces (ext4 lines); stat /var/lib/perpetuity/vols/<uuid>.img (apparent vs allocated)", "Container inspection: docker inspect <container_id> → HostConfig.{Memory=2147483648, PidsLimit=512, NanoCpus=1000000000}"]
drill_down_paths:
  []
duration: ""
verification_result: passed
completed_at: 2026-04-25T11:30:09.667Z
blocker_discovered: false
---

# S02: Loopback-ext4 hard-cap volumes + per-container resource limits

**Per-(user, team) workspace storage now lives on a sparse loopback-ext4 .img file whose ext4 size is the kernel-enforced hard cap, owned by a workspace_volume Postgres row, with container resource limits (mem=2 GiB, pids=512, cpus=1.0) re-verified on every spawn.**

## What Happened

## What this slice delivered

S02 replaces the plain-dir bind-mount under each (user, team) workspace container with a sparse loopback-ext4 `.img` file, and lifts the per-volume effective `size_gb` cap into a new `workspace_volume` Postgres row. The cap is now enforced by the kernel — `dd` past the limit returns ENOSPC at ~1 GiB and the host filesystem is untouched. Per-container resource limits (mem_limit=2g, pids_limit=512, nano_cpus=1e9) carry over from S01 and are explicitly re-verified by `docker inspect` after each provision.

S02 was implemented as a four-task chain:

- **T01 — Postgres shape (`workspace_volume`).** New table with id (UUID PK), user_id + team_id (both FK with ON DELETE CASCADE), size_gb (INTEGER, app-level 1..256), img_path (VARCHAR(512) UNIQUE — canonical "one volume per file" invariant), created_at. Composite UniqueConstraint(user_id, team_id) named `uq_workspace_volume_user_team` is the D004/MEM004 invariant. Lookup indexes on user_id and team_id. Alembic revision `s04_workspace_volume` chains off `s03_team_invites` and is fully reversible (named indexes are dropped explicitly per MEM025). SQLModel `WorkspaceVolume` lives in `backend/app/models.py`. Migration test follows the MEM016 lock-hazard pattern verbatim.

- **T02 — Volume manager (`orchestrator/orchestrator/volumes.py`).** Self-contained host-side module with three async public functions: `allocate_image(volume_id, size_gb, vols_dir)` runs `truncate -sNG` (sparse) + `mkfs.ext4 -F -q -m 0` (`-m 0` reclaims the 5% root-reserved blocks for these single-user volumes); `mount_image(img_path, mountpoint)` runs `losetup --find --show` + `mount -t ext4` and returns the loop device, idempotent via `os.path.ismount` + `losetup -j` lookup; `unmount_image(mountpoint)` runs `umount` + `losetup -d` and is a no-op on already-unmounted paths. Every subprocess call goes through one `_run` helper that wraps `asyncio.to_thread` and converts non-zero / FileNotFoundError / TimeoutExpired into `VolumeProvisionFailed(step, reason)` where step ∈ {truncate, mkfs, losetup, mount, umount}. Reason is the first non-empty stderr line, truncated to 200 chars — losetup output can leak neighbor uuid-keyed paths and MEM134 forbids that. `mkfs_check` defaults to False so re-provisions never re-mkfs (zeroing user data). Failure cleanup detaches loop devices when mount fails after losetup succeeded. Added `e2fsprogs` to the orchestrator Dockerfile (python:3.12-slim ships util-linux but not mkfs.ext4).

- **T03 — Wire-up + asyncpg.** New `orchestrator/orchestrator/volume_store.py` owns the asyncpg pool (size 5, `command_timeout=5s`) and the `get_volume` / `create_volume` SQL ops; `ensure_volume_for(pool, user_id, team_id, mountpoint)` is the find-or-create + allocate + mount composer. Concurrent-create races on (user_id, team_id) catch `asyncpg.UniqueViolationError` and refetch the winner's row. `provision_container` now calls `ensure_volume_for` before touching Docker, so partial failures leave a recoverable (DB row + .img + maybe-mount) state. `main.py` opens the pool best-effort at lifespan (warns + serves on pg unreachable rather than crash-looping). `VolumeProvisionFailed` → 500 `{detail, step, reason}`; `WorkspaceVolumeStoreUnavailable` → 503 `{detail, reason}`. **Compose changes** (the unanticipated-but-required ones): `privileged: true` on orchestrator (SYS_ADMIN alone is insufficient on Docker Desktop / linuxkit — LOOP_SET_FD returns EPERM); `propagation=rshared` on the workspaces bind so loopback mounts inside the orchestrator propagate back to dockerd's namespace; new `workspace-mount-init` sidecar (`alpine + nsenter -t 1 -m + mount --bind self + mount --make-shared`) that converts the host's `/var/lib/perpetuity/workspaces` into a shared mountpoint BEFORE orchestrator boot. Privilege is bounded to the orchestrator service only — workspace containers (which run user code) stay unprivileged. `_ensure_loop_device_nodes(count=32)` runs at startup because linuxkit's privileged container only ships `/dev/loop0..7`.

- **T04 — End-to-end demo test.** `backend/tests/integration/test_m002_s02_volume_cap_e2e.py` is the slice's demo-truth: sign up two fresh users (alice + bob, RFC-2606 example.com per MEM131), provision alice on an ephemeral orchestrator with `DEFAULT_VOLUME_SIZE_GB=1`, WS-attach, run `dd if=/dev/zero of=/workspaces/<t>/big bs=1M count=1100`, assert ENOSPC at the kernel boundary AND `stat -c %s` ≤ 1.05 GiB. Restore the compose orchestrator (default 4 GiB), provision bob, run `df -BG` (asserts ~4 GiB total + Use% < 10) and `ls -la` (asserts NO `big` entry from alice — neighbor isolation). Query Postgres directly and assert alice's row has `size_gb=1` and a uuid-keyed img_path different from bob's. `docker inspect` on alice's container asserts `HostConfig.Memory == 2147483648`, `PidsLimit == 512`, `NanoCpus == 1000000000`. Final log-redaction sweep (`docker compose logs orchestrator backend | grep <emails+names>`) finds zero matches. Wall-clock 17.87 s — well under the 60 s slice budget.

## Patterns established for downstream slices

- **`workspace_volume` row is the source of truth** for the per-(user, team) effective volume cap. S03 will swap the orchestrator's hardcoded `DEFAULT_VOLUME_SIZE_GB=4` for a `system_settings.workspace_volume_size_gb` lookup; D015 stays — the per-row size_gb is canonical, the system setting only governs new-row creation.
- **Idempotency contract**: `allocate_image` skips mkfs on existing files (`mkfs_check=False` default); `mount_image` short-circuits on `os.path.ismount`; `ensure_volume_for` finds-or-creates the row. A re-provision after host reboot or container restart converges to the same (row, .img, mount) without zeroing user data. S04's reaper + multi-session work depends on this — it can tear down/re-provision freely.
- **Failure surface shape**: `VolumeProvisionFailed(step, reason)` → 500 `{detail, step, reason}` is structured for machine parsing. S03/S04 should reuse `OrchestratorError` + a step-tagged exception when adding their own failure modes.
- **Compose privilege boundary**: orchestrator is `privileged: true`; everything else (backend, db, redis, workspace containers) stays at default. Workspace containers DO NOT inherit privilege from the orchestrator that spawns them.
- **Concurrent-create on a per-(user, team) singleton**: catch `UniqueViolationError`, refetch the winner. S04 will reuse this for the multi-session-per-container case.

## Things downstream slices should know

- The `DEFAULT_VOLUME_SIZE_GB` env knob is **boot-time only** (no per-request override). S03's admin API will write `system_settings.workspace_volume_size_gb`; the orchestrator will read it on each `create_volume` call. Existing rows are NOT migrated — the partial-apply shrink rule lives in S03.
- The backend image bakes alembic versions; **any new migration requires `docker compose build backend`** before e2e tests work (MEM147). S03 lands `system_settings`; expect to rebuild.
- Real loopback only works on Docker Desktop with `privileged: true`. CI runners without `/dev/loop-control` will hit the `requires_loopback` skip in `test_volumes.py`.
- The `workspace-mount-init` sidecar is a one-shot — it has no liveness signal. If host workspaces ever shows up un-shared inside the orchestrator (e.g. after a Docker daemon restart that re-mounts the host bind), provisions will silently fail to enforce the cap. Look for this if S04's reaper sees "size cap not biting" symptoms.
- Tests under `orchestrator/tests/` are NOT baked into the orchestrator image (Dockerfile copies only `orchestrator/orchestrator/`). Either `docker cp orchestrator/tests perpetuity-orchestrator-1:/app/tests` first, or use the `docker run -v $PWD/orchestrator:/work` form (MEM137).

## Verification

## Slice-level verification

All four task verification commands re-ran green at slice closure time (post-restart, fresh state):

| # | Command | Result | Duration |
|---|---------|--------|----------|
| 1 | `cd backend && POSTGRES_PORT=5432 uv run pytest tests/migrations/test_s04_migration.py -v` | 4 passed | 240ms |
| 2 | `docker cp orchestrator/tests perpetuity-orchestrator-1:/app/tests && docker compose exec orchestrator /app/.venv/bin/pytest tests/integration/test_volumes.py -v` | 14 passed (0 skipped — privileged + shared-mount enable real loopback) | 5.70s |
| 3 | `cd backend && uv run pytest -m e2e tests/integration/test_m002_s02_volume_cap_e2e.py -v` | 1 passed (the slice demo) | 21.04s |
| 4 | `cd backend && uv run pytest -m e2e tests/integration/test_m002_s01_e2e.py -v` | 1 passed (S01 regression check — no protocol/lifecycle drift) | 19.78s |

Observability surfaces verified live during T03's run:

- Structured INFO `volume_provisioned volume_id=<uuid> user_id=<uuid> team_id=<uuid> size_gb=N img_path=<uuid-keyed>` and `volume_mounted volume_id=<uuid> loop=/dev/loopN mount=<uuid-keyed>` emit UUIDs only — never email/full_name/team slug. Confirmed by T04's redaction sweep (`docker compose logs orchestrator backend | grep <alice.email>|<alice.full_name>|<bob.email>|<bob.full_name>` returns zero matches).
- Inspection surfaces work: `SELECT id, user_id, team_id, size_gb, img_path FROM workspace_volume` is the DB source of truth; `losetup -a` inside orchestrator binds each .img to a loop device; `mount | grep /var/lib/perpetuity/workspaces` shows ext4; `stat -c %s /var/lib/perpetuity/vols/<uuid>.img` proves apparent vs allocated size.
- Failure visibility: `VolumeProvisionFailed` exceptions carry the failing step (`truncate|mkfs|losetup|mount`) in the `step` field; the orchestrator's exception handler returns 500 with `{detail: 'volume_provision_failed', step, reason}` so the failing step is visible from the backend's POST /api/v1/sessions response. ENOSPC inside the workspace surfaces as a normal write error from the user's `dd` — this is the contract, not a failure mode (proved by T04).
- Redaction: `.img` paths are uuid-keyed by construction so logging them is safe; the 200-char + first-non-empty-line truncation on `VolumeProvisionFailed.reason` prevents `losetup -a`-style stderr from leaking neighbor volumes' paths.

## Operational Readiness

- **Health signal:** orchestrator `/v1/health` plus the `pg_pool_opened size=5` boot log line confirm the asyncpg pool is up. Container is `(healthy)` per compose's healthcheck.
- **Failure signal:** `VolumeProvisionFailed` → HTTP 500 `{detail: 'volume_provision_failed', step, reason}` from POST /v1/sessions. `WorkspaceVolumeStoreUnavailable` → HTTP 503 from POST /v1/sessions. Backend surfaces both verbatim. Orchestrator startup logs `pg_pool_open_failed` if Postgres is unreachable at boot but does NOT crash-loop (best-effort) — POST /v1/sessions then 503s until pg comes up.
- **Recovery procedure:** A failed provision is fully recoverable — `ensure_volume_for` is idempotent, so the next POST /v1/sessions for the same (user, team) finds-or-creates the row, finds the .img already-allocated (or allocates it), and finds the mountpoint already-mounted (or mounts it). User data is never zeroed because `mkfs_check=False` is the default. To force re-mount after a host reboot, just retry POST /v1/sessions.
- **Monitoring gaps:** No metrics endpoint yet (M002 is logs-only by design — Grafana arrives in a later milestone). The `workspace-mount-init` sidecar is a one-shot with no liveness signal; if it ever fails to make the host workspaces dir shared, provisions silently fail to enforce the cap — there is no automated probe for this. The .img orphan path (concurrent-create loser's pre-allocated file lingering in `/var/lib/perpetuity/vols/`) has no cleanup job — uuid-keyed and harmless but disk usage grows unboundedly under sustained race.

## Requirements Advanced

- R005 — Per-(user, team) workspace storage now lives on a kernel-enforced ext4 hard cap (was a plain bind mount in S01); the workspace_volume row is the canonical per-volume cap; the per-container resource limits (Memory=2 GiB, PidsLimit=512, NanoCpus=1e9) are explicitly re-verified on every spawn
- R006 — The .img file + workspace_volume row survive orchestrator restarts (1:1 bind on /var/lib/perpetuity/vols); a re-provision finds-or-creates the row and remounts the existing .img — concrete substrate for S04's idle reaper + S06's volumes-persist-across-shutdowns contract

## Requirements Validated

None.

## New Requirements Surfaced

None.

## Requirements Invalidated or Re-scoped

None.

## Operational Readiness

None.

## Deviations

"- Plan said per-request size_gb_override via TEST_DEFAULT_VOLUME_SIZE_GB. T03 settled on DEFAULT_VOLUME_SIZE_GB as a boot-time orchestrator env (no per-request override). T04 adapts via the live-orchestrator swap pattern (MEM149).
- Plan said cap_add: SYS_ADMIN + /dev/loop-control device passthrough. Reality on Docker Desktop / linuxkit required privileged: true (LOOP_SET_FD EPERM under SYS_ADMIN; AppArmor blocks loop attach). Privilege bounded to orchestrator only — workspace containers stay unprivileged (MEM136).
- Plan did not anticipate that the host workspaces bind needs to be a shared mountpoint AND propagation=rshared on the orchestrator's bind for loopback mounts to propagate back to dockerd. Both required for the size cap to actually bite (MEM138, MEM139). Solution: workspace-mount-init compose sidecar.
- Plan did not anticipate _ensure_loop_device_nodes; needed because linuxkit's privileged container only ships /dev/loop0..7 (MEM140).
- Plan's negative-test 'mkfs.ext4 on a path with a non-existent parent' implemented via mock (orchestrator runs as root, silently creates intermediate parents). Same contract enforced; deterministic equivalent.
- Plan's verification command 'docker compose exec orchestrator pytest …' for the sessions test suite needs the docker CLI which the orchestrator image doesn't ship. Sessions tests run from the host instead with REDIS_PASSWORD/POSTGRES_* env exported (MEM141). Volume tests still run inside the orchestrator container.
- Plan's negative tests for size_gb=0 / size_gb>256 deferred to the orchestrator-level test_volumes.py suite (where they already pass). E2e budget couldn't accommodate a third orchestrator boot."

## Known Limitations

"- DEFAULT_VOLUME_SIZE_GB is a boot-time orchestrator env, not per-request — S03 will swap the hardcoded default for a system_settings.workspace_volume_size_gb lookup with a partial-apply shrink rule.
- workspace-mount-init sidecar is a one-shot with no liveness probe; if the host workspaces bind ever shows up un-shared (e.g. after a Docker daemon restart that re-mounts the host bind), provisions silently fail to enforce the size cap. No automated detection.
- Concurrent-create loser's pre-allocated .img file lingers in /var/lib/perpetuity/vols/ uuid-keyed; harmless individually but no GC under sustained race.
- Real loopback only works on Docker Desktop with privileged: true; CI runners without /dev/loop-control hit the requires_loopback skip in test_volumes.py (10 of 14 tests still cover validation, idempotent shutdown, all 4 step-tagged failure mappings, and timeout contracts without losetup).
- Backend image bakes alembic versions — any new migration requires docker compose build backend before e2e tests work (MEM147). S03 will hit this when it lands system_settings.
- The .env POSTGRES_PORT=55432 vs in-network 5432 drift (MEM021) means migrations must be run with POSTGRES_PORT=5432 override on this dev host. Pre-existing; orchestrator depends_on intentionally skips the prestart service to keep boot clean."

## Follow-ups

"- S03 will replace orchestrator's hardcoded DEFAULT_VOLUME_SIZE_GB=4 with a per-request lookup against system_settings.workspace_volume_size_gb. The workspace_volume row's per-volume size_gb stays canonical for existing volumes (D015 / partial-apply shrink rule).
- S04 will add the idle reaper that tears down workspace containers ONLY when both Redis last_activity and the in-memory active-WS map agree no live attach exists. Tear-down must call unmount_image on the volume mountpoint (idempotent — already wired). The .img file persists; next provision re-mounts it.
- S05's final integrated acceptance test should add a step that signs up a third user, provisions, attaches, writes a sentinel, restarts the orchestrator, and re-attaches — to prove the .img + row survive orchestrator restarts (currently asserted indirectly via T03 idempotency tests + T04 alice survival).
- Reconcile the .env POSTGRES_PORT=55432 vs in-network 5432 drift (MEM021) so prestart can run unattended; until then the orchestrator's depends_on intentionally skips prestart.
- Add a liveness probe for the workspace-mount-init sidecar's outcome (e.g. orchestrator boot asserts /var/lib/perpetuity/workspaces has propagation=shared in /proc/self/mountinfo) so a silently-un-shared host mountpoint is detected immediately rather than discovered when the size cap fails to bite.
- Optional .img orphan GC for concurrent-create losers."

## Files Created/Modified

- `backend/app/alembic/versions/s04_workspace_volume.py` — New alembic revision creating workspace_volume table with named constraints + lookup indexes; fully reversible
- `backend/app/models.py` — Added WorkspaceVolume(SQLModel, table=True) with FK CASCADE + UniqueConstraint(user_id, team_id)
- `backend/tests/migrations/test_s04_migration.py` — Migration test using MEM016 lock-hazard pattern; 4 cases (upgrade shape + FK enforcement, downgrade, dup user_team, dup img_path)
- `backend/tests/integration/test_m002_s02_volume_cap_e2e.py` — Slice S02 e2e demo test (alice 1 GiB ENOSPC + bob 4 GiB neighbor isolation + DB-matches-disk + container limits + log redaction sweep)
- `orchestrator/orchestrator/volumes.py` — Async loopback-ext4 manager (allocate_image/mount_image/unmount_image), step-tagged failures, idempotent, asyncio.to_thread off-loop subprocess
- `orchestrator/orchestrator/volume_store.py` — Asyncpg pool owner + get_volume/create_volume + ensure_volume_for composer; UniqueViolationError race handling
- `orchestrator/orchestrator/sessions.py` — provision_container now calls ensure_volume_for before Docker; partial-failure recoverable state
- `orchestrator/orchestrator/main.py` — Lifespan opens asyncpg pool best-effort; new VolumeProvisionFailed → 500 and WorkspaceVolumeStoreUnavailable → 503 handlers; _ensure_loop_device_nodes(count=32) boot helper
- `orchestrator/orchestrator/config.py` — Added database_url, vols_dir, default_volume_size_gb=4 (S03 will swap hardcoded for system_settings lookup)
- `orchestrator/orchestrator/errors.py` — Added VolumeProvisionFailed(reason, step) and WorkspaceVolumeStoreUnavailable(reason) subclasses of OrchestratorError
- `orchestrator/Dockerfile` — Added e2fsprogs apt install (python:3.12-slim ships util-linux but not mkfs.ext4)
- `orchestrator/pyproject.toml` — Added asyncpg dependency
- `orchestrator/tests/integration/test_volumes.py` — 14 tests (10 loopback-free + 4 loopback-real); requires_loopback gate auto-activates under privileged
- `orchestrator/tests/integration/test_sessions_lifecycle.py` — Extended provision tests assert workspace_volume row + losetup -a + ext4 mount + docker inspect resource limits; new test_provision_idempotent_volume + test_volume_hard_cap_enospc
- `docker-compose.yml` — Orchestrator: privileged: true + /var/lib/perpetuity/{vols,workspaces} binds (workspaces :rshared) + DATABASE_URL + workspace-mount-init sidecar (alpine + nsenter -t 1 -m + mount --bind self + mount --make-shared)
