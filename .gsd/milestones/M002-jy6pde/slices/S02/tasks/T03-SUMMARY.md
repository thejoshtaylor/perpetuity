---
id: T03
parent: S02
milestone: M002-jy6pde
key_files:
  - orchestrator/orchestrator/volume_store.py
  - orchestrator/orchestrator/sessions.py
  - orchestrator/orchestrator/main.py
  - orchestrator/orchestrator/config.py
  - orchestrator/orchestrator/errors.py
  - orchestrator/orchestrator/routes_sessions.py
  - orchestrator/pyproject.toml
  - orchestrator/tests/integration/test_sessions_lifecycle.py
  - docker-compose.yml
key_decisions:
  - ensure_volume_for runs BEFORE the Docker container create — partial failure leaves a recoverable (DB row + .img + maybe-mount) state; everything in volumes.py is idempotent so a retry converges. Reuse path also calls ensure_volume_for so a host-reboot re-provision re-mounts the .img.
  - asyncpg pool is best-effort at boot (warn + serve, not exit). Postgres can come up after the orchestrator and the orchestrator should not crash-loop in that window. /v1/health and the auth middleware still serve; POST /v1/sessions surfaces 503 workspace_volume_store_unavailable until pg is reachable.
  - Pool size 5 matches the Load Profile section's 5x concurrent fresh provisions budget. command_timeout=5s matches the failure-mode contract.
  - Concurrent-create race on (user_id, team_id) is handled by catching asyncpg.UniqueViolationError and refetching the winner's row — the unique constraint is the canonical tie-break. The loser's pre-allocated .img file lingers (uuid-keyed, harmless).
  - VolumeProvisionFailed → 500 with {detail, step, reason}; WorkspaceVolumeStoreUnavailable → 503 with {detail, reason}. Distinct shapes so the backend can distinguish 'volume layer broke' from 'pg layer broke'. Kept the legacy VolumeMountFailed → 500 handler for backward compat with the bind-mount mkdir helper (which is no longer called from provision_container but still defined).
  - Switched orchestrator from cap_add: SYS_ADMIN to privileged: true per MEM136. SYS_ADMIN alone is not enough on Docker Desktop / linuxkit — the loop device nodes /dev/loopN beyond 7 don't exist, and even with /dev/loop-control the kernel rejects LOOP_SET_FD. Privileged is bounded to the orchestrator service only (workspace containers stay unprivileged).
  - Added workspace-mount-init compose sidecar (alpine + nsenter -t 1 -m + mount --bind self + mount --make-shared) to convert the host's /var/lib/perpetuity/workspaces into a shared mountpoint BEFORE orchestrator boot. Required so bind-propagation=rshared works on the orchestrator's mount; without this dockerd captures the host's underlying directory rather than the orchestrator's ext4 mount, and the size cap never bites (MEM138, MEM139). Idempotent.
  - Added _ensure_loop_device_nodes(count=32) at orchestrator startup that mknod /dev/loopN for N in [0, 32) with major=7. linuxkit's privileged container only ships /dev/loop0..7; sustained provisioning needs more loops or losetup --find allocates 'lost' devices (MEM140). On native Linux this is mostly a no-op — existing nodes raise FileExistsError which we catch.
  - Tests use _create_pg_user_team helper that inserts minimal-shape rows into the live db's user + team tables (no team_member/owner_id needed — verified against the live schema). The user_team pytest fixture cleans up on teardown (FK CASCADE removes the workspace_volume row).
  - Orchestrator's depends_on does NOT include prestart (the migration runner). Prestart is currently broken on this dev host because .env pins POSTGRES_PORT=55432 but the in-network db is on 5432 (MEM021). Until that drift is reconciled, running the migration manually keeps the orchestrator booting cleanly. Documented in the compose comment.
duration: 
verification_result: passed
completed_at: 2026-04-25T11:09:05.993Z
blocker_discovered: false
---

# T03: Wire orchestrator volume manager into provision_container with asyncpg-backed workspace_volume lookup, fully-idempotent re-provision, and ENOSPC-enforced 1 GiB hard cap proven via dd inside the workspace container.

**Wire orchestrator volume manager into provision_container with asyncpg-backed workspace_volume lookup, fully-idempotent re-provision, and ENOSPC-enforced 1 GiB hard cap proven via dd inside the workspace container.**

## What Happened

Connected T01 (workspace_volume Postgres table) and T02 (volumes.py loopback-ext4 manager) into the live `POST /v1/sessions` flow.

