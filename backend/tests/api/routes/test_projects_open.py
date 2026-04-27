"""Endpoint tests for POST /api/v1/projects/{id}/open (M004 / S04 / T03).

Real TestClient + real Postgres so the team-member auth gate, project
loading, and cross-team enumeration block all run for real. The
orchestrator hops are stubbed via the same `_FakeAsyncClient` pattern
used by test_github_install / test_sessions (MEM172/MEM184).

Coverage matrix:
  - happy path: ensure → materialize-mirror → materialize-user → 200
  - mirror-step 502 propagation
  - user-step 502 propagation
  - 503 from any orchestrator hop → orchestrator_unavailable
  - 404 project_not_found (cross-team caller)
  - 404 project_not_found (project missing)
  - idempotent second-open (both hops return reused)
  - request bodies are forwarded into the orchestrator with the project's
    repo_full_name + installation_id from the DB
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, delete

from app.core.config import settings
from app.models import GitHubAppInstallation, Project, ProjectPushRule
from tests.utils.utils import random_email, random_lower_string

API = settings.API_V1_STR
SIGNUP_URL = f"{API}/auth/signup"
TEAMS_URL = f"{API}/teams/"


# ---------------------------------------------------------------------------
# Test isolation — match test_projects.py
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_projects_state(db: Session):
    db.execute(delete(ProjectPushRule))
    db.execute(delete(Project))
    db.execute(delete(GitHubAppInstallation))
    db.commit()
    yield
    db.execute(delete(ProjectPushRule))
    db.execute(delete(Project))
    db.execute(delete(GitHubAppInstallation))
    db.commit()


# ---------------------------------------------------------------------------
# Helpers — minimal subset of test_projects.py helpers
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
    client: TestClient, cookies: httpx.Cookies, name: str = "OpenTeam"
) -> str:
    r = client.post(TEAMS_URL, json={"name": name}, cookies=cookies)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _seed_installation(
    db: Session, *, team_id: str, installation_id: int, login: str = "acme"
) -> None:
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
# _FakeAsyncClient — orchestrator stub, mirrors test_github_install (MEM184)
# ---------------------------------------------------------------------------


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

    Routes are matched by suffix (so the test doesn't have to bake in the
    full orchestrator base URL). Each entry is either a `_FakeResponse` or
    an Exception to raise on call.
    """

    last_calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def __init__(self, route_map: dict[tuple[str, str], object]) -> None:
        self._routes = route_map

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def _resolve(self, method: str, url: str) -> object:
        for (m, suffix), handler in self._routes.items():
            if m == method and url.endswith(suffix):
                return handler
        raise AssertionError(
            f"FakeAsyncClient: no route for {method} {url}; "
            f"have {list(self._routes.keys())}"
        )

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
    routes: dict[tuple[str, str], object],
) -> type[_FakeAsyncClient]:
    import app.api.routes.projects as projects_mod

    _FakeAsyncClient.last_calls = []

    def _factory(*_args: object, **_kwargs: object) -> _FakeAsyncClient:
        return _FakeAsyncClient(routes)

    monkeypatch.setattr(projects_mod.httpx, "AsyncClient", _factory)
    return _FakeAsyncClient


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_open_project_happy_path_chains_three_orch_hops(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ensure → materialize-mirror → materialize-user → 200, with bodies forwarded."""
    user_id, cookies = _signup(client)
    team_id = _create_team(client, cookies, "OpenHappy")
    _seed_installation(db, team_id=team_id, installation_id=42, login="acme")
    pr = _create_project(
        client, cookies, team_id=team_id, installation_id=42, name="widgets"
    )
    project_id = pr["id"]

    fake = _install_fake_orch(
        monkeypatch,
        {
            ("POST", f"/v1/teams/{team_id}/mirror/ensure"): _FakeResponse(
                200,
                {
                    "container_id": "mirrorabc1234567890",
                    "network_addr": "team-mirror-xxxx:9418",
                    "reused": False,
                },
            ),
            ("POST", f"/v1/projects/{project_id}/materialize-mirror"): _FakeResponse(
                200, {"result": "created", "duration_ms": 1500}
            ),
            ("POST", f"/v1/projects/{project_id}/materialize-user"): _FakeResponse(
                200,
                {
                    "result": "created",
                    "duration_ms": 350,
                    "workspace_path": f"/workspaces/{user_id}/{team_id}/widgets",
                },
            ),
        },
    )

    r = client.post(f"{API}/projects/{project_id}/open", cookies=cookies)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mirror_status"] == "created"
    assert body["user_status"] == "created"
    assert body["workspace_path"].endswith("/widgets")
    assert isinstance(body["duration_ms"], int)

    # Three POSTs in order, with the right bodies.
    posts = [c for c in fake.last_calls if c[0] == "POST"]
    assert len(posts) == 3, posts
    # ensure call has no body (we send nothing).
    assert posts[0][1].endswith(f"/v1/teams/{team_id}/mirror/ensure")
    # materialize-mirror body carries the project's repo + installation_id from DB.
    mirror_body = posts[1][2]
    assert mirror_body == {
        "team_id": team_id,
        "repo_full_name": "acme/widgets",
        "installation_id": 42,
    }
    # materialize-user body carries the calling user_id + project name.
    user_body = posts[2][2]
    assert user_body == {
        "user_id": user_id,
        "team_id": team_id,
        "project_name": "widgets",
    }


def test_open_project_logs_project_opened(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    user_id, cookies = _signup(client)
    team_id = _create_team(client, cookies, "OpenLog")
    _seed_installation(db, team_id=team_id, installation_id=43)
    pr = _create_project(
        client, cookies, team_id=team_id, installation_id=43, name="logged"
    )
    project_id = pr["id"]

    _install_fake_orch(
        monkeypatch,
        {
            ("POST", f"/v1/teams/{team_id}/mirror/ensure"): _FakeResponse(
                200,
                {
                    "container_id": "abc",
                    "network_addr": "team-mirror-x:9418",
                    "reused": True,
                },
            ),
            ("POST", f"/v1/projects/{project_id}/materialize-mirror"): _FakeResponse(
                200, {"result": "created", "duration_ms": 0}
            ),
            ("POST", f"/v1/projects/{project_id}/materialize-user"): _FakeResponse(
                200,
                {
                    "result": "created",
                    "duration_ms": 0,
                    "workspace_path": f"/workspaces/{user_id}/{team_id}/logged",
                },
            ),
        },
    )

    logging.getLogger("app.api.routes.projects").disabled = False
    with caplog.at_level(logging.INFO, logger="app.api.routes.projects"):
        r = client.post(f"{API}/projects/{project_id}/open", cookies=cookies)
    assert r.status_code == 200, r.text

    captured = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "project_opened" in captured
    assert f"project_id={project_id}" in captured
    assert f"user_id={user_id}" in captured


def test_open_project_idempotent_second_open_returns_reused(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second /open after a successful first one passes through cleanly.

    The orchestrator hops are themselves idempotent (mirror returns
    `reused`, user returns `reused`); the backend forwards both verbatim.
    """
    user_id, cookies = _signup(client)
    team_id = _create_team(client, cookies, "OpenReused")
    _seed_installation(db, team_id=team_id, installation_id=44)
    pr = _create_project(
        client, cookies, team_id=team_id, installation_id=44, name="reuse"
    )
    project_id = pr["id"]

    _install_fake_orch(
        monkeypatch,
        {
            ("POST", f"/v1/teams/{team_id}/mirror/ensure"): _FakeResponse(
                200,
                {
                    "container_id": "abc",
                    "network_addr": "team-mirror-x:9418",
                    "reused": True,
                },
            ),
            ("POST", f"/v1/projects/{project_id}/materialize-mirror"): _FakeResponse(
                200, {"result": "reused", "duration_ms": 0}
            ),
            ("POST", f"/v1/projects/{project_id}/materialize-user"): _FakeResponse(
                200,
                {
                    "result": "reused",
                    "duration_ms": 0,
                    "workspace_path": f"/workspaces/{user_id}/{team_id}/reuse",
                },
            ),
        },
    )

    # Two opens.
    r1 = client.post(f"{API}/projects/{project_id}/open", cookies=cookies)
    r2 = client.post(f"{API}/projects/{project_id}/open", cookies=cookies)
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r1.json()["user_status"] == "reused"
    assert r2.json()["user_status"] == "reused"


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------


def test_open_project_mirror_step_502_propagates(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 502 from the materialize-mirror hop surfaces as 502 to the user."""
    _user_id, cookies = _signup(client)
    team_id = _create_team(client, cookies, "OpenMirrorFail")
    _seed_installation(db, team_id=team_id, installation_id=45)
    pr = _create_project(
        client, cookies, team_id=team_id, installation_id=45, name="mfail"
    )
    project_id = pr["id"]

    _install_fake_orch(
        monkeypatch,
        {
            ("POST", f"/v1/teams/{team_id}/mirror/ensure"): _FakeResponse(
                200,
                {
                    "container_id": "abc",
                    "network_addr": "team-mirror-x:9418",
                    "reused": False,
                },
            ),
            ("POST", f"/v1/projects/{project_id}/materialize-mirror"): _FakeResponse(
                502,
                {
                    "detail": {
                        "detail": "github_clone_failed",
                        "status": 401,
                        "reason": "401:Unauthorized",
                    }
                },
            ),
        },
    )

    r = client.post(f"{API}/projects/{project_id}/open", cookies=cookies)
    assert r.status_code == 502, r.text
    body = r.json()
    # The orchestrator's `detail` payload is preserved on the user side
    # so the FE can branch on `reason`.
    detail = body["detail"]
    assert isinstance(detail, dict)
    assert detail["detail"] == "github_clone_failed"
    assert detail["reason"] == "401:Unauthorized"


def test_open_project_user_step_502_propagates(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 502 from the materialize-user hop surfaces as 502.

    The most common cause in steady-state would be a MEM264 regression —
    user container can't resolve `team-mirror-...:9418`, git clone exits
    128, orchestrator returns reason=user_clone_exit_128.
    """
    _user_id, cookies = _signup(client)
    team_id = _create_team(client, cookies, "OpenUserFail")
    _seed_installation(db, team_id=team_id, installation_id=46)
    pr = _create_project(
        client, cookies, team_id=team_id, installation_id=46, name="ufail"
    )
    project_id = pr["id"]

    _install_fake_orch(
        monkeypatch,
        {
            ("POST", f"/v1/teams/{team_id}/mirror/ensure"): _FakeResponse(
                200,
                {
                    "container_id": "abc",
                    "network_addr": "team-mirror-x:9418",
                    "reused": True,
                },
            ),
            ("POST", f"/v1/projects/{project_id}/materialize-mirror"): _FakeResponse(
                200, {"result": "created", "duration_ms": 0}
            ),
            ("POST", f"/v1/projects/{project_id}/materialize-user"): _FakeResponse(
                502,
                {
                    "detail": {
                        "detail": "user_clone_failed",
                        "status": 128,
                        "reason": "user_clone_exit_128",
                    }
                },
            ),
        },
    )

    r = client.post(f"{API}/projects/{project_id}/open", cookies=cookies)
    assert r.status_code == 502, r.text
    detail = r.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["detail"] == "user_clone_failed"
    assert detail["reason"] == "user_clone_exit_128"


def test_open_project_orchestrator_503_returns_orchestrator_unavailable(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 503 from the ensure hop surfaces as orchestrator_unavailable."""
    _user_id, cookies = _signup(client)
    team_id = _create_team(client, cookies, "Open503")
    _seed_installation(db, team_id=team_id, installation_id=47)
    pr = _create_project(
        client, cookies, team_id=team_id, installation_id=47, name="o503"
    )
    project_id = pr["id"]

    _install_fake_orch(
        monkeypatch,
        {
            ("POST", f"/v1/teams/{team_id}/mirror/ensure"): _FakeResponse(
                503, {"detail": "docker_unavailable"}
            ),
        },
    )

    r = client.post(f"{API}/projects/{project_id}/open", cookies=cookies)
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == "orchestrator_unavailable"


def test_open_project_orchestrator_connect_error_returns_503(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An httpx ConnectError surfaces as 503 orchestrator_unavailable."""
    _user_id, cookies = _signup(client)
    team_id = _create_team(client, cookies, "OpenConnErr")
    _seed_installation(db, team_id=team_id, installation_id=48)
    pr = _create_project(
        client, cookies, team_id=team_id, installation_id=48, name="connerr"
    )
    project_id = pr["id"]

    _install_fake_orch(
        monkeypatch,
        {
            ("POST", f"/v1/teams/{team_id}/mirror/ensure"): httpx.ConnectError(
                "connection refused"
            ),
        },
    )

    r = client.post(f"{API}/projects/{project_id}/open", cookies=cookies)
    assert r.status_code == 503, r.text
    assert r.json()["detail"] == "orchestrator_unavailable"


# ---------------------------------------------------------------------------
# AuthZ — cross-team + missing project
# ---------------------------------------------------------------------------


def test_open_project_404_when_project_missing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _user_id, cookies = _signup(client)
    bogus = uuid.uuid4()

    # Even though the orchestrator is fake, /open must short-circuit with
    # 404 BEFORE calling the orchestrator. So no routes registered.
    _install_fake_orch(monkeypatch, {})

    r = client.post(f"{API}/projects/{bogus}/open", cookies=cookies)
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "project_not_found"


def test_open_project_404_for_cross_team_caller(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user who is not a member of the project's team gets 404, not 403."""
    _, cookies_a = _signup(client)
    team_a = _create_team(client, cookies_a, "OpenCross")
    _seed_installation(db, team_id=team_a, installation_id=49)
    pr = _create_project(
        client, cookies_a, team_id=team_a, installation_id=49, name="cross"
    )

    _, cookies_b = _signup(client)
    _install_fake_orch(monkeypatch, {})  # never called

    r = client.post(f"{API}/projects/{pr['id']}/open", cookies=cookies_b)
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "project_not_found"


def test_open_project_requires_authentication(
    client: TestClient,
) -> None:
    """No cookie → 401 (cookie-auth dep)."""
    client.cookies.clear()
    bogus = uuid.uuid4()
    r = client.post(f"{API}/projects/{bogus}/open")
    assert r.status_code == 401, r.text
