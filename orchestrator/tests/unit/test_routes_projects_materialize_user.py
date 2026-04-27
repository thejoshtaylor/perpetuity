"""Route-level unit tests for POST /v1/projects/{id}/materialize-user.

Mirrors the test_routes_projects_materialize_mirror pattern:
  - TestClient lifespan
  - app.state pinned with _FakePool / _FakeRegistry AFTER lifespan startup
  - clone_to_user_workspace is monkey-patched at the module-import path used
    inside routes_projects (`orchestrator.routes_projects.clone_to_user_workspace`)
    so tests don't need to spin up a fake Docker

Coverage:
  - 200 happy path returns {result, duration_ms, workspace_path}
  - 200 reused path returns {result:'reused', duration_ms:0, workspace_path}
  - 502 user_clone_failed when _CloneExecFailed bubbles up (with reason)
  - 500 clone_credential_leak when CloneCredentialLeakDetected bubbles up
  - 503 docker_unavailable when docker handle is None on app.state
  - 401 unauthorized when X-Orchestrator-Key is missing
  - 422 on malformed UUID in path
  - 422 on missing required body fields
  - request body forwarded into clone_to_user_workspace verbatim
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Iterator

# SKIP boot-time side effects before importing orchestrator modules.
os.environ.setdefault("SKIP_IMAGE_PULL_ON_BOOT", "1")
os.environ.setdefault("SKIP_PG_POOL_ON_BOOT", "1")
os.environ.setdefault("ORCHESTRATOR_API_KEY", "unit-test-current-key")
os.environ.setdefault(
    "SYSTEM_SETTINGS_ENCRYPTION_KEY",
    "kfk5l7mPRFpBV7PzWJxYmO6LRRQAdZ4iGYZRG6xL0fY=",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from orchestrator import routes_projects as routes_projects_mod  # noqa: E402
from orchestrator.clone import _CloneExecFailed  # noqa: E402
from orchestrator.config import settings  # noqa: E402
from orchestrator.errors import (  # noqa: E402
    CloneCredentialLeakDetected,
)


def _auth_headers() -> dict[str, str]:
    return {"X-Orchestrator-Key": settings.orchestrator_api_key}


class _FakePool:
    pass


class _FakeRegistry:
    def __init__(self) -> None:
        self._client = None  # noqa: SLF001 — mirrors production registry shape


class _FakeDockerHandle:
    """Truthy stand-in so the route's `if docker is None` short-circuit
    doesn't fire."""


@pytest.fixture
def app_state() -> Iterator[tuple[Any, _FakePool, _FakeRegistry]]:
    from orchestrator.main import app

    yield app, _FakePool(), _FakeRegistry()


def _install_state(
    app: Any,
    pool: _FakePool,
    registry: _FakeRegistry,
    *,
    docker: Any | None = None,
) -> None:
    from orchestrator.volume_store import set_pool

    app.state.pg = pool
    app.state.registry = registry
    app.state.docker = docker if docker is not None else _FakeDockerHandle()
    set_pool(pool)


def test_route_happy_path_returns_created(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, pool, registry = app_state
    captured: dict[str, Any] = {}

    async def _stub(
        docker: Any,
        pool_arg: Any,
        *,
        user_id: str,
        team_id: str,
        project_id: str,
        project_name: str,
    ) -> dict[str, Any]:
        captured["user_id"] = user_id
        captured["team_id"] = team_id
        captured["project_id"] = project_id
        captured["project_name"] = project_name
        return {
            "result": "created",
            "duration_ms": 999,
            "workspace_path": f"/workspaces/{user_id}/{team_id}/{project_name}",
        }

    monkeypatch.setattr(
        routes_projects_mod, "clone_to_user_workspace", _stub
    )

    project_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())
    body = {
        "user_id": user_id,
        "team_id": team_id,
        "project_name": "widgets",
    }

    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            f"/v1/projects/{project_id}/materialize-user",
            headers=_auth_headers(),
            json=body,
        )

    assert r.status_code == 200, r.text
    js = r.json()
    assert js["result"] == "created"
    assert js["duration_ms"] == 999
    assert js["workspace_path"].endswith("/widgets")
    # Body fields forwarded verbatim.
    assert captured == {
        "user_id": user_id,
        "team_id": team_id,
        "project_id": project_id,
        "project_name": "widgets",
    }


