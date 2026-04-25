"""Integration tests for the M002/T05 backend sessions router.

These tests boot a real orchestrator container (via the same pattern as the
orchestrator's `tests/integration/test_ws_bridge.py`) and exercise the
backend sessions router end-to-end against the live compose network. There
are no mocks — the slice plan demands real-orchestrator coverage so we
prove the backend ↔ orchestrator wire shape doesn't drift.

Tests skip cleanly when:
  - `SKIP_INTEGRATION=1` is set, OR
  - the docker socket is not reachable, OR
  - the `perpetuity_default` compose network is not present, OR
  - the redis service in that network is not reachable.

Verification matrix (T05 plan):
  (a) signed-in user A POST /sessions with their personal team → 200
  (b) cookie missing → 401
  (c) cookie valid but team_id is a team A is NOT a member of → 403
  (d) GET /sessions returns A's session
  (e) WS /ws/terminal/<sid> no cookie → close(1008, 'missing_cookie')
  (f) WS valid cookie + own sid → first frame `attach`; echo round-trip
  (g) WS user B's cookie attaching to A's sid → close(1008, 'session_not_owned')
  (h) WS attaching to never-existed sid → close(1008, 'session_not_owned')
  (i) DELETE /sessions/<sid> as owner → 200; subsequent WS attach → 1008
      'session_not_owned' (record gone, identical close shape)
  (j) Orchestrator stopped mid-test → POST 503; restart → 200; WS while down
      → close(1011, 'orchestrator_unavailable')
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from starlette.websockets import WebSocketDisconnect

from app import crud
from app.core.config import settings
from tests.utils.user import login_cookie_headers
from tests.utils.utils import random_email, random_lower_string

ORCH_IMAGE = "orchestrator:latest"
WORKSPACE_IMAGE = "perpetuity/workspace:test"
NETWORK = "perpetuity_default"
API_KEY = "integration-test-t05-key"


def _docker(
    *args: str, check: bool = True, capture: bool = True, timeout: int = 60
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        check=check,
        capture_output=capture,
        text=True,
        timeout=timeout,
    )


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return os.path.exists("/var/run/docker.sock")


def _network_exists(name: str) -> bool:
    try:
        r = _docker("network", "inspect", name, check=False)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _redis_reachable_in_network() -> bool:
    """Probe `redis:6379` from inside the compose network.

    Spawns a one-shot busybox container on `perpetuity_default` and tries to
    open a TCP connection. We can't reach `redis` from the host because the
    service publishes no host port (M002 CONTEXT, MEM093).
    """
    try:
        r = _docker(
            "run",
            "--rm",
            "--network",
            NETWORK,
            "busybox:latest",
            "nc",
            "-z",
            "-w",
            "2",
            "redis",
            "6379",
            check=False,
            timeout=15,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _env_redis_password() -> str:
    candidates = [
        os.path.join(os.getcwd(), ".env"),
        os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".env")
        ),
    ]
    for path in candidates:
        try:
            with open(path) as fp:
                for line in fp:
                    stripped = line.strip()
                    if stripped.startswith("REDIS_PASSWORD="):
                        value = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                        if value:
                            return value
        except OSError:
            continue
    return "changethis"


def _wait_for_health(base_url: str, *, timeout_s: float = 60.0) -> None:
    deadline = time.time() + timeout_s
    last_exc: Exception | None = None
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base_url}/v1/health", timeout=2.0)
            if r.status_code == 200 and r.json().get("image_present"):
                return
        except (httpx.HTTPError, OSError) as exc:
            last_exc = exc
        time.sleep(0.5)
    raise AssertionError(
        f"orchestrator never reported image_present=True at {base_url}; "
        f"last_err={last_exc!r}"
    )


def _skip_if_no_orchestrator_env() -> None:
    if os.environ.get("SKIP_INTEGRATION") == "1":
        pytest.skip("SKIP_INTEGRATION=1 set")
    if not _docker_available():
        pytest.skip("docker not available — skipping orchestrator-backed tests")
    if not _network_exists(NETWORK):
        pytest.skip(
            f"compose network {NETWORK!r} not present — bring up `docker compose "
            f"up -d redis` first"
        )
    if not _redis_reachable_in_network():
        pytest.skip("redis not reachable in compose network")


def _docker_image_present(image: str) -> bool:
    try:
        r = _docker("image", "inspect", image, check=False)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


@pytest.fixture
def orchestrator(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, str]]:
    """Boot a fresh orchestrator container; teardown removes spawned workspaces.

    Mirrors `orchestrator/tests/integration/test_ws_bridge.py::orchestrator`
    and additionally rewrites `settings.ORCHESTRATOR_BASE_URL` /
    `ORCHESTRATOR_API_KEY` so the backend's TestClient app talks to the
    fresh container instead of the compose `orchestrator` service (which
    might be the one we're killing in scenario (j)).
    """
    _skip_if_no_orchestrator_env()
    if not _docker_image_present(ORCH_IMAGE):
        pytest.skip(
            f"orchestrator image {ORCH_IMAGE!r} not built — run `docker compose "
            f"build orchestrator`"
        )
    if not _docker_image_present(WORKSPACE_IMAGE):
        pytest.skip(
            f"workspace image {WORKSPACE_IMAGE!r} not built — run "
            f"`docker build -f orchestrator/tests/fixtures/Dockerfile.test "
            f"-t {WORKSPACE_IMAGE} orchestrator/workspace-image/`"
        )

    name = f"orch-t05-{uuid.uuid4().hex[:8]}"
    host_port = _free_port()
    redis_password = (
        os.environ.get("REDIS_PASSWORD") or _env_redis_password()
    )
    host_workspace_root = "/var/lib/perpetuity/workspaces"
    try:
        os.makedirs(host_workspace_root, exist_ok=True)
    except PermissionError:
        pass

    _docker(
        "run",
        "-d",
        "--name",
        name,
        "--network",
        NETWORK,
        "-p",
        f"{host_port}:8001",
        "-v",
        "/var/run/docker.sock:/var/run/docker.sock",
        "-v",
        f"{host_workspace_root}:{host_workspace_root}",
        "--cap-add",
        "SYS_ADMIN",
        "-e",
        f"WORKSPACE_IMAGE={WORKSPACE_IMAGE}",
        "-e",
        f"ORCHESTRATOR_API_KEY={API_KEY}",
        "-e",
        "REDIS_HOST=redis",
        "-e",
        f"REDIS_PASSWORD={redis_password}",
        ORCH_IMAGE,
    )

    base_url = f"http://localhost:{host_port}"
    try:
        _wait_for_health(base_url)
    except Exception:
        logs = _docker("logs", name, check=False).stdout or ""
        _docker("rm", "-f", name, check=False)
        raise AssertionError(f"orchestrator boot failed; logs:\n{logs}")

    # Re-point the backend at our ephemeral orchestrator.
    monkeypatch.setattr(settings, "ORCHESTRATOR_BASE_URL", base_url)
    monkeypatch.setattr(settings, "ORCHESTRATOR_API_KEY", API_KEY)

    info = {"name": name, "base_url": base_url, "api_key": API_KEY}
    try:
        yield info
    finally:
        # Reap any workspace containers spawned by this orchestrator.
        ws = _docker(
            "ps",
            "-aq",
            "--filter",
            "label=perpetuity.managed=true",
            check=False,
        )
        if ws.stdout.strip():
            _docker(
                "rm",
                "-f",
                *ws.stdout.split(),
                check=False,
                timeout=120,
            )
        _docker("rm", "-f", name, check=False)


# ----- helpers -----------------------------------------------------------


def _signup_user(client: TestClient) -> tuple[uuid.UUID, str, httpx.Cookies]:
    """Create a fresh user + their personal team, return (id, email, cookies).

    Uses the auth/signup endpoint so the personal team is auto-created (the
    M001 path), matching the demo description.
    """
    email = random_email()
    password = random_lower_string()
    client.cookies.clear()
    r = client.post(
        f"{settings.API_V1_STR}/auth/signup",
        json={"email": email, "password": password},
    )
    assert r.status_code == 200, r.text
    user_id = uuid.UUID(r.json()["id"])
    cookies = httpx.Cookies()
    for cookie in client.cookies.jar:
        cookies.set(cookie.name, cookie.value)
    client.cookies.clear()
    # Re-login to ensure cookies are clean (signup may not set them in all envs).
    cookies = login_cookie_headers(client=client, email=email, password=password)
    return user_id, email, cookies


def _personal_team_id(client: TestClient, cookies: httpx.Cookies) -> uuid.UUID:
    r = client.get(f"{settings.API_V1_STR}/teams/", cookies=cookies)
    assert r.status_code == 200, r.text
    rows = r.json()["data"]
    personal = next(t for t in rows if t["is_personal"])
    return uuid.UUID(personal["id"])


def _b64(s: str | bytes) -> str:
    if isinstance(s, str):
        s = s.encode("utf-8")
    return base64.b64encode(s).decode("ascii")


def _b64dec(s: str) -> bytes:
    return base64.b64decode(s, validate=True)


# ----- HTTP cases (a)-(d) -----------------------------------------------


def test_a_create_session_for_personal_team_returns_200(
    client: TestClient, orchestrator: dict[str, str]  # noqa: ARG001
) -> None:  # noqa: ARG001
    user_id, _, cookies = _signup_user(client)
    team_id = _personal_team_id(client, cookies)

    r = client.post(
        f"{settings.API_V1_STR}/sessions",
        json={"team_id": str(team_id)},
        cookies=cookies,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    sid = body["session_id"]
    assert uuid.UUID(sid)
    assert body["team_id"] == str(team_id)
    assert "created_at" in body

    # Orchestrator-side: by-id record exists with our user_id stamped on it.
    rec = httpx.get(
        f"{orchestrator['base_url']}/v1/sessions/by-id/{sid}",
        headers={"X-Orchestrator-Key": API_KEY},
        timeout=5.0,
    )
    assert rec.status_code == 200, rec.text
    assert rec.json()["user_id"] == str(user_id)


def test_b_create_session_without_cookie_returns_401(
    client: TestClient, orchestrator: dict[str, str]  # noqa: ARG001
) -> None:  # noqa: ARG001
    client.cookies.clear()
    r = client.post(
        f"{settings.API_V1_STR}/sessions", json={"team_id": str(uuid.uuid4())}
    )
    assert r.status_code == 401, r.text


def test_c_create_session_for_other_team_returns_403(
    client: TestClient, orchestrator: dict[str, str]  # noqa: ARG001
) -> None:  # noqa: ARG001
    """User A cannot create a session for a team they're not a member of."""
    _, _, cookies_a = _signup_user(client)
    _, _, cookies_b = _signup_user(client)

    # User B owns this personal team. User A is not a member.
    other_team = _personal_team_id(client, cookies_b)

    r = client.post(
        f"{settings.API_V1_STR}/sessions",
        json={"team_id": str(other_team)},
        cookies=cookies_a,
    )
    assert r.status_code == 403, r.text


def test_d_list_sessions_returns_callers_session(
    client: TestClient, orchestrator: dict[str, str]  # noqa: ARG001
) -> None:  # noqa: ARG001
    user_id, _, cookies = _signup_user(client)
    team_id = _personal_team_id(client, cookies)

    r = client.post(
        f"{settings.API_V1_STR}/sessions",
        json={"team_id": str(team_id)},
        cookies=cookies,
    )
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]

    r = client.get(
        f"{settings.API_V1_STR}/sessions",
        params={"team_id": str(team_id)},
        cookies=cookies,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] >= 1
    sids = {row["tmux_session"] for row in body["data"]}
    assert sid in sids
    # Defense-in-depth: every row has our user_id.
    for row in body["data"]:
        assert row["user_id"] == str(user_id)


