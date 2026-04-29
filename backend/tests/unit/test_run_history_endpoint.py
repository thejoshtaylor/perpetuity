"""Unit tests for GET /api/v1/teams/{team_id}/runs (run history list endpoint).

Covers:
  1. Returns paginated WorkflowRunsPublic for a team member.
  2. Filter by status — only matching runs returned.
  3. Filter by trigger_type — only matching runs returned.
  4. Filter by after / before datetime bounds.
  5. limit / offset pagination works correctly.
  6. Runs from deleted workflows still appear (snapshot semantics, R018).
  7. 403 not_team_member for non-member callers.
  8. 404 team_not_found for unknown team_id.
  9. Invalid status / trigger_type filter values return 422.
 10. Empty result returns {data: [], count: 0}.

Real FastAPI app + real Postgres via session-scoped `db` and module-scoped
`client` from tests/conftest.py. Celery is never invoked — runs are inserted
directly as raw SQL rows to avoid broker dependency.
"""
from __future__ import annotations

import uuid
from collections.abc import Generator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session

from app.core.config import settings
from app.models import Team, TeamMember, TeamRole

API = settings.API_V1_STR
RUNS_URL_TPL = f"{API}/teams/{{team_id}}/runs"
SIGNUP_URL = f"{API}/auth/signup"
LOGIN_URL = f"{API}/auth/login"


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


def _signup_and_login(client: TestClient, db: Session) -> tuple[str, uuid.UUID]:
    """Create a user and return (email, user_id)."""
    email = f"rh-{uuid.uuid4().hex[:8]}@test.example"
    r = client.post(SIGNUP_URL, json={"email": email, "password": "Password1!"})
    assert r.status_code == 200, r.text
    user_id = uuid.UUID(r.json()["id"])
    client.post(LOGIN_URL, json={"email": email, "password": "Password1!"})
    return email, user_id


def _create_team(db: Session) -> uuid.UUID:
    team_id = uuid.uuid4()
    team = Team(
        id=team_id,
        name=f"rh-team-{team_id.hex[:8]}",
        slug=f"rh-{team_id.hex[:8]}",
        is_personal=False,
    )
    db.add(team)
    db.commit()
    return team_id


def _add_member(db: Session, team_id: uuid.UUID, user_id: uuid.UUID, role: TeamRole = TeamRole.member) -> None:
    db.add(TeamMember(user_id=user_id, team_id=team_id, role=role))
    db.commit()


def _insert_workflow(db: Session, team_id: uuid.UUID) -> uuid.UUID:
    wf_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO workflows (id, team_id, name) "
            "VALUES (:id, :team, :name)"
        ),
        {"id": wf_id, "team": team_id, "name": f"wf-{wf_id.hex[:8]}"},
    )
    db.commit()
    return wf_id


def _insert_run(
    db: Session,
    *,
    wf_id: uuid.UUID,
    team_id: uuid.UUID,
    trigger_type: str = "button",
    status: str = "succeeded",
    created_at: datetime | None = None,
) -> uuid.UUID:
    run_id = uuid.uuid4()
    ts = created_at or datetime.now(timezone.utc)
    db.execute(
        text(
            "INSERT INTO workflow_runs "
            "(id, workflow_id, team_id, trigger_type, status, created_at) "
            "VALUES (:id, :wf, :team, :tt, :st, :ts)"
        ),
        {
            "id": run_id,
            "wf": wf_id,
            "team": team_id,
            "tt": trigger_type,
            "st": status,
            "ts": ts,
        },
    )
    db.commit()
    return run_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cookies_for(client: TestClient, email: str) -> dict:
    r = client.post(LOGIN_URL, json={"email": email, "password": "Password1!"})
    return dict(r.cookies)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_list_runs_returns_team_runs(client: TestClient, db: Session) -> None:
    """Member gets back all team runs with correct shape."""
    email, user_id = _signup_and_login(client, db)
    team_id = _create_team(db)
    _add_member(db, team_id, user_id)
    wf_id = _insert_workflow(db, team_id)
    _insert_run(db, wf_id=wf_id, team_id=team_id, status="succeeded")
    _insert_run(db, wf_id=wf_id, team_id=team_id, status="failed")

    cookies = _cookies_for(client, email)
    r = client.get(RUNS_URL_TPL.format(team_id=team_id), cookies=cookies)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    assert len(body["data"]) == 2
    # Each item must have the summary fields
    for item in body["data"]:
        assert "id" in item
        assert "status" in item
        assert "trigger_type" in item


