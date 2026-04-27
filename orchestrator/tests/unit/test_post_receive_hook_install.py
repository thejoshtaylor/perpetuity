"""Unit tests for clone._install_post_receive_hook / _uninstall_post_receive_hook
(M004/S04/T04).

Hermetic. Same fake-Docker harness as test_clone_to_mirror.py — stripped down
to the exec-recorder shape because the install/uninstall paths don't need
ensure-mirror plumbing.

Coverage:
  - install: mode=auto writes hook + chmod 0755, returns True
  - install: mode=rule does NOT write a hook, returns False (no exec calls)
  - install: mode=manual_workflow does NOT write a hook, returns False
  - install: hook script content matches _POST_RECEIVE_HOOK_SCRIPT byte-for-byte
  - install: hook content references PROJECT_ID and PERPETUITY_ORCH_KEY
    placeholders (NOT pre-expanded — must expand at hook execution time)
  - install: install_post_receive_hook log emitted with project_id +
    truncated container_id
  - install: non-zero exit → _CloneExecFailed(op='install_post_receive_hook')
  - uninstall: rm -f the right path, returns True, logs uninstalled
  - uninstall: rm non-zero → returns False, logs warning, does NOT raise
  - uninstall is a no-op-friendly call (rm -f on missing file = exit 0 in
    real shells; we just check that the path is right)
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Callable

# SKIP boot-time side effects before importing orchestrator modules.
os.environ.setdefault("SKIP_IMAGE_PULL_ON_BOOT", "1")
os.environ.setdefault("SKIP_PG_POOL_ON_BOOT", "1")
os.environ.setdefault("ORCHESTRATOR_API_KEY", "unit-test-current-key")

import pytest  # noqa: E402

from orchestrator.clone import (  # noqa: E402
    _CloneExecFailed,
    _POST_RECEIVE_HOOK_SCRIPT,
    _hook_path,
    _install_post_receive_hook,
    _uninstall_post_receive_hook,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _ExecCall:
    def __init__(
        self, cmd: list[str], environment: dict[str, str] | None
    ) -> None:
        self.cmd = list(cmd)
        self.environment = (
            dict(environment) if environment is not None else None
        )


class _ExecResult:
    def __init__(self, stdout: str, exit_code: int) -> None:
        self._stdout = stdout
        self._exit_code = exit_code

    def start(self, *, detach: bool = False) -> "_ExecStream":
        return _ExecStream(self._stdout)

    async def inspect(self) -> dict[str, Any]:
        return {"ExitCode": self._exit_code}


class _ExecStream:
    def __init__(self, stdout: str) -> None:
        self._stdout = stdout
        self._yielded = False

    async def __aenter__(self) -> "_ExecStream":
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None

    async def read_out(self) -> "_ExecMsg | None":
        if self._yielded:
            return None
        self._yielded = True
        return _ExecMsg(self._stdout.encode("utf-8"))


class _ExecMsg:
    def __init__(self, data: bytes) -> None:
        self.data = data


class _FakeContainerHandle:
    def __init__(
        self,
        container_id: str,
        *,
        script: Callable[[_ExecCall], _ExecResult],
        recorder: list[_ExecCall],
    ) -> None:
        self.id = container_id
        self._script = script
        self._recorder = recorder

    async def exec(
        self,
        *,
        cmd: list[str],
        stdout: bool = True,
        stderr: bool = True,
        environment: dict[str, str] | None = None,
        **_kw: Any,
    ) -> _ExecResult:
        call = _ExecCall(cmd, environment)
        self._recorder.append(call)
        return self._script(call)


class _FakeContainers:
    def __init__(self) -> None:
        self.exec_calls: list[_ExecCall] = []
        self.exec_script: Callable[[_ExecCall], _ExecResult] = (
            lambda call: _ExecResult("", 0)
        )

    async def get(self, container_id: str) -> _FakeContainerHandle:
        return _FakeContainerHandle(
            container_id,
            script=self.exec_script,
            recorder=self.exec_calls,
        )


class _FakeDocker:
    def __init__(self) -> None:
        self.containers = _FakeContainers()


# ---------------------------------------------------------------------------
# Tests — install
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_auto_mode_writes_hook(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """mode=auto → exactly one exec call writing the hook + chmod, returns True."""
    docker = _FakeDocker()
    container_id = "mirrorcontainer1234567890"
    project_id = str(uuid.uuid4())

    with caplog.at_level(logging.INFO, logger="orchestrator"):
        installed = await _install_post_receive_hook(
            docker,
            mirror_container_id=container_id,
            project_id=project_id,
            push_rule_mode="auto",
        )

    assert installed is True
    assert len(docker.containers.exec_calls) == 1
    call = docker.containers.exec_calls[0]
    # The install fires `sh -c <heredoc-script>`.
    assert call.cmd[0] == "sh"
    assert call.cmd[1] == "-c"
    body = call.cmd[2]
    # Hook path appears in the cat redirection.
    assert _hook_path(project_id) in body
    # chmod 0755 is part of the install script.
    assert "chmod 0755" in body

    msgs = [r.message for r in caplog.records]
    assert any(
        "post_receive_hook_installed" in m and project_id in m for m in msgs
    )


@pytest.mark.asyncio
async def test_install_rule_mode_no_hook_no_exec() -> None:
    """mode=rule → no hook installed, no exec calls, returns False."""
    docker = _FakeDocker()
    project_id = str(uuid.uuid4())

    installed = await _install_post_receive_hook(
        docker,
        mirror_container_id="mirror123",
        project_id=project_id,
        push_rule_mode="rule",
    )

    assert installed is False
    assert docker.containers.exec_calls == []


@pytest.mark.asyncio
async def test_install_manual_workflow_mode_no_hook_no_exec() -> None:
    """mode=manual_workflow → no hook installed."""
    docker = _FakeDocker()
    project_id = str(uuid.uuid4())

    installed = await _install_post_receive_hook(
        docker,
        mirror_container_id="mirror123",
        project_id=project_id,
        push_rule_mode="manual_workflow",
    )

    assert installed is False
    assert docker.containers.exec_calls == []


@pytest.mark.asyncio
async def test_install_hook_script_byte_for_byte() -> None:
    """The installed script body matches _POST_RECEIVE_HOOK_SCRIPT verbatim."""
    docker = _FakeDocker()
    project_id = str(uuid.uuid4())

    await _install_post_receive_hook(
        docker,
        mirror_container_id="mirror123",
        project_id=project_id,
        push_rule_mode="auto",
    )
    call = docker.containers.exec_calls[0]
    body = call.cmd[2]
    # Heredoc body contains the full script content.
    assert _POST_RECEIVE_HOOK_SCRIPT in body


@pytest.mark.asyncio
async def test_install_hook_uses_runtime_env_var_expansion() -> None:
    """The installed script references $PROJECT_ID/$PERPETUITY_ORCH_KEY (not pre-expanded)."""
    docker = _FakeDocker()
    project_id = str(uuid.uuid4())

    await _install_post_receive_hook(
        docker,
        mirror_container_id="mirror123",
        project_id=project_id,
        push_rule_mode="auto",
    )
    call = docker.containers.exec_calls[0]
    body = call.cmd[2]
    # Both env-var references must appear unexpanded in the installed script.
    assert "$PROJECT_ID" in body
    assert "$PERPETUITY_ORCH_KEY" in body
    # The single-quoted heredoc terminator disables expansion at install time.
    assert "<<'EOF'" in body
    # Hook fires at the orchestrator's well-known intra-network URL.
    assert "http://orchestrator:8001/v1/projects/$PROJECT_ID/auto-push-callback" in body


@pytest.mark.asyncio
async def test_install_hook_log_carries_truncated_container_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The installed log line truncates container_id to first-12 (MEM134)."""
    docker = _FakeDocker()
    project_id = str(uuid.uuid4())
    long_container_id = "abcdef0123456789abcdef0123456789"

    with caplog.at_level(logging.INFO, logger="orchestrator"):
        await _install_post_receive_hook(
            docker,
            mirror_container_id=long_container_id,
            project_id=project_id,
            push_rule_mode="auto",
        )

    msg = next(
        r.message for r in caplog.records
        if "post_receive_hook_installed" in r.message
    )
    # First-12-chars only.
    assert "abcdef012345" in msg
    assert "abcdef0123456789abcdef0123456789" not in msg