# ----- WS cases (e)-(i) -------------------------------------------------


def _ws_url(session_id: str) -> str:
    return f"{settings.API_V1_STR}/ws/terminal/{session_id}"


def test_e_ws_without_cookie_closes_1008_missing_cookie(
    client: TestClient, orchestrator: dict[str, str]  # noqa: ARG001
) -> None:  # noqa: ARG001
    sid = str(uuid.uuid4())
    client.cookies.clear()
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(_ws_url(sid)) as ws:
            ws.receive_text()
    assert exc.value.code == 1008
    assert exc.value.reason == "missing_cookie"


def test_f_ws_with_own_session_attaches_and_round_trips(
    client: TestClient, orchestrator: dict[str, str]  # noqa: ARG001
) -> None:  # noqa: ARG001
    """Happy path: create a session, attach via WS, observe attach frame, send
    `echo hi`, observe a data frame whose payload contains `hi`.
    """
    _, _, cookies = _signup_user(client)
    team_id = _personal_team_id(client, cookies)
    r = client.post(
        f"{settings.API_V1_STR}/sessions",
        json={"team_id": str(team_id)},
        cookies=cookies,
    )
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]

    client.cookies.clear()
    for n, v in cookies.items():
        client.cookies.set(n, v)

    with client.websocket_connect(_ws_url(sid)) as ws:
        first = json.loads(ws.receive_text())
        assert first["type"] == "attach"
        # scrollback decodes
        _b64dec(first["scrollback"])

        ws.send_text(json.dumps({"type": "input", "bytes": _b64("echo hi-t05\n")}))

        deadline = time.monotonic() + 10.0
        seen_hi = False
        while time.monotonic() < deadline:
            text = ws.receive_text()
            frame = json.loads(text)
            if frame.get("type") == "data" and b"hi-t05" in _b64dec(
                frame.get("bytes", "")
            ):
                seen_hi = True
                break
        assert seen_hi, "expected `hi-t05` to round-trip via the WS bridge"


