"""Unit tests for `app.workflows.executors.git.run_git_step`.

Covers:
  * Happy path — git checkout, pull, fetch, push all route correctly.
  * Rendered cmd is [git, subcommand, *args].
  * team_mirror → unsupported_action_for_target (S04).
  * Invalid subcommand → orchestrator_exec_failed.
  * Orchestrator HTTP error → orchestrator_exec_failed.
  * cli_nonzero exit code → step failed.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import text
from sqlmodel import Session

from app.models import StepRun, Team
from app.workflows.executors import git as git_executor
from app.workflows.executors.git import run_git_step

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
    team = Team(name=f"git-test-{suffix}", slug=f"git-test-{suffix}")
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


def _make_user(db: Session) -> uuid.UUID:
    from app.core.security import get_password_hash
    from app.models import User
    user = User(
        email=f"git-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=get_password_hash("x"),
        full_name="Git Test",
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
    workflow_id = uuid.uuid4()
    run_id = uuid.uuid4()
    step_run_id = uuid.uuid4()
    cfg = {"subcommand": "checkout", "args": ["main"]} if config is None else config
    snapshot = json.dumps({
        "id": str(uuid.uuid4()),
        "workflow_id": str(workflow_id),
        "step_index": 0,
        "action": "git",
        "config": cfg,
        "target_container": target_container,
    })
    db.execute(
        text("INSERT INTO workflows (id, team_id, name, scope, system_owned) VALUES (:id, :t, :n, 'user', FALSE)"),
        {"id": workflow_id, "t": team.id, "n": f"git-{uuid.uuid4().hex[:6]}"},
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
    monkeypatch.setattr(git_executor.httpx, "Client", lambda *a, **k: fake)


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("subcommand,extra_args", [
    ("checkout", ["main"]),
    ("pull", []),
    ("fetch", ["origin"]),
    ("push", ["origin", "HEAD"]),
])
def test_run_git_step_happy_path(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    subcommand: str,
    extra_args: list,
) -> None:
    team = _make_team(db)
    user_id = _make_user(db)
    step_run_id = _make_step_run(
        db, team, user_id,
        config={"subcommand": subcommand, "args": extra_args},
    )
    fake = _FakeClient(_FakeResponse(200, {"stdout": "Done", "exit_code": 0, "duration_ms": 3}))
    _patch_httpx(monkeypatch, fake)

    run_git_step(db, step_run_id)

    db.expire_all()
    row = db.get(StepRun, step_run_id)
    assert row is not None
    assert row.status == "succeeded"
    assert row.exit_code == 0

    # Cmd rendered as [git, subcommand, *extra_args].
    expected_cmd = ["git", subcommand] + extra_args
    assert fake.captured["json"]["cmd"] == expected_cmd
    assert fake.captured["json"]["action"] == "git"


# ---------------------------------------------------------------------------
# team_mirror short-circuit
# ---------------------------------------------------------------------------


def test_run_git_step_team_mirror_unsupported(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    team = _make_team(db)
    user_id = _make_user(db)
    step_run_id = _make_step_run(db, team, user_id, target_container="team_mirror")

    fake = _FakeClient(_FakeResponse(200, {}))
    _patch_httpx(monkeypatch, fake)

    run_git_step(db, step_run_id)

    db.expire_all()
    row = db.get(StepRun, step_run_id)
    assert row is not None
    assert row.status == "failed"
    assert row.error_class == "unsupported_action_for_target"
    assert fake.captured == {}


# ---------------------------------------------------------------------------
# Invalid subcommand
# ---------------------------------------------------------------------------


def test_run_git_step_invalid_subcommand(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    team = _make_team(db)
    user_id = _make_user(db)
    step_run_id = _make_step_run(db, team, user_id, config={"subcommand": "rm", "args": ["-rf", "/"]})

    fake = _FakeClient(_FakeResponse(200, {}))
    _patch_httpx(monkeypatch, fake)

    run_git_step(db, step_run_id)

    db.expire_all()
    row = db.get(StepRun, step_run_id)
    assert row is not None
    assert row.status == "failed"
    assert row.error_class == "orchestrator_exec_failed"
    assert fake.captured == {}


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_run_git_step_http_error(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx
    team = _make_team(db)
    user_id = _make_user(db)
    step_run_id = _make_step_run(db, team, user_id)

    fake = _FakeClient(httpx.ConnectError("refused"))
    _patch_httpx(monkeypatch, fake)
    monkeypatch.setattr("app.workflows.executors._retry.time.sleep", lambda _: None)

    run_git_step(db, step_run_id)

    db.expire_all()
    row = db.get(StepRun, step_run_id)
    assert row is not None
    assert row.status == "failed"
    assert row.error_class in ("orchestrator_exec_failed", "orchestrator_exec_failed_after_retries")


def test_run_git_step_cli_nonzero(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    team = _make_team(db)
    user_id = _make_user(db)
    step_run_id = _make_step_run(db, team, user_id)

    fake = _FakeClient(_FakeResponse(200, {"stdout": "error: branch not found", "exit_code": 1, "duration_ms": 2}))
    _patch_httpx(monkeypatch, fake)

    run_git_step(db, step_run_id)

    db.expire_all()
    row = db.get(StepRun, step_run_id)
    assert row is not None
    assert row.status == "failed"
    assert row.error_class == "cli_nonzero"
    assert row.exit_code == 1
