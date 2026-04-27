"""Unit tests for orchestrator/clone.py::clone_to_user_workspace (M004/S04/T03).

Hermetic — no real Docker, no real Postgres. Reuses the same exec-harness
shape as test_clone_to_mirror.py so the two clone hops are tested with
matching primitives.

Coverage:
  1. happy path: provision → clone → verify → ok (created)
  2. idempotent re-clone short-circuits when .git/HEAD already exists (reused)
  3. provision failure (DockerUnavailable) propagates
  4. mkdir parent dir failure → _CloneExecFailed(op=mkdir_user_parent)
  5. git-clone non-zero → _CloneExecFailed(op=user_git_clone) + log marker
  6. verify-remote-url failure → _CloneExecFailed(op=verify_remote_url)
  7. credential-leak detection: remote URL contains x-access-token →
     CloneCredentialLeakDetected + half-clone rm -rf
  8. credential-leak detection: remote URL is https://github.com/... →
     CloneCredentialLeakDetected
  9. credential-leak detection: remote URL is empty / not git:// →
     CloneCredentialLeakDetected
 10. user_clone_started + user_clone_completed log markers (with team/user/project ids)
 11. credential-free clone — no `environment` dict on the git clone exec
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

from orchestrator import clone as clone_mod  # noqa: E402
from orchestrator.clone import (  # noqa: E402
    _CloneExecFailed,
    clone_to_user_workspace,
)
from orchestrator.errors import (  # noqa: E402
    CloneCredentialLeakDetected,
    DockerUnavailable,
)


# ---------------------------------------------------------------------------
# Exec harness — same shape as test_clone_to_mirror, deliberately duplicated
# so changes to either don't ripple through. Keeps the failure surface tight.
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
        exec_raises: Exception | None = None,
    ) -> None:
        self.id = container_id
        self._script = script
        self._recorder = recorder
        self._exec_raises = exec_raises

    async def exec(
        self,
        *,
        cmd: list[str],
        stdout: bool = True,
        stderr: bool = True,
        environment: dict[str, str] | None = None,
        **_kw: Any,
    ) -> _ExecResult:
        if self._exec_raises is not None:
            raise self._exec_raises
        call = _ExecCall(cmd, environment)
        self._recorder.append(call)
        return self._script(call)


class _FakeContainers:
    def __init__(self) -> None:
        self.exec_calls: list[_ExecCall] = []
        self.exec_script: Callable[[_ExecCall], _ExecResult] = (
            lambda call: _ExecResult("", 0)
        )
        self.exec_raises: Exception | None = None

    async def get(self, container_id: str) -> _FakeContainerHandle:
        return _FakeContainerHandle(
            container_id,
            script=self.exec_script,
            recorder=self.exec_calls,
            exec_raises=self.exec_raises,
        )


class _FakeDocker:
    def __init__(self) -> None:
        self.containers = _FakeContainers()


# ---------------------------------------------------------------------------
# Scripted exec — first matching predicate wins, default is (stdout="", exit=0).
# ---------------------------------------------------------------------------


class _ScriptedExec:
    def __init__(self) -> None:
        self.rules: list[tuple[Callable[[_ExecCall], bool], _ExecResult]] = []
        self.default: _ExecResult = _ExecResult("", 0)

    def __call__(self, call: _ExecCall) -> _ExecResult:
        for predicate, result in self.rules:
            if predicate(call):
                return result
        return self.default

    def add(
        self,
        predicate: Callable[[_ExecCall], bool],
        *,
        stdout: str = "",
        exit_code: int = 0,
    ) -> None:
        self.rules.append((predicate, _ExecResult(stdout, exit_code)))


def _is_workspace_head_test(call: _ExecCall) -> bool:
    return (
        len(call.cmd) >= 3
        and call.cmd[0] == "test"
        and call.cmd[1] == "-f"
        and call.cmd[2].endswith("/.git/HEAD")
    )


def _is_mkdir(call: _ExecCall) -> bool:
    return (
        len(call.cmd) >= 3
        and call.cmd[0] == "mkdir"
        and call.cmd[1] == "-p"
    )


def _is_git_clone(call: _ExecCall) -> bool:
    return (
        len(call.cmd) >= 4
        and call.cmd[0] == "git"
        and call.cmd[1] == "clone"
        and call.cmd[2].startswith("git://")
    )


def _is_remote_url_get(call: _ExecCall) -> bool:
    return (
        len(call.cmd) >= 5
        and call.cmd[0] == "git"
        and call.cmd[1].startswith("--git-dir=")
        and call.cmd[2] == "config"
        and call.cmd[3] == "--get"
        and call.cmd[4] == "remote.origin.url"
    )


def _is_rm_rf(call: _ExecCall) -> bool:
    return (
        len(call.cmd) >= 3
        and call.cmd[0] == "rm"
        and call.cmd[1] == "-rf"
    )


# ---------------------------------------------------------------------------
# `provision_container` patcher — every test wants a deterministic stub.
# ---------------------------------------------------------------------------


def _patch_provision(
    monkeypatch: pytest.MonkeyPatch,
    *,
    container_id: str = "userctnr01234567abcd",
    raises: Exception | None = None,
) -> dict[str, Any]:
    state: dict[str, Any] = {"calls": []}

    async def _stub(
        docker: Any,
        user_id: str,
        team_id: str,
        *,
        pg: Any,
    ) -> tuple[str, bool]:
        state["calls"].append((user_id, team_id))
        if raises is not None:
            raise raises
        return container_id, True

    monkeypatch.setattr(clone_mod, "provision_container", _stub)
    return state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_returns_created(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cold-start: provision → mkdir → clone → verify → ok."""
    docker = _FakeDocker()
    user_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    project_name = "widgets"

    script = _ScriptedExec()
    # No prior workspace — HEAD missing.
    script.add(_is_workspace_head_test, exit_code=1)
    # remote-url verify returns the credential-free git:// URL.
    script.add(
        _is_remote_url_get,
        stdout=f"git://team-mirror-{team_id.replace('-','')[:8]}:9418/{project_id}.git\n",
        exit_code=0,
    )
    docker.containers.exec_script = script
    _patch_provision(monkeypatch)

    with caplog.at_level(logging.INFO, logger="orchestrator"):
        result = await clone_to_user_workspace(
            docker,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            user_id=user_id,
            team_id=team_id,
            project_id=project_id,
            project_name=project_name,
        )

    assert result["result"] == "created"
    assert isinstance(result["duration_ms"], int)
    assert result["workspace_path"] == (
        f"/workspaces/{user_id}/{team_id}/{project_name}"
    )

    msgs = [r.message for r in caplog.records]
    assert any(
        "user_clone_started" in m and user_id in m and project_id in m
        for m in msgs
    ), msgs
    assert any(
        "user_clone_completed" in m
        and project_id in m
        and "result=created" in m
        for m in msgs
    ), msgs