def test_g_ws_with_other_users_session_closes_1008_session_not_owned(
    client: TestClient, orchestrator: dict[str, str]  # noqa: ARG001
) -> None:  # noqa: ARG001
    _, _, cookies_a = _signup_user(client)
    team_a = _personal_team_id(client, cookies_a)
    r = client.post(
        f"{settings.API_V1_STR}/sessions",
        json={"team_id": str(team_a)},
        cookies=cookies_a,
    )
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]  # owned by A

    _, _, cookies_b = _signup_user(client)
    client.cookies.clear()
    for n, v in cookies_b.items():
        client.cookies.set(n, v)

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(_ws_url(sid)) as ws:
            ws.receive_text()
    assert exc.value.code == 1008
    assert exc.value.reason == "session_not_owned"


def test_h_ws_for_never_existed_sid_closes_1008_session_not_owned(
    client: TestClient, orchestrator: dict[str, str]  # noqa: ARG001
) -> None:  # noqa: ARG001
    """No-enumeration: identical close to (g)."""
    _, _, cookies = _signup_user(client)
    bogus = str(uuid.uuid4())
    client.cookies.clear()
    for n, v in cookies.items():
        client.cookies.set(n, v)

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(_ws_url(bogus)) as ws:
            ws.receive_text()
    assert exc.value.code == 1008
    assert exc.value.reason == "session_not_owned"


