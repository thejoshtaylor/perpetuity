"""Unit tests for `app.workflows.executors.shell.run_shell_step`.

Covers:
  * Happy path — orchestrator returns exit 0 → step succeeded, stdout stored.
  * cli_nonzero — orchestrator returns exit 1 → step failed with cli_nonzero.
  * team_mirror target_container → step failed with unsupported_action_for_target.
  * Missing cmd in config → step failed with orchestrator_exec_failed.
  * Orchestrator HTTP error → step failed with orchestrator_exec_failed.
  * Orchestrator non-200 → step failed with orchestrator_exec_failed.
  * Cmd is passed through to orchestrator as-is (no rendering here — runner did it).
  * cwd and env optional fields forwarded in body when present.
"""
from __future__ import annotations

import uuid
from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import text
from sqlmodel import Session

from app.models import StepRun, Team, WorkflowRun
from app.workflows.executors import shell as shell_executor
from app.workflows.executors.shell import run_shell_step

_TEST_FERNET_KEY = "q14YMz9s4jrbfD29GvcRfe_4krg82w6_mPWUu_y3LTo="


@pytest.fixture(autouse=True)
def _set_encryption_key(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("SYSTEM_SETTINGS_ENCRYPTION_KEY", _TEST_FERNET_KEY)
    from app.core import encryption as _enc
    _enc._load_key.cache_clear()
    yield
    _enc._load_key.cache_clear()


@pytest.fixture(autouse=True)
def _clean_workflow_rows(db: Session) -> Generator[None, None, None]:
    db.execute(text("DELETE FROM step_runs"))
    db.execute(text("DELETE FROM workflow_runs"))
    db.execute(text("DELETE FROM workflow_steps"))
    db.execute(text("DELETE FROM workflows"))
    db.commit()
    yield
    db.execute(text("DELETE FROM step_runs"))
    db.execute(text("DELETE FROM workflow_runs"))
    db.execute(text("DELETE FROM workflow_steps"))
    db.execute(text("DELETE FROM workflows"))
    db.commit()


def _make_team(db: Session) -> Team:
    suffix = uuid.uuid4().hex[:8]
    team = Team(name=f"shell-test-{suffix}", slug=f"shell-test-{suffix}")
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


def _make_user(db: Session) -> uuid.UUID:
    from app.core.security import get_password_hash
    from app.models import User
    user = User(
        email=f"shell-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=get_password_hash("x"),
        full_name="Shell Test",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user.id


def _make_step_run(
    db: Session,
    team: Team,
    user_id: uuid.UUID,
    *,
    config: dict | None = None,
    target_container: str = "user_workspace",
) -> uuid.UUID:
    """Insert a minimal workflow + run + step_run in running status."""
    workflow_id = uuid.uuid4()
    run_id = uuid.uuid4()
    step_run_id = uuid.uuid4()
    cfg = {"cmd": ["echo", "hello"]} if config is None else config
    import json
    snapshot = json.dumps({
        "id": str(uuid.uuid4()),
        "workflow_id": str(workflow_id),
        "step_index": 0,
        "action": "shell",
        "config": cfg,
        "target_container": target_container,
    })
    db.execute(
        text("INSERT INTO workflows (id, team_id, name, scope, system_owned) VALUES (:id, :t, :n, 'user', FALSE)"),
        {"id": workflow_id, "t": team.id, "n": f"sh-{uuid.uuid4().hex[:6]}"},
    )
    db.execute(
        text(
            "INSERT INTO workflow_runs (id, workflow_id, team_id, trigger_type, triggered_by_user_id, target_user_id, trigger_payload, status) "
            "VALUES (:id, :wf, :t, 'button', :u, :u, CAST(:p AS JSONB), 'running')"
        ),
        {"id": run_id, "wf": workflow_id, "t": team.id, "u": user_id, "p": "{}"},
    )
    db.execute(
        text("INSERT INTO step_runs (id, workflow_run_id, step_index, snapshot, status) VALUES (:id, :r, 0, CAST(:s AS JSONB), 'running')"),
        {"id": step_run_id, "r": run_id, "s": snapshot},
    )
    db.commit()
    return step_run_id


class _FakeResponse:
    def __init__(self, status_code: int, body: dict | None = None):
        self.status_code = status_code
        self._body = body or {}

    def json(self) -> dict:
        return self._body


class _FakeClient:
    def __init__(self, response: _FakeResponse | Exception):
        self._response = response
        self.captured: dict[str, Any] = {}

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def post(self, url: str, *, json: dict | None = None, headers: dict | None = None) -> _FakeResponse:
        self.captured = {"url": url, "json": json, "headers": headers}
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _patch_httpx(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> None:
    monkeypatch.setattr(shell_executor.httpx, "Client", lambda *a, **k: fake)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_run_shell_step_happy_path(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    team = _make_team(db)
    user_id = _make_user(db)
    step_run_id = _make_step_run(db, team, user_id, config={"cmd": ["ls", "-la"]})

    fake = _FakeClient(_FakeResponse(200, {"stdout": "file.txt", "exit_code": 0, "duration_ms": 5}))
    _patch_httpx(monkeypatch, fake)

    run_shell_step(db, step_run_id)

    db.expire_all()
    row = db.get(StepRun, step_run_id)
    assert row is not None
    assert row.status == "succeeded"
    assert row.exit_code == 0
    assert row.stdout == "file.txt"
    assert row.error_class is None
    # Cmd passed through as-is.
    assert fake.captured["json"]["cmd"] == ["ls", "-la"]
    assert fake.captured["json"]["action"] == "shell"


def test_run_shell_step_forwards_cwd_and_env(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    team = _make_team(db)
    user_id = _make_user(db)
    step_run_id = _make_step_run(
        db, team, user_id,
        config={"cmd": ["npm", "install"], "cwd": "/workspace", "env": {"NODE_ENV": "test"}},
    )
    fake = _FakeClient(_FakeResponse(200, {"stdout": "ok", "exit_code": 0, "duration_ms": 10}))
    _patch_httpx(monkeypatch, fake)

    run_shell_step(db, step_run_id)

    assert fake.captured["json"]["cwd"] == "/workspace"
    assert fake.captured["json"]["env"]["NODE_ENV"] == "test"


# ---------------------------------------------------------------------------
# cli_nonzero
# ---------------------------------------------------------------------------


def test_run_shell_step_cli_nonzero(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    team = _make_team(db)
    user_id = _make_user(db)
    step_run_id = _make_step_run(db, team, user_id)

    fake = _FakeClient(_FakeResponse(200, {"stdout": "error output", "exit_code": 2, "duration_ms": 5}))
    _patch_httpx(monkeypatch, fake)

    run_shell_step(db, step_run_id)

    db.expire_all()
    row = db.get(StepRun, step_run_id)
    assert row is not None
    assert row.status == "failed"
    assert row.error_class == "cli_nonzero"
    assert row.exit_code == 2
    assert "error output" in row.stdout


# ---------------------------------------------------------------------------
# target_container=team_mirror short-circuits
# ---------------------------------------------------------------------------


def test_run_shell_step_team_mirror_unsupported(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    team = _make_team(db)
    user_id = _make_user(db)
    step_run_id = _make_step_run(db, team, user_id, target_container="team_mirror")

    fake = _FakeClient(_FakeResponse(200, {"stdout": "", "exit_code": 0}))
    _patch_httpx(monkeypatch, fake)

    run_shell_step(db, step_run_id)

    db.expire_all()
    row = db.get(StepRun, step_run_id)
    assert row is not None
    assert row.status == "failed"
    assert row.error_class == "unsupported_action_for_target"
    # Orchestrator must NOT have been called.
    assert fake.captured == {}


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_run_shell_step_missing_cmd(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    team = _make_team(db)
    user_id = _make_user(db)
    step_run_id = _make_step_run(db, team, user_id, config={})

    fake = _FakeClient(_FakeResponse(200, {}))
    _patch_httpx(monkeypatch, fake)

    run_shell_step(db, step_run_id)

    db.expire_all()
    row = db.get(StepRun, step_run_id)
    assert row is not None
    assert row.status == "failed"
    assert row.error_class == "orchestrator_exec_failed"
    assert fake.captured == {}


def test_run_shell_step_http_error(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx
    team = _make_team(db)
    user_id = _make_user(db)
    step_run_id = _make_step_run(db, team, user_id)

    fake = _FakeClient(httpx.ConnectError("refused"))
    _patch_httpx(monkeypatch, fake)
    monkeypatch.setattr("app.workflows.executors._retry.time.sleep", lambda _: None)

    run_shell_step(db, step_run_id)

    db.expire_all()
    row = db.get(StepRun, step_run_id)
    assert row is not None
    assert row.status == "failed"
    assert row.error_class in ("orchestrator_exec_failed", "orchestrator_exec_failed_after_retries")


def test_run_shell_step_non_200_status(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    team = _make_team(db)
    user_id = _make_user(db)
    step_run_id = _make_step_run(db, team, user_id)

    fake = _FakeClient(_FakeResponse(503))
    _patch_httpx(monkeypatch, fake)
    monkeypatch.setattr("app.workflows.executors._retry.time.sleep", lambda _: None)

    run_shell_step(db, step_run_id)

    db.expire_all()
    row = db.get(StepRun, step_run_id)
    assert row is not None
    assert row.status == "failed"
    assert row.error_class in ("orchestrator_exec_failed", "orchestrator_exec_failed_after_retries")