@pytest.mark.asyncio
async def test_install_non_zero_exit_raises_clone_exec_failed() -> None:
    """A non-zero exit on the install heredoc → _CloneExecFailed."""
    docker = _FakeDocker()
    project_id = str(uuid.uuid4())

    docker.containers.exec_script = lambda call: _ExecResult("", 1)

    with pytest.raises(_CloneExecFailed) as exc_info:
        await _install_post_receive_hook(
            docker,
            mirror_container_id="mirror123",
            project_id=project_id,
            push_rule_mode="auto",
        )

    assert exc_info.value.exit_code == 1
    assert exc_info.value.op == "install_post_receive_hook"


# ---------------------------------------------------------------------------
# Tests — uninstall
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uninstall_runs_rm_minus_f_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """uninstall fires `rm -f <hook_path>`, logs uninstalled, returns True."""
    docker = _FakeDocker()
    project_id = str(uuid.uuid4())

    with caplog.at_level(logging.INFO, logger="orchestrator"):
        ok = await _uninstall_post_receive_hook(
            docker,
            mirror_container_id="mirror123",
            project_id=project_id,
        )

    assert ok is True
    assert len(docker.containers.exec_calls) == 1
    call = docker.containers.exec_calls[0]
    assert call.cmd[0] == "rm"
    assert call.cmd[1] == "-f"
    assert call.cmd[2] == _hook_path(project_id)

    msgs = [r.message for r in caplog.records]
    assert any(
        "post_receive_hook_uninstalled" in m and project_id in m for m in msgs
    )