def test_i_delete_then_ws_attach_closes_1008_session_not_owned(
    client: TestClient, orchestrator: dict[str, str]  # noqa: ARG001
) -> None:  # noqa: ARG001
    _, _, cookies = _signup_user(client)
    team_id = _personal_team_id(client, cookies)
    r = client.post(
        f"{settings.API_V1_STR}/sessions",
        json={"team_id": str(team_id)},
        cookies=cookies,
    )
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]

    r = client.delete(
        f"{settings.API_V1_STR}/sessions/{sid}", cookies=cookies
    )
    assert r.status_code == 200, r.text

    client.cookies.clear()
    for n, v in cookies.items():
        client.cookies.set(n, v)

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(_ws_url(sid)) as ws:
            ws.receive_text()
    assert exc.value.code == 1008
    assert exc.value.reason == "session_not_owned"


# ----- (j): orchestrator down → 503 / 1011 ------------------------------


def test_j_orchestrator_down_returns_503_and_1011(
    client: TestClient, orchestrator: dict[str, str]  # noqa: ARG001
) -> None:  # noqa: ARG001
    """Stop the orchestrator, observe HTTP 503 and WS 1011, restart, retry → 200.

    We stop and start the same container we booted in the fixture so we
    don't disturb the compose-managed orchestrator (which other tests on a
    parallel runner may be using).
    """
    _, _, cookies = _signup_user(client)
    team_id = _personal_team_id(client, cookies)

    name = orchestrator["name"]
    _docker("stop", name, check=True, timeout=30)

    r = client.post(
        f"{settings.API_V1_STR}/sessions",
        json={"team_id": str(team_id)},
        cookies=cookies,
    )
    assert r.status_code == 503, r.text

    # WS attach while down → 1011 orchestrator_unavailable. We use a random
    # sid here because we never created one — the lookup phase is what
    # fails first, and the close shape is what we're verifying.
    bogus = str(uuid.uuid4())
    client.cookies.clear()
    for n, v in cookies.items():
        client.cookies.set(n, v)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(_ws_url(bogus)) as ws:
            ws.receive_text()
    assert exc.value.code == 1011
    assert exc.value.reason == "orchestrator_unavailable"

    # Restart the orchestrator and verify the create path recovers.
    _docker("start", name, check=True, timeout=30)
    _wait_for_health(orchestrator["base_url"])

    r = client.post(
        f"{settings.API_V1_STR}/sessions",
        json={"team_id": str(team_id)},
        cookies=cookies,
    )
    assert r.status_code == 200, r.text


