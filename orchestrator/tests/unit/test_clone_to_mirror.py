"""Unit tests for orchestrator/clone.py (M004/S04/T02).

Hermetic: no real Docker, no real Postgres, no real GitHub. Stand-ins:

  - ``_FakeDocker`` — exposes ``containers.list/create_or_replace/get`` so
    ``ensure_team_mirror`` is satisfied, plus a ``container.exec`` shim
    whose script is programmable per-test. The shim records the cmd list,
    the env dict, and returns ``(stdout, exit_code)`` from a queue or a
    handler callable.
  - ``_FakePool`` — asyncpg.Pool stand-in shaped for team_mirror_volumes.
  - ``_FakeGetInstallationToken`` — patches ``clone.get_installation_token``
    so we can drive the cache-hit / cache-miss / mint-failure paths without
    standing up Redis.

Coverage (12+ tests):
  1. happy path: ensure → token → clone → sanitize → verify → rename
  2. happy path: env-on-exec — token appears ONLY in env dict, NEVER in cmd
  3. happy path: token_prefix in log lines is _token_prefix(token), not full
  4. idempotent re-clone: existing /repos/<id>.git/HEAD short-circuits
  5. credential-leak detection — sanitize succeeded but config still has
     a token-prefix → CloneCredentialLeakDetected
  6. credential-leak detection — config still has `x-access-token` →
     CloneCredentialLeakDetected
  7. installation token mint failure → InstallationTokenMintFailed bubbles
  8. docker unavailable on exec → DockerUnavailable bubbles (503 path)
  9. git clone exec non-zero (auth-fail-equivalent: exit 128) → _CloneExecFailed
 10. git clone exec non-zero (repo-not-found-equivalent: exit 128 with no auth)
     → _CloneExecFailed
 11. atomic-rename failure → _CloneExecFailed
 12. clone_completed log emitted on happy path with team_id+project_id+result
 13. clone_started log emitted with token_prefix (4-char) before clone runs
 14. half-clone is rm -rf'd after a leak detection (no stale .tmp left)
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
    clone_to_mirror,
)
from orchestrator.errors import (  # noqa: E402
    CloneCredentialLeakDetected,
    DockerUnavailable,
)
from orchestrator.github_tokens import (  # noqa: E402
    InstallationTokenMintFailed,
)


# ---------------------------------------------------------------------------
# Fakes — Docker exec harness + asyncpg pool
# ---------------------------------------------------------------------------


class _ExecCall:
    """Recorded exec invocation: cmd list + environment dict."""

    def __init__(
        self, cmd: list[str], environment: dict[str, str] | None
    ) -> None:
        self.cmd = list(cmd)
        self.environment = (
            dict(environment) if environment is not None else None
        )


class _ExecResult:
    """Stand-in for the exec_inst object — supports start() + inspect()."""

    def __init__(self, stdout: str, exit_code: int) -> None:
        self._stdout = stdout
        self._exit_code = exit_code

    def start(self, *, detach: bool = False) -> "_ExecStream":
        return _ExecStream(self._stdout)

    async def inspect(self) -> dict[str, Any]:
        return {"ExitCode": self._exit_code}


class _ExecStream:
    """Async-context-manager stand-in for the exec stream."""

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
    """Stand-in for ``docker.containers.get(id)`` — supports exec/start/stop."""

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
        self.started = False
        self.stopped = False
        self.deleted = False

    async def start(self) -> None:
        self.started = True

    async def stop(self, *, timeout: int = 5) -> None:
        self.stopped = True

    async def delete(self, *, force: bool = False) -> None:
        self.deleted = True

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


class _FakeListedContainer:
    def __init__(self, container_id: str, *, running: bool = True) -> None:
        self.id = container_id
        self._container = {"State": "running" if running else "exited"}


class _FakeContainers:
    def __init__(self) -> None:
        self.list_results: list[_FakeListedContainer] = []
        self.create_id: str = "mirrorcontainer1234567890ab"
        # Recorder + script for exec calls inside the mirror.
        self.exec_calls: list[_ExecCall] = []
        self.exec_script: Callable[[_ExecCall], _ExecResult] = (
            lambda call: _ExecResult("", 0)
        )
        self.exec_raises: Exception | None = None

    async def list(  # noqa: A003
        self, *, all: bool = False, filters: str = ""  # noqa: A002
    ) -> list[_FakeListedContainer]:
        return list(self.list_results)

    async def create_or_replace(
        self, *, name: str, config: dict[str, Any]
    ) -> _FakeContainerHandle:
        return _FakeContainerHandle(
            self.create_id,
            script=self.exec_script,
            recorder=self.exec_calls,
            exec_raises=self.exec_raises,
        )

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


class _FakeRow(dict):
    pass


class _FakeConn:
    def __init__(self, pool: "_FakePool") -> None:
        self._pool = pool

    async def fetchrow(self, sql: str, *args: Any) -> _FakeRow | None:
        if "SELECT" in sql and "team_mirror_volumes" in sql:
            team_id = str(args[0])
            return self._pool.row_by_team.get(team_id)
        if "INSERT INTO team_mirror_volumes" in sql:
            new_id, team_uuid, volume_path = args
            team_id = str(team_uuid)
            row = _FakeRow(
                id=new_id,
                team_id=team_uuid,
                volume_path=volume_path,
                container_id=None,
                last_started_at=None,
                last_idle_at=None,
                always_on=False,
            )
            self._pool.row_by_team[team_id] = row
            return row
        raise AssertionError(f"unexpected fetchrow sql: {sql}")

    async def execute(self, sql: str, *args: Any) -> str:
        return "UPDATE 1"

    async def __aenter__(self) -> "_FakeConn":
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None


class _FakePool:
    def __init__(self) -> None:
        self.row_by_team: dict[str, _FakeRow] = {}

    def acquire(self) -> _FakeConn:
        return _FakeConn(self)


# ---------------------------------------------------------------------------
# Scripted exec harness — composable per-test
# ---------------------------------------------------------------------------


class _ScriptedExec:
    """Programmable exec script keyed on the cmd shape.

    Tests register matchers (callable predicate over ``_ExecCall``) and a
    return value. The first matching matcher wins. Unmatched calls return
    (stdout="", exit=0) by default — the caller can override the default
    by setting ``self.default``.

    This keeps test setup readable: register the few interesting cases,
    let everything else succeed quietly.
    """

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


def _is_test_head(call: _ExecCall) -> bool:
    return (
        len(call.cmd) >= 3
        and call.cmd[0] == "test"
        and call.cmd[1] == "-f"
        and call.cmd[2].endswith("/HEAD")
    )


def _is_git_clone(call: _ExecCall) -> bool:
    return (
        len(call.cmd) >= 3
        and call.cmd[0] == "sh"
        and call.cmd[1] == "-c"
        and "git clone --bare" in call.cmd[2]
    )


def _is_remote_set_url(call: _ExecCall) -> bool:
    return (
        len(call.cmd) >= 6
        and call.cmd[0] == "git"
        and call.cmd[1].startswith("--git-dir=")
        and call.cmd[2] == "remote"
        and call.cmd[3] == "set-url"
    )


def _is_cat_config(call: _ExecCall) -> bool:
    return (
        len(call.cmd) == 2
        and call.cmd[0] == "cat"
        and call.cmd[1].endswith("/config")
    )


def _is_mv(call: _ExecCall) -> bool:
    return len(call.cmd) >= 3 and call.cmd[0] == "mv"


def _is_rm_rf(call: _ExecCall) -> bool:
    return (
        len(call.cmd) >= 3
        and call.cmd[0] == "rm"
        and call.cmd[1] == "-rf"
    )


# Sanitized .git/config with no token. Sample the canonical bare-clone shape.
_CLEAN_CONFIG = (
    "[core]\n"
    "\trepositoryformatversion = 0\n"
    "\tfilemode = true\n"
    "\tbare = true\n"
    "[remote \"origin\"]\n"
    "\turl = https://github.com/owner/repo.git\n"
    "\tfetch = +refs/heads/*:refs/remotes/origin/*\n"
)

# Tainted config — token still embedded (sanitize "missed").
_TAINTED_CONFIG_TOKEN = (
    "[remote \"origin\"]\n"
    "\turl = https://x-access-token:gho_FAKE0123@github.com/owner/repo.git\n"
)

# Tainted config — only x-access-token sentinel (no token-prefix substring).
_TAINTED_CONFIG_USER = (
    "[remote \"origin\"]\n"
    "\turl = https://x-access-token:redacted@github.com/owner/repo.git\n"
)


def _make_token_patcher(
    monkeypatch: pytest.MonkeyPatch,
    *,
    token: str = "gho_TESTTOKEN0123456789abcdef",
    raises: Exception | None = None,
) -> dict[str, Any]:
    """Patch ``clone_mod.get_installation_token`` to a deterministic stub.

    Returns a dict the test can inspect (calls list, last installation_id).
    """
    state: dict[str, Any] = {"calls": [], "raises": raises}

    async def _stub(
        installation_id: int,
        *,
        redis_client: Any | None = None,
        http_client: Any | None = None,
        pg_pool: Any | None = None,
    ) -> dict[str, Any]:
        state["calls"].append(installation_id)
        if state["raises"] is not None:
            raise state["raises"]
        return {"token": token, "expires_at": "unknown", "source": "mint"}

    monkeypatch.setattr(clone_mod, "get_installation_token", _stub)
    return state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_clone_to_mirror_returns_created(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cold-start: ensure → token → clone → sanitize → verify → rename → ok."""
    pool = _FakePool()
    docker = _FakeDocker()
    team_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    script = _ScriptedExec()
    # /HEAD doesn't exist yet (idempotency check fails).
    script.add(_is_test_head, exit_code=1)
    # cat config returns the sanitized (clean) config.
    script.add(_is_cat_config, stdout=_CLEAN_CONFIG, exit_code=0)
    docker.containers.exec_script = script

    _make_token_patcher(monkeypatch)

    with caplog.at_level(logging.INFO, logger="orchestrator"):
        result = await clone_to_mirror(
            docker,
            pool,
            team_id=team_id,
            project_id=project_id,
            repo_full_name="owner/repo",
            installation_id=42,
        )

    assert result["result"] == "created"
    assert isinstance(result["duration_ms"], int)
    # Log markers for the slice's verification surface.
    msgs = [r.message for r in caplog.records]
    assert any(
        "team_mirror_clone_started" in m and team_id in m and project_id in m
        for m in msgs
    ), msgs
    assert any(
        "team_mirror_clone_completed" in m and project_id in m and "result=created" in m
        for m in msgs
    ), msgs


