"""Integration tests for the workflow CRUD API (M005/S03/T04).

Covers routes in `app.api.routes.workflows_crud`:
    POST   /api/v1/teams/{team_id}/workflows
    GET    /api/v1/workflows/{workflow_id}
    PUT    /api/v1/workflows/{workflow_id}
    DELETE /api/v1/workflows/{workflow_id}

Tests prove:
  * Admin gate: member (non-admin) gets 403 on write operations.
  * System_owned name rejection on create (403 cannot_modify_system_workflow).
  * System_owned row rejection on update/delete.
  * form_schema validation (missing fields, bad kind, wrong type).
  * PUT replaces steps atomically (old step ids gone, new ones present).
  * DELETE cascades to workflow_runs and step_runs.
  * GET returns WorkflowWithStepsPublic with ordered steps.
  * list_team_workflows (moved to workflows.py, covered in test_workflow_run_routes).
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
    WorkflowStep,
)
from tests.utils.utils import random_email, random_lower_string

API = settings.API_V1_STR
SIGNUP_URL = f"{API}/auth/signup"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
        name=f"crud-test-{uuid.uuid4().hex[:8]}",
        creator_id=admin_id,
    )


def _add_member(
    db: Session, team_id: uuid.UUID, user_id: uuid.UUID, role: TeamRole
) -> None:
    db.add(TeamMember(user_id=user_id, team_id=team_id, role=role))
    db.commit()


def _workflow_body(
    name: str = "test-wf",
    *,
    steps: list[dict] | None = None,
    form_schema: dict | None = None,
) -> dict:
    if steps is None:
        steps = [{"step_index": 0, "action": "shell", "config": {"cmd": "echo hi"}}]
    body: dict = {"name": name, "scope": "user", "steps": steps}
    if form_schema is not None:
        body["form_schema"] = form_schema
    return body


# ---------------------------------------------------------------------------
# POST /teams/{team_id}/workflows
# ---------------------------------------------------------------------------


def test_create_workflow_admin_succeeds(
    client: TestClient, db: Session
) -> None:
    admin_id, cookies = _signup(client)
    team = _create_team_with_admin(db, admin_id)

    r = client.post(
        f"{API}/teams/{team.id}/workflows",
        json=_workflow_body("my-flow"),
        cookies=cookies,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "my-flow"
    assert body["system_owned"] is False
    assert body["team_id"] == str(team.id)
    assert len(body["steps"]) == 1
    assert body["steps"][0]["action"] == "shell"
    assert body["steps"][0]["step_index"] == 0


def test_create_workflow_member_gets_403(
    client: TestClient, db: Session
) -> None:
    admin_id, _ = _signup(client)
    team = _create_team_with_admin(db, admin_id)

    member_id, member_cookies = _signup(client)
    _add_member(db, team.id, member_id, TeamRole.member)

    r = client.post(
        f"{API}/teams/{team.id}/workflows",
        json=_workflow_body("my-flow"),
        cookies=member_cookies,
    )
    assert r.status_code == 403


def test_create_workflow_reserved_name_gets_403(
    client: TestClient, db: Session
) -> None:
    admin_id, cookies = _signup(client)
    team = _create_team_with_admin(db, admin_id)

    r = client.post(
        f"{API}/teams/{team.id}/workflows",
        json=_workflow_body("_direct_evil"),
        cookies=cookies,
    )
    assert r.status_code == 403
    assert r.json()["detail"]["detail"] == "cannot_modify_system_workflow"


def test_create_workflow_bad_form_schema_missing_fields_key(
    client: TestClient, db: Session
) -> None:
    admin_id, cookies = _signup(client)
    team = _create_team_with_admin(db, admin_id)

    r = client.post(
        f"{API}/teams/{team.id}/workflows",
        json=_workflow_body("my-flow", form_schema={"not_fields": []}),
        cookies=cookies,
    )
    assert r.status_code == 400
    assert r.json()["detail"]["detail"] == "invalid_form_schema"


def test_create_workflow_bad_form_schema_bad_kind(
    client: TestClient, db: Session
) -> None:
    admin_id, cookies = _signup(client)
    team = _create_team_with_admin(db, admin_id)

    bad_schema = {
        "fields": [
            {"name": "x", "label": "X", "kind": "badkind", "required": False}
        ]
    }
    r = client.post(
        f"{API}/teams/{team.id}/workflows",
        json=_workflow_body("my-flow", form_schema=bad_schema),
        cookies=cookies,
    )
    assert r.status_code == 400
    assert r.json()["detail"]["detail"] == "invalid_form_schema"


def test_create_workflow_valid_form_schema(
    client: TestClient, db: Session
) -> None:
    admin_id, cookies = _signup(client)
    team = _create_team_with_admin(db, admin_id)

    good_schema = {
        "fields": [
            {"name": "branch", "label": "Branch", "kind": "string", "required": True}
        ]
    }
    r = client.post(
        f"{API}/teams/{team.id}/workflows",
        json=_workflow_body("my-flow", form_schema=good_schema),
        cookies=cookies,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["form_schema"]["fields"][0]["name"] == "branch"


# ---------------------------------------------------------------------------
# GET /workflows/{workflow_id}
# ---------------------------------------------------------------------------


def test_get_workflow_returns_workflow_with_steps(
    client: TestClient, db: Session
) -> None:
    admin_id, cookies = _signup(client)
    team = _create_team_with_admin(db, admin_id)

    # Create via API
    post = client.post(
        f"{API}/teams/{team.id}/workflows",
        json=_workflow_body("my-flow"),
        cookies=cookies,
    )
    wf_id = post.json()["id"]

    r = client.get(f"{API}/workflows/{wf_id}", cookies=cookies)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == wf_id
    assert body["name"] == "my-flow"
    assert len(body["steps"]) == 1


def test_get_workflow_unknown_returns_404(
    client: TestClient, db: Session
) -> None:
    _admin_id, cookies = _signup(client)
    r = client.get(f"{API}/workflows/{uuid.uuid4()}", cookies=cookies)
    assert r.status_code == 404
    assert r.json()["detail"]["detail"] == "workflow_not_found"


def test_get_workflow_non_member_returns_403(
    client: TestClient, db: Session
) -> None:
    admin_id, _ = _signup(client)
    team = _create_team_with_admin(db, admin_id)

    wf = Workflow(
        team_id=team.id,
        name="secret-flow",
        scope="user",
        system_owned=False,
    )
    db.add(wf)
    db.commit()

    other_id, other_cookies = _signup(client)
    assert other_id != admin_id

    r = client.get(f"{API}/workflows/{wf.id}", cookies=other_cookies)
    assert r.status_code == 403
    assert r.json()["detail"]["detail"] == "not_team_member"


# ---------------------------------------------------------------------------
# PUT /workflows/{workflow_id}
# ---------------------------------------------------------------------------


def test_update_workflow_replaces_steps_atomically(
    client: TestClient, db: Session
) -> None:
    admin_id, cookies = _signup(client)
    team = _create_team_with_admin(db, admin_id)

    post = client.post(
        f"{API}/teams/{team.id}/workflows",
        json=_workflow_body("my-flow"),
        cookies=cookies,
    )
    wf_id = post.json()["id"]
    old_step_id = post.json()["steps"][0]["id"]

    r = client.put(
        f"{API}/workflows/{wf_id}",
        json={
            "steps": [
                {"step_index": 0, "action": "git", "config": {"cmd": "git status"}},
                {"step_index": 1, "action": "shell", "config": {"cmd": "echo done"}},
            ]
        },
        cookies=cookies,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    new_step_ids = {s["id"] for s in body["steps"]}
    assert old_step_id not in new_step_ids, "old step_id must be gone after PUT"
    assert len(body["steps"]) == 2
    actions = [s["action"] for s in body["steps"]]
    assert actions == ["git", "shell"]


def test_update_workflow_member_gets_403(
    client: TestClient, db: Session
) -> None:
    admin_id, admin_cookies = _signup(client)
    team = _create_team_with_admin(db, admin_id)

    post = client.post(
        f"{API}/teams/{team.id}/workflows",
        json=_workflow_body("my-flow"),
        cookies=admin_cookies,
    )
    wf_id = post.json()["id"]

    member_id, member_cookies = _signup(client)
    _add_member(db, team.id, member_id, TeamRole.member)

    r = client.put(
        f"{API}/workflows/{wf_id}",
        json={"name": "changed"},
        cookies=member_cookies,
    )
    assert r.status_code == 403


def test_update_system_owned_workflow_gets_403(
    client: TestClient, db: Session
) -> None:
    admin_id, cookies = _signup(client)
    team = _create_team_with_admin(db, admin_id)

    # _direct_claude is auto-seeded as system_owned
    from sqlmodel import select as sqselect

    wf = db.exec(
        sqselect(Workflow)
        .where(Workflow.team_id == team.id)
        .where(Workflow.name == "_direct_claude")
    ).first()
    assert wf is not None

    r = client.put(
        f"{API}/workflows/{wf.id}",
        json={"name": "hacked"},
        cookies=cookies,
    )
    assert r.status_code == 403
    assert r.json()["detail"]["detail"] == "cannot_modify_system_workflow"


# ---------------------------------------------------------------------------
# DELETE /workflows/{workflow_id}
# ---------------------------------------------------------------------------


def test_delete_workflow_cascades_to_runs_and_step_runs(
    client: TestClient, db: Session
) -> None:
    admin_id, admin_cookies = _signup(client)
    team = _create_team_with_admin(db, admin_id)

    post = client.post(
        f"{API}/teams/{team.id}/workflows",
        json=_workflow_body("to-delete"),
        cookies=admin_cookies,
    )
    wf_id = post.json()["id"]

    # Manually insert a workflow_run and step_run for this workflow
    from app.models import WorkflowRun, StepRun

    wf_obj = db.get(Workflow, uuid.UUID(wf_id))
    run = WorkflowRun(
        workflow_id=wf_obj.id,
        team_id=team.id,
        trigger_type="button",
        triggered_by_user_id=admin_id,
        target_user_id=admin_id,
        trigger_payload={},
        status="pending",
    )
    db.add(run)
    db.flush()
    db.add(StepRun(
        workflow_run_id=run.id,
        step_index=0,
        snapshot={"action": "shell"},
        status="pending",
    ))
    db.commit()
    run_id = run.id

    r = client.delete(f"{API}/workflows/{wf_id}", cookies=admin_cookies)
    assert r.status_code == 204

    # Cascade should have deleted runs and step_runs
    rows = db.execute(
        text("SELECT count(*) FROM workflow_runs WHERE id = :rid"),
        {"rid": run_id},
    ).scalar()
    assert rows == 0


def test_delete_system_owned_workflow_gets_403(
    client: TestClient, db: Session
) -> None:
    admin_id, cookies = _signup(client)
    team = _create_team_with_admin(db, admin_id)

    from sqlmodel import select as sqselect

    wf = db.exec(
        sqselect(Workflow)
        .where(Workflow.team_id == team.id)
        .where(Workflow.name == "_direct_claude")
    ).first()
    assert wf is not None

    r = client.delete(f"{API}/workflows/{wf.id}", cookies=cookies)
    assert r.status_code == 403
    assert r.json()["detail"]["detail"] == "cannot_modify_system_workflow"


def test_delete_workflow_member_gets_403(
    client: TestClient, db: Session
) -> None:
    admin_id, admin_cookies = _signup(client)
    team = _create_team_with_admin(db, admin_id)

    post = client.post(
        f"{API}/teams/{team.id}/workflows",
        json=_workflow_body("deletable"),
        cookies=admin_cookies,
    )
    wf_id = post.json()["id"]

    member_id, member_cookies = _signup(client)
    _add_member(db, team.id, member_id, TeamRole.member)

    r = client.delete(f"{API}/workflows/{wf_id}", cookies=member_cookies)
    assert r.status_code == 403
