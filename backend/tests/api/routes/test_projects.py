"""Endpoint tests for the projects router (M004 / S04 / T01).

Real TestClient + real Postgres so the team-admin/member auth gate, the
installation FK validation, the UNIQUE (team_id, name) check, and the
push-rule UPSERT path all run for real. No orchestrator hop in T01.

Coverage matrix:

  list  / create  / patch / delete                  — admin/member gating
  cross-team enumeration → 404 project_not_found    — MEM263 pattern
  unknown installation_id → 404 installation_not_in_team
  cross-team installation → 404 installation_not_in_team
  duplicate (team_id, name) → 409 project_name_taken
  default push_rule on create → mode=manual_workflow
  push-rule PUT for all three modes (auto, rule, manual_workflow)
  push-rule mode-specific field validation (422)
  push-rule unknown mode → 422
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, delete, select

from app.core.config import settings
from app.models import (
    GitHubAppInstallation,
    Notification,
    Project,
    ProjectPushRule,
)
from tests.utils.utils import random_email, random_lower_string

API = settings.API_V1_STR
SIGNUP_URL = f"{API}/auth/signup"
TEAMS_URL = f"{API}/teams/"


# ---------------------------------------------------------------------------
# Test isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_projects_state(db: Session):
    """Wipe projects + push_rules + installations before AND after each test.

    We deliberately do NOT delete users, teams, or memberships — those leak
    across modules by design via the session-scoped `db` fixture. Cleanup
    order matches the FK chain: notifications (FK to projects via
    source_project_id, ON DELETE SET NULL — but cheap to clean for test
    determinism) → push_rules → projects → installations.
    """
    db.execute(delete(Notification))
    db.execute(delete(ProjectPushRule))
    db.execute(delete(Project))
    db.execute(delete(GitHubAppInstallation))
    db.commit()
    yield
    db.execute(delete(Notification))
    db.execute(delete(ProjectPushRule))
    db.execute(delete(Project))
    db.execute(delete(GitHubAppInstallation))
    db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _signup(client: TestClient) -> tuple[str, httpx.Cookies]:
    email = random_email()
    password = random_lower_string()
    client.cookies.clear()
    r = client.post(SIGNUP_URL, json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    jar = httpx.Cookies()
    for cookie in client.cookies.jar:
        jar.set(cookie.name, cookie.value)
    client.cookies.clear()
    return r.json()["id"], jar


def _create_team(
    client: TestClient, cookies: httpx.Cookies, name: str = "ProjTeam"
) -> str:
    r = client.post(TEAMS_URL, json={"name": name}, cookies=cookies)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _issue_invite(
    client: TestClient, cookies: httpx.Cookies, team_id: str
) -> str:
    r = client.post(f"{API}/teams/{team_id}/invite", cookies=cookies)
    assert r.status_code == 200, r.text
    return r.json()["code"]


def _join_team(
    client: TestClient, cookies: httpx.Cookies, code: str
) -> None:
    r = client.post(f"{API}/teams/join/{code}", cookies=cookies)
    assert r.status_code == 200, r.text


def _seed_installation(
    db: Session, *, team_id: str, installation_id: int, login: str = "acme"
) -> None:
    """Seed a github_app_installations row directly — bypasses the install
    handshake which is exercised in test_github_install."""
    db.execute(
        text(
            """
            INSERT INTO github_app_installations
                (id, team_id, installation_id, account_login, account_type,
                 created_at)
            VALUES
                (:id, :team, :inst, :login, 'Organization', NOW())
            """
        ),
        {
            "id": uuid.uuid4(),
            "team": team_id,
            "inst": installation_id,
            "login": login,
        },
    )
    db.commit()


def _create_project(
    client: TestClient,
    cookies: httpx.Cookies,
    *,
    team_id: str,
    installation_id: int,
    name: str = "widgets",
    repo: str = "acme/widgets",
) -> dict[str, Any]:
    r = client.post(
        f"{API}/teams/{team_id}/projects",
        json={
            "installation_id": installation_id,
            "github_repo_full_name": repo,
            "name": name,
        },
        cookies=cookies,
    )
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# POST /teams/{id}/projects — create
# ---------------------------------------------------------------------------


def test_create_project_happy_path_persists_with_default_push_rule(
    client: TestClient, db: Session
) -> None:
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "HappyCreate")
    _seed_installation(db, team_id=team_id, installation_id=1001)

    body = _create_project(
        client, cookies, team_id=team_id, installation_id=1001
    )
    assert body["team_id"] == team_id
    assert body["installation_id"] == 1001
    assert body["github_repo_full_name"] == "acme/widgets"
    assert body["name"] == "widgets"
    assert body["last_push_status"] is None
    assert body["last_push_error"] is None

    # Default push_rule must be present at mode=manual_workflow.
    db.expire_all()
    rule_row = db.execute(
        text(
            "SELECT mode, branch_pattern, workflow_id"
            " FROM project_push_rules WHERE project_id = :pid"
        ),
        {"pid": body["id"]},
    ).one()
    assert rule_row.mode == "manual_workflow"
    assert rule_row.branch_pattern is None
    assert rule_row.workflow_id is None


def test_create_project_logs_project_created(
    client: TestClient, db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "LogCreate")
    _seed_installation(db, team_id=team_id, installation_id=1010)

    # Defensive: alembic-using tests run `fileConfig(disable_existing_loggers=True)`
    # in their setup which silently disables this logger if those tests ran
    # earlier in the same pytest session. Re-enable explicitly.
    logging.getLogger("app.api.routes.projects").disabled = False

    with caplog.at_level(logging.INFO, logger="app.api.routes.projects"):
        body = _create_project(
            client, cookies, team_id=team_id, installation_id=1010
        )

    captured = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "project_created" in captured
    assert f"project_id={body['id']}" in captured
    assert f"team_id={team_id}" in captured
    assert "repo=acme/widgets" in captured


def test_create_project_403_when_caller_is_not_admin(
    client: TestClient, db: Session
) -> None:
    _admin_id, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "AdminOnlyCreate")
    code = _issue_invite(client, cookies_a, team_id)
    _member_id, cookies_b = _signup(client)
    _join_team(client, cookies_b, code)
    _seed_installation(db, team_id=team_id, installation_id=2002)

    r = client.post(
        f"{API}/teams/{team_id}/projects",
        json={
            "installation_id": 2002,
            "github_repo_full_name": "acme/m",
            "name": "m",
        },
        cookies=cookies_b,
    )
    assert r.status_code == 403, r.text


def test_create_project_403_when_caller_is_not_member(
    client: TestClient, db: Session
) -> None:
    _, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "MemberOnlyCreate")
    _seed_installation(db, team_id=team_id, installation_id=3003)

    _, cookies_b = _signup(client)
    r = client.post(
        f"{API}/teams/{team_id}/projects",
        json={
            "installation_id": 3003,
            "github_repo_full_name": "acme/m",
            "name": "m",
        },
        cookies=cookies_b,
    )
    assert r.status_code == 403, r.text


def test_create_project_404_when_team_missing(
    client: TestClient,
) -> None:
    _, cookies = _signup(client)
    bogus = uuid.uuid4()
    r = client.post(
        f"{API}/teams/{bogus}/projects",
        json={
            "installation_id": 1,
            "github_repo_full_name": "acme/m",
            "name": "m",
        },
        cookies=cookies,
    )
    assert r.status_code == 404, r.text


def test_create_project_404_when_installation_unknown(
    client: TestClient,
) -> None:
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "UnknownInstall")

    r = client.post(
        f"{API}/teams/{team_id}/projects",
        json={
            "installation_id": 9999999,
            "github_repo_full_name": "acme/x",
            "name": "x",
        },
        cookies=cookies,
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "installation_not_in_team"


def test_create_project_404_when_installation_belongs_to_other_team(
    client: TestClient, db: Session
) -> None:
    """Cross-team installation reference → 404 installation_not_in_team."""
    _, cookies_a = _signup(client)
    team_a = _create_team(client, cookies_a, "OwnerA")
    _seed_installation(db, team_id=team_a, installation_id=4004)

    _, cookies_b = _signup(client)
    team_b = _create_team(client, cookies_b, "OtherB")

    # team_b admin tries to attach team_a's installation.
    r = client.post(
        f"{API}/teams/{team_b}/projects",
        json={
            "installation_id": 4004,
            "github_repo_full_name": "acme/m",
            "name": "m",
        },
        cookies=cookies_b,
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "installation_not_in_team"


def test_create_project_409_on_duplicate_name_in_team(
    client: TestClient, db: Session
) -> None:
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "DupName")
    _seed_installation(db, team_id=team_id, installation_id=5005)

    _create_project(
        client, cookies, team_id=team_id, installation_id=5005, name="dup"
    )
    r = client.post(
        f"{API}/teams/{team_id}/projects",
        json={
            "installation_id": 5005,
            "github_repo_full_name": "acme/different",
            "name": "dup",
        },
        cookies=cookies,
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "project_name_taken"


# ---------------------------------------------------------------------------
# GET /teams/{id}/projects — list
# ---------------------------------------------------------------------------


def test_list_projects_returns_envelope_member_can_read(
    client: TestClient, db: Session
) -> None:
    _, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "ListTeam")
    _seed_installation(db, team_id=team_id, installation_id=6006)
    _create_project(
        client, cookies_a, team_id=team_id, installation_id=6006, name="p1"
    )
    _create_project(
        client,
        cookies_a,
        team_id=team_id,
        installation_id=6006,
        name="p2",
        repo="acme/p2",
    )

    # Member (non-admin) should still be able to list.
    code = _issue_invite(client, cookies_a, team_id)
    _, cookies_b = _signup(client)
    _join_team(client, cookies_b, code)

    r = client.get(
        f"{API}/teams/{team_id}/projects", cookies=cookies_b
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    names = sorted(p["name"] for p in body["data"])
    assert names == ["p1", "p2"]


def test_list_projects_403_when_not_member(
    client: TestClient,
) -> None:
    _, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "ListAlien")
    _, cookies_b = _signup(client)
    r = client.get(
        f"{API}/teams/{team_id}/projects", cookies=cookies_b
    )
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# GET /projects/{id}
# ---------------------------------------------------------------------------


def test_get_project_404_when_missing(
    client: TestClient,
) -> None:
    _, cookies = _signup(client)
    bogus = uuid.uuid4()
    r = client.get(f"{API}/projects/{bogus}", cookies=cookies)
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "project_not_found"


def test_get_project_404_for_cross_team_caller(
    client: TestClient, db: Session
) -> None:
    """Cross-team caller must get 404, not 403 (no enumeration)."""
    _, cookies_a = _signup(client)
    team_a = _create_team(client, cookies_a, "Get404A")
    _seed_installation(db, team_id=team_a, installation_id=7007)
    pr = _create_project(
        client, cookies_a, team_id=team_a, installation_id=7007
    )

    _, cookies_b = _signup(client)
    r = client.get(f"{API}/projects/{pr['id']}", cookies=cookies_b)
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "project_not_found"


def test_get_project_happy_path_member(
    client: TestClient, db: Session
) -> None:
    _, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "GetMember")
    _seed_installation(db, team_id=team_id, installation_id=7077)
    pr = _create_project(
        client, cookies_a, team_id=team_id, installation_id=7077
    )

    code = _issue_invite(client, cookies_a, team_id)
    _, cookies_b = _signup(client)
    _join_team(client, cookies_b, code)

    r = client.get(f"{API}/projects/{pr['id']}", cookies=cookies_b)
    assert r.status_code == 200, r.text
    assert r.json()["id"] == pr["id"]


# ---------------------------------------------------------------------------
# PATCH /projects/{id}
# ---------------------------------------------------------------------------


def test_patch_project_renames_admin(
    client: TestClient, db: Session
) -> None:
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "PatchHappy")
    _seed_installation(db, team_id=team_id, installation_id=8080)
    pr = _create_project(
        client, cookies, team_id=team_id, installation_id=8080, name="old"
    )

    r = client.patch(
        f"{API}/projects/{pr['id']}",
        json={"name": "new"},
        cookies=cookies,
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "new"


def test_patch_project_403_for_non_admin_member(
    client: TestClient, db: Session
) -> None:
    _, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "PatchNonAdm")
    _seed_installation(db, team_id=team_id, installation_id=8081)
    pr = _create_project(
        client, cookies_a, team_id=team_id, installation_id=8081
    )
    code = _issue_invite(client, cookies_a, team_id)
    _, cookies_b = _signup(client)
    _join_team(client, cookies_b, code)

    r = client.patch(
        f"{API}/projects/{pr['id']}",
        json={"name": "renamed"},
        cookies=cookies_b,
    )
    assert r.status_code == 403, r.text


def test_patch_project_409_on_duplicate_name(
    client: TestClient, db: Session
) -> None:
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "PatchDup")
    _seed_installation(db, team_id=team_id, installation_id=8082)
    _create_project(
        client, cookies, team_id=team_id, installation_id=8082, name="alpha"
    )
    pr2 = _create_project(
        client,
        cookies,
        team_id=team_id,
        installation_id=8082,
        name="beta",
        repo="acme/beta",
    )

    r = client.patch(
        f"{API}/projects/{pr2['id']}",
        json={"name": "alpha"},
        cookies=cookies,
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "project_name_taken"


# ---------------------------------------------------------------------------
# DELETE /projects/{id}
# ---------------------------------------------------------------------------


def test_delete_project_admin_204_and_cascades_push_rule(
    client: TestClient, db: Session
) -> None:
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "DelHappy")
    _seed_installation(db, team_id=team_id, installation_id=9090)
    pr = _create_project(
        client, cookies, team_id=team_id, installation_id=9090
    )

    r = client.delete(f"{API}/projects/{pr['id']}", cookies=cookies)
    assert r.status_code == 204, r.text

    # Cascade check: rule row gone.
    db.expire_all()
    rule_count = db.execute(
        text(
            "SELECT COUNT(*) FROM project_push_rules"
            " WHERE project_id = :pid"
        ),
        {"pid": pr["id"]},
    ).scalar_one()
    assert rule_count == 0


def test_delete_project_404_for_cross_team(
    client: TestClient, db: Session
) -> None:
    _, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "DelCross")
    _seed_installation(db, team_id=team_id, installation_id=9091)
    pr = _create_project(
        client, cookies_a, team_id=team_id, installation_id=9091
    )

    _, cookies_b = _signup(client)
    r = client.delete(f"{API}/projects/{pr['id']}", cookies=cookies_b)
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# GET /projects/{id}/push-rule
# ---------------------------------------------------------------------------


def test_get_push_rule_returns_default_after_create(
    client: TestClient, db: Session
) -> None:
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "PRGet")
    _seed_installation(db, team_id=team_id, installation_id=11011)
    pr = _create_project(
        client, cookies, team_id=team_id, installation_id=11011
    )

    r = client.get(f"{API}/projects/{pr['id']}/push-rule", cookies=cookies)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "manual_workflow"
    assert body["branch_pattern"] is None
    assert body["workflow_id"] is None


# ---------------------------------------------------------------------------
# PUT /projects/{id}/push-rule — three modes + validation
# ---------------------------------------------------------------------------


def test_put_push_rule_mode_auto_clears_extras(
    client: TestClient, db: Session
) -> None:
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "PRAuto")
    _seed_installation(db, team_id=team_id, installation_id=12012)
    pr = _create_project(
        client, cookies, team_id=team_id, installation_id=12012
    )

    # First set rule with extras → ensure switching to auto wipes them.
    r1 = client.put(
        f"{API}/projects/{pr['id']}/push-rule",
        json={"mode": "rule", "branch_pattern": "main"},
        cookies=cookies,
    )
    assert r1.status_code == 200, r1.text

    r2 = client.put(
        f"{API}/projects/{pr['id']}/push-rule",
        json={
            "mode": "auto",
            "branch_pattern": "should-be-ignored",
            "workflow_id": "wf-x",
        },
        cookies=cookies,
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["mode"] == "auto"
    assert body["branch_pattern"] is None
    assert body["workflow_id"] is None


def test_put_push_rule_mode_rule_persists_branch_pattern(
    client: TestClient, db: Session
) -> None:
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "PRRule")
    _seed_installation(db, team_id=team_id, installation_id=13013)
    pr = _create_project(
        client, cookies, team_id=team_id, installation_id=13013
    )

    r = client.put(
        f"{API}/projects/{pr['id']}/push-rule",
        json={"mode": "rule", "branch_pattern": "release/*"},
        cookies=cookies,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "rule"
    assert body["branch_pattern"] == "release/*"
    assert body["workflow_id"] is None


def test_put_push_rule_mode_manual_workflow_persists_workflow_id(
    client: TestClient, db: Session
) -> None:
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "PRManual")
    _seed_installation(db, team_id=team_id, installation_id=14014)
    pr = _create_project(
        client, cookies, team_id=team_id, installation_id=14014
    )

    r = client.put(
        f"{API}/projects/{pr['id']}/push-rule",
        json={"mode": "manual_workflow", "workflow_id": "deploy.yml"},
        cookies=cookies,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "manual_workflow"
    assert body["workflow_id"] == "deploy.yml"
    assert body["branch_pattern"] is None


def test_put_push_rule_unknown_mode_returns_422(
    client: TestClient, db: Session
) -> None:
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "PRBad")
    _seed_installation(db, team_id=team_id, installation_id=15015)
    pr = _create_project(
        client, cookies, team_id=team_id, installation_id=15015
    )

    r = client.put(
        f"{API}/projects/{pr['id']}/push-rule",
        json={"mode": "banana"},
        cookies=cookies,
    )
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["detail"] == "invalid_push_rule_mode"
    assert detail["mode"] == "banana"


def test_put_push_rule_mode_rule_missing_branch_pattern_returns_422(
    client: TestClient, db: Session
) -> None:
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "PRRuleMiss")
    _seed_installation(db, team_id=team_id, installation_id=16016)
    pr = _create_project(
        client, cookies, team_id=team_id, installation_id=16016
    )

    r = client.put(
        f"{API}/projects/{pr['id']}/push-rule",
        json={"mode": "rule"},
        cookies=cookies,
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["detail"] == "branch_pattern_required"


def test_put_push_rule_mode_manual_workflow_missing_workflow_id_returns_422(
    client: TestClient, db: Session
) -> None:
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "PRManMiss")
    _seed_installation(db, team_id=team_id, installation_id=17017)
    pr = _create_project(
        client, cookies, team_id=team_id, installation_id=17017
    )

    r = client.put(
        f"{API}/projects/{pr['id']}/push-rule",
        json={"mode": "manual_workflow"},
        cookies=cookies,
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["detail"] == "workflow_id_required"


def test_put_push_rule_403_for_non_admin_member(
    client: TestClient, db: Session
) -> None:
    _, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "PRNonAdm")
    _seed_installation(db, team_id=team_id, installation_id=18018)
    pr = _create_project(
        client, cookies_a, team_id=team_id, installation_id=18018
    )
    code = _issue_invite(client, cookies_a, team_id)
    _, cookies_b = _signup(client)
    _join_team(client, cookies_b, code)

    r = client.put(
        f"{API}/projects/{pr['id']}/push-rule",
        json={"mode": "auto"},
        cookies=cookies_b,
    )
    assert r.status_code == 403, r.text


def test_put_push_rule_logs_update(
    client: TestClient, db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "PRLog")
    _seed_installation(db, team_id=team_id, installation_id=19019)
    pr = _create_project(
        client, cookies, team_id=team_id, installation_id=19019
    )

    # See note in test_create_project_logs_project_created — alembic's
    # fileConfig(disable_existing_loggers=True) from migration tests can
    # silently disable this logger across the pytest session.
    logging.getLogger("app.api.routes.projects").disabled = False

    with caplog.at_level(logging.INFO, logger="app.api.routes.projects"):
        r = client.put(
            f"{API}/projects/{pr['id']}/push-rule",
            json={"mode": "auto"},
            cookies=cookies,
        )
        assert r.status_code == 200, r.text

    captured = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "project_push_rule_updated" in captured
    assert f"project_id={pr['id']}" in captured
    assert "mode=auto" in captured


# ---------------------------------------------------------------------------
# Auth absence
# ---------------------------------------------------------------------------


def test_endpoints_require_authentication(client: TestClient) -> None:
    """Smoke test: the router is mounted behind the cookie auth dep."""
    client.cookies.clear()
    bogus = uuid.uuid4()
    for path in (
        f"{API}/teams/{bogus}/projects",
        f"{API}/projects/{bogus}",
        f"{API}/projects/{bogus}/push-rule",
    ):
        r = client.get(path)
        assert r.status_code == 401, (path, r.status_code)


# ---------------------------------------------------------------------------
# PUT /push-rule hook transitions (M004/S04/T04)
# ---------------------------------------------------------------------------


# Mirror the _FakeAsyncClient pattern from test_projects_open.py — keep the
# helper module-local rather than promoting to conftest because the route's
# `httpx.AsyncClient` import path differs across modules under test.


class _FakeResponse:
    def __init__(
        self,
        status_code: int,
        json_body: object | None = None,
    ) -> None:
        self.status_code = status_code
        self._json = json_body
        self.request = httpx.Request("POST", "http://fake")

    def json(self) -> object:
        return self._json


class _FakeAsyncClient:
    """Stub for `httpx.AsyncClient` as imported by `app.api.routes.projects`.

    Records every POST so transition tests can assert which orchestrator
    hop the PUT /push-rule fired (or DIDN'T fire).
    """

    last_calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def __init__(self, route_map: dict[tuple[str, str], object]) -> None:
        self._routes = route_map

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def _resolve(self, method: str, url: str) -> object:
        for (m, suffix), handler in self._routes.items():
            if m == method and url.endswith(suffix):
                return handler
        # Default 200 for any unmatched route — keeps the test from caring
        # about ordering details when the focus is "did the hook fire?".
        return _FakeResponse(200, {"result": "installed"})

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        **_: object,
    ) -> _FakeResponse:
        type(self).last_calls.append(("POST", url, json))
        handler = self._resolve("POST", url)
        if isinstance(handler, Exception):
            raise handler
        assert isinstance(handler, _FakeResponse)
        return handler


def _install_fake_orch(
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[tuple[str, str], object] | None = None,
) -> type[_FakeAsyncClient]:
    import app.api.routes.projects as projects_mod

    _FakeAsyncClient.last_calls = []

    def _factory(*_args: object, **_kwargs: object) -> _FakeAsyncClient:
        return _FakeAsyncClient(routes or {})

    monkeypatch.setattr(projects_mod.httpx, "AsyncClient", _factory)
    return _FakeAsyncClient


def test_put_push_rule_transition_to_auto_installs_hook(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """manual_workflow → auto fires POST install-push-hook on the orchestrator."""
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "PRTransitAuto")
    _seed_installation(db, team_id=team_id, installation_id=20020)
    pr = _create_project(
        client, cookies, team_id=team_id, installation_id=20020
    )
    fake = _install_fake_orch(monkeypatch)

    # Default mode is manual_workflow; PUT to auto.
    r = client.put(
        f"{API}/projects/{pr['id']}/push-rule",
        json={"mode": "auto"},
        cookies=cookies,
    )
    assert r.status_code == 200, r.text

    posts = [c for c in fake.last_calls if c[0] == "POST"]
    install_calls = [
        c for c in posts if c[1].endswith(f"/v1/projects/{pr['id']}/install-push-hook")
    ]
    assert len(install_calls) == 1, posts
    body = install_calls[0][2]
    assert body == {"team_id": team_id}


def test_put_push_rule_transition_from_auto_uninstalls_hook(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """auto → rule fires POST uninstall-push-hook on the orchestrator."""
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "PRTransitOff")
    _seed_installation(db, team_id=team_id, installation_id=21021)
    pr = _create_project(
        client, cookies, team_id=team_id, installation_id=21021
    )

    # First flip to auto — fires install (which we'll reset on the next monkey).
    fake = _install_fake_orch(monkeypatch)
    r1 = client.put(
        f"{API}/projects/{pr['id']}/push-rule",
        json={"mode": "auto"},
        cookies=cookies,
    )
    assert r1.status_code == 200, r1.text

    # Reset call log + re-install fake (last_calls is class-level state).
    fake = _install_fake_orch(monkeypatch)
    r2 = client.put(
        f"{API}/projects/{pr['id']}/push-rule",
        json={"mode": "rule", "branch_pattern": "main"},
        cookies=cookies,
    )
    assert r2.status_code == 200, r2.text

    posts = [c for c in fake.last_calls if c[0] == "POST"]
    uninstall_calls = [
        c for c in posts
        if c[1].endswith(f"/v1/projects/{pr['id']}/uninstall-push-hook")
    ]
    assert len(uninstall_calls) == 1, posts
    body = uninstall_calls[0][2]
    assert body == {"team_id": team_id}


def test_put_push_rule_no_transition_does_not_call_orchestrator(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rule → manual_workflow does NOT call the hook endpoints (neither side is auto)."""
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "PRNoTransit")
    _seed_installation(db, team_id=team_id, installation_id=22022)
    pr = _create_project(
        client, cookies, team_id=team_id, installation_id=22022
    )

    # Set mode=rule first.
    r0 = client.put(
        f"{API}/projects/{pr['id']}/push-rule",
        json={"mode": "rule", "branch_pattern": "main"},
        cookies=cookies,
    )
    assert r0.status_code == 200, r0.text

    # Now flip to manual_workflow; reset the fake call log.
    fake = _install_fake_orch(monkeypatch)
    r1 = client.put(
        f"{API}/projects/{pr['id']}/push-rule",
        json={"mode": "manual_workflow", "workflow_id": "deploy.yml"},
        cookies=cookies,
    )
    assert r1.status_code == 200, r1.text

    posts = [c for c in fake.last_calls if c[0] == "POST"]
    assert posts == []


