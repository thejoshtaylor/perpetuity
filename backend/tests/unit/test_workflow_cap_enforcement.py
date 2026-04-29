"""Unit tests for workflow operational cap enforcement (T02).

Covers:
  1. cap=None (both None) — dispatch proceeds without error.
  2. Concurrent cap hit — third run returns WorkflowCapExceededError.
  3. Hourly cap hit — second run in the same hour returns WorkflowCapExceededError.
  4. Both caps None — _check_workflow_caps is a no-op.
  5. Audit WorkflowRun row (status='rejected', error_class='cap_exceeded') written on hit.
  6. HTTP layer: 429 returned with correct JSON body on concurrent cap hit.
  7. HTTP layer: 429 returned with correct JSON body on hourly cap hit.
  8. Under-cap dispatches succeed (boundary: count < limit).

Real FastAPI app + real Postgres via session-scoped `db` and module-scoped
`client` from tests/conftest.py. Celery `.delay()` patched to avoid broker.
"""
from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, select

from app.core.config import settings
from app.models import Team, TeamMember, TeamRole, WorkflowRun, WorkflowRunStatus
from app.services.workflow_dispatch import WorkflowCapExceededError, _check_workflow_caps

API = settings.API_V1_STR
DISPATCH_URL_TPL = f"{API}/workflows/{{workflow_id}}/run"
SIGNUP_URL = f"{API}/auth/signup"
LOGIN_URL = f"{API}/auth/login"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _signup_and_login(client: TestClient) -> tuple[str, uuid.UUID, dict]:
    email = f"cap-{uuid.uuid4().hex[:8]}@test.example"
    r = client.post(SIGNUP_URL, json={"email": email, "password": "Password1!"})
    assert r.status_code == 200, r.text
    user_id = uuid.UUID(r.json()["id"])
    r2 = client.post(LOGIN_URL, json={"email": email, "password": "Password1!"})
    return email, user_id, dict(r2.cookies)


def _create_team(db: Session) -> uuid.UUID:
    team_id = uuid.uuid4()
    team = Team(
        id=team_id,
        name=f"cap-team-{team_id.hex[:6]}",
        slug=f"cap-{team_id.hex[:6]}",
        is_personal=False,
    )
    db.add(team)
    db.commit()
    return team_id


def _add_member(db: Session, team_id: uuid.UUID, user_id: uuid.UUID) -> None:
    member = TeamMember(team_id=team_id, user_id=user_id, role=TeamRole.member)
    db.add(member)
    db.commit()


def _create_workflow(
    db: Session,
    team_id: uuid.UUID,
    *,
    max_concurrent_runs: int | None = None,
    max_runs_per_hour: int | None = None,
) -> uuid.UUID:
    wf_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO workflows "
            "(id, team_id, name, scope, max_concurrent_runs, max_runs_per_hour) "
            "VALUES (:id, :tid, :name, 'user', :mcr, :mrph)"
        ),
        {
            "id": str(wf_id),
            "tid": str(team_id),
            "name": f"wf-{wf_id.hex[:6]}",
            "mcr": max_concurrent_runs,
            "mrph": max_runs_per_hour,
        },
    )
    db.commit()
    return wf_id


def _insert_run(
    db: Session,
    workflow_id: uuid.UUID,
    team_id: uuid.UUID,
    *,
    status: str = "running",
    created_at: datetime | None = None,
) -> uuid.UUID:
    run_id = uuid.uuid4()
    ts = created_at or datetime.now(timezone.utc)
    db.execute(
        text(
            "INSERT INTO workflow_runs "
            "(id, workflow_id, team_id, trigger_type, "
            "trigger_payload, status, created_at) "
            "VALUES (:id, :wid, :tid, 'button', '{}', :status, :ts)"
        ),
        {
            "id": str(run_id),
            "wid": str(workflow_id),
            "tid": str(team_id),
            "status": status,
            "ts": ts,
        },
    )
    db.commit()
    return run_id


def _make_workflow_ns(
    workflow_id: uuid.UUID,
    team_id: uuid.UUID,
    *,
    max_concurrent_runs: int | None = None,
    max_runs_per_hour: int | None = None,
) -> SimpleNamespace:
    """Lightweight stand-in for app.models.Workflow for unit-layer tests."""
    return SimpleNamespace(
        id=workflow_id,
        team_id=team_id,
        max_concurrent_runs=max_concurrent_runs,
        max_runs_per_hour=max_runs_per_hour,
    )