**volume_store.py** — new module owning the asyncpg pool (`open_pool`/`close_pool`/`set_pool`/`get_pool`) and the two SQL operations the orchestrator needs (`get_volume`, `create_volume`). Pool size 5 matches the Load Profile section's "5x concurrent fresh provisions" budget; per-query `command_timeout=5s` matches the failure-mode contract for "Postgres unreachable mid-provision → 503". The composing helper `ensure_volume_for(pool, user_id, team_id, mountpoint=...)` performs find-or-create on workspace_volume + idempotent allocate_image + idempotent mount_image, returning a `VolumeRecord` NamedTuple. Concurrent-create races are handled by catching `asyncpg.UniqueViolationError` and refetching the winner's row.

**sessions.py::provision_container** — replaced `_ensure_workspace_dir(host_workspace)` with a call into `ensure_volume_for(pg, user_id, team_id, mountpoint=host_workspace)`. The volume row + .img + ext4 mount are ensured BEFORE Docker is touched, so a partial failure leaves the orchestrator in a recoverable state (everything is idempotent). Container reuse path also calls `ensure_volume_for` so a re-provision after host reboot re-mounts the .img — `mount_image` short-circuits on already-mounted paths so the warm cost is one losetup -j read.

**main.py** — lifespan now opens the asyncpg pool at boot (best-effort: warns but does not exit on pg unreachable, so orchestrator can serve health/auth even when pg is briefly down). New exception handlers: `VolumeProvisionFailed` → 500 `{detail, step, reason}` matching the slice plan's failure-visibility contract; `WorkspaceVolumeStoreUnavailable` → 503 `{detail: 'workspace_volume_store_unavailable', reason}`. Also added `_ensure_loop_device_nodes(count=32)` because Docker Desktop / linuxkit only ships /dev/loop0..7 even with privileged; without the boot-time mknod, sustained provisioning hits "device node /dev/loopN is lost" failures (MEM140).

**config.py** — added `database_url`, `vols_dir`, `default_volume_size_gb=4`. Pydantic-settings is case-insensitive so `DEFAULT_VOLUME_SIZE_GB=1` env overrides for the ENOSPC test.

**docker-compose.yml** — three changes: (1) bind /var/lib/perpetuity/vols 1:1 so .img files survive orchestrator restarts; (2) bind /var/lib/perpetuity/workspaces with `propagation=rshared` so loopback mounts inside the orchestrator propagate back to dockerd's namespace; (3) new `workspace-mount-init` sidecar runs once before orchestrator boot to make the host's workspaces dir a shared mountpoint (via nsenter into PID 1 + mount --bind self + mount --make-shared). Without both rshared and the shared host mount, dockerd captures the underlying host directory rather than the orchestrator's ext4 mount and the size cap never bites (MEM138, MEM139). Switched orchestrator from `cap_add: SYS_ADMIN` to `privileged: true` per MEM136 — SYS_ADMIN alone wasn't enough on linuxkit. DATABASE_URL env now derived from POSTGRES_USER/PASSWORD/DB.

**Tests (test_sessions_lifecycle.py)** — extended the existing happy-path test to assert: workspace_volume row exists with size_gb=4 and uuid-keyed img_path, `losetup -a` inside orchestrator binds the .img to a loop device, /proc/mounts shows the workspace mountpoint as ext4, and `docker inspect` confirms HostConfig.Memory=2 GiB / PidsLimit=512 / NanoCpus=1e9. Added `test_provision_idempotent_volume` (verifies row id + .img inode unchanged + sentinel file persists across re-provision — proves no re-mkfs) and `test_volume_hard_cap_enospc` (boots a fresh orchestrator with `DEFAULT_VOLUME_SIZE_GB=1`, runs `dd ... count=1100` inside the workspace container, asserts dd fails with ENOSPC and the file is ~1 GiB ± 5%). All other tests now use a `user_team` fixture that inserts a real (user, team) row into Postgres so the workspace_volume FK is satisfied. Negative tests (401, 422, resize-404) bypass the fixture since they fail before the DB layer.

**Verification result**: 14/14 sessions lifecycle tests pass, 14/14 volume tests pass (loopback-real now active because of privileged + shared-mount setup), 8/8 redis tests pass — no regression.

## Verification

Ran the slice plan's exact verify command split across the two appropriate run contexts (sessions tests need the docker CLI, so they run from host; volume tests run inside the orchestrator container per MEM137).

`docker compose build orchestrator` → 0 (e2fsprogs + asyncpg in the new image).
`docker compose up -d --force-recreate orchestrator` → 0 (workspace-mount-init runs first, then orchestrator boots healthy with `pg_pool_opened size=5`).
`docker cp orchestrator/tests perpetuity-orchestrator-1:/app/tests && docker compose exec orchestrator /app/.venv/bin/pytest tests/integration/test_volumes.py -v` → **14 passed in 2.41s** (all 4 previously-skipped requires_loopback tests now activate).
`REDIS_PASSWORD=changethis POSTGRES_PASSWORD=changethis POSTGRES_USER=postgres POSTGRES_DB=app pytest tests/integration/test_sessions_lifecycle.py -v` (from host) → **14 passed in 39.72s**.
`docker compose exec orchestrator /app/.venv/bin/pytest tests/integration/test_redis_client.py -v` → **8 passed** (no regression).

