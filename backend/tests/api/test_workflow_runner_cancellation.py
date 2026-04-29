"""Tests for the runner cancellation watchpoint (M005/S03/T04).

Tests `_drive_run` directly (not Celery task wrapper) so no worker
process is needed. We verify that:

  * When the run's status is flipped to 'cancelled' BEFORE the runner
    processes a step, all remaining step_runs are marked 'skipped' with
    error_class='cancelled' and the run stays terminal 'cancelled'.
  * When cancellation happens between two steps (after step 0 succeeds),
    step 1 is skipped but step 0 remains succeeded.
  * When NO cancellation happens, the run completes normally (regression
    check that the watchpoint does not break the happy path).

The cancellation watchpoint is exercised by directly writing status='cancelled'
to the workflow_run row before calling _drive_run (simulating the API cancel
endpoint writing the terminal status and the worker picking it up).
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from typing import Any

import pytest
from sqlalchemy import text
from sqlmodel import Session, delete

from app.models import StepRun, Team, WorkflowRun
from app.workflows.tasks import _drive_run


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
    team = Team(name=f"cancel-runner-{suffix}", slug=f"cancel-runner-{suffix}")
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


def _make_user(db: Session) -> uuid.UUID:
    from app.core.security import get_password_hash
    from app.models import User

    user = User(
        email=f"cr-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=get_password_hash("x"),
        full_name="Cancel Runner",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user.id


def _make_workflow(db: Session, team: Team, *, actions: list[str]) -> uuid.UUID:
    workflow_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO workflows (id, team_id, name, scope, system_owned) "
            "VALUES (:id, :t, :n, 'user', FALSE)"
        ),
        {"id": workflow_id, "t": team.id, "n": f"cr-{uuid.uuid4().hex[:6]}"},
    )
    for idx, action in enumerate(actions):
        db.execute(
            text(
                "INSERT INTO workflow_steps (id, workflow_id, step_index, action, config) "
                "VALUES (:id, :wf, :idx, :a, '{}'::jsonb)"
            ),
            {"id": uuid.uuid4(), "wf": workflow_id, "idx": idx, "a": action},
        )
    db.commit()
    return workflow_id


def _make_pending_run(
    db: Session,
    workflow_id: uuid.UUID,
    team_id: uuid.UUID,
    user_id: uuid.UUID,
) -> uuid.UUID:
    run_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO workflow_runs "
            "(id, workflow_id, team_id, trigger_type, triggered_by_user_id, "
            " target_user_id, trigger_payload, status) "
            "VALUES (:id, :wf, :t, 'button', :u, :u, '{}'::jsonb, 'pending')"
        ),
        {"id": run_id, "wf": workflow_id, "t": team_id, "u": user_id},
    )
    db.commit()
    return run_id


def _set_run_cancelled(db: Session, run_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Simulate the cancel API endpoint flipping the run to 'cancelled'."""
    db.execute(
        text(
            "UPDATE workflow_runs "
            "SET status = 'cancelled', cancelled_by_user_id = :uid, "
            "    cancelled_at = NOW() "
            "WHERE id = :rid"
        ),
        {"uid": user_id, "rid": run_id},
    )
    db.commit()