# ---------------------------------------------------------------------------
# Cleanup fixture — rolls back any aborted txn before deleting
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean(db: Session) -> Generator[None, None, None]:
    db.rollback()
    db.execute(text("DELETE FROM workflow_runs"))
    db.execute(text("DELETE FROM workflows"))
    db.execute(text("DELETE FROM team_member"))
    db.execute(text("DELETE FROM team"))
    db.commit()
    yield
    db.rollback()
    db.execute(text("DELETE FROM workflow_runs"))
    db.execute(text("DELETE FROM workflows"))
    db.execute(text("DELETE FROM team_member"))
    db.execute(text("DELETE FROM team"))
    db.commit()


# ---------------------------------------------------------------------------
# Unit tests for _check_workflow_caps (no HTTP layer)
# ---------------------------------------------------------------------------


class TestCheckWorkflowCapsUnit:
    def test_both_caps_none_is_noop(self, db: Session) -> None:
        team_id = _create_team(db)
        wf_id = _create_workflow(db, team_id)
        wf = _make_workflow_ns(wf_id, team_id)
        _insert_run(db, wf_id, team_id, status="running")
        _check_workflow_caps(db, wf)  # must not raise

    def test_concurrent_cap_not_hit_below_limit(self, db: Session) -> None:
        team_id = _create_team(db)
        wf_id = _create_workflow(db, team_id)
        wf = _make_workflow_ns(wf_id, team_id, max_concurrent_runs=2)
        _insert_run(db, wf_id, team_id, status="running")  # count=1, limit=2
        _check_workflow_caps(db, wf)  # must not raise

    def test_concurrent_cap_hit_at_limit(self, db: Session) -> None:
        team_id = _create_team(db)
        wf_id = _create_workflow(db, team_id)
        wf = _make_workflow_ns(wf_id, team_id, max_concurrent_runs=2)
        _insert_run(db, wf_id, team_id, status="running")
        _insert_run(db, wf_id, team_id, status="pending")  # count=2, limit=2
        with pytest.raises(WorkflowCapExceededError) as exc_info:
            _check_workflow_caps(db, wf)
        err = exc_info.value
        assert err.cap_type == "concurrent"
        assert err.current_count == 2
        assert err.limit == 2

    def test_hourly_cap_hit_at_limit(self, db: Session) -> None:
        team_id = _create_team(db)
        wf_id = _create_workflow(db, team_id)
        wf = _make_workflow_ns(wf_id, team_id, max_runs_per_hour=3)
        recent = datetime.now(timezone.utc) - timedelta(minutes=30)
        for _ in range(3):
            _insert_run(db, wf_id, team_id, status="succeeded", created_at=recent)
        with pytest.raises(WorkflowCapExceededError) as exc_info:
            _check_workflow_caps(db, wf)
        err = exc_info.value
        assert err.cap_type == "hourly"
        assert err.current_count == 3
        assert err.limit == 3

    def test_hourly_cap_ignores_old_runs(self, db: Session) -> None:
        team_id = _create_team(db)
        wf_id = _create_workflow(db, team_id)
        wf = _make_workflow_ns(wf_id, team_id, max_runs_per_hour=2)
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        for _ in range(5):
            _insert_run(db, wf_id, team_id, status="succeeded", created_at=old)
        _check_workflow_caps(db, wf)  # old runs outside 1h window, must not raise

    def test_concurrent_cap_counts_pending_and_running_only(self, db: Session) -> None:
        team_id = _create_team(db)
        wf_id = _create_workflow(db, team_id)
        wf = _make_workflow_ns(wf_id, team_id, max_concurrent_runs=2)
        _insert_run(db, wf_id, team_id, status="succeeded")
        _insert_run(db, wf_id, team_id, status="failed")
        _insert_run(db, wf_id, team_id, status="cancelled")
        _insert_run(db, wf_id, team_id, status="running")  # count=1, limit=2
        _check_workflow_caps(db, wf)  # must not raise

    def test_hourly_cap_none_skips_check(self, db: Session) -> None:
        team_id = _create_team(db)
        wf_id = _create_workflow(db, team_id)
        wf = _make_workflow_ns(wf_id, team_id, max_runs_per_hour=None)
        recent = datetime.now(timezone.utc) - timedelta(minutes=5)
        for _ in range(100):
            _insert_run(db, wf_id, team_id, status="succeeded", created_at=recent)
        _check_workflow_caps(db, wf)  # must not raise — hourly cap is None

    def test_concurrent_cap_none_skips_check(self, db: Session) -> None:
        team_id = _create_team(db)
        wf_id = _create_workflow(db, team_id)
        wf = _make_workflow_ns(wf_id, team_id, max_concurrent_runs=None)
        for _ in range(50):
            _insert_run(db, wf_id, team_id, status="running")
        _check_workflow_caps(db, wf)  # must not raise — concurrent cap is None


# ---------------------------------------------------------------------------
# Integration tests via HTTP (cap enforcement in dispatch route)
# ---------------------------------------------------------------------------


