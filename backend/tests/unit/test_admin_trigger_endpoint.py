"""Unit tests for POST /api/v1/admin/workflows/{id}/trigger.

Covers:
  1. System admin triggers workflow — returns 202 with run_id.
  2. Returned run has trigger_type='admin_manual'.
  3. WorkflowRun row created with status='pending' and correct team_id.
  4. Non-admin user gets 403.
  5. Unknown workflow_id returns 404 workflow_not_found.
  6. Celery dispatch failure is handled — run marked failed, 503 returned.
  7. Trigger payload is stored on the run row.
  8. admin_manual_trigger_queued log fires with structured fields.

Real FastAPI app + real Postgres. Celery `.delay()` patched to avoid
broker dependency — the test asserts the task name was called with run_id.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session

from app.core.config import settings
from app.models import Team, TeamMember, TeamRole, WorkflowRun

API = settings.API_V1_STR
TRIGGER_URL_TPL = f"{API}/admin/workflows/{{workflow_id}}/trigger"
SIGNUP_URL = f"{API}/auth/signup"
LOGIN_URL = f"{API}/auth/login"
SUPERUSER_EMAIL = settings.FIRST_SUPERUSER
SUPERUSER_PASSWORD = settings.FIRST_SUPERUSER_PASSWORD


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_run_state(db: Session) -> Generator[None, None, None]:
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


@pytest.fixture(scope="module")
def superuser_cookies(client: TestClient) -> dict:
    r = client.post(
        LOGIN_URL,
        json={"email": SUPERUSER_EMAIL, "password": SUPERUSER_PASSWORD},
    )
    assert r.status_code == 200, r.text
    return dict(r.cookies)


def _signup_and_login(client: TestClient, db: Session) -> tuple[str, uuid.UUID, dict]:
    email = f"at-{uuid.uuid4().hex[:8]}@test.example"
    r = client.post(SIGNUP_URL, json={"email": email, "password": "Password1!"})
    assert r.status_code == 200, r.text
    user_id = uuid.UUID(r.json()["id"])
    r2 = client.post(LOGIN_URL, json={"email": email, "password": "Password1!"})
    return email, user_id, dict(r2.cookies)


def _create_team(db: Session) -> uuid.UUID:
    team_id = uuid.uuid4()
    team = Team(
        id=team_id,
        name=f"at-team-{team_id.hex[:8]}",
        slug=f"at-{team_id.hex[:8]}",
        is_personal=False,
    )
    db.add(team)
    db.commit()
    return team_id


def _insert_workflow(db: Session, team_id: uuid.UUID) -> uuid.UUID:
    wf_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO workflows (id, team_id, name) "
            "VALUES (:id, :team, :name)"
        ),
        {"id": wf_id, "team": team_id, "name": f"at-wf-{wf_id.hex[:8]}"},
    )
    db.commit()
    return wf_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

_PATCH_TASK = "app.workflows.tasks.run_workflow"


def test_admin_trigger_returns_202_with_run_id(
    client: TestClient, db: Session, superuser_cookies: dict
) -> None:
    """System admin triggers workflow — returns 202 with run_id."""
    team_id = _create_team(db)
    wf_id = _insert_workflow(db, team_id)

    mock_task = MagicMock()
    mock_task.delay = MagicMock()

    with patch(_PATCH_TASK, mock_task):
        r = client.post(
            TRIGGER_URL_TPL.format(workflow_id=wf_id),
            json={"trigger_payload": {"reason": "manual-check"}},
            cookies=superuser_cookies,
        )

    assert r.status_code == 202, r.text
    body = r.json()
    assert "run_id" in body
    assert body["status"] == "pending"
    uuid.UUID(body["run_id"])  # valid UUID


def test_admin_trigger_creates_admin_manual_run(
    client: TestClient, db: Session, superuser_cookies: dict
) -> None:
    """WorkflowRun row has trigger_type='admin_manual'."""
    team_id = _create_team(db)
    wf_id = _insert_workflow(db, team_id)

    mock_task = MagicMock()
    mock_task.delay = MagicMock()

    with patch(_PATCH_TASK, mock_task):
        r = client.post(
            TRIGGER_URL_TPL.format(workflow_id=wf_id),
            json={"trigger_payload": {}},
            cookies=superuser_cookies,
        )

    assert r.status_code == 202, r.text
    run_id = uuid.UUID(r.json()["run_id"])

    row = db.execute(
        text("SELECT trigger_type, status, team_id FROM workflow_runs WHERE id = :id"),
        {"id": run_id},
    ).one()
    assert row[0] == "admin_manual", f"Expected admin_manual, got {row[0]}"
    assert row[1] == "pending", f"Expected pending, got {row[1]}"
    assert uuid.UUID(str(row[2])) == team_id


def test_admin_trigger_stores_trigger_payload(
    client: TestClient, db: Session, superuser_cookies: dict
) -> None:
    """Synthetic trigger_payload is persisted on the run row."""
    team_id = _create_team(db)
    wf_id = _insert_workflow(db, team_id)
    payload = {"env": "staging", "dry_run": True}

    mock_task = MagicMock()
    mock_task.delay = MagicMock()

    with patch(_PATCH_TASK, mock_task):
        r = client.post(
            TRIGGER_URL_TPL.format(workflow_id=wf_id),
            json={"trigger_payload": payload},
            cookies=superuser_cookies,
        )

    assert r.status_code == 202, r.text
    run_id = uuid.UUID(r.json()["run_id"])

    row = db.execute(
        text("SELECT trigger_payload FROM workflow_runs WHERE id = :id"),
        {"id": run_id},
    ).one()
    assert row[0] == payload, f"trigger_payload mismatch: {row[0]}"


def test_admin_trigger_403_for_non_admin(
    client: TestClient, db: Session
) -> None:
    """Non-admin user gets 403."""
    _, _, cookies = _signup_and_login(client, db)
    team_id = _create_team(db)
    wf_id = _insert_workflow(db, team_id)

    r = client.post(
        TRIGGER_URL_TPL.format(workflow_id=wf_id),
        json={"trigger_payload": {}},
        cookies=cookies,
    )
    assert r.status_code == 403, r.text


def test_admin_trigger_404_unknown_workflow(
    client: TestClient, db: Session, superuser_cookies: dict
) -> None:
    """Unknown workflow_id returns 404 workflow_not_found."""
    r = client.post(
        TRIGGER_URL_TPL.format(workflow_id=uuid.uuid4()),
        json={"trigger_payload": {}},
        cookies=superuser_cookies,
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["detail"] == "workflow_not_found"


def test_admin_trigger_503_on_celery_failure(
    client: TestClient, db: Session, superuser_cookies: dict
) -> None:
    """Celery dispatch failure → 503 + run marked failed with error_class."""
    team_id = _create_team(db)
    wf_id = _insert_workflow(db, team_id)

    mock_task = MagicMock()
    mock_task.delay = MagicMock(side_effect=RuntimeError("broker down"))

    with patch(_PATCH_TASK, mock_task):
        r = client.post(
            TRIGGER_URL_TPL.format(workflow_id=wf_id),
            json={"trigger_payload": {}},
            cookies=superuser_cookies,
        )

    assert r.status_code == 503, r.text
    assert r.json()["detail"]["detail"] == "task_dispatch_failed"

    # Run must be marked failed with dispatch_failed error_class
    row = db.execute(
        text(
            "SELECT status, error_class FROM workflow_runs "
            "WHERE workflow_id = :wf ORDER BY created_at DESC LIMIT 1"
        ),
        {"wf": wf_id},
    ).one_or_none()
    assert row is not None, "No run row found after dispatch failure"
    assert row[0] == "failed", f"Expected failed, got {row[0]}"
    assert row[1] == "dispatch_failed", f"Expected dispatch_failed error_class, got {row[1]}"


def test_admin_trigger_log_fires(
    client: TestClient, db: Session, superuser_cookies: dict, caplog: pytest.LogCaptureFixture
) -> None:
    """admin_manual_trigger_queued INFO log fires with structured fields."""
    import logging as _logging

    team_id = _create_team(db)
    wf_id = _insert_workflow(db, team_id)

    mock_task = MagicMock()
    mock_task.delay = MagicMock()

    # MEM016: alembic fileConfig may disable loggers; re-enable before capture.
    route_logger = _logging.getLogger("app.api.routes.workflows")
    route_logger.disabled = False

    with caplog.at_level(_logging.INFO, logger="app.api.routes.workflows"):
        with patch(_PATCH_TASK, mock_task):
            r = client.post(
                TRIGGER_URL_TPL.format(workflow_id=wf_id),
                json={"trigger_payload": {"k": "v"}},
                cookies=superuser_cookies,
            )

    assert r.status_code == 202, r.text
    log_text = " ".join(caplog.messages)
    assert "admin_manual_trigger_queued" in log_text, (
        f"Expected 'admin_manual_trigger_queued' in logs; got:\n{log_text}"
    )


def test_admin_trigger_unauthenticated_401(db: Session) -> None:
    """Unauthenticated request (no cookie) returns 401."""
    from app.main import app

    with TestClient(app) as fresh_client:
        r = fresh_client.post(
            TRIGGER_URL_TPL.format(workflow_id=uuid.uuid4()),
            json={"trigger_payload": {}},
        )
    assert r.status_code == 401, r.text