@pytest.mark.asyncio
async def test_idempotent_reclone_short_circuits_when_workspace_present(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If <workspace>/.git/HEAD exists, return reused without re-cloning."""
    docker = _FakeDocker()
    user_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    script = _ScriptedExec()
    # Workspace already cloned — HEAD present.
    script.add(_is_workspace_head_test, exit_code=0)
    docker.containers.exec_script = script
    _patch_provision(monkeypatch)

    with caplog.at_level(logging.INFO, logger="orchestrator"):
        result = await clone_to_user_workspace(
            docker,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            user_id=user_id,
            team_id=team_id,
            project_id=project_id,
            project_name="widgets",
        )

    assert result == {
        "result": "reused",
        "duration_ms": 0,
        "workspace_path": f"/workspaces/{user_id}/{team_id}/widgets",
    }
    # No git clone attempted.
    assert not any(
        _is_git_clone(c) for c in docker.containers.exec_calls
    ), docker.containers.exec_calls
    msgs = [r.message for r in caplog.records]
    assert any(
        "user_clone_completed" in m and "result=reused" in m for m in msgs
    ), msgs


@pytest.mark.asyncio
async def test_provision_failure_propagates_docker_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """provision_container raising DockerUnavailable bubbles up."""
    docker = _FakeDocker()
    _patch_provision(monkeypatch, raises=DockerUnavailable("docker_handle_unavailable"))

    with pytest.raises(DockerUnavailable):
        await clone_to_user_workspace(
            docker,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            user_id=str(uuid.uuid4()),
            team_id=str(uuid.uuid4()),
            project_id=str(uuid.uuid4()),
            project_name="widgets",
        )


@pytest.mark.asyncio
async def test_mkdir_parent_failure_raises_clone_exec_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing `mkdir -p /workspaces/<u>/<t>` raises with op=mkdir_user_parent."""
    docker = _FakeDocker()
    user_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())

    script = _ScriptedExec()
    script.add(_is_workspace_head_test, exit_code=1)
    script.add(_is_mkdir, exit_code=2)
    docker.containers.exec_script = script
    _patch_provision(monkeypatch)

    with pytest.raises(_CloneExecFailed) as exc_info:
        await clone_to_user_workspace(
            docker,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            user_id=user_id,
            team_id=team_id,
            project_id=str(uuid.uuid4()),
            project_name="widgets",
        )

    assert exc_info.value.op == "mkdir_user_parent"
    assert exc_info.value.exit_code == 2


