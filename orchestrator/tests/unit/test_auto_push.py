"""Unit tests for orchestrator/auto_push.py (M004/S04/T04).

Hermetic: no real Docker, no real Postgres, no real GitHub. Same fake-Docker
shape as test_clone_to_mirror.py — exec calls are programmable and recorded;
the asyncpg pool is a per-test in-memory dict.

Coverage (10+ tests):
  1. happy path: load → rule check → token mint → push --all + push --tags
     → projects.last_push_status='ok'
  2. happy path: env-on-exec — token in env dict, NEVER in cmd; logs only
     carry token_prefix
  3. push --all rejected by remote (exit 1) → status='failed', stderr scrubbed
     into last_push_error, WARNING auto_push_rejected_by_remote emitted
  4. push --tags rejected by remote (exit 1, --all succeeded) → status='failed'
  5. rule changed to non-auto between hook install and callback → skipped
  6. project missing → project_not_found, no exec calls fired
  7. token mint failure → token_mint_failed, last_push_status='failed' with
     a status-coded error
  8. mirror container missing → mirror_unavailable, last_push_status untouched
  9. stderr scrubbing — gho_/ghs_/ghu_/ghr_/github_pat_ patterns redacted
     from both DB persist and log line
 10. _push_all succeeded but stops if --tags fails — both runs counted
 11. last_push_status='ok' clears any prior last_push_error
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, Callable

# SKIP boot-time side effects before importing orchestrator modules.
os.environ.setdefault("SKIP_IMAGE_PULL_ON_BOOT", "1")
os.environ.setdefault("SKIP_PG_POOL_ON_BOOT", "1")
os.environ.setdefault("ORCHESTRATOR_API_KEY", "unit-test-current-key")

import pytest  # noqa: E402

from orchestrator import auto_push as auto_push_mod  # noqa: E402
from orchestrator.auto_push import (  # noqa: E402
    _scrub_token_substrings,
    run_auto_push,
)
from orchestrator.github_tokens import (  # noqa: E402
    InstallationTokenMintFailed,
)


# ---------------------------------------------------------------------------
# Fakes — Docker exec harness + asyncpg pool (mirrors test_clone_to_mirror)
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


class _FakeListedContainer:
    def __init__(self, container_id: str, *, running: bool = True) -> None:
        self.id = container_id
        self._container = {"State": "running" if running else "exited"}


class _FakeContainers:
    def __init__(self) -> None:
        self.list_results: list[_FakeListedContainer] = []
        self.exec_calls: list[_ExecCall] = []
        self.exec_script: Callable[[_ExecCall], _ExecResult] = (
            lambda call: _ExecResult("", 0)
        )

    async def list(  # noqa: A003
        self, *, all: bool = False, filters: str = ""  # noqa: A002
    ) -> list[_FakeListedContainer]:
        # The auto_push container-finder filters by team_id label; tests
        # populate self.list_results directly so we just hand them back.
        return list(self.list_results)

    async def get(self, container_id: str) -> _FakeContainerHandle:
        return _FakeContainerHandle(
            container_id,
            script=self.exec_script,
            recorder=self.exec_calls,
        )


class _FakeDocker:
    def __init__(self) -> None:
        self.containers = _FakeContainers()


class _FakeRow(dict):
    pass


class _FakePool:
    def __init__(self) -> None:
        self.projects: dict[str, _FakeRow] = {}
        self.rules: dict[str, _FakeRow] = {}
        # Per-project capture of (status, error) UPDATE writes.
        self.last_push_writes: list[tuple[str, str | None]] = []

    def acquire(self) -> "_FakeConn":
        return _FakeConn(self)


class _FakeConn:
    def __init__(self, pool: _FakePool) -> None:
        self._pool = pool

    async def __aenter__(self) -> "_FakeConn":
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None

    async def fetchrow(self, sql: str, *args: Any) -> _FakeRow | None:
        if "FROM projects" in sql:
            project_id = str(args[0])
            return self._pool.projects.get(project_id)
        if "FROM project_push_rules" in sql:
            project_id = str(args[0])
            return self._pool.rules.get(project_id)
        raise AssertionError(f"unexpected fetchrow sql: {sql}")

    async def execute(self, sql: str, *args: Any) -> str:
        if "UPDATE projects SET last_push_status" in sql:
            status, error, _id = args
            self._pool.last_push_writes.append((status, error))
            return "UPDATE 1"
        return "UPDATE 1"


# ---------------------------------------------------------------------------
# Scripted exec harness
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


def _is_push_all(call: _ExecCall) -> bool:
    return (
        len(call.cmd) >= 3
        and call.cmd[0] == "sh"
        and call.cmd[1] == "-c"
        and "push --all --prune" in call.cmd[2]
    )


def _is_push_tags(call: _ExecCall) -> bool:
    return (
        len(call.cmd) >= 3
        and call.cmd[0] == "sh"
        and call.cmd[1] == "-c"
        and "push --tags" in call.cmd[2]
    )


# ---------------------------------------------------------------------------
# Helpers — seed pool + token patcher
# ---------------------------------------------------------------------------


def _seed_project(
    pool: _FakePool,
    *,
    project_id: str,
    team_id: str,
    installation_id: int = 42,
    repo_full_name: str = "owner/repo",
) -> None:
    pool.projects[project_id] = _FakeRow(
        team_id=uuid.UUID(team_id),
        installation_id=installation_id,
        github_repo_full_name=repo_full_name,
    )


def _seed_rule(
    pool: _FakePool, *, project_id: str, mode: str = "auto"
) -> None:
    pool.rules[project_id] = _FakeRow(mode=mode)


def _seed_mirror(
    docker: _FakeDocker, container_id: str = "mirrorabc1234567890"
) -> None:
    docker.containers.list_results = [
        _FakeListedContainer(container_id, running=True)
    ]


def _make_token_patcher(
    monkeypatch: pytest.MonkeyPatch,
    *,
    token: str = "gho_TESTTOKEN0123456789abcdef",
    raises: Exception | None = None,
) -> dict[str, Any]:
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

    monkeypatch.setattr(auto_push_mod, "get_installation_token", _stub)
    return state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_pushes_all_and_tags_persists_ok(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ok path: load → rule=auto → mint → push-all + push-tags → status=ok."""
    pool = _FakePool()
    docker = _FakeDocker()
    project_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())

    _seed_project(pool, project_id=project_id, team_id=team_id)
    _seed_rule(pool, project_id=project_id, mode="auto")
    _seed_mirror(docker)

    docker.containers.exec_script = _ScriptedExec()
    _make_token_patcher(monkeypatch)

    with caplog.at_level(logging.INFO, logger="orchestrator"):
        result = await run_auto_push(docker, pool, project_id=project_id)

    assert result["result"] == "ok"
    # Both push commands ran.
    assert any(_is_push_all(c) for c in docker.containers.exec_calls)
    assert any(_is_push_tags(c) for c in docker.containers.exec_calls)
    # Status persisted.
    assert pool.last_push_writes[-1] == ("ok", None)

    msgs = [r.message for r in caplog.records]
    assert any("auto_push_started" in m and project_id in m for m in msgs)
    assert any(
        "auto_push_completed" in m and "result=ok" in m for m in msgs
    )


