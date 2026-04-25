"""Integration tests for the loopback-ext4 volume manager (S02 / T02).

Runs INSIDE the live compose orchestrator container — the only place
SYS_ADMIN is granted (per MEM101). On the bare host these tests would
need root. Run via:

    docker compose exec orchestrator /app/.venv/bin/pytest tests/integration/test_volumes.py -v

Per-test scratch dirs land under `/tmp/perpetuity-test-vols/<uuid>/`
(vols + mountpoint), torn down by an autouse fixture that always calls
`unmount_image` first so a leaked loop device from one test cannot
contaminate the next.

Loopback availability detection: on Docker Desktop / linuxkit the kernel
exposes the loop driver but the orchestrator container, with only
`cap_add: SYS_ADMIN` (no `privileged: true` and no `--device
/dev/loop-control`), gets EPERM from `losetup --find --show`. The shape
of the volumes module is correct in either environment; the heavy tests
that need real losetup self-skip when this constraint bites. T03 is the
slice's wiring task and owns any compose-level changes (privileged or
device passthrough) needed to make the kernel-enforced hard cap live.
The same skip behavior also covers CI runners without loop support.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from orchestrator.errors import VolumeProvisionFailed
from orchestrator.volumes import (
    allocate_image,
    mount_image,
    unmount_image,
)

# Root for per-test scratch dirs. Inside /tmp so it never collides with
# the real /var/lib/perpetuity/vols path the orchestrator uses for live
# workspaces.
_SCRATCH_ROOT = Path("/tmp/perpetuity-test-vols")


@pytest.fixture
def scratch_dir() -> Iterator[Path]:
    """Per-test scratch root: `<_SCRATCH_ROOT>/<uuid>/`.

    Holds two children — `vols/` (img files) and `mnt/` (mountpoint).
    Cleanup is best-effort; a leaked mount or loop device from a buggy
    test is unmounted by the per-test fixture below before this dir is
    removed.
    """
    test_id = uuid.uuid4().hex
    root = _SCRATCH_ROOT / test_id
    root.mkdir(parents=True, exist_ok=True)
    (root / "vols").mkdir(exist_ok=True)
    (root / "mnt").mkdir(exist_ok=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
async def clean_mount(scratch_dir: Path) -> AsyncIterator[Path]:
    """Yield the mountpoint, then run unmount_image on teardown.

    `unmount_image` is idempotent on already-unmounted paths so we can
    call it unconditionally. This guards against a test failing
    mid-mount and leaving the mountpoint+loop hanging around.
    """
    mountpoint = scratch_dir / "mnt"
    try:
        yield mountpoint
    finally:
        await unmount_image(str(mountpoint))


def _losetup_works() -> bool:
    """Probe whether losetup --find --show actually works in this env.

    Docker Desktop / linuxkit grants SYS_ADMIN but still rejects the
    syscall set losetup needs to associate a backing file. We allocate a
    tiny throwaway file in /tmp and try the real call; if it succeeds we
    detach immediately and return True.
    """
    probe_dir = _SCRATCH_ROOT / f"_probe_{uuid.uuid4().hex}"
    try:
        probe_dir.mkdir(parents=True, exist_ok=True)
        probe = probe_dir / "probe.img"
        # 1 MB is enough for losetup to bind; we don't need ext4 to be
        # valid (we won't mount it).
        rc = subprocess.run(
            ["truncate", "-s", "1M", str(probe)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if rc.returncode != 0:
            return False
        attach = subprocess.run(
            ["losetup", "--find", "--show", str(probe)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if attach.returncode != 0:
            return False
        loop = (attach.stdout or "").strip().split("\n")[0]
        # Best-effort detach so we don't leak a loop device on every
        # collection of this test module.
        subprocess.run(
            ["losetup", "-d", loop],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)


# Cache the probe — it spawns three subprocesses, no need to repeat per test.
_LOOPBACK_AVAILABLE = _losetup_works()
requires_loopback = pytest.mark.skipif(
    not _LOOPBACK_AVAILABLE,
    reason=(
        "losetup --find --show fails in this environment "
        "(typical for Docker Desktop without privileged mode). "
        "S02/T03 owns any compose-level fix to enable real loopback."
    ),
)


# ---------------------------------------------------------------------------
# Validation / unit-shaped tests — never need loopback.
# ---------------------------------------------------------------------------


async def test_allocate_image_rejects_zero_size_gb(scratch_dir: Path) -> None:
    """allocate_image(size_gb=0) raises ValueError before any subprocess.

    Boundary check at the API edge so the truncate command never sees a
    nonsense size.
    """
    with pytest.raises(ValueError, match="size_gb"):
        await allocate_image(
            uuid.uuid4().hex,
            0,
            vols_dir=str(scratch_dir / "vols"),
        )


async def test_allocate_image_rejects_negative_size_gb(scratch_dir: Path) -> None:
    """Negative size also fails fast at the validation boundary."""
    with pytest.raises(ValueError, match="size_gb"):
        await allocate_image(
            uuid.uuid4().hex,
            -1,
            vols_dir=str(scratch_dir / "vols"),
        )


async def test_unmount_image_idempotent_on_unmounted_path(
    scratch_dir: Path,
) -> None:
    """unmount_image on a never-mounted path is a no-op (idempotent shutdown)."""
    mountpoint = scratch_dir / "mnt"
    # Should not raise even though nothing is mounted there.
    await unmount_image(str(mountpoint))
    # And again — re-running must still be a no-op.
    await unmount_image(str(mountpoint))


async def test_unmount_image_idempotent_on_missing_path(
    scratch_dir: Path,
) -> None:
    """unmount_image on a path that does not exist is a no-op."""
    missing = scratch_dir / "does-not-exist"
    await unmount_image(str(missing))


async def test_mkfs_step_tagged_failure_via_mock(scratch_dir: Path) -> None:
    """Force mkfs.ext4 to return non-zero; assert step='mkfs' propagates.

    Plan-aligned negative test: 'Step-tagged failures — mkfs.ext4 on a
    path with a non-existent parent raises VolumeProvisionFailed(step=
    'mkfs').' The container runs as root (which bypasses POSIX mode-bit
    enforcement) and silently makes intermediate parents, so we can't
    reliably trigger a real mkfs.ext4 failure from within the suite —
    the mock path is the deterministic equivalent and pins the same
    contract: a non-zero mkfs exit raises VolumeProvisionFailed(step=
    'mkfs') with the stderr first line as the reason.
    """
    from orchestrator import volumes as volumes_mod

    truncate_ok = subprocess.CompletedProcess(
        args=["truncate", "-s", "1G", "/dev/null"],
        returncode=0,
        stdout="",
        stderr="",
    )
    mkfs_fail = subprocess.CompletedProcess(
        args=["mkfs.ext4", "-F", "-q", "-m", "0", "/dev/null"],
        returncode=1,
        stdout="",
        stderr=(
            "mkfs.ext4: Device size (0x0 blocks) too small for filesystem\n"
        ),
    )

    call_count = {"n": 0}

    def fake_run(cmd: list[str], **_kwargs):  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        if cmd[0] == "truncate":
            return truncate_ok
        if cmd[0] == "mkfs.ext4":
            return mkfs_fail
        raise AssertionError(f"unexpected command in test: {cmd!r}")

    with patch.object(volumes_mod, "_run_subprocess_sync", side_effect=fake_run):
        with pytest.raises(VolumeProvisionFailed) as excinfo:
            await allocate_image(
                uuid.uuid4().hex,
                1,
                vols_dir=str(scratch_dir / "vols"),
            )

    assert excinfo.value.step == "mkfs"
    assert "Device size" in excinfo.value.reason
    assert call_count["n"] == 2  # truncate + mkfs


async def test_allocate_image_creates_vols_dir_with_mode_700(
    scratch_dir: Path,
) -> None:
    """allocate_image creates the vols_dir with 0o700 mode.

    Defensive — the mode is in the contract because workspace volumes
    must not be world-readable on a multi-tenant host. When tests run
    inside the orchestrator container as root this is mostly cosmetic,
    but on a host run the mode protects neighbor volumes.
    """
    from orchestrator import volumes as volumes_mod

    vols_dir = scratch_dir / "fresh-vols"
    assert not vols_dir.exists()

    # Mock subprocess so we don't actually call truncate/mkfs.
    ok = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="/dev/loop0\n", stderr=""
    )
    with patch.object(volumes_mod, "_run_subprocess_sync", return_value=ok):
        await allocate_image(
            uuid.uuid4().hex,
            1,
            vols_dir=str(vols_dir),
        )

    assert vols_dir.exists()
    # Mask out the file-type bits — we only care about the permission bits.
    actual_mode = vols_dir.stat().st_mode & 0o777
    assert actual_mode == 0o700, f"vols_dir mode={oct(actual_mode)}"


async def test_mount_image_step_tagged_failure_on_missing_image(
    scratch_dir: Path,
) -> None:
    """mount_image on a non-existent .img fails at step='losetup'.

    losetup --find --show on a path that doesn't exist returns non-zero
    immediately (no kernel involvement), so this test does NOT need
    working loopback — it exercises the error mapping.
    """
    missing = scratch_dir / "does-not-exist.img"
    mountpoint = scratch_dir / "mnt"
    with pytest.raises(VolumeProvisionFailed) as excinfo:
        await mount_image(str(missing), str(mountpoint))
    # losetup is the first thing that touches the .img path and is what
    # detects the missing file (assuming losetup is installed).
    assert excinfo.value.step == "losetup", (
        f"unexpected step={excinfo.value.step!r} "
        f"reason={excinfo.value.reason!r}"
    )
    assert excinfo.value.reason


async def test_subprocess_failure_truncate_step_via_mock(
    scratch_dir: Path,
) -> None:
    """Force truncate to return non-zero; assert step='truncate' propagates.

    Covers the malformed-input-via-injected-failure path the plan calls
    out under Negative Tests > 'Error paths'. We patch the synchronous
    helper inside volumes.py to return a CompletedProcess with returncode=1
    and a stderr line — verifies our error wrapper picks the right step.
    """
    from orchestrator import volumes as volumes_mod

    fake_result = subprocess.CompletedProcess(
        args=["truncate", "-s", "1G", "/dev/null"],
        returncode=1,
        stdout="",
        stderr="truncate: failed to truncate '...': No space left on device\n",
    )
    with patch.object(volumes_mod, "_run_subprocess_sync", return_value=fake_result):
        with pytest.raises(VolumeProvisionFailed) as excinfo:
            await allocate_image(
                uuid.uuid4().hex,
                1,
                vols_dir=str(scratch_dir / "vols"),
            )
    assert excinfo.value.step == "truncate"
    assert "No space left on device" in excinfo.value.reason


async def test_subprocess_failure_losetup_step_via_mock(
    scratch_dir: Path,
) -> None:
    """Force losetup to return non-zero; assert step='losetup'.

    Simulates the kernel-loop-exhaustion case from the Load Profile
    section without needing 8+ concurrent volumes.
    """
    from orchestrator import volumes as volumes_mod

    # Pre-create a real .img file so the os.path.ismount short-circuit
    # doesn't fire (the mountpoint won't be a real mountpoint, so we
    # proceed into the losetup call).
    vols_dir = scratch_dir / "vols"
    vols_dir.mkdir(exist_ok=True)
    img = vols_dir / "fake.img"
    img.write_bytes(b"\0" * 1024)  # tiny placeholder

    fake_result = subprocess.CompletedProcess(
        args=["losetup", "--find", "--show", str(img)],
        returncode=1,
        stdout="",
        stderr="losetup: cannot find an unused loop device\n",
    )
    with patch.object(volumes_mod, "_run_subprocess_sync", return_value=fake_result):
        with pytest.raises(VolumeProvisionFailed) as excinfo:
            await mount_image(str(img), str(scratch_dir / "mnt"))
    assert excinfo.value.step == "losetup"
    assert "loop device" in excinfo.value.reason


async def test_subprocess_timeout_surfaces_as_timeout_reason(
    scratch_dir: Path,
) -> None:
    """A subprocess.TimeoutExpired raises VolumeProvisionFailed(reason='timeout').

    Failure-Mode-table claim: every command's 30s timeout converts to a
    structured timeout error rather than crashing the event loop with a
    raw TimeoutExpired.
    """
    from orchestrator import volumes as volumes_mod

    def _raise_timeout(*_args, **_kwargs) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["truncate"], timeout=30)

    with patch.object(volumes_mod, "_run_subprocess_sync", side_effect=_raise_timeout):
        with pytest.raises(VolumeProvisionFailed) as excinfo:
            await allocate_image(
                uuid.uuid4().hex,
                1,
                vols_dir=str(scratch_dir / "vols"),
            )
    assert excinfo.value.step == "truncate"
    assert excinfo.value.reason == "timeout"


# ---------------------------------------------------------------------------
# Heavy tests — require real losetup. Skip cleanly on Docker Desktop without
# privileged mode; pass once T03 enables it (or in any environment where the
# orchestrator process is genuinely able to call losetup).
# ---------------------------------------------------------------------------


@requires_loopback
async def test_allocate_then_mount_round_trip(
    scratch_dir: Path, clean_mount: Path
) -> None:
    """Full happy path: allocate a 1-GB image, mount it, verify state.

    Postconditions:
      - `losetup -j <img_path>` shows the .img bound to a /dev/loopN
      - `/proc/mounts` lists the mountpoint with fstype=ext4
    """
    volume_id = uuid.uuid4().hex
    vols_dir = scratch_dir / "vols"
    img_path = await allocate_image(
        volume_id, size_gb=1, vols_dir=str(vols_dir)
    )
    assert os.path.isfile(img_path)
    # Sparse: apparent size is 1 GiB but allocated blocks should be tiny.
    assert os.path.getsize(img_path) == 1024 * 1024 * 1024

    loop_dev = await mount_image(img_path, str(clean_mount))
    assert loop_dev.startswith("/dev/loop")

    # losetup -j must show the association.
    result = subprocess.run(
        ["losetup", "-j", img_path],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert loop_dev in result.stdout

    # /proc/mounts must list the mountpoint with ext4.
    with open("/proc/mounts") as fh:
        mounts = fh.read()
    assert str(clean_mount) in mounts
    assert any(
        line.split()[1] == str(clean_mount) and line.split()[2] == "ext4"
        for line in mounts.splitlines()
        if len(line.split()) >= 3
    )


@requires_loopback
async def test_mount_image_idempotent_returns_same_loop_device(
    scratch_dir: Path, clean_mount: Path
) -> None:
    """Calling mount_image twice on the same (img, mountpoint) pair returns
    the same loop device — does NOT consume a second one.

    Critical for T03's re-provision path: a re-call into ensure_volume
    must short-circuit so we don't exhaust the kernel's 8-loop default.
    """
    volume_id = uuid.uuid4().hex
    img_path = await allocate_image(
        volume_id, size_gb=1, vols_dir=str(scratch_dir / "vols")
    )
    first = await mount_image(img_path, str(clean_mount))
    second = await mount_image(img_path, str(clean_mount))
    assert first == second


@requires_loopback
async def test_volume_hard_cap_enforced_via_dd(
    scratch_dir: Path, clean_mount: Path
) -> None:
    """Writing past the 1-GB cap returns ENOSPC.

    The slice's headline assertion: ext4 inside a 1-GB loopback volume
    refuses writes after ~1 GB. We use a smaller dd target (~50 MB beyond
    the cap) than the slice demo's 1100 MB to keep the test fast — the
    contract is identical.
    """
    volume_id = uuid.uuid4().hex
    img_path = await allocate_image(
        volume_id, size_gb=1, vols_dir=str(scratch_dir / "vols")
    )
    await mount_image(img_path, str(clean_mount))

    target = clean_mount / "big"
    # Run dd off-loop so we don't block on the synchronous write.
    proc = await asyncio.to_thread(
        subprocess.run,
        [
            "dd",
            "if=/dev/zero",
            f"of={target}",
            "bs=1M",
            "count=1100",
            "status=none",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode != 0, (
        "dd should fail with ENOSPC at the 1 GB cap; "
        f"returncode={proc.returncode}, stderr={proc.stderr!r}"
    )
    # Different dd builds say "No space left on device" or "no space
    # left on device" — match case-insensitively. ENOSPC is the only
    # acceptable failure mode here; any other error means the cap isn't
    # actually enforced and the test should fail loud.
    assert "no space left on device" in proc.stderr.lower(), (
        f"expected ENOSPC in stderr, got {proc.stderr!r}"
    )
    # File should be at most ~1 GiB — ext4 metadata eats a sliver, so
    # we accept up to 1.05 GiB to mirror the e2e test's tolerance.
    assert os.path.getsize(target) <= int(1.05 * 1024 * 1024 * 1024)


@requires_loopback
async def test_unmount_image_releases_loop_device(
    scratch_dir: Path,
) -> None:
    """After unmount_image, the .img is no longer in `losetup -a` output.

    Belt-and-suspenders for the shutdown path — leaked loop devices are
    the failure mode that bites you 8 workspaces later.
    """
    volume_id = uuid.uuid4().hex
    img_path = await allocate_image(
        volume_id, size_gb=1, vols_dir=str(scratch_dir / "vols")
    )
    mountpoint = scratch_dir / "mnt"
    loop_dev = await mount_image(img_path, str(mountpoint))

    # Confirm pre-unmount association exists.
    pre = subprocess.run(
        ["losetup", "-a"], capture_output=True, text=True, timeout=5
    )
    assert loop_dev in pre.stdout

    await unmount_image(str(mountpoint))

    # Post-unmount: the loop device should no longer be associated with
    # this img_path. (Other tests' associations are unrelated.)
    post = subprocess.run(
        ["losetup", "-a"], capture_output=True, text=True, timeout=5
    )
    assert img_path not in post.stdout