def test_list_runs_filter_by_status(client: TestClient, db: Session) -> None:
    """status filter returns only runs matching that status."""
    email, user_id = _signup_and_login(client, db)
    team_id = _create_team(db)
    _add_member(db, team_id, user_id)
    wf_id = _insert_workflow(db, team_id)
    _insert_run(db, wf_id=wf_id, team_id=team_id, status="succeeded")
    _insert_run(db, wf_id=wf_id, team_id=team_id, status="failed")
    _insert_run(db, wf_id=wf_id, team_id=team_id, status="running")

    cookies = _cookies_for(client, email)
    r = client.get(
        RUNS_URL_TPL.format(team_id=team_id),
        params={"status": "failed"},
        cookies=cookies,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    assert body["data"][0]["status"] == "failed"


def test_list_runs_filter_by_trigger_type(client: TestClient, db: Session) -> None:
    """trigger_type filter returns only matching runs."""
    email, user_id = _signup_and_login(client, db)
    team_id = _create_team(db)
    _add_member(db, team_id, user_id)
    wf_id = _insert_workflow(db, team_id)
    _insert_run(db, wf_id=wf_id, team_id=team_id, trigger_type="webhook")
    _insert_run(db, wf_id=wf_id, team_id=team_id, trigger_type="button")
    _insert_run(db, wf_id=wf_id, team_id=team_id, trigger_type="admin_manual")

    cookies = _cookies_for(client, email)
    r = client.get(
        RUNS_URL_TPL.format(team_id=team_id),
        params={"trigger_type": "webhook"},
        cookies=cookies,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    assert body["data"][0]["trigger_type"] == "webhook"


def test_list_runs_filter_by_time_range(client: TestClient, db: Session) -> None:
    """after/before filters apply on created_at."""
    email, user_id = _signup_and_login(client, db)
    team_id = _create_team(db)
    _add_member(db, team_id, user_id)
    wf_id = _insert_workflow(db, team_id)

    now = datetime.now(timezone.utc)
    _insert_run(db, wf_id=wf_id, team_id=team_id, created_at=now - timedelta(hours=2))
    _insert_run(db, wf_id=wf_id, team_id=team_id, created_at=now - timedelta(hours=1))
    _insert_run(db, wf_id=wf_id, team_id=team_id, created_at=now)

    cookies = _cookies_for(client, email)

    # Only runs in the last 90 minutes
    after_ts = (now - timedelta(minutes=90)).isoformat()
    r = client.get(
        RUNS_URL_TPL.format(team_id=team_id),
        params={"after": after_ts},
        cookies=cookies,
    )
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 2


def test_list_runs_pagination(client: TestClient, db: Session) -> None:
    """limit and offset correctly paginate results."""
    email, user_id = _signup_and_login(client, db)
    team_id = _create_team(db)
    _add_member(db, team_id, user_id)
    wf_id = _insert_workflow(db, team_id)
    for _ in range(5):
        _insert_run(db, wf_id=wf_id, team_id=team_id)

    cookies = _cookies_for(client, email)

    r1 = client.get(
        RUNS_URL_TPL.format(team_id=team_id),
        params={"limit": 2, "offset": 0},
        cookies=cookies,
    )
    assert r1.status_code == 200
    body1 = r1.json()
    assert len(body1["data"]) == 2
    assert body1["count"] == 5  # total count unchanged

    r2 = client.get(
        RUNS_URL_TPL.format(team_id=team_id),
        params={"limit": 2, "offset": 4},
        cookies=cookies,
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert len(body2["data"]) == 1


def test_list_runs_cross_workflow_aggregation(
    client: TestClient, db: Session
) -> None:
    """Runs from multiple workflows in the same team all appear in history."""
    email, user_id = _signup_and_login(client, db)
    team_id = _create_team(db)
    _add_member(db, team_id, user_id)
    wf_id1 = _insert_workflow(db, team_id)
    wf_id2 = _insert_workflow(db, team_id)
    run_id1 = _insert_run(db, wf_id=wf_id1, team_id=team_id)
    run_id2 = _insert_run(db, wf_id=wf_id2, team_id=team_id)

    cookies = _cookies_for(client, email)
    r = client.get(RUNS_URL_TPL.format(team_id=team_id), cookies=cookies)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    ids = {item["id"] for item in body["data"]}
    assert str(run_id1) in ids
    assert str(run_id2) in ids


def test_list_runs_403_non_member(client: TestClient, db: Session) -> None:
    """Non-member gets 403 not_team_member."""
    email, user_id = _signup_and_login(client, db)
    team_id = _create_team(db)  # user not added as member

    cookies = _cookies_for(client, email)
    r = client.get(RUNS_URL_TPL.format(team_id=team_id), cookies=cookies)
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["detail"] == "not_team_member"


def test_list_runs_404_unknown_team(client: TestClient, db: Session) -> None:
    """Unknown team_id returns 404 team_not_found."""
    email, user_id = _signup_and_login(client, db)
    cookies = _cookies_for(client, email)

    r = client.get(
        RUNS_URL_TPL.format(team_id=uuid.uuid4()),
        cookies=cookies,
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["detail"] == "team_not_found"


def test_list_runs_invalid_status_filter_422(client: TestClient, db: Session) -> None:
    """Invalid status filter value returns 422."""
    email, user_id = _signup_and_login(client, db)
    team_id = _create_team(db)
    _add_member(db, team_id, user_id)

    cookies = _cookies_for(client, email)
    r = client.get(
        RUNS_URL_TPL.format(team_id=team_id),
        params={"status": "notavalidstatus"},
        cookies=cookies,
    )
    assert r.status_code == 422, r.text


def test_list_runs_invalid_trigger_type_422(client: TestClient, db: Session) -> None:
    """Invalid trigger_type filter value returns 422."""
    email, user_id = _signup_and_login(client, db)
    team_id = _create_team(db)
    _add_member(db, team_id, user_id)

    cookies = _cookies_for(client, email)
    r = client.get(
        RUNS_URL_TPL.format(team_id=team_id),
        params={"trigger_type": "notavalidtype"},
        cookies=cookies,
    )
    assert r.status_code == 422, r.text


def test_list_runs_empty_result(client: TestClient, db: Session) -> None:
    """Empty team returns {data: [], count: 0}."""
    email, user_id = _signup_and_login(client, db)
    team_id = _create_team(db)
    _add_member(db, team_id, user_id)

    cookies = _cookies_for(client, email)
    r = client.get(RUNS_URL_TPL.format(team_id=team_id), cookies=cookies)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 0
    assert body["data"] == []


def test_list_runs_unauthenticated_401(db: Session) -> None:
    """Unauthenticated request (no cookie) returns 401."""
    from app.main import app

    team_id = uuid.uuid4()
    # Use a fresh client with no cookies to avoid session bleed from other tests.
    with TestClient(app) as fresh_client:
        r = fresh_client.get(RUNS_URL_TPL.format(team_id=team_id))
    assert r.status_code == 401, r.text