@pytest.mark.asyncio
async def test_token_passed_via_env_dict_never_in_cmd(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The structural credential-discipline assertion (MEM228 / MEM274)."""
    pool = _FakePool()
    docker = _FakeDocker()
    project_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())

    _seed_project(pool, project_id=project_id, team_id=team_id)
    _seed_rule(pool, project_id=project_id, mode="auto")
    _seed_mirror(docker)

    docker.containers.exec_script = _ScriptedExec()
    token = "gho_SECRETTOKENABC123XYZ"
    _make_token_patcher(monkeypatch, token=token)

    with caplog.at_level(logging.INFO, logger="orchestrator"):
        await run_auto_push(docker, pool, project_id=project_id)

    push_all = next(
        c for c in docker.containers.exec_calls if _is_push_all(c)
    )
    assert push_all.environment is not None
    assert push_all.environment.get("TOKEN") == token
    joined = " ".join(push_all.cmd)
    assert "$TOKEN" in joined
    assert token not in joined

    # Logs only carry the prefix.
    for r in caplog.records:
        assert token not in r.getMessage()


@pytest.mark.asyncio
async def test_push_all_rejected_persists_failed_with_scrubbed_stderr(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """push --all exit 1 → status=failed, last_push_error scrubbed of token."""
    pool = _FakePool()
    docker = _FakeDocker()
    project_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())

    _seed_project(pool, project_id=project_id, team_id=team_id)
    _seed_rule(pool, project_id=project_id, mode="auto")
    _seed_mirror(docker)

    # Stderr that includes a token-prefix substring (the redact target).
    tainted_stderr = (
        "fatal: unable to access "
        "'https://x-access-token:gho_AAAA1111BBBB2222@github.com/owner/repo.git':"
        " The requested URL returned error: 403\n"
    )

    script = _ScriptedExec()
    script.add(_is_push_all, stdout=tainted_stderr, exit_code=1)
    docker.containers.exec_script = script
    _make_token_patcher(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="orchestrator"):
        result = await run_auto_push(docker, pool, project_id=project_id)

    assert result["result"] == "failed"
    assert result["exit_code"] == 1

    # Last write is failed + scrubbed.
    status, error = pool.last_push_writes[-1]
    assert status == "failed"
    assert error is not None
    assert "gho_AAAA1111BBBB2222" not in error
    assert "<redacted-token>" in error

    msgs = [r.getMessage() for r in caplog.records]
    assert any(
        "auto_push_rejected_by_remote" in m and project_id in m
        for m in msgs
    )
    # Log line also has scrubbed stderr.
    assert not any("gho_AAAA1111BBBB2222" in m for m in msgs)


@pytest.mark.asyncio
async def test_push_tags_failed_after_push_all_succeeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """push-all=0, push-tags=1 → result=failed with the tags exit code."""
    pool = _FakePool()
    docker = _FakeDocker()
    project_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())

    _seed_project(pool, project_id=project_id, team_id=team_id)
    _seed_rule(pool, project_id=project_id, mode="auto")
    _seed_mirror(docker)

    script = _ScriptedExec()
    script.add(_is_push_all, exit_code=0)
    script.add(_is_push_tags, stdout="bad tag\n", exit_code=128)
    docker.containers.exec_script = script
    _make_token_patcher(monkeypatch)

    result = await run_auto_push(docker, pool, project_id=project_id)
    assert result["result"] == "failed"
    assert result["exit_code"] == 128
    assert pool.last_push_writes[-1][0] == "failed"


@pytest.mark.asyncio
async def test_rule_changed_skipped_no_exec(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If mode is no longer auto on re-read → skipped_rule_changed; no push."""
    pool = _FakePool()
    docker = _FakeDocker()
    project_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())

    _seed_project(pool, project_id=project_id, team_id=team_id)
    _seed_rule(pool, project_id=project_id, mode="manual_workflow")
    _seed_mirror(docker)
    _make_token_patcher(monkeypatch)

    with caplog.at_level(logging.INFO, logger="orchestrator"):
        result = await run_auto_push(docker, pool, project_id=project_id)

    assert result == {"result": "skipped_rule_changed"}
    # No push exec ran.
    assert not any(
        _is_push_all(c) or _is_push_tags(c)
        for c in docker.containers.exec_calls
    )
    msgs = [r.message for r in caplog.records]
    assert any(
        "auto_push_skipped" in m and "rule_changed" in m for m in msgs
    )