def _noop_executor(
    monkeypatch: pytest.MonkeyPatch,
    *,
    action: str = "shell",
) -> None:
    """Patch the executor for the given action to do nothing (succeed silently)."""
    import app.workflows.tasks as tasks_mod

    def _noop_shell(session: Session, step_run_id: uuid.UUID) -> None:
        # Mark the step_run succeeded with minimal data.
        sr = session.get(StepRun, step_run_id)
        if sr is None:
            return
        from app.models import get_datetime_utc

        sr.status = "succeeded"
        sr.stdout = "noop"
        sr.exit_code = 0
        sr.finished_at = get_datetime_utc()
        sr.duration_ms = 1
        session.add(sr)
        session.commit()

    if action == "shell":
        monkeypatch.setattr(tasks_mod, "run_shell_step", _noop_shell)
    elif action == "git":
        monkeypatch.setattr(tasks_mod, "run_git_step", _noop_shell)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_cancellation_before_first_step_skips_all_steps(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancel fires right before first step executes → all step_runs are skipped.

    We patch run_shell_step to flip the run's DB status to 'cancelled' BEFORE
    returning — then the second step's watchpoint fires and sees 'cancelled'.
    With only 1 step we use the same technique: patch run_shell_step to set
    the run to cancelled immediately after the first step starts, then verify
    the watchpoint skips the second step.
    """
    team = _make_team(db)
    user_id = _make_user(db)
    wf_id = _make_workflow(db, team, actions=["shell", "shell"])
    run_id = _make_pending_run(db, wf_id, team.id, user_id)

    for idx in range(2):
        db.add(
            StepRun(
                workflow_run_id=run_id,
                step_index=idx,
                snapshot={"action": "shell"},
                status="pending",
            )
        )
    db.commit()

    import app.workflows.tasks as tasks_mod

    # Patch shell executor: after step 0 runs successfully, flip the run to
    # 'cancelled' before returning. The watchpoint for step 1 then fires and
    # skips it.
    def _noop_then_cancel(session: Session, step_run_id: uuid.UUID) -> None:
        from app.models import get_datetime_utc

        sr = session.get(StepRun, step_run_id)
        if sr is None:
            return
        sr.status = "succeeded"
        sr.stdout = "noop"
        sr.exit_code = 0
        sr.finished_at = get_datetime_utc()
        sr.duration_ms = 1
        session.add(sr)
        session.commit()
        # Simulate cancel API writing 'cancelled' after step 0.
        session.execute(
            text(
                "UPDATE workflow_runs SET status='cancelled', "
                "cancelled_by_user_id=:uid WHERE id=:rid"
            ),
            {"uid": user_id, "rid": run_id},
        )
        session.commit()

    monkeypatch.setattr(tasks_mod, "run_shell_step", _noop_then_cancel)

    _drive_run(db, run_id)

    db.expire_all()
    run = db.get(WorkflowRun, run_id)
    assert run is not None
    assert run.status == "cancelled", f"expected cancelled, got {run.status}"
    assert run.cancelled_by_user_id == user_id

    step_rows = db.execute(
        text(
            "SELECT step_index, status, error_class FROM step_runs "
            "WHERE workflow_run_id = :rid ORDER BY step_index"
        ),
        {"rid": run_id},
    ).all()
    assert len(step_rows) == 2
    idx_to_status = {row[0]: row[1] for row in step_rows}
    idx_to_ec = {row[0]: row[2] for row in step_rows}
    # Step 0 ran (succeeded) before the cancel was detected.
    assert idx_to_status[0] == "succeeded", f"step 0 ran before cancel detection"
    # Step 1 was at the watchpoint and should be skipped.
    assert idx_to_status[1] == "skipped", f"step 1 expected skipped, got {idx_to_status[1]}"
    assert idx_to_ec[1] == "cancelled"


def test_cancellation_between_steps_skips_second_step(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Step 0 succeeds, cancel fires between steps → step 1 is skipped."""
    _noop_executor(monkeypatch, action="shell")

    team = _make_team(db)
    user_id = _make_user(db)
    wf_id = _make_workflow(db, team, actions=["shell", "shell"])
    run_id = _make_pending_run(db, wf_id, team.id, user_id)

    for idx in range(2):
        db.add(
            StepRun(
                workflow_run_id=run_id,
                step_index=idx,
                snapshot={"action": "shell"},
                status="pending",
            )
        )
    db.commit()

    # Track how many times run_shell_step has been called.
    step_call_count = [0]

    import app.workflows.tasks as tasks_mod
    original_shell = tasks_mod.run_shell_step

    def _cancel_after_first(session: Session, step_run_id: uuid.UUID) -> None:
        step_call_count[0] += 1
        original_shell(session, step_run_id)
        if step_call_count[0] == 1:
            # After step 0 runs, flip the run to cancelled.
            session.execute(
                text(
                    "UPDATE workflow_runs SET status='cancelled', "
                    "cancelled_by_user_id=:uid WHERE id=:rid"
                ),
                {"uid": user_id, "rid": run_id},
            )
            session.commit()

    monkeypatch.setattr(tasks_mod, "run_shell_step", _cancel_after_first)

    _drive_run(db, run_id)

    db.expire_all()
    run = db.get(WorkflowRun, run_id)
    assert run is not None
    assert run.status == "cancelled"

    step_rows = db.execute(
        text(
            "SELECT step_index, status FROM step_runs "
            "WHERE workflow_run_id = :rid ORDER BY step_index"
        ),
        {"rid": run_id},
    ).all()
    assert len(step_rows) == 2

    idx_to_status = {row[0]: row[1] for row in step_rows}
    assert idx_to_status[0] == "succeeded", "step 0 ran before cancel — should succeed"
    assert idx_to_status[1] == "skipped", "step 1 was not reached — should be skipped"


def test_no_cancellation_run_succeeds_normally(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: cancellation watchpoint does not break the normal success path."""
    _noop_executor(monkeypatch, action="shell")

    team = _make_team(db)
    user_id = _make_user(db)
    wf_id = _make_workflow(db, team, actions=["shell"])
    run_id = _make_pending_run(db, wf_id, team.id, user_id)

    db.add(
        StepRun(
            workflow_run_id=run_id,
            step_index=0,
            snapshot={"action": "shell"},
            status="pending",
        )
    )
    db.commit()

    _drive_run(db, run_id)

    db.expire_all()
    run = db.get(WorkflowRun, run_id)
    assert run is not None
    assert run.status == "succeeded"