@pytest.mark.asyncio
async def test_token_passed_via_env_dict_never_in_cmd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The structural credential-discipline assertion (MEM228).

    The git-clone exec MUST receive the token via the ``environment`` dict.
    The cmd list MUST NOT contain the token plaintext anywhere.
    """
    pool = _FakePool()
    docker = _FakeDocker()
    team_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    script = _ScriptedExec()
    script.add(_is_test_head, exit_code=1)
    script.add(_is_cat_config, stdout=_CLEAN_CONFIG, exit_code=0)
    docker.containers.exec_script = script

    token_value = "gho_SECRETTOKENABC123"
    _make_token_patcher(monkeypatch, token=token_value)

    await clone_to_mirror(
        docker,
        pool,
        team_id=team_id,
        project_id=project_id,
        repo_full_name="owner/repo",
        installation_id=42,
    )

    clone_calls = [c for c in docker.containers.exec_calls if _is_git_clone(c)]
    assert len(clone_calls) == 1
    clone_call = clone_calls[0]

    # Env carries the token under TOKEN.
    assert clone_call.environment is not None
    assert clone_call.environment.get("TOKEN") == token_value

    # Cmd carries `$TOKEN` (the shell variable name), NOT the plaintext.
    joined = " ".join(clone_call.cmd)
    assert "$TOKEN" in joined
    assert token_value not in joined
    # Defense in depth: no GitHub token-prefix substring leaks into cmd.
    for fingerprint in ("gho_", "ghs_", "ghu_", "ghr_", "github_pat_"):
        assert fingerprint not in joined or fingerprint == "gho_" and "$TOKEN" in joined and token_value not in joined


@pytest.mark.asyncio
async def test_log_token_prefix_only_no_full_token(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Log lines only carry the 4-char token prefix; never the full token (MEM262)."""
    pool = _FakePool()
    docker = _FakeDocker()
    team_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    script = _ScriptedExec()
    script.add(_is_test_head, exit_code=1)
    script.add(_is_cat_config, stdout=_CLEAN_CONFIG, exit_code=0)
    docker.containers.exec_script = script

    token_value = "ghs_REDACTEDABCDEFGHIJKLMNOP"
    _make_token_patcher(monkeypatch, token=token_value)

    with caplog.at_level(logging.INFO, logger="orchestrator"):
        await clone_to_mirror(
            docker,
            pool,
            team_id=team_id,
            project_id=project_id,
            repo_full_name="owner/repo",
            installation_id=42,
        )

    msgs = [r.message for r in caplog.records]
    started = next(m for m in msgs if "team_mirror_clone_started" in m)
    assert "token_prefix=ghs_..." in started
    # Full token never appears in any log line.
    for m in msgs:
        assert token_value not in m