@pytest.mark.asyncio
async def test_project_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing project row → project_not_found; no exec calls."""
    pool = _FakePool()
    docker = _FakeDocker()
    project_id = str(uuid.uuid4())
    _make_token_patcher(monkeypatch)

    result = await run_auto_push(docker, pool, project_id=project_id)
    assert result == {"result": "project_not_found"}
    assert docker.containers.exec_calls == []


@pytest.mark.asyncio
async def test_token_mint_failure_returns_token_mint_failed(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """get_installation_token raising → token_mint_failed + status=failed."""
    pool = _FakePool()
    docker = _FakeDocker()
    project_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())

    _seed_project(pool, project_id=project_id, team_id=team_id)
    _seed_rule(pool, project_id=project_id, mode="auto")
    _seed_mirror(docker)

    raised = InstallationTokenMintFailed(404, "404:Not Found")
    _make_token_patcher(monkeypatch, raises=raised)

    with caplog.at_level(logging.ERROR, logger="orchestrator"):
        result = await run_auto_push(docker, pool, project_id=project_id)

    assert result["result"] == "token_mint_failed"
    assert result["status"] == 404
    # last_push_status row was updated to failed with a status-coded error.
    status, error = pool.last_push_writes[-1]
    assert status == "failed"
    assert error is not None
    assert "404" in error
    msgs = [r.message for r in caplog.records]
    assert any("auto_push_token_mint_failed" in m for m in msgs)


@pytest.mark.asyncio
async def test_mirror_unavailable_returns_no_op(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No running mirror container → mirror_unavailable; no last_push update."""
    pool = _FakePool()
    docker = _FakeDocker()  # No list_results seeded — empty mirror set.
    project_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())

    _seed_project(pool, project_id=project_id, team_id=team_id)
    _seed_rule(pool, project_id=project_id, mode="auto")
    _make_token_patcher(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="orchestrator"):
        result = await run_auto_push(docker, pool, project_id=project_id)

    assert result == {"result": "mirror_unavailable"}
    # No push exec ran (no mirror to exec into).
    assert docker.containers.exec_calls == []
    # No last_push_status write (the prior status remains correct).
    assert pool.last_push_writes == []
    msgs = [r.message for r in caplog.records]
    assert any("auto_push_mirror_unavailable" in m for m in msgs)


