"""Integration tests for the workflow cancellation API (M005/S03/T04).

Covers:
    POST /api/v1/workflow_runs/{run_id}/cancel

Tests prove:
  * Happy path: pending run → status='cancelled', returns 202.
  * Happy path: running run → status='cancelled', returns 202.
  * cancelled_by_user_id + cancelled_at are stamped.
  * Already-terminal run → 409 workflow_run_not_cancellable.
  * Non-member → 403.
  * Non-existent run_id → 404.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session

from app import crud
from app.core.config import settings
from app.models import (
    Team,
    TeamMember,
    TeamRole,
    Workflow,
    WorkflowRun,
    WorkflowStep,
)
from tests.utils.utils import random_email, random_lower_string

API = settings.API_V1_STR
SIGNUP_URL = f"{API}/auth/signup"


@pytest.fixture(autouse=True)
def _clean_workflow_state(db: Session) -> Generator[None, None, None]:
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


def _signup(client: TestClient) -> tuple[uuid.UUID, dict]:
    email = random_email()
    password = random_lower_string()
    client.cookies.clear()
    r = client.post(SIGNUP_URL, json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    user_id = uuid.UUID(r.json()["id"])
    cookies = {c.name: c.value for c in client.cookies.jar}
    client.cookies.clear()
    return user_id, cookies


def _create_team_with_admin(db: Session, admin_id: uuid.UUID) -> Team:
    return crud.create_team_with_admin(
        session=db,
        name=f"cancel-test-{uuid.uuid4().hex[:8]}",
        creator_id=admin_id,
    )


def _make_workflow_with_step(db: Session, team_id: uuid.UUID) -> Workflow:
    wf = Workflow(
        team_id=team_id,
        name=f"cancel-wf-{uuid.uuid4().hex[:6]}",
        scope="user",
        system_owned=False,
    )
    db.add(wf)
    db.flush()
    db.add(
        WorkflowStep(
            workflow_id=wf.id,
            step_index=0,
            action="shell",
            config={"cmd": "echo hi"},
        )
    )
    db.commit()
    db.refresh(wf)
    return wf


def _make_run(
    db: Session,
    wf: Workflow,
    user_id: uuid.UUID,
    status: str = "pending",
) -> WorkflowRun:
    run = WorkflowRun(
        workflow_id=wf.id,
        team_id=wf.team_id,
        trigger_type="button",
        triggered_by_user_id=user_id,
        target_user_id=user_id,
        trigger_payload={},
        status=status,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_cancel_pending_run_returns_202(
    client: TestClient, db: Session
) -> None:
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)
    wf = _make_workflow_with_step(db, team.id)
    run = _make_run(db, wf, user_id, "pending")

    r = client.post(f"{API}/workflow_runs/{run.id}/cancel", cookies=cookies)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "cancelling"

    db.refresh(run)
    assert run.status == "cancelled"
    assert run.cancelled_by_user_id == user_id
    assert run.cancelled_at is not None


def test_cancel_running_run_returns_202(
    client: TestClient, db: Session
) -> None:
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)
    wf = _make_workflow_with_step(db, team.id)
    run = _make_run(db, wf, user_id, "running")

    r = client.post(f"{API}/workflow_runs/{run.id}/cancel", cookies=cookies)
    assert r.status_code == 202, r.text

    db.refresh(run)
    assert run.status == "cancelled"
    assert run.cancelled_by_user_id == user_id


# ---------------------------------------------------------------------------
# Negative: already-terminal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("terminal_status", ["succeeded", "failed", "cancelled"])
def test_cancel_terminal_run_returns_409(
    client: TestClient, db: Session, terminal_status: str
) -> None:
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)
    wf = _make_workflow_with_step(db, team.id)
    run = _make_run(db, wf, user_id, terminal_status)

    r = client.post(f"{API}/workflow_runs/{run.id}/cancel", cookies=cookies)
    assert r.status_code == 409
    body = r.json()
    assert body["detail"]["detail"] == "workflow_run_not_cancellable"
    assert body["detail"]["current_status"] == terminal_status


# ---------------------------------------------------------------------------
# Negative: access control
# ---------------------------------------------------------------------------


def test_cancel_non_member_returns_403(
    client: TestClient, db: Session
) -> None:
    owner_id, _ = _signup(client)
    team = _create_team_with_admin(db, owner_id)
    wf = _make_workflow_with_step(db, team.id)
    run = _make_run(db, wf, owner_id, "running")

    other_id, other_cookies = _signup(client)
    assert other_id != owner_id

    r = client.post(f"{API}/workflow_runs/{run.id}/cancel", cookies=other_cookies)
    assert r.status_code == 403
    assert r.json()["detail"]["detail"] == "not_team_member"


def test_cancel_unknown_run_returns_404(
    client: TestClient, db: Session
) -> None:
    _user_id, cookies = _signup(client)
    r = client.post(f"{API}/workflow_runs/{uuid.uuid4()}/cancel", cookies=cookies)
    assert r.status_code == 404
    assert r.json()["detail"]["detail"] == "workflow_run_not_found"


def test_cancel_unauthenticated_returns_401(client: TestClient) -> None:
    client.cookies.clear()
    r = client.post(f"{API}/workflow_runs/{uuid.uuid4()}/cancel")
    assert r.status_code == 401
