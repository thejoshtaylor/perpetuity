"""Unit tests for run_auto_push mode='rule' branch fnmatch executor (M005/S04/T02).

Hermetic: no real Docker, no real Postgres. Reuses the same fake harness
shape as test_auto_push.py — imports helpers from there to avoid duplication.

Coverage (5 tests):
  1. mode=rule, ref matches branch_pattern → executes push (ok)
  2. mode=rule, ref does NOT match branch_pattern → skipped_branch_pattern_no_match
  3. mode=rule, no branch_pattern in DB → skipped_rule_no_branch_pattern
  4. mode=manual_workflow → skipped_rule_manual_workflow
  5. mode=auto, no ref passed → unchanged happy path (backward compat)
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Callable

os.environ.setdefault("SKIP_IMAGE_PULL_ON_BOOT", "1")
os.environ.setdefault("SKIP_PG_POOL_ON_BOOT", "1")
os.environ.setdefault("ORCHESTRATOR_API_KEY", "unit-test-current-key")

import pytest  # noqa: E402

from orchestrator import auto_push as auto_push_mod  # noqa: E402
from orchestrator.auto_push import run_auto_push  # noqa: E402
from orchestrator.github_tokens import InstallationTokenMintFailed  # noqa: E402


# ---------------------------------------------------------------------------
# Inline fake harness (same shape as test_auto_push.py)
# ---------------------------------------------------------------------------


class _ExecCall:
    def __init__(self, cmd: list[str], environment: dict[str, str] | None) -> None:
        self.cmd = list(cmd)
        self.environment = dict(environment) if environment is not None else None


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

    async def list(self, *, all: bool = False, filters: str = "") -> list[_FakeListedContainer]:  # noqa: A002, A003
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
            return self._pool.projects.get(str(args[0]))
        if "FROM project_push_rules" in sql:
            return self._pool.rules.get(str(args[0]))
        raise AssertionError(f"unexpected fetchrow sql: {sql}")

    async def execute(self, sql: str, *args: Any) -> str:
        if "UPDATE projects SET last_push_status" in sql:
            status, error, _id = args
            self._pool.last_push_writes.append((status, error))
        return "UPDATE 1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_project(pool: _FakePool, *, project_id: str, team_id: str) -> None:
    pool.projects[project_id] = _FakeRow(
        team_id=uuid.UUID(team_id),
        installation_id=42,
        github_repo_full_name="owner/repo",
    )


def _seed_rule(
    pool: _FakePool,
    *,
    project_id: str,
    mode: str,
    branch_pattern: str | None = None,
) -> None:
    pool.rules[project_id] = _FakeRow(
        mode=mode,
        branch_pattern=branch_pattern,
    )


def _seed_mirror(docker: _FakeDocker) -> None:
    docker.containers.list_results = [
        _FakeListedContainer("mirrorabc1234567890", running=True)
    ]


def _make_token_patcher(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _stub(
        installation_id: int,
        *,
        redis_client: Any | None = None,
        http_client: Any | None = None,
        pg_pool: Any | None = None,
    ) -> dict[str, Any]:
        return {"token": "gho_TESTTOKEN0123456789abcdef", "expires_at": "unknown", "source": "mint"}

    monkeypatch.setattr(auto_push_mod, "get_installation_token", _stub)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mode_rule_branch_matches_executes_push(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """mode=rule + ref matches branch_pattern → executes push and returns ok."""
    pool = _FakePool()
    docker = _FakeDocker()
    project_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())

    _seed_project(pool, project_id=project_id, team_id=team_id)
    _seed_rule(pool, project_id=project_id, mode="rule", branch_pattern="feature/*")
    _seed_mirror(docker)
    _make_token_patcher(monkeypatch)

    with caplog.at_level(logging.INFO, logger="orchestrator"):
        result = await run_auto_push(
            docker,
            pool,
            project_id=project_id,
            ref="refs/heads/feature/my-branch",
        )

    assert result["result"] == "ok"
    # Both push commands ran.
    exec_cmds = [" ".join(c.cmd) for c in docker.containers.exec_calls]
    assert any("push --all --prune" in cmd for cmd in exec_cmds)
    assert any("push --tags" in cmd for cmd in exec_cmds)
    msgs = [r.message for r in caplog.records]
    assert any(
        "auto_push_started" in m and "rule_mode=rule" in m and project_id in m
        for m in msgs
    )
    assert any("auto_push_completed" in m and "result=ok" in m for m in msgs)


@pytest.mark.asyncio
async def test_mode_rule_branch_no_match_returns_skipped(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """mode=rule + ref does NOT match pattern → skipped_branch_pattern_no_match."""
    pool = _FakePool()
    docker = _FakeDocker()
    project_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())

    _seed_project(pool, project_id=project_id, team_id=team_id)
    _seed_rule(pool, project_id=project_id, mode="rule", branch_pattern="feature/*")
    _seed_mirror(docker)
    _make_token_patcher(monkeypatch)

    with caplog.at_level(logging.INFO, logger="orchestrator"):
        result = await run_auto_push(
            docker,
            pool,
            project_id=project_id,
            ref="refs/heads/main",
        )

    assert result["result"] == "skipped_branch_pattern_no_match"
    # No push exec ran.
    assert docker.containers.exec_calls == []
    msgs = [r.message for r in caplog.records]
    assert any(
        "auto_push_skipped" in m
        and "branch_pattern_no_match" in m
        and project_id in m
        for m in msgs
    )


@pytest.mark.asyncio
async def test_mode_rule_no_branch_pattern_returns_skipped(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """mode=rule but no branch_pattern in DB → skipped_rule_no_branch_pattern."""
    pool = _FakePool()
    docker = _FakeDocker()
    project_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())

    _seed_project(pool, project_id=project_id, team_id=team_id)
    _seed_rule(pool, project_id=project_id, mode="rule", branch_pattern=None)
    _seed_mirror(docker)
    _make_token_patcher(monkeypatch)

    with caplog.at_level(logging.INFO, logger="orchestrator"):
        result = await run_auto_push(
            docker,
            pool,
            project_id=project_id,
            ref="refs/heads/feature/foo",
        )

    assert result["result"] == "skipped_rule_no_branch_pattern"
    assert docker.containers.exec_calls == []
    msgs = [r.message for r in caplog.records]
    assert any(
        "auto_push_skipped" in m
        and "rule_no_branch_pattern" in m
        and project_id in m
        for m in msgs
    )


@pytest.mark.asyncio
async def test_mode_manual_workflow_returns_skipped(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """mode=manual_workflow → skipped_rule_manual_workflow; no push exec."""
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

    assert result["result"] == "skipped_rule_manual_workflow"
    assert docker.containers.exec_calls == []
    msgs = [r.message for r in caplog.records]
    assert any(
        "auto_push_skipped" in m and "rule_manual_workflow" in m for m in msgs
    )


@pytest.mark.asyncio
async def test_mode_auto_no_ref_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """mode=auto with no ref kwarg → backward-compat happy path unchanged."""
    pool = _FakePool()
    docker = _FakeDocker()
    project_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())

    _seed_project(pool, project_id=project_id, team_id=team_id)
    # branch_pattern omitted — mode=auto never consults it
    _seed_rule(pool, project_id=project_id, mode="auto")
    _seed_mirror(docker)
    _make_token_patcher(monkeypatch)

    with caplog.at_level(logging.INFO, logger="orchestrator"):
        result = await run_auto_push(docker, pool, project_id=project_id)

    assert result["result"] == "ok"
    exec_cmds = [" ".join(c.cmd) for c in docker.containers.exec_calls]
    assert any("push --all --prune" in cmd for cmd in exec_cmds)
    msgs = [r.message for r in caplog.records]
    assert any(
        "auto_push_started" in m and "rule_mode=auto" in m for m in msgs
    )
