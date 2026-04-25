"""Per-(user, team) loopback-ext4 volume manager (S02 / T02).

Pure subprocess plumbing — no Docker, no Postgres. T03 wires this into
`provision_container` and the lifespan-opened pg connection.

Why a separate module: keeps `sessions.py` focused on container/tmux
concerns (D012/D018 boundary) and lets T02 ship with its own integration
tests before T03 touches `provision_container`.

Surface (asyncio-first; orchestrator is a FastAPI/asyncio process):
  - `allocate_image(volume_id, size_gb, vols_dir)` → `truncate` (sparse) +
    `mkfs.ext4 -F -q -m 0`. Idempotent on existing files unless
    `mkfs_check=True` is passed (default False — re-mkfs would zero a
    user's data).
  - `mount_image(img_path, mountpoint)` → `losetup --find --show` +
    `mount -t ext4`. Idempotent: if `mountpoint` is already a mountpoint,
    parses `losetup -j <img_path>` and returns the existing loop device.
  - `unmount_image(mountpoint)` → `umount` + `losetup -d`. Best-effort;
    logs WARNING but does NOT raise on already-unmounted state (idempotent
    shutdown path for tests).

All `subprocess.run` calls go through `asyncio.to_thread` so the event
loop stays responsive — mkfs of a 4 GB ext4 typically completes in
<500 ms but the slice's hard-cap variant uses 1 GB; mount/losetup are
<100 ms each.

Failures raise `VolumeProvisionFailed(step=..., reason=stderr_first_line)`
where `step ∈ {truncate, mkfs, losetup, mount, umount}`. The `step` field
pins which command to re-run by hand from inside the container.

Logging discipline (MEM134): UUIDs only — never log host paths that
could carry email or team slug. The .img path is uuid-keyed by
construction so it's safe to log directly.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from typing import Final

from orchestrator.errors import VolumeProvisionFailed

logger = logging.getLogger("orchestrator")


_SUBPROCESS_TIMEOUT_SECONDS: Final[int] = 30
# umount runs on the shutdown path — give it less rope so a stuck
# unmount doesn't drag out test teardown for half a minute.
_UMOUNT_TIMEOUT_SECONDS: Final[int] = 10
_MAX_REASON_CHARS: Final[int] = 200


def _short_reason(stderr: str | None) -> str:
    """First non-empty line of stderr, truncated to 200 chars.

    Logs/exceptions only ever surface this short form so we never leak
    a multi-line stderr containing neighbor volumes' paths.
    """
    if not stderr:
        return ""
    for line in stderr.splitlines():
        line = line.strip()
        if line:
            return line[:_MAX_REASON_CHARS]
    return ""


def _run_subprocess_sync(
    cmd: list[str],
    *,
    timeout: int = _SUBPROCESS_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Synchronous subprocess.run intended to be called via asyncio.to_thread.

    `check=False` here — callers inspect `returncode` and raise the
    domain-specific `VolumeProvisionFailed(step=...)`. Going through
    `check=True` would force us to convert `CalledProcessError` back into
    our shape twice, which obscures the failing step.
    """
    return subprocess.run(  # noqa: S603 — argv is a fixed allowlist below
        cmd,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


async def _run(
    cmd: list[str],
    step: str,
    *,
    timeout: int = _SUBPROCESS_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    """Run `cmd` off-loop and convert non-zero / timeout into VolumeProvisionFailed.

    `step` pins the dependency so the caller does not have to repeat it
    in two places. We deliberately do NOT log the raw stderr — `_short_reason`
    truncates it (MEM134) before it reaches a log line or exception body.
    """
    try:
        result = await asyncio.to_thread(
            _run_subprocess_sync, cmd, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise VolumeProvisionFailed(reason="timeout", step=step) from exc
    except FileNotFoundError as exc:
        # The binary itself is missing — distinct from a non-zero exit.
        raise VolumeProvisionFailed(
            reason=f"binary_not_found:{cmd[0]}", step=step
        ) from exc
    if result.returncode != 0:
        raise VolumeProvisionFailed(
            reason=_short_reason(result.stderr) or f"exit:{result.returncode}",
            step=step,
        )
    return result


async def allocate_image(
    volume_id: str,
    size_gb: int,
    vols_dir: str = "/var/lib/perpetuity/vols",
    *,
    mkfs_check: bool = False,
) -> str:
    """Allocate a sparse `<volume_id>.img` of `size_gb` GiB and mkfs.ext4 it.

    Returns the absolute img_path. Steps:
      1. `os.makedirs(vols_dir, mode=0o700, exist_ok=True)`
      2. `truncate -s <size_gb>G <img_path>` — sparse, instant, no disk
         actually allocated until first write
      3. `mkfs.ext4 -F -q -m 0 <img_path>` — `-m 0` reclaims the 5%
         root-reserved blocks (this volume is single-user)

    Idempotency: if `img_path` already exists with non-zero size, we skip
    mkfs unless `mkfs_check=True`. The default is `False` because re-running
    mkfs.ext4 on a populated .img would zero the user's data — the loud
    crash on T03's re-provision path is the wrong shape; trusting the
    existing file is the right shape (T03 also persists the (user, team)
    → img_path mapping in Postgres so we don't blindly trust strangers).
    """
    if size_gb <= 0:
        # Defensive — `truncate -s 0G` would create an empty file and
        # mkfs.ext4 would then fail with a less-helpful error. Surface
        # the bad input at the boundary instead.
        raise ValueError(f"size_gb must be >= 1, got {size_gb}")
    os.makedirs(vols_dir, mode=0o700, exist_ok=True)
    img_path = os.path.join(vols_dir, f"{volume_id}.img")

    already_exists = (
        os.path.isfile(img_path) and os.path.getsize(img_path) > 0
    )
    if already_exists and not mkfs_check:
        logger.info(
            "volume_image_reused volume_id=%s size_gb=%d",
            volume_id,
            size_gb,
        )
        return img_path

    try:
        await _run(["truncate", "-s", f"{size_gb}G", img_path], step="truncate")
        await _run(
            ["mkfs.ext4", "-F", "-q", "-m", "0", img_path], step="mkfs"
        )
    except VolumeProvisionFailed as exc:
        logger.error(
            "volume_provision_failed step=%s volume_id=%s reason=%s",
            exc.step,
            volume_id,
            exc.reason,
        )
        raise

    logger.info(
        "volume_image_allocated volume_id=%s img_path=%s size_gb=%d",
        volume_id,
        img_path,
        size_gb,
    )
    return img_path


async def _losetup_lookup(img_path: str) -> str | None:
    """Return the loop device backing `img_path`, or None.

    `losetup -j <img_path>` prints `/dev/loopN: [<dev>]:<inode> (<img_path>)`
    on the first line per association, exit 0 with empty stdout if no
    association exists. We parse the first `/dev/loop` token.
    """
    # `losetup -j` exits 0 even on no-match, so we use `_run_subprocess_sync`
    # directly rather than `_run` (which would only raise on non-zero).
    try:
        result = await asyncio.to_thread(
            _run_subprocess_sync, ["losetup", "-j", img_path]
        )
    except subprocess.TimeoutExpired as exc:
        raise VolumeProvisionFailed(reason="timeout", step="losetup") from exc
    except FileNotFoundError as exc:
        raise VolumeProvisionFailed(
            reason="binary_not_found:losetup", step="losetup"
        ) from exc
    if result.returncode != 0:
        raise VolumeProvisionFailed(
            reason=_short_reason(result.stderr) or f"exit:{result.returncode}",
            step="losetup",
        )
    stdout = result.stdout or ""
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("/dev/loop"):
            # Format: `/dev/loopN: [...]:... (path)` — split on `:` for
            # the device, fall back to whitespace.
            head = line.split(":", 1)[0].strip()
            if head.startswith("/dev/loop"):
                return head
    return None


def _parse_loop_device(stdout: str) -> str | None:
    """Pull the first `/dev/loopN` token out of `losetup --find --show` stdout."""
    for line in (stdout or "").splitlines():
        line = line.strip()
        if line.startswith("/dev/loop"):
            return line.split()[0]
    return None


async def mount_image(img_path: str, mountpoint: str) -> str:
    """Mount `img_path` (an ext4-formatted file) at `mountpoint` via loopback.

    Returns the loop device assigned (e.g. `/dev/loop3`). Steps:
      1. `os.makedirs(mountpoint, mode=0o700, exist_ok=True)`
      2. If `mountpoint` is already a mountpoint, look up the loop device
         via `losetup -j <img_path>` and return — fully idempotent,
         allowing T03's re-provision path to short-circuit.
      3. `losetup --find --show <img_path>` — kernel picks a free loop
         device, prints it to stdout (e.g. `/dev/loop3`)
      4. `mount -t ext4 <loop_dev> <mountpoint>`

    Idempotency rule: if `os.path.ismount(mountpoint)` is true we trust it.
    A mountpoint can only be a mountpoint if a previous mount succeeded;
    re-running losetup would just consume another scarce loop device.

    Failure cleanup: if `mount` fails AFTER `losetup --find` succeeded, we
    detach the loop device so we don't leak it (the kernel default is 8
    loop devices; leaking on every failed mount would exhaust them fast).
    """
    os.makedirs(mountpoint, mode=0o700, exist_ok=True)

    if os.path.ismount(mountpoint):
        existing = await _losetup_lookup(img_path)
        if existing is None:
            # Mountpoint is mounted but our img_path isn't bound to a loop
            # device — that's a foreign mount we don't own. This is the
            # only reasonable place to flag the discrepancy; refuse to
            # claim it.
            raise VolumeProvisionFailed(
                reason="mountpoint_owned_by_other_image", step="losetup"
            )
        logger.warning(
            "volume_already_mounted img_path=%s loop=%s mount=%s",
            img_path,
            existing,
            mountpoint,
        )
        return existing

    losetup = await _run(
        ["losetup", "--find", "--show", img_path], step="losetup"
    )
    loop_dev = _parse_loop_device(losetup.stdout)
    if loop_dev is None:
        raise VolumeProvisionFailed(
            reason="unparseable_output", step="losetup"
        )

    try:
        await _run(["mount", "-t", "ext4", loop_dev, mountpoint], step="mount")
    except VolumeProvisionFailed:
        # Best-effort cleanup of the leaked loop device. `losetup -d` on a
        # device we just allocated should always succeed; if it doesn't we
        # log and propagate the original mount failure (the more useful
        # of the two errors for diagnostics).
        try:
            await _run(["losetup", "-d", loop_dev], step="losetup")
        except VolumeProvisionFailed as cleanup_exc:
            logger.warning(
                "volume_loop_detach_failed loop=%s reason=%s",
                loop_dev,
                cleanup_exc.reason,
            )
        raise

    logger.info(
        "volume_mounted loop=%s mount=%s img_path=%s",
        loop_dev,
        mountpoint,
        img_path,
    )
    return loop_dev


async def unmount_image(mountpoint: str) -> None:
    """Best-effort umount of `mountpoint` plus detach of its loop device.

    Idempotent shutdown path for tests and the future S04 reaper. Logs
    WARNING on every non-fatal hiccup but never raises — the worst case
    on shutdown is a leaked loop device, which is bounded by the kernel
    cap and reclaimable by `losetup -D` from the host.

    Order: `umount` first (so the kernel releases the ext4 superblock
    reference), then `losetup -d` (so the loop device is freed). Reversing
    the order returns EBUSY from `losetup -d`.
    """
    if not os.path.ismount(mountpoint):
        # Fully idempotent — calling unmount on a path that was never
        # mounted (e.g. the test fixture's first-time setup) is a no-op.
        return

    # Capture the loop device BEFORE umount so we still have it after
    # the kernel disassociates the mount.
    loop_dev: str | None = None
    try:
        # Read /proc/mounts to find the loop device backing this mountpoint.
        # `findmnt -n -o SOURCE <mountpoint>` would also work but adds a
        # dependency; /proc/mounts is always available and parses cleanly.
        with open("/proc/mounts") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == mountpoint:
                    if parts[0].startswith("/dev/loop"):
                        loop_dev = parts[0]
                    break
    except OSError as exc:
        # /proc/mounts read failure is exotic but survivable — we'll fall
        # back to umount-only and skip the loop detach.
        logger.warning(
            "volume_unmount_proc_read_failed mount=%s reason=%s",
            mountpoint,
            type(exc).__name__,
        )

    try:
        await _run(
            ["umount", mountpoint],
            step="umount",
            timeout=_UMOUNT_TIMEOUT_SECONDS,
        )
    except VolumeProvisionFailed as exc:
        logger.warning(
            "volume_unmount_failed mount=%s reason=%s",
            mountpoint,
            exc.reason,
        )
        # Don't try to detach the loop device if umount failed — that
        # would EBUSY and add log noise. Caller can retry.
        return

    if loop_dev is not None:
        try:
            await _run(
                ["losetup", "-d", loop_dev],
                step="umount",
                timeout=_UMOUNT_TIMEOUT_SECONDS,
            )
        except VolumeProvisionFailed as exc:
            logger.warning(
                "volume_loop_detach_failed loop=%s reason=%s",
                loop_dev,
                exc.reason,
            )
            return

    logger.info("volume_unmounted mount=%s", mountpoint)
