"""Route-level unit tests for POST /v1/projects/{id}/materialize-mirror.

Mirrors the test_github_tokens route-surface pattern:
  - TestClient lifespan
  - app.state pinned with _FakePool / _FakeRegistry AFTER lifespan startup
  - clone_to_mirror is monkey-patched at the module-import path used inside
    routes_projects (``orchestrator.routes_projects.clone_to_mirror``) so
    tests don't need to spin up a fake Docker

Coverage:
  - 200 happy path returns {"result": "created", "duration_ms": <int>}
  - 200 reused path returns {"result": "reused", "duration_ms": 0}
  - 502 github_clone_failed when InstallationTokenMintFailed bubbles up
  - 502 github_clone_failed when _CloneExecFailed bubbles up
    (with reason=git_clone_exit_<code>)
  - 500 clone_credential_leak when CloneCredentialLeakDetected bubbles up
  - 503 docker_unavailable when docker handle is None on app.state
  - 401 unauthorized when X-Orchestrator-Key is missing (shared-secret middleware)
  - 422 on malformed UUID in the path
  - 422 on missing required body fields (team_id, repo_full_name, installation_id)
  - request body is forwarded into clone_to_mirror as-is (team_id, repo, install_id)
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
from orchestrator.github_tokens import (  # noqa: E402
    InstallationTokenMintFailed,
)


def _auth_headers() -> dict[str, str]:
    return {"X-Orchestrator-Key": settings.orchestrator_api_key}


class _FakePool:
    """Minimal pool the route doesn't actually use (we patch clone_to_mirror)."""

    def __init__(self) -> None:
        pass


class _FakeRegistry:
    """Stand-in registry — exposes a `_client` (None) like the real one."""

    def __init__(self) -> None:
        self._client = None  # noqa: SLF001 — mirrors production registry shape


class _FakeDockerHandle:
    """Truthy stand-in so the route's `if docker is None` short-circuit
    doesn't fire. The patched clone_to_mirror never touches it."""


@pytest.fixture
def app_state() -> Iterator[tuple[Any, _FakePool, _FakeRegistry]]:
    """Yield (app, pool, registry).

    The TestClient consumer pins state inside the `with TestClient(app)`
    block — the lifespan otherwise overwrites both during startup.
    """
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
    # The route calls get_pool() which reads the module-level singleton —
    # pin the fake there too so the route doesn't 503 on pg_pool_unset.
    set_pool(pool)


# ---------------------------------------------------------------------------
# 200 happy paths
# ---------------------------------------------------------------------------


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
        team_id: str,
        project_id: str,
        repo_full_name: str,
        installation_id: int,
        redis_client: Any | None = None,
    ) -> dict[str, Any]:
        captured["team_id"] = team_id
        captured["project_id"] = project_id
        captured["repo_full_name"] = repo_full_name
        captured["installation_id"] = installation_id
        return {"result": "created", "duration_ms": 1234}

    monkeypatch.setattr(routes_projects_mod, "clone_to_mirror", _stub)

    project_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())
    body = {
        "team_id": team_id,
        "repo_full_name": "owner/repo",
        "installation_id": 42,
    }

    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            f"/v1/projects/{project_id}/materialize-mirror",
            headers=_auth_headers(),
            json=body,
        )

    assert r.status_code == 200, r.text
    assert r.json() == {"result": "created", "duration_ms": 1234}
    # Body was forwarded into the call.
    assert captured["team_id"] == team_id
    assert captured["project_id"] == project_id
    assert captured["repo_full_name"] == "owner/repo"
    assert captured["installation_id"] == 42


def test_route_reused_path_returns_zero_duration(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, pool, registry = app_state

    async def _stub(*_a: Any, **_kw: Any) -> dict[str, Any]:
        return {"result": "reused", "duration_ms": 0}

    monkeypatch.setattr(routes_projects_mod, "clone_to_mirror", _stub)

    project_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())
    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            f"/v1/projects/{project_id}/materialize-mirror",
            headers=_auth_headers(),
            json={
                "team_id": team_id,
                "repo_full_name": "owner/repo",
                "installation_id": 42,
            },
        )

    assert r.status_code == 200
    assert r.json() == {"result": "reused", "duration_ms": 0}


# ---------------------------------------------------------------------------
# Error mappings
# ---------------------------------------------------------------------------


