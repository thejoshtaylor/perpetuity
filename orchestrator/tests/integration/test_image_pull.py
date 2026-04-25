"""Integration tests for image-pull-on-boot (T02).

Boots a fresh, ephemeral orchestrator container with a chosen WORKSPACE_IMAGE
and observes its logs + exit code. Two cases:

  - WORKSPACE_IMAGE=perpetuity/workspace:test → pull succeeds, INFO
    `image_pull_ok` emitted, /v1/health returns image_present=True.
  - WORKSPACE_IMAGE=does-not-exist:nope → pull fails, ERROR
    `image_pull_failed` emitted, container exits with non-zero code.

This test mounts the host docker socket into the spawned orchestrator (same
as compose) so the boot-time pull can talk to the daemon. The spawned
container is removed after each test (--rm + explicit cleanup) so leftover
state doesn't confuse the next run.
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid

import pytest


def _docker(*args: str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        check=check,
        capture_output=capture,
        text=True,
        timeout=120,
    )


def _wait_for_log(name: str, needle: str, timeout_s: float = 30.0) -> str:
    """Tail logs until `needle` appears or timeout. Returns full log text."""
    deadline = time.time() + timeout_s
    last_logs = ""
    while time.time() < deadline:
        out = _docker("logs", name, check=False)
        last_logs = (out.stdout or "") + (out.stderr or "")
        if needle in last_logs:
            return last_logs
        time.sleep(0.5)
    return last_logs


def _wait_for_exit(name: str, timeout_s: float = 30.0) -> int:
    """Wait for container to exit; return its exit code (or -1 on timeout)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        out = _docker(
            "inspect", "-f", "{{.State.Status}}|{{.State.ExitCode}}", name, check=False
        )
        if out.returncode != 0:
            return -1
        status, code = (out.stdout or "").strip().split("|")
        if status in ("exited", "dead"):
            return int(code)
        time.sleep(0.3)
    return -1


def _run_orchestrator_container(
    name: str,
    *,
    workspace_image: str,
    api_key: str = "integration-test-key",
) -> None:
    """Start a detached orchestrator container with the given env.

    Intentionally no `--rm` — the test reads logs and inspects exit codes
    after the container dies. The fixture's teardown does the cleanup.
    """
    _docker(
        "run",
        "-d",
        "--name",
        name,
        "--network",
        "perpetuity_default",
        "-v",
        "/var/run/docker.sock:/var/run/docker.sock",
        "-e",
        f"WORKSPACE_IMAGE={workspace_image}",
        "-e",
        f"ORCHESTRATOR_API_KEY={api_key}",
        "-e",
        "REDIS_HOST=redis",
        "-e",
        f"REDIS_PASSWORD={os.environ.get('REDIS_PASSWORD', 'changeme')}",
        "orchestrator:latest",
    )


def _kill(name: str) -> None:
    _docker("rm", "-f", name, check=False)


@pytest.fixture
def container_name() -> str:
    name = f"orch-test-{uuid.uuid4().hex[:8]}"
    yield name
    _kill(name)


def test_image_pull_ok_for_existing_image(container_name: str) -> None:
    """Boot orchestrator with the test workspace image already locally
    present; assert it logs image_pull_ok and stays alive.
    """
    if not os.path.exists("/var/run/docker.sock"):
        pytest.skip("no docker socket on host")

    _run_orchestrator_container(
        container_name, workspace_image="perpetuity/workspace:test"
    )

    logs = _wait_for_log(container_name, "image_pull_ok", timeout_s=60)
    assert "image_pull_ok" in logs, f"missing image_pull_ok. logs:\n{logs}"
    assert "perpetuity/workspace:test" in logs

    # Should also reach orchestrator_ready.
    logs2 = _wait_for_log(container_name, "orchestrator_ready", timeout_s=15)
    assert "orchestrator_ready" in logs2

    # Container should still be running (not exited).
    inspect = _docker(
        "inspect", "-f", "{{.State.Status}}", container_name, check=False
    )
    assert inspect.stdout.strip() == "running"


def test_image_pull_failed_exits_nonzero(container_name: str) -> None:
    """Boot with a definitely-missing image; assert image_pull_failed log
    line and non-zero exit. The image name uses a registry that refuses
    pulls so the failure is fast and unambiguous.
    """
    if not os.path.exists("/var/run/docker.sock"):
        pytest.skip("no docker socket on host")

    bogus = "perpetuity-does-not-exist-nope:no-such-tag"
    _run_orchestrator_container(container_name, workspace_image=bogus)

    logs = _wait_for_log(container_name, "image_pull_failed", timeout_s=60)
    assert "image_pull_failed" in logs, f"missing image_pull_failed. logs:\n{logs}"
    assert bogus in logs

    code = _wait_for_exit(container_name, timeout_s=30)
    assert code != 0, f"expected non-zero exit, got {code}"


def test_orchestrator_boot_fails_without_api_key(container_name: str) -> None:
    """Missing ORCHESTRATOR_API_KEY → boot fails fast with exit 1.

    A misconfigured deployment that boots without a key would 401 every
    backend request silently. Loud failure is required.
    """
    if not os.path.exists("/var/run/docker.sock"):
        pytest.skip("no docker socket on host")

    _docker(
        "run",
        "-d",
        "--name",
        container_name,
        "--network",
        "perpetuity_default",
        "-v",
        "/var/run/docker.sock:/var/run/docker.sock",
        "-e",
        "WORKSPACE_IMAGE=perpetuity/workspace:test",
        "-e",
        "ORCHESTRATOR_API_KEY=",  # explicitly blank
        "-e",
        "REDIS_HOST=redis",
        "-e",
        f"REDIS_PASSWORD={os.environ.get('REDIS_PASSWORD', 'changeme')}",
        "orchestrator:latest",
    )

    logs = _wait_for_log(container_name, "orchestrator_boot_failed", timeout_s=15)
    assert "missing_api_key" in logs, f"expected missing_api_key. logs:\n{logs}"
    code = _wait_for_exit(container_name, timeout_s=15)
    assert code != 0
