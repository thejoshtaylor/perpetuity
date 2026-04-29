"""Unit tests for `app.workflows.tasks._drive_run` (M005/S02/T03).

The runner is the engine spine: it owns workflow_runs.status transitions
and step iteration, delegating each step to its executor. We test
`_drive_run` directly (not the Celery task wrapper) so we don't need a
worker process — the wrapper just opens a Session and re-raises, which is
covered by the runtime contract rather than unit-tested here.

Coverage:
  * Happy path — pending → running → succeeded with one ai step.
  * Idempotency guard — running run is left alone (re-delivery safe).
  * Step failure propagates: error_class flows from step_run to
    workflow_run, run is marked failed, finished_at + duration_ms set.
  * Empty workflow (zero steps) succeeds.
  * Unknown action (`shell` in S02) → step failed with
    error_class='unsupported_action', run failed with same.
  * Unknown run_id → log + return, no crash.
  * Snapshot freezes the WorkflowStep config at dispatch time (R018).

The orchestrator HTTP boundary is again replaced with a `_FakeClient` so
the AI executor's run-time path is exercised end-to-end without a real
docker daemon.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import text
from sqlmodel import Session, delete

from app.api.team_secrets import set_team_secret
from app.api.team_secrets_registry import CLAUDE_API_KEY
from app.models import StepRun, Team, TeamSecret, WorkflowRun
from app.workflows import tasks as runner
from app.workflows.executors import ai as ai_executor
from app.workflows.tasks import _drive_run

_TEST_FERNET_KEY = "q14YMz9s4jrbfD29GvcRfe_4krg82w6_mPWUu_y3LTo="
_VALID_CLAUDE_KEY = "sk-ant-" + ("A" * 40)


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
    db.execute(delete(TeamSecret))
    db.commit()
    yield
    db.execute(text("DELETE FROM step_runs"))
    db.execute(text("DELETE FROM workflow_runs"))
    db.execute(text("DELETE FROM workflow_steps"))
    db.execute(text("DELETE FROM workflows"))
    db.execute(delete(TeamSecret))
    db.commit()


def _make_team(db: Session) -> Team:
    suffix = uuid.uuid4().hex[:8]
    team = Team(
        name=f"runner-test-{suffix}",
        slug=f"runner-test-{suffix}",
    )
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


def _make_user(db: Session) -> uuid.UUID:
    from app.core.security import get_password_hash
    from app.models import User

    user = User(
        email=f"runner-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=get_password_hash("not-checked-here"),
        full_name="Runner Test",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user.id


def _make_workflow(
    db: Session,
    team: Team,
    *,
    actions: list[str],
    config: dict[str, Any] | None = None,
) -> uuid.UUID:
    """Insert a workflow with N steps. Returns workflow id.

    actions[i] sets workflow_steps.action for step_index=i. config (if
    given) is stored on every step.
    """
    workflow_id = uuid.uuid4()
    cfg_json = '{"prompt_template":"{prompt}"}' if config is None else None
    db.execute(
        text(
            """
            INSERT INTO workflows (id, team_id, name, scope, system_owned)
            VALUES (:id, :t, :n, 'user', FALSE)
            """
        ),
        {"id": workflow_id, "t": team.id, "n": f"runner-{uuid.uuid4().hex[:6]}"},
    )
    for idx, action in enumerate(actions):
        if config is not None:
            import json as _json

            cfg_payload = _json.dumps(config)
        else:
            cfg_payload = cfg_json
        db.execute(
            text(
                """
                INSERT INTO workflow_steps (id, workflow_id, step_index, action, config)
                VALUES (:id, :wf, :idx, :a, CAST(:cfg AS JSONB))
                """
            ),
            {
                "id": uuid.uuid4(),
                "wf": workflow_id,
                "idx": idx,
                "a": action,
                "cfg": cfg_payload,
            },
        )
    db.commit()
    return workflow_id


def _make_pending_run(
    db: Session,
    workflow_id: uuid.UUID,
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    prompt: str = "do the thing",
) -> uuid.UUID:
    run_id = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO workflow_runs
                (id, workflow_id, team_id, trigger_type,
                 triggered_by_user_id, target_user_id,
                 trigger_payload, status)
            VALUES
                (:id, :wf, :t, 'button', :u, :u,
                 CAST(:p AS JSONB), 'pending')
            """
        ),
        {
            "id": run_id,
            "wf": workflow_id,
            "t": team_id,
            "u": user_id,
            "p": '{"prompt": "' + prompt + '"}',
        },
    )
    db.commit()
    return run_id


class _FakeResponse:
    def __init__(self, status_code: int, body: dict[str, Any] | None = None):
        self.status_code = status_code
        self._body = body or {}

    def json(self) -> dict[str, Any]:
        return self._body