# ----- observability sanity check --------------------------------------


def test_logs_emit_uuid_only_no_email_or_full_name(
    client: TestClient, db: Session, orchestrator: dict[str, str], caplog  # noqa: ARG001
) -> None:  # noqa: ARG001
    """Slice rule: log lines that include user/team/session/container ids
    MUST emit UUIDs only — never email or full_name.

    Signs up via the M001 endpoint (which auto-creates the personal team),
    then patches the user's full_name to a unique sentinel. Drives a real
    POST /sessions and grep-asserts the captured log records for any
    occurrence of the email or full_name.
    """
    import logging

    email = random_email()
    password = random_lower_string()
    sentinel = f"FullNameSentinel-{uuid.uuid4().hex[:8]}"
    client.cookies.clear()
    r = client.post(
        f"{settings.API_V1_STR}/auth/signup",
        json={"email": email, "password": password},
    )
    assert r.status_code == 200, r.text
    user_id = uuid.UUID(r.json()["id"])

    # Patch full_name on the user row so the log-grep is meaningful.
    user = db.get(crud.User if hasattr(crud, "User") else None, user_id)
    if user is None:
        from app.models import User as _User

        user = db.get(_User, user_id)
        assert user is not None
    user.full_name = sentinel
    db.add(user)
    db.commit()

    cookies = login_cookie_headers(client=client, email=email, password=password)
    team_id = _personal_team_id(client, cookies)

    with caplog.at_level(logging.INFO, logger="app.api.routes.sessions"):
        r = client.post(
            f"{settings.API_V1_STR}/sessions",
            json={"team_id": str(team_id)},
            cookies=cookies,
        )
        assert r.status_code == 200, r.text

    captured = "\n".join(rec.getMessage() for rec in caplog.records)
    assert email not in captured, captured
    assert sentinel not in captured, captured
    assert str(user_id) in captured, captured


# ----- T03: GET /sessions/{sid}/scrollback unit tests -------------------
#
# These exercise the new scrollback proxy in isolation by monkeypatching
# `httpx.AsyncClient` inside `app.api.routes.sessions` so no real
# orchestrator is needed. The S04/T04 integration test covers the
# end-to-end real-orchestrator path.
#
# The fake client lets each test script the orchestrator's responses for
# the GET /v1/sessions/by-id/<sid> lookup and the POST
# /v1/sessions/<sid>/scrollback fetch separately.


class _FakeResponse:
    def __init__(
        self,
        status_code: int,
        json_body: object | None = None,
        request: httpx.Request | None = None,
    ) -> None:
        self.status_code = status_code
        self._json = json_body
        self.request = request or httpx.Request("GET", "http://fake")

    def json(self) -> object:
        return self._json


class _FakeAsyncClient:
    """Stub for httpx.AsyncClient used by the scrollback proxy.

    `route_map` keys are (method, endswith-suffix) tuples; values are
    either a `_FakeResponse` to return or an `Exception` instance to
    raise. Order of insertion does not matter — we match by suffix so
    tests don't have to spell out the full ORCHESTRATOR_BASE_URL.
    """

    last_calls: list[tuple[str, str]] = []

    def __init__(
        self,
        route_map: dict[tuple[str, str], object],
    ) -> None:
        self._routes = route_map

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def _resolve(self, method: str, url: str) -> object:
        type(self).last_calls.append((method, url))
        for (m, suffix), handler in self._routes.items():
            if m == method and url.endswith(suffix):
                return handler
        raise AssertionError(
            f"FakeAsyncClient: no route registered for {method} {url}; "
            f"have {list(self._routes.keys())}"
        )

    async def get(
        self, url: str, *, headers: dict[str, str] | None = None, **_: object
    ) -> _FakeResponse:
        handler = self._resolve("GET", url)
        if isinstance(handler, Exception):
            raise handler
        assert isinstance(handler, _FakeResponse)
        return handler

    async def post(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json: object | None = None,
        **_: object,
    ) -> _FakeResponse:
        handler = self._resolve("POST", url)
        if isinstance(handler, Exception):
            raise handler
        assert isinstance(handler, _FakeResponse)
        return handler