def test_scrub_token_substrings_redacts_all_prefix_families() -> None:
    """All five GitHub token prefix families get redacted."""
    cases = [
        "gho_FAKE0123456789abcdef",
        "ghs_FAKE0123456789abcdef",
        "ghu_FAKE0123456789abcdef",
        "ghr_FAKE0123456789abcdef",
        "github_pat_FAKE0123abc456789",
    ]
    for token in cases:
        text = f"fatal: blah {token} blah blah\n"
        scrubbed = _scrub_token_substrings(text)
        assert token not in scrubbed
        assert "<redacted-token>" in scrubbed


def test_scrub_token_substrings_preserves_safe_text() -> None:
    """Non-token text passes through unchanged."""
    text = "fatal: unable to access 'https://github.com/owner/repo.git'"
    assert _scrub_token_substrings(text) == text


def test_scrub_token_substrings_empty_input() -> None:
    """Empty / None-shaped input is returned as-is."""
    assert _scrub_token_substrings("") == ""


@pytest.mark.asyncio
async def test_url_includes_repo_full_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The push URL contains the project's repo_full_name verbatim."""
    pool = _FakePool()
    docker = _FakeDocker()
    project_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())

    _seed_project(
        pool,
        project_id=project_id,
        team_id=team_id,
        repo_full_name="some-org/some-repo",
    )
    _seed_rule(pool, project_id=project_id, mode="auto")
    _seed_mirror(docker)

    docker.containers.exec_script = _ScriptedExec()
    _make_token_patcher(monkeypatch)

    await run_auto_push(docker, pool, project_id=project_id)

    push_all = next(
        c for c in docker.containers.exec_calls if _is_push_all(c)
    )
    joined = " ".join(push_all.cmd)
    assert "github.com/some-org/some-repo.git" in joined


@pytest.mark.asyncio
async def test_push_all_cmd_uses_correct_git_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The push command targets the right /repos/<id>.git via --git-dir."""
    pool = _FakePool()
    docker = _FakeDocker()
    project_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())

    _seed_project(pool, project_id=project_id, team_id=team_id)
    _seed_rule(pool, project_id=project_id, mode="auto")
    _seed_mirror(docker)

    docker.containers.exec_script = _ScriptedExec()
    _make_token_patcher(monkeypatch)

    await run_auto_push(docker, pool, project_id=project_id)

    push_all = next(
        c for c in docker.containers.exec_calls if _is_push_all(c)
    )
    joined = " ".join(push_all.cmd)
    assert f"--git-dir=/repos/{project_id}.git" in joined


@pytest.mark.asyncio
async def test_mirror_filter_includes_team_id_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The container-list filter scopes by the team_id from the project row."""
    pool = _FakePool()
    docker = _FakeDocker()
    project_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())

    _seed_project(pool, project_id=project_id, team_id=team_id)
    _seed_rule(pool, project_id=project_id, mode="auto")
    _seed_mirror(docker)

    captured: dict[str, Any] = {}
    real_list = docker.containers.list

    async def _spy(  # type: ignore[no-untyped-def]
        *, all: bool = False, filters: str = ""  # noqa: A002
    ):
        captured["filters"] = filters
        return await real_list(all=all, filters=filters)

    docker.containers.list = _spy  # type: ignore[assignment]
    docker.containers.exec_script = _ScriptedExec()
    _make_token_patcher(monkeypatch)

    await run_auto_push(docker, pool, project_id=project_id)

    parsed = json.loads(captured["filters"])
    label_filter = parsed["label"]
    assert any(f"team_id={team_id}" in lbl for lbl in label_filter)
    assert "perpetuity.team_mirror=true" in label_filter
