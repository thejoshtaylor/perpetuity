---
estimated_steps: 9
estimated_files: 3
skills_used: []
---

# T02: Implement loopback-ext4 volume manager (truncate + mkfs + losetup + mount)

Create `orchestrator/orchestrator/volumes.py` — a self-contained host-side module that allocates and mounts a per-(user, team) loopback-ext4 volume. Pure subprocess plumbing, no Docker calls, no Postgres calls. T03 wires this into `provision_container` and into the lifespan-opened pg connection.

Public surface (async functions; orchestrator is asyncio-first):
  - `async def allocate_image(volume_id: str, size_gb: int, vols_dir: str = '/var/lib/perpetuity/vols') -> str` — returns absolute img_path. Steps: `os.makedirs(vols_dir, mode=0o700, exist_ok=True)`, then `subprocess.run(['truncate', '-s', f'{size_gb}G', img_path])` (sparse file — instant, no disk consumed yet), then `subprocess.run(['mkfs.ext4', '-F', '-q', '-m', '0', img_path])` (-m 0 reclaims the 5% root-reserved blocks; this volume is single-user). Idempotent: if img_path already exists with non-zero size, re-mkfs only if `mkfs_check=True` (default False — we trust existing files).
  - `async def mount_image(img_path: str, mountpoint: str) -> str` — returns the loop device assigned (e.g. `/dev/loop3`). Steps: `os.makedirs(mountpoint, mode=0o700, exist_ok=True)`, `subprocess.run(['losetup', '--find', '--show', img_path])` returns the loop device on stdout, then `subprocess.run(['mount', '-t', 'ext4', loop_dev, mountpoint])`. Idempotent: if `mountpoint` is already a mountpoint per `os.path.ismount(mountpoint)`, skip and return the loop device by parsing `losetup -j <img_path>` output.
  - `async def unmount_image(mountpoint: str) -> None` — `umount` + `losetup -d <loop_dev>`. Best-effort; logs a WARNING but does not raise if already unmounted (idempotent shutdown path for tests).
  - Custom exception `VolumeProvisionFailed(reason: str, step: str)` (subclass of `OrchestratorError`) where `step` is one of `truncate|mkfs|losetup|mount|umount`. Mapped to 500 by a new exception handler T03 registers in `main.py`.

Every `subprocess.run` uses `check=True, capture_output=True, text=True, timeout=30` and re-raises subprocess failures as `VolumeProvisionFailed(step=..., reason=stderr_first_line)`. Use `asyncio.to_thread` to call subprocess from async code so the event loop stays responsive (mkfs of a 4 GB ext4 typically completes in <500 ms but the test variant uses 1 GB; mount/losetup are <100 ms).

Why a separate module: keeps `sessions.py` focused on container/tmux concerns (D012/D018 boundary) and lets T02 ship with its own unit + integration tests before T03 touches `provision_container`. Tests run inside the orchestrator container (where SYS_ADMIN is granted per MEM101) — running them on the bare host would require root.

Test plan: `orchestrator/tests/integration/test_volumes.py` runs INSIDE the live compose orchestrator container (the only place SYS_ADMIN is available). Per-test scratch dirs under `/tmp/perpetuity-test-vols/<uuid>/` (vols + mountpoint), torn down by a fixture that always calls `unmount_image` first. Cases: (1) allocate+mount round-trip — `losetup -j` shows the .img associated with a /dev/loopN; the mount appears in `/proc/mounts`. (2) Hard-cap honored — write 1100 MB into a 1-GB volume; subprocess call returns non-zero with ENOSPC in stderr. (3) Idempotent re-mount — calling `mount_image` twice returns the same loop device. (4) Step-tagged failures — mkfs.ext4 on a path with a non-existent parent raises VolumeProvisionFailed(step='mkfs').

## Failure Modes

| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| `truncate` (coreutils) | Raise `VolumeProvisionFailed(step='truncate', reason=stderr_first_line)` | 30 s timeout → raise with reason='timeout' | N/A — coreutils returns exit codes only |
| `mkfs.ext4` (e2fsprogs) | Raise `VolumeProvisionFailed(step='mkfs', reason=stderr_first_line)` | 30 s timeout → raise (1-GB mkfs typically <500 ms; timeout indicates host I/O stall) | N/A |
| `losetup` (util-linux) | Raise `VolumeProvisionFailed(step='losetup', reason=stderr_first_line)` — common: kernel `loop` module not loaded, no free loop devices | 30 s timeout → raise | Unexpected stdout (no `/dev/loopN` line) → raise reason='unparseable_output' |
| `mount` (util-linux) | Raise `VolumeProvisionFailed(step='mount', reason=stderr_first_line)` — common: kernel ext4 not loaded, image corrupted | 30 s timeout → raise | N/A |
| `umount` (best-effort, shutdown path) | Log WARNING `volume_unmount_failed reason=<stderr>` and continue | 10 s timeout → log WARNING and continue | N/A |

## Load Profile

- **Shared resources**: kernel loop devices (default 8 on most distros; `max_loop` boot param can raise to 255) and orchestrator CPU/IO during mkfs.ext4. Each (user, team) holds exactly one loop device for the lifetime of the workspace.
- **Per-operation cost**: 1 truncate (sparse, instant), 1 mkfs.ext4 (~500 ms for 1 GB, scales O(N) with size), 1 losetup (<100 ms), 1 mount (<100 ms). Memory: negligible. Disk: sparse — no allocation until first write inside the workspace.
- **10x breakpoint**: kernel loop-device exhaustion at ~8 concurrent workspaces on a default-configured host. `losetup --find` returns ENOSPC-on-loop-devices as a `losetup` step error today; a future ops milestone will raise `max_loop` via boot param. Documented; not blocking S02 because dev-deployment scale is well below 8 active workspaces.

## Negative Tests

- **Malformed inputs**: `allocate_image(size_gb=0)` raises ValueError before subprocess call. `mount_image(img_path='/does/not/exist')` raises VolumeProvisionFailed(step='losetup'). `unmount_image('/not-a-mountpoint')` is a no-op (idempotent shutdown).
- **Error paths**: simulate kernel-loop-exhaustion via injected fake `losetup` returning non-zero (unit test); simulate ENOSPC on host filesystem during truncate → VolumeProvisionFailed(step='truncate').
- **Boundary conditions**: minimum 1-GB volume (the test variant's value), maximum 256-GB volume (config-bounded). Idempotent mount (called twice on same path returns the same loop device). Idempotent unmount (called on already-unmounted path returns silently).

## Inputs

- ``orchestrator/orchestrator/errors.py``
- ``orchestrator/orchestrator/sessions.py``
- ``orchestrator/orchestrator/config.py``
- ``orchestrator/tests/integration/conftest.py``

## Expected Output

- ``orchestrator/orchestrator/volumes.py``
- ``orchestrator/orchestrator/errors.py``
- ``orchestrator/tests/integration/test_volumes.py``

## Verification

docker compose exec orchestrator /app/.venv/bin/pytest tests/integration/test_volumes.py -v

## Observability Impact

New INFO log keys: `volume_image_allocated volume_id=<uuid> img_path=<path> size_gb=N`, `volume_mounted volume_id=<uuid> loop=/dev/loopN mount=<path>`, `volume_unmounted volume_id=<uuid> mount=<path>`. ERROR `volume_provision_failed step=<truncate|mkfs|losetup|mount> volume_id=<uuid> reason=<first-line-of-stderr-truncated-to-200-chars>`. Failure inspection: a future agent who hits a failed provision can re-run the failing subprocess by hand from inside the orchestrator container — the `step` field pins which command to retry. UUID-only logging discipline preserved (MEM134); never log paths that contain user email.
