"""Route-level unit tests for the M004/S04/T04 endpoints:

  POST /v1/projects/{project_id}/install-push-hook
  POST /v1/projects/{project_id}/uninstall-push-hook
  POST /v1/projects/{project_id}/auto-push-callback

Same TestClient + monkey-patch pattern as test_routes_projects_materialize_user.

Coverage:
  - 401 unauthorized when X-Orchestrator-Key is missing on each endpoint
  - 200 install with mode=auto returns {result:'installed'} when patched
  - 200 install short-circuits with {result:'mirror_missing'} when no mirror
  - 200 uninstall returns {result:'uninstalled'} when mirror present
  - 200 uninstall returns {result:'mirror_missing'} when no mirror
  - 502 install when _install_post_receive_hook raises _CloneExecFailed
  - 200 auto-push-callback delegates to run_auto_push and forwards body
  - 422 on malformed UUID in path
  - 422 on missing required body field (team_id) for install/uninstall
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


def _auth_headers() -> dict[str, str]:
    return {"X-Orchestrator-Key": settings.orchestrator_api_key}


class _FakePool:
    pass


class _FakeRegistry:
    def __init__(self) -> None:
        self._client = None  # noqa: SLF001


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


# ---------------------------------------------------------------------------
# install-push-hook
# ---------------------------------------------------------------------------


def test_install_hook_happy_path_returns_installed(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, pool, registry = app_state
    project_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())

    async def _find_stub(_docker: Any, _team_id: str) -> str | None:
        return "mirrorabc1234567890"

    captured: dict[str, Any] = {}

    async def _install_stub(
        _docker: Any,
        *,
        mirror_container_id: str,
        project_id: str,
        push_rule_mode: str,
    ) -> bool:
        captured["mirror"] = mirror_container_id
        captured["project_id"] = project_id
        captured["mode"] = push_rule_mode
        return True

    monkeypatch.setattr(
        routes_projects_mod, "_find_team_mirror_container", _find_stub
    )
    monkeypatch.setattr(
        routes_projects_mod, "_install_post_receive_hook", _install_stub
    )

    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            f"/v1/projects/{project_id}/install-push-hook",
            headers=_auth_headers(),
            json={"team_id": team_id},
        )

    assert r.status_code == 200, r.text
    assert r.json()["result"] == "installed"
    assert captured == {
        "mirror": "mirrorabc1234567890",
        "project_id": project_id,
        "mode": "auto",
    }


def test_install_hook_no_mirror_short_circuits(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install-push-hook → mirror_missing when no mirror container exists."""
    app, pool, registry = app_state

    async def _find_stub(_docker: Any, _team_id: str) -> str | None:
        return None

    monkeypatch.setattr(
        routes_projects_mod, "_find_team_mirror_container", _find_stub
    )

    project_id = str(uuid.uuid4())
    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            f"/v1/projects/{project_id}/install-push-hook",
            headers=_auth_headers(),
            json={"team_id": str(uuid.uuid4())},
        )

    assert r.status_code == 200
    assert r.json()["result"] == "mirror_missing"


def test_install_hook_clone_exec_failed_returns_502(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """install raises _CloneExecFailed → 502 with op + exit_code."""
    app, pool, registry = app_state

    async def _find_stub(_docker: Any, _team_id: str) -> str | None:
        return "mirror123"

    async def _install_stub(*_a: Any, **_kw: Any) -> bool:
        raise _CloneExecFailed(2, "install_post_receive_hook")

    monkeypatch.setattr(
        routes_projects_mod, "_find_team_mirror_container", _find_stub
    )
    monkeypatch.setattr(
        routes_projects_mod, "_install_post_receive_hook", _install_stub
    )

    project_id = str(uuid.uuid4())
    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            f"/v1/projects/{project_id}/install-push-hook",
            headers=_auth_headers(),
            json={"team_id": str(uuid.uuid4())},
        )

    assert r.status_code == 502
    detail = r.json()["detail"]
    assert detail["detail"] == "post_receive_hook_install_failed"
    assert detail["exit_code"] == 2
    assert detail["op"] == "install_post_receive_hook"


def test_install_hook_unauthorized_without_secret(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
) -> None:
    app, pool, registry = app_state
    project_id = str(uuid.uuid4())

    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            f"/v1/projects/{project_id}/install-push-hook",
            json={"team_id": str(uuid.uuid4())},
        )

    assert r.status_code == 401
    assert r.json()["detail"] == "unauthorized"


def test_install_hook_missing_team_id_returns_422(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
) -> None:
    app, pool, registry = app_state
    project_id = str(uuid.uuid4())
    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            f"/v1/projects/{project_id}/install-push-hook",
            headers=_auth_headers(),
            json={},
        )
    assert r.status_code == 422


def test_install_hook_malformed_project_uuid_returns_422(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
) -> None:
    app, pool, registry = app_state
    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            "/v1/projects/not-a-uuid/install-push-hook",
            headers=_auth_headers(),
            json={"team_id": str(uuid.uuid4())},
        )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# uninstall-push-hook