class _FakeClient:
    def __init__(self, response: _FakeResponse | Exception):
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def post(
        self,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _FakeResponse:
        self.calls.append({"url": url, "json": json, "headers": headers})
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _patch_orchestrator(
    monkeypatch: pytest.MonkeyPatch,
    response: _FakeResponse | Exception,
) -> _FakeClient:
    fake = _FakeClient(response)
    monkeypatch.setattr(ai_executor.httpx, "Client", lambda *a, **k: fake)
    return fake


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_drive_run_pending_to_succeeded(
    db: Session, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Pending run with one claude step → status flips to running, then
    succeeded; started_at, finished_at, duration_ms all populated;
    one step_run row written with status=succeeded."""
    team = _make_team(db)
    user_id = _make_user(db)
    set_team_secret(db, team.id, CLAUDE_API_KEY, _VALID_CLAUDE_KEY)
    workflow_id = _make_workflow(db, team, actions=["claude"])
    run_id = _make_pending_run(db, workflow_id, team.id, user_id)

    _patch_orchestrator(
        monkeypatch,
        _FakeResponse(200, {"stdout": "hello", "exit_code": 0, "duration_ms": 5}),
    )

    with caplog.at_level(logging.INFO, logger="app.workflows.tasks"):
        _drive_run(db, run_id)

    db.expire_all()
    run = db.get(WorkflowRun, run_id)
    assert run is not None
    assert run.status == "succeeded"
    assert run.error_class is None
    assert run.started_at is not None
    assert run.finished_at is not None
    assert run.duration_ms is not None
    assert run.last_heartbeat_at is not None

    step_rows = list(
        db.exec(
            text("SELECT status, exit_code FROM step_runs WHERE workflow_run_id = :r ORDER BY step_index").bindparams(r=run_id)
        ).all()
    )
    assert len(step_rows) == 1
    assert step_rows[0][0] == "succeeded"
    assert step_rows[0][1] == 0

    log_text = " ".join(r.getMessage() for r in caplog.records)
    assert "workflow_run_started" in log_text
    assert "workflow_run_succeeded" in log_text


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_drive_run_skips_non_pending_run(
    db: Session, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Run already in 'running' status → log skipped + return; no step_runs
    inserted, no orchestrator call."""
    team = _make_team(db)
    user_id = _make_user(db)
    set_team_secret(db, team.id, CLAUDE_API_KEY, _VALID_CLAUDE_KEY)
    workflow_id = _make_workflow(db, team, actions=["claude"])
    run_id = _make_pending_run(db, workflow_id, team.id, user_id)
    db.execute(
        text("UPDATE workflow_runs SET status = 'running' WHERE id = :r"),
        {"r": run_id},
    )
    db.commit()

    fake = _patch_orchestrator(
        monkeypatch,
        _FakeResponse(200, {"stdout": "", "exit_code": 0, "duration_ms": 0}),
    )

    with caplog.at_level(logging.INFO, logger="app.workflows.tasks"):
        _drive_run(db, run_id)

    db.expire_all()
    run = db.get(WorkflowRun, run_id)
    assert run is not None
    # Status NOT touched.
    assert run.status == "running"

    step_count = db.execute(
        text("SELECT COUNT(*) FROM step_runs WHERE workflow_run_id = :r"),
        {"r": run_id},
    ).scalar_one()
    assert step_count == 0

    # Orchestrator never called.
    assert fake.calls == []

    log_text = " ".join(r.getMessage() for r in caplog.records)
    assert "workflow_run_skipped_not_pending" in log_text


# ---------------------------------------------------------------------------
# Failure propagation
# ---------------------------------------------------------------------------


def test_drive_run_propagates_step_error_class_to_run(
    db: Session, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Step fails with cli_nonzero → run.status='failed',
    error_class='cli_nonzero', finished_at + duration_ms set."""
    team = _make_team(db)
    user_id = _make_user(db)
    set_team_secret(db, team.id, CLAUDE_API_KEY, _VALID_CLAUDE_KEY)
    workflow_id = _make_workflow(db, team, actions=["claude"])
    run_id = _make_pending_run(db, workflow_id, team.id, user_id)

    _patch_orchestrator(
        monkeypatch,
        _FakeResponse(
            200, {"stdout": "boom", "exit_code": 2, "duration_ms": 99}
        ),
    )

    with caplog.at_level(logging.INFO, logger="app.workflows.tasks"):
        _drive_run(db, run_id)

    db.expire_all()
    run = db.get(WorkflowRun, run_id)
    assert run is not None
    assert run.status == "failed"
    assert run.error_class == "cli_nonzero"
    assert run.finished_at is not None
    assert run.duration_ms is not None

    log_text = " ".join(r.getMessage() for r in caplog.records)
    assert "workflow_run_failed" in log_text
    assert "cli_nonzero" in log_text


def test_drive_run_missing_secret_propagates(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Step fails with missing_team_secret → run.error_class same."""
    team = _make_team(db)
    user_id = _make_user(db)
    # No team secret set.
    workflow_id = _make_workflow(db, team, actions=["claude"])
    run_id = _make_pending_run(db, workflow_id, team.id, user_id)

    fake = _patch_orchestrator(
        monkeypatch,
        _FakeResponse(200, {"stdout": "", "exit_code": 0, "duration_ms": 0}),
    )

    _drive_run(db, run_id)

    db.expire_all()
    run = db.get(WorkflowRun, run_id)
    assert run is not None
    assert run.status == "failed"
    assert run.error_class == "missing_team_secret"
    # Orchestrator never called.
    assert fake.calls == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_drive_run_empty_workflow_succeeds(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Workflow with zero steps — runner transitions pending → running →
    succeeded without writing any step_runs and without calling the
    orchestrator."""
    team = _make_team(db)
    user_id = _make_user(db)
    workflow_id = _make_workflow(db, team, actions=[])
    run_id = _make_pending_run(db, workflow_id, team.id, user_id)

    fake = _patch_orchestrator(
        monkeypatch,
        _FakeResponse(200, {"stdout": "", "exit_code": 0, "duration_ms": 0}),
    )

    _drive_run(db, run_id)

    db.expire_all()
    run = db.get(WorkflowRun, run_id)
    assert run is not None
    assert run.status == "succeeded"
    assert run.duration_ms is not None
    step_count = db.execute(
        text("SELECT COUNT(*) FROM step_runs WHERE workflow_run_id = :r"),
        {"r": run_id},
    ).scalar_one()
    assert step_count == 0
    assert fake.calls == []


def test_drive_run_unknown_action_fails_step(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`shell` action in S02 (no executor wired) → step_run failed with
    error_class='unsupported_action', run mirrors the same error_class."""
    team = _make_team(db)
    user_id = _make_user(db)
    workflow_id = _make_workflow(db, team, actions=["shell"])
    run_id = _make_pending_run(db, workflow_id, team.id, user_id)

    _drive_run(db, run_id)

    db.expire_all()
    run = db.get(WorkflowRun, run_id)
    assert run is not None
    assert run.status == "failed"
    assert run.error_class == "unsupported_action"

    step_row = db.execute(
        text(
            "SELECT status, error_class FROM step_runs "
            "WHERE workflow_run_id = :r"
        ),
        {"r": run_id},
    ).one()
    assert step_row[0] == "failed"
    assert step_row[1] == "unsupported_action"


def test_drive_run_unknown_run_id_returns_quietly(
    db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """`run_id` that doesn't exist in workflow_runs → log + return; no raise.

    This is the path the Celery task hits when the broker re-delivers a
    message after the run was hard-deleted (e.g. operator cleanup).
    """
    fake_run_id = uuid.uuid4()
    with caplog.at_level(logging.WARNING, logger="app.workflows.tasks"):
        _drive_run(db, fake_run_id)
    log_text = " ".join(r.getMessage() for r in caplog.records)
    assert "workflow_run_missing" in log_text


# ---------------------------------------------------------------------------
# Snapshot freezing (R018)
# ---------------------------------------------------------------------------


def test_drive_run_step_run_snapshot_freezes_step_config(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The step_runs.snapshot column must capture the WorkflowStep's full
    shape at dispatch time so a later edit to the step config doesn't
    rewrite history (R018).
    """
    team = _make_team(db)
    user_id = _make_user(db)
    set_team_secret(db, team.id, CLAUDE_API_KEY, _VALID_CLAUDE_KEY)
    workflow_id = _make_workflow(
        db,
        team,
        actions=["claude"],
        config={"prompt_template": "Original: {prompt}", "extra": "v1"},
    )
    run_id = _make_pending_run(db, workflow_id, team.id, user_id, prompt="x")

    _patch_orchestrator(
        monkeypatch,
        _FakeResponse(200, {"stdout": "ok", "exit_code": 0, "duration_ms": 1}),
    )

    _drive_run(db, run_id)

    snapshot_row = db.execute(
        text(
            "SELECT snapshot FROM step_runs WHERE workflow_run_id = :r"
        ),
        {"r": run_id},
    ).scalar_one()
    # JSONB returns dict in psycopg.
    assert snapshot_row["action"] == "claude"
    assert snapshot_row["step_index"] == 0
    assert snapshot_row["config"] == {
        "prompt_template": "Original: {prompt}",
        "extra": "v1",
    }


# ---------------------------------------------------------------------------
# Celery task wrapper
# ---------------------------------------------------------------------------


def test_run_workflow_task_is_registered_with_celery() -> None:
    """The `@celery_app.task` decorator must register `run_workflow` under
    the explicit name; the API layer (T04) enqueues by name string."""
    from app.core.celery_app import celery_app

    assert "app.workflows.run_workflow" in celery_app.tasks
    task = celery_app.tasks["app.workflows.run_workflow"]
    # Acks-late + reject-on-worker-lost set per the slice plan.
    assert task.acks_late is True
    assert task.reject_on_worker_lost is True


def test_run_workflow_task_handles_bad_run_id_uuid(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A non-UUID run_id (corrupt broker message) → log + return,
    NO raise. The Celery task is invoked directly here (no worker)."""
    with caplog.at_level(logging.WARNING, logger="app.workflows.tasks"):
        runner.run_workflow.run("not-a-uuid")
    log_text = " ".join(r.getMessage() for r in caplog.records)
    assert "workflow_run_bad_run_id" in log_text