def test_route_token_mint_failed_returns_502(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, pool, registry = app_state

    async def _stub(*_a: Any, **_kw: Any) -> dict[str, Any]:
        raise InstallationTokenMintFailed(404, "404:Not Found")

    monkeypatch.setattr(routes_projects_mod, "clone_to_mirror", _stub)

    project_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())
    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            f"/v1/projects/{project_id}/materialize-mirror",
            headers=_auth_headers(),
            json={
                "team_id": team_id,
                "repo_full_name": "owner/repo",
                "installation_id": 42,
            },
        )

    assert r.status_code == 502
    detail = r.json()["detail"]
    assert detail["detail"] == "github_clone_failed"
    assert detail["status"] == 404
    assert detail["reason"] == "404:Not Found"


def test_route_git_clone_exit_non_zero_returns_502(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A _CloneExecFailed (git clone exit 128) maps to 502 with reason."""
    app, pool, registry = app_state

    async def _stub(*_a: Any, **_kw: Any) -> dict[str, Any]:
        raise _CloneExecFailed(128, "git_clone")

    monkeypatch.setattr(routes_projects_mod, "clone_to_mirror", _stub)

    project_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())
    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            f"/v1/projects/{project_id}/materialize-mirror",
            headers=_auth_headers(),
            json={
                "team_id": team_id,
                "repo_full_name": "owner/repo",
                "installation_id": 42,
            },
        )

    assert r.status_code == 502
    detail = r.json()["detail"]
    assert detail["detail"] == "github_clone_failed"
    assert detail["reason"] == "git_clone_exit_128"


def test_route_credential_leak_returns_500(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, pool, registry = app_state
    project_id = str(uuid.uuid4())

    async def _stub(*_a: Any, **_kw: Any) -> dict[str, Any]:
        raise CloneCredentialLeakDetected(project_id)

    monkeypatch.setattr(routes_projects_mod, "clone_to_mirror", _stub)

    team_id = str(uuid.uuid4())
    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            f"/v1/projects/{project_id}/materialize-mirror",
            headers=_auth_headers(),
            json={
                "team_id": team_id,
                "repo_full_name": "owner/repo",
                "installation_id": 42,
            },
        )

    assert r.status_code == 500
    detail = r.json()["detail"]
    assert detail["detail"] == "clone_credential_leak"
    assert detail["project_id"] == project_id


def test_route_docker_handle_none_returns_503(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
) -> None:
    """When the lifespan ran with SKIP_IMAGE_PULL_ON_BOOT=1 (docker is None),
    the clone path can't function — 503 is the correct surface."""
    app, pool, registry = app_state

    project_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())
    with TestClient(app) as c:
        # Override docker to None explicitly.
        from orchestrator.volume_store import set_pool

        app.state.pg = pool
        app.state.registry = registry
        app.state.docker = None
        set_pool(pool)
        r = c.post(
            f"/v1/projects/{project_id}/materialize-mirror",
            headers=_auth_headers(),
            json={
                "team_id": team_id,
                "repo_full_name": "owner/repo",
                "installation_id": 42,
            },
        )

    assert r.status_code == 503
    assert r.json()["detail"] == "docker_unavailable"


# ---------------------------------------------------------------------------
# Auth + validation
# ---------------------------------------------------------------------------


def test_route_unauthorized_without_shared_secret(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
) -> None:
    """SharedSecretMiddleware coverage: no key → 401 unauthorized."""
    app, pool, registry = app_state

    project_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())
    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            f"/v1/projects/{project_id}/materialize-mirror",
            json={
                "team_id": team_id,
                "repo_full_name": "owner/repo",
                "installation_id": 42,
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
            "/v1/projects/not-a-uuid/materialize-mirror",
            headers=_auth_headers(),
            json={
                "team_id": str(uuid.uuid4()),
                "repo_full_name": "owner/repo",
                "installation_id": 42,
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
            f"/v1/projects/{project_id}/materialize-mirror",
            headers=_auth_headers(),
            json={"team_id": str(uuid.uuid4())},  # missing repo_full_name + install_id
        )

    assert r.status_code == 422


def test_route_invalid_installation_id_zero_returns_422(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
) -> None:
    """installation_id must be >= 1 — Pydantic enforces."""
    app, pool, registry = app_state
    project_id = str(uuid.uuid4())

    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            f"/v1/projects/{project_id}/materialize-mirror",
            headers=_auth_headers(),
            json={
                "team_id": str(uuid.uuid4()),
                "repo_full_name": "owner/repo",
                "installation_id": 0,
            },
        )

    assert r.status_code == 422