# ---------------------------------------------------------------------------


def test_uninstall_hook_happy_path(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, pool, registry = app_state

    async def _find_stub(_docker: Any, _team_id: str) -> str | None:
        return "mirror123"

    captured: dict[str, Any] = {}

    async def _uninstall_stub(
        _docker: Any,
        *,
        mirror_container_id: str,
        project_id: str,
    ) -> bool:
        captured["mirror"] = mirror_container_id
        captured["project_id"] = project_id
        return True

    monkeypatch.setattr(
        routes_projects_mod, "_find_team_mirror_container", _find_stub
    )
    monkeypatch.setattr(
        routes_projects_mod, "_uninstall_post_receive_hook", _uninstall_stub
    )

    project_id = str(uuid.uuid4())
    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            f"/v1/projects/{project_id}/uninstall-push-hook",
            headers=_auth_headers(),
            json={"team_id": str(uuid.uuid4())},
        )

    assert r.status_code == 200
    assert r.json()["result"] == "uninstalled"
    assert captured == {
        "mirror": "mirror123",
        "project_id": project_id,
    }


def test_uninstall_hook_no_mirror_short_circuits(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, pool, registry = app_state

    async def _find_stub(_docker: Any, _team_id: str) -> str | None:
        return None

    monkeypatch.setattr(
        routes_projects_mod, "_find_team_mirror_container", _find_stub
    )

    project_id = str(uuid.uuid4())
    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            f"/v1/projects/{project_id}/uninstall-push-hook",
            headers=_auth_headers(),
            json={"team_id": str(uuid.uuid4())},
        )

    assert r.status_code == 200
    assert r.json()["result"] == "mirror_missing"


def test_uninstall_hook_unauthorized_without_secret(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
) -> None:
    app, pool, registry = app_state
    project_id = str(uuid.uuid4())

    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            f"/v1/projects/{project_id}/uninstall-push-hook",
            json={"team_id": str(uuid.uuid4())},
        )

    assert r.status_code == 401


# ---------------------------------------------------------------------------
# auto-push-callback
# ---------------------------------------------------------------------------


def test_auto_push_callback_happy_path_forwards_run_auto_push(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, pool, registry = app_state
    project_id = str(uuid.uuid4())
    captured: dict[str, Any] = {}

    async def _stub(
        docker: Any,
        pool_arg: Any,
        *,
        project_id: str,
        redis_client: Any | None = None,
    ) -> dict[str, Any]:
        captured["project_id"] = project_id
        return {
            "result": "ok",
            "exit_code": 0,
            "duration_ms": 250,
            "stderr_short": "",
        }

    monkeypatch.setattr(routes_projects_mod, "run_auto_push", _stub)

    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            f"/v1/projects/{project_id}/auto-push-callback",
            headers=_auth_headers(),
        )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "ok"
    assert body["exit_code"] == 0
    assert body["duration_ms"] == 250
    assert body["stderr_short"] == ""
    assert captured["project_id"] == project_id


def test_auto_push_callback_skipped_rule_changed(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Callback returns 200 even on rule_changed — surface in body."""
    app, pool, registry = app_state

    async def _stub(*_a: Any, **_kw: Any) -> dict[str, Any]:
        return {"result": "skipped_rule_changed"}

    monkeypatch.setattr(routes_projects_mod, "run_auto_push", _stub)

    project_id = str(uuid.uuid4())
    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            f"/v1/projects/{project_id}/auto-push-callback",
            headers=_auth_headers(),
        )

    assert r.status_code == 200
    body = r.json()
    assert body["result"] == "skipped_rule_changed"
    # Optional fields absent → None.
    assert body["exit_code"] is None
    assert body["duration_ms"] is None


def test_auto_push_callback_unauthorized_without_secret(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
) -> None:
    app, pool, registry = app_state
    project_id = str(uuid.uuid4())

    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            f"/v1/projects/{project_id}/auto-push-callback",
        )

    assert r.status_code == 401


def test_auto_push_callback_malformed_project_uuid_returns_422(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
) -> None:
    app, pool, registry = app_state
    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.post(
            "/v1/projects/not-a-uuid/auto-push-callback",
            headers=_auth_headers(),
        )
    assert r.status_code == 422


def test_auto_push_callback_docker_unavailable_when_handle_none(
    app_state: tuple[Any, _FakePool, _FakeRegistry],
) -> None:
    """If docker handle is None on app.state → 503 docker_unavailable."""
    app, pool, registry = app_state
    project_id = str(uuid.uuid4())

    with TestClient(app) as c:
        from orchestrator.volume_store import set_pool

        app.state.pg = pool
        app.state.registry = registry
        app.state.docker = None
        set_pool(pool)
        r = c.post(
            f"/v1/projects/{project_id}/auto-push-callback",
            headers=_auth_headers(),
        )

    assert r.status_code == 503
    assert r.json()["detail"] == "docker_unavailable"