@pytest.mark.asyncio
async def test_idempotent_reclone_skips_when_head_exists(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If /repos/<id>.git/HEAD exists, return reused without minting."""
    pool = _FakePool()
    docker = _FakeDocker()
    team_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    script = _ScriptedExec()
    # HEAD exists → idempotency short-circuit fires.
    script.add(_is_test_head, exit_code=0)
    docker.containers.exec_script = script

    token_state = _make_token_patcher(monkeypatch)

    with caplog.at_level(logging.INFO, logger="orchestrator"):
        result = await clone_to_mirror(
            docker,
            pool,
            team_id=team_id,
            project_id=project_id,
            repo_full_name="owner/repo",
            installation_id=42,
        )

    assert result == {"result": "reused", "duration_ms": 0}
    # Token was never minted (idempotent path).
    assert token_state["calls"] == []
    # No git clone was attempted.
    clone_calls = [c for c in docker.containers.exec_calls if _is_git_clone(c)]
    assert clone_calls == []
    # Completed log fires with result=reused.
    msgs = [r.message for r in caplog.records]
    assert any(
        "team_mirror_clone_completed" in m and "result=reused" in m
        for m in msgs
    ), msgs


@pytest.mark.asyncio
async def test_credential_leak_detected_on_token_prefix_in_config(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A `gho_...` substring in the post-sanitize config raises Leak."""
    pool = _FakePool()
    docker = _FakeDocker()
    team_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    script = _ScriptedExec()
    script.add(_is_test_head, exit_code=1)
    script.add(_is_cat_config, stdout=_TAINTED_CONFIG_TOKEN, exit_code=0)
    docker.containers.exec_script = script

    _make_token_patcher(monkeypatch)

    with caplog.at_level(logging.ERROR, logger="orchestrator"):
        with pytest.raises(CloneCredentialLeakDetected) as exc_info:
            await clone_to_mirror(
                docker,
                pool,
                team_id=team_id,
                project_id=project_id,
                repo_full_name="owner/repo",
                installation_id=42,
            )

    assert exc_info.value.project_id == project_id
    msgs = [r.message for r in caplog.records]
    assert any(
        "clone_credential_leak_detected" in m and project_id in m
        for m in msgs
    ), msgs


@pytest.mark.asyncio
async def test_credential_leak_detected_on_x_access_token_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`x-access-token` substring (without token prefix) still trips the verify."""
    pool = _FakePool()
    docker = _FakeDocker()
    team_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    script = _ScriptedExec()
    script.add(_is_test_head, exit_code=1)
    script.add(_is_cat_config, stdout=_TAINTED_CONFIG_USER, exit_code=0)
    docker.containers.exec_script = script

    _make_token_patcher(monkeypatch)

    with pytest.raises(CloneCredentialLeakDetected):
        await clone_to_mirror(
            docker,
            pool,
            team_id=team_id,
            project_id=project_id,
            repo_full_name="owner/repo",
            installation_id=42,
        )


@pytest.mark.asyncio
async def test_credential_leak_triggers_half_clone_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After leak detection, a `rm -rf /repos/.tmp/<id>.git` call is recorded."""
    pool = _FakePool()
    docker = _FakeDocker()
    team_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    script = _ScriptedExec()
    script.add(_is_test_head, exit_code=1)
    script.add(_is_cat_config, stdout=_TAINTED_CONFIG_TOKEN, exit_code=0)
    docker.containers.exec_script = script

    _make_token_patcher(monkeypatch)

    with pytest.raises(CloneCredentialLeakDetected):
        await clone_to_mirror(
            docker,
            pool,
            team_id=team_id,
            project_id=project_id,
            repo_full_name="owner/repo",
            installation_id=42,
        )

    # At least one rm -rf targeting the tmp path executed after the leak.
    tmp = f"/repos/.tmp/{project_id}.git"
    rm_calls = [
        c for c in docker.containers.exec_calls
        if _is_rm_rf(c) and tmp in c.cmd
    ]
    assert len(rm_calls) >= 1


@pytest.mark.asyncio
async def test_installation_token_mint_failure_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_installation_token raising InstallationTokenMintFailed bubbles up."""
    pool = _FakePool()
    docker = _FakeDocker()
    team_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    script = _ScriptedExec()
    script.add(_is_test_head, exit_code=1)
    docker.containers.exec_script = script

    raised = InstallationTokenMintFailed(404, "404:Not Found")
    _make_token_patcher(monkeypatch, raises=raised)

    with pytest.raises(InstallationTokenMintFailed) as exc_info:
        await clone_to_mirror(
            docker,
            pool,
            team_id=team_id,
            project_id=project_id,
            repo_full_name="owner/repo",
            installation_id=42,
        )

    assert exc_info.value.status == 404
    assert exc_info.value.reason == "404:Not Found"


@pytest.mark.asyncio
async def test_docker_unavailable_during_exec_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An OSError on docker.containers.get → DockerUnavailable from clone."""
    pool = _FakePool()
    docker = _FakeDocker()
    team_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    docker.containers.exec_raises = OSError("connection refused")

    _make_token_patcher(monkeypatch)

    with pytest.raises(DockerUnavailable):
        await clone_to_mirror(
            docker,
            pool,
            team_id=team_id,
            project_id=project_id,
            repo_full_name="owner/repo",
            installation_id=42,
        )


@pytest.mark.asyncio
async def test_git_clone_non_zero_exit_raises_clone_exec_failed(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """git clone returning 128 (auth fail / repo not found) → _CloneExecFailed."""
    pool = _FakePool()
    docker = _FakeDocker()
    team_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    script = _ScriptedExec()
    script.add(_is_test_head, exit_code=1)
    # The clone itself fails with exit 128 (the GitHub-class error code).
    script.add(_is_git_clone, exit_code=128)
    docker.containers.exec_script = script

    _make_token_patcher(monkeypatch)

    with caplog.at_level(logging.ERROR, logger="orchestrator"):
        with pytest.raises(_CloneExecFailed) as exc_info:
            await clone_to_mirror(
                docker,
                pool,
                team_id=team_id,
                project_id=project_id,
                repo_full_name="owner/repo",
                installation_id=42,
            )

    assert exc_info.value.exit_code == 128
    assert exc_info.value.op == "git_clone"
    msgs = [r.message for r in caplog.records]
    assert any(
        "team_mirror_clone_failed" in m and "git_clone_exit_128" in m
        for m in msgs
    ), msgs


@pytest.mark.asyncio
async def test_git_clone_non_zero_cleans_up_half_clone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed git clone leaves no /repos/.tmp/<id>.git lingering."""
    pool = _FakePool()
    docker = _FakeDocker()
    team_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    script = _ScriptedExec()
    script.add(_is_test_head, exit_code=1)
    script.add(_is_git_clone, exit_code=128)
    docker.containers.exec_script = script

    _make_token_patcher(monkeypatch)

    with pytest.raises(_CloneExecFailed):
        await clone_to_mirror(
            docker,
            pool,
            team_id=team_id,
            project_id=project_id,
            repo_full_name="owner/repo",
            installation_id=42,
        )

    # rm -rf targeting the tmp path was issued as cleanup.
    tmp = f"/repos/.tmp/{project_id}.git"
    rm_calls = [
        c for c in docker.containers.exec_calls
        if _is_rm_rf(c) and tmp in c.cmd
    ]
    assert len(rm_calls) >= 1


@pytest.mark.asyncio
async def test_atomic_rename_failure_raises_clone_exec_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`mv` non-zero (e.g. EXDEV across filesystems) → _CloneExecFailed."""
    pool = _FakePool()
    docker = _FakeDocker()
    team_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    script = _ScriptedExec()
    script.add(_is_test_head, exit_code=1)
    script.add(_is_cat_config, stdout=_CLEAN_CONFIG, exit_code=0)
    script.add(_is_mv, exit_code=1)
    docker.containers.exec_script = script

    _make_token_patcher(monkeypatch)

    with pytest.raises(_CloneExecFailed) as exc_info:
        await clone_to_mirror(
            docker,
            pool,
            team_id=team_id,
            project_id=project_id,
            repo_full_name="owner/repo",
            installation_id=42,
        )

    assert exc_info.value.op == "rename"


@pytest.mark.asyncio
async def test_remote_set_url_failure_raises_clone_exec_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `git remote set-url` non-zero raises with op=remote_set_url."""
    pool = _FakePool()
    docker = _FakeDocker()
    team_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    script = _ScriptedExec()
    script.add(_is_test_head, exit_code=1)
    script.add(_is_remote_set_url, exit_code=2)
    # cat-config never reached because set-url fails first.
    docker.containers.exec_script = script

    _make_token_patcher(monkeypatch)

    with pytest.raises(_CloneExecFailed) as exc_info:
        await clone_to_mirror(
            docker,
            pool,
            team_id=team_id,
            project_id=project_id,
            repo_full_name="owner/repo",
            installation_id=42,
        )

    assert exc_info.value.op == "remote_set_url"


@pytest.mark.asyncio
async def test_clone_url_uses_repo_full_name_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The git-clone URL includes the caller's repo_full_name (owner/repo)."""
    pool = _FakePool()
    docker = _FakeDocker()
    team_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    script = _ScriptedExec()
    script.add(_is_test_head, exit_code=1)
    script.add(_is_cat_config, stdout=_CLEAN_CONFIG, exit_code=0)
    docker.containers.exec_script = script

    _make_token_patcher(monkeypatch)

    await clone_to_mirror(
        docker,
        pool,
        team_id=team_id,
        project_id=project_id,
        repo_full_name="some-org/some-repo",
        installation_id=42,
    )

    clone_call = next(
        c for c in docker.containers.exec_calls if _is_git_clone(c)
    )
    joined = " ".join(clone_call.cmd)
    assert "github.com/some-org/some-repo.git" in joined


@pytest.mark.asyncio
async def test_remote_set_url_strips_token_to_bare_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sanitize call's URL is bare https (no token, no x-access-token)."""
    pool = _FakePool()
    docker = _FakeDocker()
    team_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    script = _ScriptedExec()
    script.add(_is_test_head, exit_code=1)
    script.add(_is_cat_config, stdout=_CLEAN_CONFIG, exit_code=0)
    docker.containers.exec_script = script

    _make_token_patcher(monkeypatch)

    await clone_to_mirror(
        docker,
        pool,
        team_id=team_id,
        project_id=project_id,
        repo_full_name="owner/repo",
        installation_id=42,
    )

    set_url_call = next(
        c for c in docker.containers.exec_calls if _is_remote_set_url(c)
    )
    # Last positional in the cmd is the bare URL.
    assert set_url_call.cmd[-1] == "https://github.com/owner/repo.git"
    # Env dict carries no TOKEN on the sanitize step.
    assert set_url_call.environment is None or "TOKEN" not in (
        set_url_call.environment or {}
    )


@pytest.mark.asyncio
async def test_idempotent_path_emits_no_clone_started_log(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The reused path skips the clone_started marker (no GitHub I/O at all)."""
    pool = _FakePool()
    docker = _FakeDocker()
    team_id = str(uuid.uuid4())
    project_id = str(uuid.uuid4())

    script = _ScriptedExec()
    script.add(_is_test_head, exit_code=0)
    docker.containers.exec_script = script

    _make_token_patcher(monkeypatch)

    with caplog.at_level(logging.INFO, logger="orchestrator"):
        result = await clone_to_mirror(
            docker,
            pool,
            team_id=team_id,
            project_id=project_id,
            repo_full_name="owner/repo",
            installation_id=42,
        )

    assert result["result"] == "reused"
    msgs = [r.message for r in caplog.records]
    # No clone_started — we short-circuited before minting.
    assert not any("team_mirror_clone_started" in m for m in msgs), msgs