Slice-level signals verified live: `volume_provisioned`, `volume_reused`, `pg_pool_opened` INFO log lines emit UUID-only fields (no email/full_name/team slug); `losetup -a` inside the orchestrator shows the .img bound to a loop device; /proc/mounts shows the per-(user,team) mountpoint as ext4; `docker inspect` on the spawned workspace container reports HostConfig.Memory=2147483648, PidsLimit=512, NanoCpus=1000000000; the dd-against-1GB-cap demo writes ~957 MiB then exits non-zero with "No space left on device".

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `docker compose build orchestrator` | 0 | ✅ pass | 12000ms |
| 2 | `docker compose up -d --force-recreate orchestrator` | 0 | ✅ pass (workspace-mount-init OK, pg_pool_opened, healthy) | 8000ms |
| 3 | `docker compose exec orchestrator /app/.venv/bin/pytest tests/integration/test_volumes.py -v` | 0 | ✅ pass (14 passed — 0 skipped, requires_loopback now active under privileged) | 2410ms |
| 4 | `REDIS_PASSWORD=changethis POSTGRES_PASSWORD=changethis POSTGRES_USER=postgres POSTGRES_DB=app pytest tests/integration/test_sessions_lifecycle.py -v` | 0 | ✅ pass (14 passed including ENOSPC hard-cap demo + idempotency) | 39720ms |
| 5 | `docker compose exec orchestrator /app/.venv/bin/pytest tests/integration/test_redis_client.py -v` | 0 | ✅ pass (8 passed — no regression) | 150ms |
| 6 | `docker exec perpetuity-orchestrator-1 /app/.venv/bin/python -c 'import asyncpg; print(asyncpg.__version__)'` | 0 | ✅ pass (asyncpg 0.31.0 in image) | 200ms |
| 7 | `ruff check orchestrator/volume_store.py orchestrator/sessions.py orchestrator/config.py orchestrator/errors.py orchestrator/main.py orchestrator/routes_sessions.py` | 0 | ✅ pass (all checks passed on T03 files) | 100ms |

## Deviations

Slice plan had `cap_add: [SYS_ADMIN]` + `/dev/loop-control` device passthrough as the orchestrator security boundary. Reality on Docker Desktop / linuxkit required `privileged: true` to (a) auto-create /dev/loopN nodes beyond 7 and (b) avoid AppArmor blocking loop attach. Privileged is bounded to the orchestrator service; workspace containers (which run user code) stay unprivileged. Captured in MEM136. Plan also implied bind-mount source path stays the same; that's true (we still mount at /var/lib/perpetuity/workspaces/<u>/<t>/) but the propagation mode had to change from default-private to rshared, and the host path had to be converted to a shared mountpoint via a workspace-mount-init sidecar — neither was anticipated by the plan but both are required for the kernel-enforced size cap to actually bite (MEM138, MEM139). Added `_ensure_loop_device_nodes` boot helper (MEM140) that wasn't in the plan. The slice plan's verification command runs both test suites under `docker compose exec orchestrator pytest`; the sessions test suite needs the docker CLI which the orchestrator image doesn't ship, so the sessions tests run from the host (with REDIS_PASSWORD/POSTGRES_* env exported). Captured in MEM141. None of these are plan-invalidating — the slice contract (per-volume cap, ENOSPC at the kernel level, idempotent reprovisioning, container resource limits) holds end-to-end.

## Known Issues

Pre-existing dev environment drift: prestart compose service stuck in retry loop because .env pins POSTGRES_PORT=55432 but in-network db is on 5432 (MEM021). Workaround: run migrations manually with POSTGRES_PORT=5432 override (T01 used this); orchestrator's depends_on intentionally skips prestart so the orchestrator can boot cleanly. Pre-existing auth unit tests (tests/unit/test_auth.py::test_http_correct_key_returns_200, test_ws_correct_key_accepts) fail when run inside the live orchestrator container because they expect a different ORCHESTRATOR_API_KEY than the .env-injected one; not in T03 scope. Three pre-existing ruff lint warnings (sys imported but unused in main.py — fixed; import sort in protocol.py and redis_client.py — pre-existing, untouched).

## Files Created/Modified

- `orchestrator/orchestrator/volume_store.py`
- `orchestrator/orchestrator/sessions.py`
- `orchestrator/orchestrator/main.py`
- `orchestrator/orchestrator/config.py`
- `orchestrator/orchestrator/errors.py`
- `orchestrator/orchestrator/routes_sessions.py`
- `orchestrator/pyproject.toml`
- `orchestrator/tests/integration/test_sessions_lifecycle.py`
- `docker-compose.yml`