@pytest.mark.asyncio
async def test_git_clone_non_zero_raises_with_log_marker(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """git clone exit 128 (typical: resolve_failed when MEM264 regresses)."""
    docker = _FakeDocker()
    user_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    script = _ScriptedExec()
    script.add(_is_workspace_head_test, exit_code=1)
    # mkdir succeeds (default).
    script.add(_is_git_clone, exit_code=128)
    docker.containers.exec_script = script
    _patch_provision(monkeypatch)

    with caplog.at_level(logging.ERROR, logger="orchestrator"):
        with pytest.raises(_CloneExecFailed) as exc_info:
            await clone_to_user_workspace(
                docker,  # type: ignore[arg-type]
                None,  # type: ignore[arg-type]
                user_id=user_id,
                team_id=team_id,
                project_id=project_id,
                project_name="widgets",
            )

    assert exc_info.value.op == "user_git_clone"
    assert exc_info.value.exit_code == 128
    msgs = [r.message for r in caplog.records]
    assert any(
        "user_clone_failed" in m
        and project_id in m
        and "git_clone_exit_128" in m
        for m in msgs
    ), msgs


@pytest.mark.asyncio
async def test_verify_remote_url_failure_raises_clone_exec_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`git config --get remote.origin.url` non-zero → op=verify_remote_url."""
    docker = _FakeDocker()
    script = _ScriptedExec()
    script.add(_is_workspace_head_test, exit_code=1)
    script.add(_is_remote_url_get, exit_code=4)
    docker.containers.exec_script = script
    _patch_provision(monkeypatch)

    with pytest.raises(_CloneExecFailed) as exc_info:
        await clone_to_user_workspace(
            docker,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            user_id=str(uuid.uuid4()),
            team_id=str(uuid.uuid4()),
            project_id=str(uuid.uuid4()),
            project_name="widgets",
        )

    assert exc_info.value.op == "verify_remote_url"


@pytest.mark.asyncio
async def test_credential_leak_x_access_token_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`x-access-token` substring in remote URL → CloneCredentialLeakDetected."""
    docker = _FakeDocker()
    project_id = str(uuid.uuid4())
    script = _ScriptedExec()
    script.add(_is_workspace_head_test, exit_code=1)
    script.add(
        _is_remote_url_get,
        stdout="https://x-access-token:gho_FAKE@github.com/owner/repo.git\n",
        exit_code=0,
    )
    docker.containers.exec_script = script
    _patch_provision(monkeypatch)

    with pytest.raises(CloneCredentialLeakDetected) as exc_info:
        await clone_to_user_workspace(
            docker,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            user_id=str(uuid.uuid4()),
            team_id=str(uuid.uuid4()),
            project_id=project_id,
            project_name="widgets",
        )

    assert exc_info.value.project_id == project_id
    # Cleanup rm -rf was issued.
    assert any(
        _is_rm_rf(c) for c in docker.containers.exec_calls
    ), docker.containers.exec_calls


@pytest.mark.asyncio
async def test_credential_leak_https_url_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare https://github.com/... remote URL is also a leak (mirror regression)."""
    docker = _FakeDocker()
    script = _ScriptedExec()
    script.add(_is_workspace_head_test, exit_code=1)
    script.add(
        _is_remote_url_get,
        stdout="https://github.com/owner/repo.git\n",
        exit_code=0,
    )
    docker.containers.exec_script = script
    _patch_provision(monkeypatch)

    with pytest.raises(CloneCredentialLeakDetected):
        await clone_to_user_workspace(
            docker,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            user_id=str(uuid.uuid4()),
            team_id=str(uuid.uuid4()),
            project_id=str(uuid.uuid4()),
            project_name="widgets",
        )


@pytest.mark.asyncio
async def test_credential_leak_empty_url_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty / non-git:// remote URL trips the leak check."""
    docker = _FakeDocker()
    script = _ScriptedExec()
    script.add(_is_workspace_head_test, exit_code=1)
    script.add(_is_remote_url_get, stdout="\n", exit_code=0)
    docker.containers.exec_script = script
    _patch_provision(monkeypatch)

    with pytest.raises(CloneCredentialLeakDetected):
        await clone_to_user_workspace(
            docker,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
            user_id=str(uuid.uuid4()),
            team_id=str(uuid.uuid4()),
            project_id=str(uuid.uuid4()),
            project_name="widgets",
        )


@pytest.mark.asyncio
async def test_git_clone_uses_credential_free_git_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The git clone exec MUST NOT carry an `environment` dict.

    The mirror→user transport is credential-free per D023. Any `environment`
    on the git clone exec would leak the same kind of token-in-cmd-or-env
    surface MEM274 was meant to prevent — even though we have nothing to
    pass, the structural assertion guards against regressions where someone
    adds a token by reflex.
    """
    docker = _FakeDocker()
    user_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    script = _ScriptedExec()
    script.add(_is_workspace_head_test, exit_code=1)
    script.add(
        _is_remote_url_get,
        stdout=f"git://team-mirror-{team_id.replace('-','')[:8]}:9418/{project_id}.git\n",
        exit_code=0,
    )
    docker.containers.exec_script = script
    _patch_provision(monkeypatch)

    await clone_to_user_workspace(
        docker,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        user_id=user_id,
        team_id=team_id,
        project_id=project_id,
        project_name="widgets",
    )

    clone_calls = [
        c for c in docker.containers.exec_calls if _is_git_clone(c)
    ]
    assert len(clone_calls) == 1
    clone_call = clone_calls[0]
    # No env dict — credential-free transport.
    assert clone_call.environment is None or clone_call.environment == {}
    # URL points at the team-mirror DNS alias on port 9418, with the
    # project_id repo path.
    joined = " ".join(clone_call.cmd)
    assert f"git://team-mirror-{team_id.replace('-','')[:8]}:9418/{project_id}.git" in joined


@pytest.mark.asyncio
async def test_workspace_path_uses_user_team_project_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Final clone target is /workspaces/<user_id>/<team_id>/<project_name>."""
    docker = _FakeDocker()
    user_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())
    project_name = "my-project"

    script = _ScriptedExec()
    script.add(_is_workspace_head_test, exit_code=1)
    script.add(
        _is_remote_url_get,
        stdout=f"git://team-mirror-x:9418/{project_id}.git\n",
        exit_code=0,
    )
    docker.containers.exec_script = script
    _patch_provision(monkeypatch)

    result = await clone_to_user_workspace(
        docker,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        user_id=user_id,
        team_id=team_id,
        project_id=project_id,
        project_name=project_name,
    )

    expected = f"/workspaces/{user_id}/{team_id}/{project_name}"
    assert result["workspace_path"] == expected
    # The clone destination on the cmd line matches that path.
    clone_call = next(
        c for c in docker.containers.exec_calls if _is_git_clone(c)
    )
    assert clone_call.cmd[-1] == expected