def test_put_push_rule_orch_unreachable_does_not_fail_put(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Orchestrator unreachable on hook-install → PUT still 200 + WARNING log.

    Per slice plan: the rule write is the source of truth; hook install
    failures are logged WARNING but do NOT fail the PUT (the next clone-
    to-mirror reconverges).
    """
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "PRUnreach")
    _seed_installation(db, team_id=team_id, installation_id=23023)
    pr = _create_project(
        client, cookies, team_id=team_id, installation_id=23023
    )

    # Stub the orchestrator hop so the install POST raises ConnectError.
    _install_fake_orch(
        monkeypatch,
        {
            ("POST", f"/v1/projects/{pr['id']}/install-push-hook"):
                httpx.ConnectError("connection refused"),
        },
    )

    logging.getLogger("app.api.routes.projects").disabled = False
    with caplog.at_level(logging.WARNING, logger="app.api.routes.projects"):
        r = client.put(
            f"{API}/projects/{pr['id']}/push-rule",
            json={"mode": "auto"},
            cookies=cookies,
        )

    assert r.status_code == 200, r.text
    captured = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "push_hook_orch_call_unreachable" in captured
    assert "install-push-hook" in captured


# ---------------------------------------------------------------------------
# notify() side-effect: project_created fans out to every team admin
# ---------------------------------------------------------------------------


def test_project_create_notifies_admins(
    client: TestClient, db: Session
) -> None:
    """Creating a project must INSERT a `project_created` notification for
    every admin on the team and zero rows for any non-admin member."""
    # Admin A creates the team. Admin A is automatically the team's first
    # admin. We then promote a second user (B) to admin and join a third
    # user (C) as a plain member.
    admin_a_id, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "ProjectNotify")

    # Promote B to admin via a second invite + role PATCH.
    code1 = _issue_invite(client, cookies_a, team_id)
    admin_b_id, cookies_b = _signup(client)
    _join_team(client, cookies_b, code1)

    promote = client.patch(
        f"{API}/teams/{team_id}/members/{admin_b_id}/role",
        json={"role": "admin"},
        cookies=cookies_a,
    )
    assert promote.status_code == 200, promote.text

    # Add C as plain member.
    code2 = _issue_invite(client, cookies_a, team_id)
    member_c_id, cookies_c = _signup(client)
    _join_team(client, cookies_c, code2)

    _seed_installation(db, team_id=team_id, installation_id=4040)
    body = _create_project(
        client, cookies_a, team_id=team_id, installation_id=4040
    )
    project_id = body["id"]

    db.expire_all()
    rows = db.exec(
        select(Notification).where(Notification.kind == "project_created")
    ).all()

    notified_user_ids = {row.user_id for row in rows}
    assert notified_user_ids == {
        uuid.UUID(admin_a_id),
        uuid.UUID(admin_b_id),
    }, "exactly the two admins must receive a project_created row"
    assert uuid.UUID(member_c_id) not in notified_user_ids

    # Payload + source columns are present and well-formed.
    one = next(iter(rows))
    assert one.payload.get("project_id") == project_id
    assert one.payload.get("project_name") == "widgets"
    assert one.payload.get("team_id") == team_id
    assert one.payload.get("repo") == "acme/widgets"
    assert one.source_team_id == uuid.UUID(team_id)
    assert one.source_project_id == uuid.UUID(project_id)