class TestDispatchCapHTTP:
    @pytest.fixture(scope="class")
    def member_cookies(self, client: TestClient) -> dict:
        _email, _uid, cookies = _signup_and_login(client)
        return cookies

    @pytest.fixture(scope="class")
    def member_uid(self, client: TestClient, member_cookies: dict) -> uuid.UUID:
        r = client.get(f"{API}/users/me", cookies=member_cookies)
        assert r.status_code == 200
        return uuid.UUID(r.json()["id"])

    def test_concurrent_cap_returns_429(
        self, client: TestClient, db: Session, member_cookies: dict, member_uid: uuid.UUID
    ) -> None:
        team_id = _create_team(db)
        _add_member(db, team_id, member_uid)
        wf_id = _create_workflow(db, team_id, max_concurrent_runs=2)
        _insert_run(db, wf_id, team_id, status="running")
        _insert_run(db, wf_id, team_id, status="pending")

        with patch("app.workflows.tasks.run_workflow.delay"):
            r = client.post(
                DISPATCH_URL_TPL.format(workflow_id=wf_id),
                json={"trigger_payload": {}},
                cookies=member_cookies,
            )

        assert r.status_code == 429, r.text
        detail = r.json()["detail"]
        assert detail["detail"] == "workflow_cap_exceeded"
        assert detail["cap_type"] == "concurrent"
        assert detail["current_count"] == 2
        assert detail["limit"] == 2

    def test_concurrent_cap_audit_row_written(
        self, client: TestClient, db: Session, member_cookies: dict, member_uid: uuid.UUID
    ) -> None:
        team_id = _create_team(db)
        _add_member(db, team_id, member_uid)
        wf_id = _create_workflow(db, team_id, max_concurrent_runs=1)
        _insert_run(db, wf_id, team_id, status="running")

        with patch("app.workflows.tasks.run_workflow.delay"):
            r = client.post(
                DISPATCH_URL_TPL.format(workflow_id=wf_id),
                json={"trigger_payload": {}},
                cookies=member_cookies,
            )
        assert r.status_code == 429, r.text

        db.expire_all()
        rejected = db.exec(
            select(WorkflowRun).where(
                WorkflowRun.workflow_id == wf_id,
                WorkflowRun.status == WorkflowRunStatus.rejected.value,
                WorkflowRun.error_class == "cap_exceeded",
            )
        ).first()
        assert rejected is not None, "Expected a rejected audit row in workflow_runs"

    def test_hourly_cap_returns_429(
        self, client: TestClient, db: Session, member_cookies: dict, member_uid: uuid.UUID
    ) -> None:
        team_id = _create_team(db)
        _add_member(db, team_id, member_uid)
        wf_id = _create_workflow(db, team_id, max_runs_per_hour=2)
        recent = datetime.now(timezone.utc) - timedelta(minutes=10)
        _insert_run(db, wf_id, team_id, status="succeeded", created_at=recent)
        _insert_run(db, wf_id, team_id, status="succeeded", created_at=recent)

        with patch("app.workflows.tasks.run_workflow.delay"):
            r = client.post(
                DISPATCH_URL_TPL.format(workflow_id=wf_id),
                json={"trigger_payload": {}},
                cookies=member_cookies,
            )

        assert r.status_code == 429, r.text
        detail = r.json()["detail"]
        assert detail["detail"] == "workflow_cap_exceeded"
        assert detail["cap_type"] == "hourly"
        assert detail["current_count"] == 2
        assert detail["limit"] == 2

    def test_under_cap_succeeds(
        self, client: TestClient, db: Session, member_cookies: dict, member_uid: uuid.UUID
    ) -> None:
        team_id = _create_team(db)
        _add_member(db, team_id, member_uid)
        wf_id = _create_workflow(db, team_id, max_concurrent_runs=2)
        _insert_run(db, wf_id, team_id, status="running")  # count=1, limit=2

        with patch("app.workflows.tasks.run_workflow.delay"):
            r = client.post(
                DISPATCH_URL_TPL.format(workflow_id=wf_id),
                json={"trigger_payload": {}},
                cookies=member_cookies,
            )

        assert r.status_code == 200, r.text

    def test_no_cap_dispatch_succeeds(
        self, client: TestClient, db: Session, member_cookies: dict, member_uid: uuid.UUID
    ) -> None:
        team_id = _create_team(db)
        _add_member(db, team_id, member_uid)
        wf_id = _create_workflow(db, team_id)  # both caps None

        with patch("app.workflows.tasks.run_workflow.delay"):
            r = client.post(
                DISPATCH_URL_TPL.format(workflow_id=wf_id),
                json={"trigger_payload": {}},
                cookies=member_cookies,
            )

        assert r.status_code == 200, r.text