@pytest.mark.asyncio
async def test_uninstall_non_zero_returns_false_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """rm exit non-zero → returns False, logs WARNING, does NOT raise."""
    docker = _FakeDocker()
    project_id = str(uuid.uuid4())

    docker.containers.exec_script = lambda call: _ExecResult("", 1)

    with caplog.at_level(logging.WARNING, logger="orchestrator"):
        ok = await _uninstall_post_receive_hook(
            docker,
            mirror_container_id="mirror123",
            project_id=project_id,
        )

    assert ok is False
    msgs = [r.message for r in caplog.records]
    assert any(
        "post_receive_hook_uninstall_failed" in m and project_id in m
        for m in msgs
    )


@pytest.mark.asyncio
async def test_install_then_uninstall_targets_same_path() -> None:
    """install and uninstall touch the exact same /repos/<id>.git/hooks/post-receive path."""
    docker = _FakeDocker()
    project_id = str(uuid.uuid4())

    await _install_post_receive_hook(
        docker,
        mirror_container_id="mirror123",
        project_id=project_id,
        push_rule_mode="auto",
    )
    install_call = docker.containers.exec_calls[0]
    install_body = install_call.cmd[2]

    docker.containers.exec_calls.clear()

    await _uninstall_post_receive_hook(
        docker,
        mirror_container_id="mirror123",
        project_id=project_id,
    )
    uninstall_call = docker.containers.exec_calls[0]

    # The path string appears in BOTH the install heredoc body AND as the
    # rm target — no construction divergence.
    expected_path = _hook_path(project_id)
    assert expected_path in install_body
    assert uninstall_call.cmd[2] == expected_path