def test_route_reused_path_returns_zero_duration(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, pool, registry = app_state

    async def _stub(*_a: Any, **_kw: Any) -> dict[str, Any]:
        return {
            "result": "reused",
            "duration_ms": 0,
            "workspace_path": "/workspaces/u/t/widgets",
        }

    monkeypatch.setattr(
        routes_projects_mod, "clone_to_user_workspace", _stub
    )

    project_id = str(uuid.uuid4())
    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            f"/v1/projects/{project_id}/materialize-user",
            headers=_auth_headers(),
            json={
                "user_id": str(uuid.uuid4()),
                "team_id": str(uuid.uuid4()),
                "project_name": "widgets",
            },
        )

    assert r.status_code == 200
    js = r.json()
    assert js["result"] == "reused"
    assert js["duration_ms"] == 0


def test_route_user_clone_exit_non_zero_returns_502(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A _CloneExecFailed (git clone exit 128) → 502 user_clone_failed."""
    app, pool, registry = app_state

    async def _stub(*_a: Any, **_kw: Any) -> dict[str, Any]:
        raise _CloneExecFailed(128, "user_git_clone")

    monkeypatch.setattr(
        routes_projects_mod, "clone_to_user_workspace", _stub
    )

    project_id = str(uuid.uuid4())
    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            f"/v1/projects/{project_id}/materialize-user",
            headers=_auth_headers(),
            json={
                "user_id": str(uuid.uuid4()),
                "team_id": str(uuid.uuid4()),
                "project_name": "widgets",
            },
        )

    assert r.status_code == 502
    detail = r.json()["detail"]
    assert detail["detail"] == "user_clone_failed"
    assert detail["reason"] == "user_clone_exit_128"
    assert detail["status"] == 128


def test_route_credential_leak_returns_500(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, pool, registry = app_state
    project_id = str(uuid.uuid4())

    async def _stub(*_a: Any, **_kw: Any) -> dict[str, Any]:
        raise CloneCredentialLeakDetected(project_id)

    monkeypatch.setattr(
        routes_projects_mod, "clone_to_user_workspace", _stub
    )

    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            f"/v1/projects/{project_id}/materialize-user",
            headers=_auth_headers(),
            json={
                "user_id": str(uuid.uuid4()),
                "team_id": str(uuid.uuid4()),
                "project_name": "widgets",
            },
        )

    assert r.status_code == 500
    detail = r.json()["detail"]
    assert detail["detail"] == "clone_credential_leak"
    assert detail["project_id"] == project_id


def test_route_docker_handle_none_returns_503(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
) -> None:
    """When docker handle is None (SKIP_IMAGE_PULL_ON_BOOT path) → 503."""
    app, pool, registry = app_state

    project_id = str(uuid.uuid4())
    with TestClient(app) as c:
        from orchestrator.volume_store import set_pool

        app.state.pg = pool
        app.state.registry = registry
        app.state.docker = None
        set_pool(pool)
        r = c.post(
            f"/v1/projects/{project_id}/materialize-user",
            headers=_auth_headers(),
            json={
                "user_id": str(uuid.uuid4()),
                "team_id": str(uuid.uuid4()),
                "project_name": "widgets",
            },
        )

    assert r.status_code == 503
    assert r.json()["detail"] == "docker_unavailable"


def test_route_unauthorized_without_shared_secret(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
) -> None:
    app, pool, registry = app_state
    project_id = str(uuid.uuid4())

    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            f"/v1/projects/{project_id}/materialize-user",
            json={
                "user_id": str(uuid.uuid4()),
                "team_id": str(uuid.uuid4()),
                "project_name": "widgets",
            },
        )

    assert r.status_code == 401
    assert r.json()["detail"] == "unauthorized"


def test_route_malformed_project_uuid_returns_422(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
) -> None:
    app, pool, registry = app_state

    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            "/v1/projects/not-a-uuid/materialize-user",
            headers=_auth_headers(),
            json={
                "user_id": str(uuid.uuid4()),
                "team_id": str(uuid.uuid4()),
                "project_name": "widgets",
            },
        )

    assert r.status_code == 422


def test_route_missing_body_fields_returns_422(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
) -> None:
    """Missing required body fields → pydantic 422."""
    app, pool, registry = app_state
    project_id = str(uuid.uuid4())

    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            f"/v1/projects/{project_id}/materialize-user",
            headers=_auth_headers(),
            json={"user_id": str(uuid.uuid4())},  # missing team_id + project_name
        )

    assert r.status_code == 422


def test_route_empty_project_name_returns_422(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
) -> None:
    """project_name min_length=1 enforced by Pydantic."""
    app, pool, registry = app_state
    project_id = str(uuid.uuid4())

    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            f"/v1/projects/{project_id}/materialize-user",
            headers=_auth_headers(),
            json={
                "user_id": str(uuid.uuid4()),
                "team_id": str(uuid.uuid4()),
                "project_name": "",
            },
        )

    assert r.status_code == 422