def _install_fake_orch(
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[tuple[str, str], object],
) -> type[_FakeAsyncClient]:
    """Patch `httpx.AsyncClient` *as imported by sessions.py* with our fake.

    Returns the class so tests can introspect `last_calls` if needed.
    """
    import app.api.routes.sessions as sessions_mod

    _FakeAsyncClient.last_calls = []

    def _factory(*_args: object, **_kwargs: object) -> _FakeAsyncClient:
        return _FakeAsyncClient(routes)

    monkeypatch.setattr(sessions_mod.httpx, "AsyncClient", _factory)
    return _FakeAsyncClient


def _scrollback_url(session_id: str) -> str:
    return f"{settings.API_V1_STR}/sessions/{session_id}/scrollback"


def _login_user(client: TestClient) -> tuple[uuid.UUID, httpx.Cookies]:
    """Sign up + log in a fresh user; returns (user_id, detached cookie jar).

    Mirrors the `_signup_user` helper above but does not require the
    orchestrator fixture (these tests stub the orchestrator).
    """
    email = random_email()
    password = random_lower_string()
    client.cookies.clear()
    r = client.post(
        f"{settings.API_V1_STR}/auth/signup",
        json={"email": email, "password": password},
    )
    assert r.status_code == 200, r.text
    user_id = uuid.UUID(r.json()["id"])
    cookies = login_cookie_headers(client=client, email=email, password=password)
    return user_id, cookies


def test_scrollback_owner_returns_200_with_orchestrator_text(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id, cookies = _login_user(client)
    sid = str(uuid.uuid4())

    routes: dict[tuple[str, str], object] = {
        ("GET", f"/v1/sessions/by-id/{sid}"): _FakeResponse(
            200, {"session_id": sid, "user_id": str(user_id)}
        ),
        ("POST", f"/v1/sessions/{sid}/scrollback"): _FakeResponse(
            200, {"scrollback": "hello\nworld\n"}
        ),
    }
    _install_fake_orch(monkeypatch, routes)

    r = client.get(_scrollback_url(sid), cookies=cookies)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"session_id": sid, "scrollback": "hello\nworld\n"}


def test_scrollback_owner_with_empty_scrollback_returns_200_empty_string(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id, cookies = _login_user(client)
    sid = str(uuid.uuid4())

    routes: dict[tuple[str, str], object] = {
        ("GET", f"/v1/sessions/by-id/{sid}"): _FakeResponse(
            200, {"session_id": sid, "user_id": str(user_id)}
        ),
        ("POST", f"/v1/sessions/{sid}/scrollback"): _FakeResponse(
            200, {"scrollback": ""}
        ),
    }
    _install_fake_orch(monkeypatch, routes)

    r = client.get(_scrollback_url(sid), cookies=cookies)
    assert r.status_code == 200, r.text
    assert r.json() == {"session_id": sid, "scrollback": ""}


def test_scrollback_non_owner_returns_404_session_not_found(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Caller authenticated, session exists, but owned by another user."""
    _, cookies = _login_user(client)
    sid = str(uuid.uuid4())
    other_user_id = uuid.uuid4()

    routes: dict[tuple[str, str], object] = {
        ("GET", f"/v1/sessions/by-id/{sid}"): _FakeResponse(
            200, {"session_id": sid, "user_id": str(other_user_id)}
        ),
        # The scrollback POST must NOT be reached when ownership fails.
    }
    _install_fake_orch(monkeypatch, routes)

    r = client.get(_scrollback_url(sid), cookies=cookies)
    assert r.status_code == 404, r.text
    assert r.json() == {"detail": "Session not found"}


def test_scrollback_missing_session_returns_404_byte_equal_to_non_owner(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No-enumeration: missing-session 404 body matches non-owner 404 body byte-for-byte."""
    _, cookies = _login_user(client)
    sid_missing = str(uuid.uuid4())
    sid_other = str(uuid.uuid4())
    other_user_id = uuid.uuid4()

    # Case 1: missing record (orch returns 404)
    routes_missing: dict[tuple[str, str], object] = {
        ("GET", f"/v1/sessions/by-id/{sid_missing}"): _FakeResponse(404, None),
    }
    _install_fake_orch(monkeypatch, routes_missing)
    r_missing = client.get(_scrollback_url(sid_missing), cookies=cookies)
    assert r_missing.status_code == 404, r_missing.text
    body_missing = r_missing.content

    # Case 2: non-owner (orch returns record owned by someone else)
    routes_other: dict[tuple[str, str], object] = {
        ("GET", f"/v1/sessions/by-id/{sid_other}"): _FakeResponse(
            200, {"session_id": sid_other, "user_id": str(other_user_id)}
        ),
    }
    _install_fake_orch(monkeypatch, routes_other)
    r_other = client.get(_scrollback_url(sid_other), cookies=cookies)
    assert r_other.status_code == 404, r_other.text
    body_other = r_other.content

    # The two response bodies must be byte-equal — the caller cannot
    # distinguish "doesn't exist" from "exists but isn't yours".
    assert body_missing == body_other, (body_missing, body_other)


def test_scrollback_unauthenticated_returns_401(client: TestClient) -> None:
    sid = str(uuid.uuid4())
    client.cookies.clear()
    r = client.get(_scrollback_url(sid))
    assert r.status_code == 401, r.text


def test_scrollback_orchestrator_unreachable_on_lookup_returns_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, cookies = _login_user(client)
    sid = str(uuid.uuid4())

    routes: dict[tuple[str, str], object] = {
        ("GET", f"/v1/sessions/by-id/{sid}"): httpx.ConnectError(
            "boom", request=httpx.Request("GET", "http://orch/x")
        ),
    }
    _install_fake_orch(monkeypatch, routes)

    r = client.get(_scrollback_url(sid), cookies=cookies)
    assert r.status_code == 503, r.text
    assert r.json() == {"detail": "orchestrator_unavailable"}


def test_scrollback_orchestrator_unreachable_on_fetch_returns_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lookup succeeds (and proves ownership), but the scrollback fetch fails."""
    user_id, cookies = _login_user(client)
    sid = str(uuid.uuid4())

    routes: dict[tuple[str, str], object] = {
        ("GET", f"/v1/sessions/by-id/{sid}"): _FakeResponse(
            200, {"session_id": sid, "user_id": str(user_id)}
        ),
        ("POST", f"/v1/sessions/{sid}/scrollback"): httpx.ReadTimeout(
            "slow", request=httpx.Request("POST", "http://orch/x")
        ),
    }
    _install_fake_orch(monkeypatch, routes)

    r = client.get(_scrollback_url(sid), cookies=cookies)
    assert r.status_code == 503, r.text
    assert r.json() == {"detail": "orchestrator_unavailable"}


def test_scrollback_orchestrator_response_missing_scrollback_key_returns_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Schema-drift safety net: missing `scrollback` key → 503, not 500."""
    user_id, cookies = _login_user(client)
    sid = str(uuid.uuid4())

    routes: dict[tuple[str, str], object] = {
        ("GET", f"/v1/sessions/by-id/{sid}"): _FakeResponse(
            200, {"session_id": sid, "user_id": str(user_id)}
        ),
        ("POST", f"/v1/sessions/{sid}/scrollback"): _FakeResponse(
            200, {"unexpected": "shape"}
        ),
    }
    _install_fake_orch(monkeypatch, routes)

    r = client.get(_scrollback_url(sid), cookies=cookies)
    assert r.status_code == 503, r.text
    assert r.json() == {"detail": "orchestrator_unavailable"}


def test_scrollback_logs_bytes_only_not_content(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """Observability rule: log byte length, never the scrollback text."""
    import logging as _logging

    user_id, cookies = _login_user(client)
    sid = str(uuid.uuid4())
    secret = "SUPER-SECRET-API-KEY-do-not-log-me"
    routes: dict[tuple[str, str], object] = {
        ("GET", f"/v1/sessions/by-id/{sid}"): _FakeResponse(
            200, {"session_id": sid, "user_id": str(user_id)}
        ),
        ("POST", f"/v1/sessions/{sid}/scrollback"): _FakeResponse(
            200, {"scrollback": secret}
        ),
    }
    _install_fake_orch(monkeypatch, routes)

    with caplog.at_level(_logging.INFO, logger="app.api.routes.sessions"):
        r = client.get(_scrollback_url(sid), cookies=cookies)
        assert r.status_code == 200, r.text

    captured = "\n".join(rec.getMessage() for rec in caplog.records)
    assert secret not in captured, captured
    assert "session_scrollback_proxied" in captured, captured
    assert f"bytes={len(secret.encode('utf-8'))}" in captured, captured
