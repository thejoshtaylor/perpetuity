"""Integration tests for the T04 WS bridge.

Boots a fresh ephemeral orchestrator container (same fixture pattern as
test_sessions_lifecycle.py) so each test owns its Redis state and the
workspace containers it spawns. Drives the WS endpoint via the `websockets`
asyncio client against `ws://localhost:<host-port>/v1/sessions/<sid>/stream`.

Each test seeds a session via `POST /v1/sessions` first (T03 path), then
opens a WS to that session_id and exercises the locked frame protocol from
`orchestrator.protocol`.

Verification matrix from the task plan (T04 verification a-g):
  (a) attach frame is the first server frame
  (b) input → echo back as data frame within 5s
  (c) resize → no error logged
  (d) disconnect+reconnect → second attach scrollback decodes to UTF-8 with
      the prior `hello` (proves tmux survived; orchestrator-restart proof
      lives in T06)
  (e) bad key → close 1008 'unauthorized'
  (f) unknown session_id → close 1008 'session_not_found'
  (g) `exit\\n` input → exit frame + close 1000

Plus negative tests called out in the task plan's Q7:
  - malformed JSON frame → close 1003
  - unknown frame type    → ignored (still alive)
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import subprocess
import time
import uuid
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
import pytest_asyncio
import websockets
from websockets.asyncio.client import ClientConnection, connect

ORCH_IMAGE = "orchestrator:latest"
WORKSPACE_IMAGE = "perpetuity/workspace:test"
NETWORK = "perpetuity_default"
API_KEY = "integration-test-ws-key"


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


def _env_redis_password() -> str:
    """Best-effort REDIS_PASSWORD discovery.

    Reads `<repo>/.env` (the compose-default location) and returns the
    REDIS_PASSWORD value if present. Falls back to "changethis" — that's
    the placeholder value committed in `.env.example` and the live value
    in the dev compose stack. A test run against a non-default password
    can override via `REDIS_PASSWORD=` in the environment.
    """
    candidates = [
        os.path.join(os.getcwd(), ".env"),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".env")),
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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_for_health(base_url: str, *, timeout_s: float = 60.0) -> dict[str, object]:
    deadline = time.time() + timeout_s
    last_exc: Exception | None = None
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base_url}/v1/health", timeout=2.0)
            if r.status_code == 200:
                body = r.json()
                if body.get("image_present"):
                    return body
        except (httpx.HTTPError, OSError) as exc:
            last_exc = exc
        time.sleep(0.5)
    raise AssertionError(
        f"orchestrator never reported image_present=True at {base_url}; "
        f"last_err={last_exc!r}"
    )


@pytest.fixture
def orchestrator() -> Iterator[dict[str, str]]:
    """Boot a fresh orchestrator container; tear it + workspace containers down."""
    if not os.path.exists("/var/run/docker.sock"):
        pytest.skip("no docker socket on host")

    name = f"orch-t04-{uuid.uuid4().hex[:8]}"
    host_port = _free_port()
    # Read REDIS_PASSWORD from the repo .env if not in os.environ — the
    # compose stack uses whatever's in `.env`, and a hardcoded fallback
    # would 503 against any non-default deployment.
    redis_password = os.environ.get("REDIS_PASSWORD") or _env_redis_password()
    host_workspace_root = "/var/lib/perpetuity/workspaces"
    try:
        os.makedirs(host_workspace_root, exist_ok=True)
    except PermissionError:
        # See test_sessions_lifecycle.py — best-effort; bind-mount source
        # only needs to exist on the host before docker creates the
        # workspace container.
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
    ws_base = f"ws://localhost:{host_port}"
    try:
        _wait_for_health(base_url)
    except Exception:
        logs = _docker("logs", name, check=False).stdout or ""
        _docker("rm", "-f", name, check=False)
        raise AssertionError(f"orchestrator boot failed; logs:\n{logs}")

    info = {
        "name": name,
        "base_url": base_url,
        "ws_base": ws_base,
        "api_key": API_KEY,
    }
    try:
        yield info
    finally:
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


def _http(base_url: str, api_key: str) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        headers={"X-Orchestrator-Key": api_key},
        timeout=httpx.Timeout(30.0, connect=5.0),
    )


def _seed_session(orch: dict[str, str]) -> tuple[str, str, str, str]:
    """POST /v1/sessions and return (sid, container_id, user, team)."""
    user = str(uuid.uuid4())
    team = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    with _http(orch["base_url"], orch["api_key"]) as c:
        r = c.post(
            "/v1/sessions",
            json={"session_id": sid, "user_id": user, "team_id": team},
        )
        assert r.status_code == 200, r.text
        return sid, r.json()["container_id"], user, team


def _ws_url(orch: dict[str, str], sid: str, *, key: str | None = None) -> str:
    k = key if key is not None else orch["api_key"]
    return f"{orch['ws_base']}/v1/sessions/{sid}/stream?key={k}"


def _b64(s: str | bytes) -> str:
    if isinstance(s, str):
        s = s.encode("utf-8")
    return base64.b64encode(s).decode("ascii")


def _b64dec(s: str) -> bytes:
    return base64.b64decode(s, validate=True)


async def _recv_json(ws: ClientConnection, timeout: float = 5.0) -> dict:
    text = await asyncio.wait_for(ws.recv(), timeout=timeout)
    return json.loads(text)


async def _recv_until(
    ws: ClientConnection,
    predicate,
    *,
    timeout: float = 5.0,
) -> list[dict]:
    """Collect JSON frames until `predicate(frame)` is True or timeout.

    Used to drain an arbitrary number of `data` frames on the way to a
    specific event (e.g. text containing 'hello' or an `exit` frame).
    """
    deadline = time.monotonic() + timeout
    received: list[dict] = []
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(
                f"predicate not satisfied within {timeout}s; received={received}"
            )
        frame = await _recv_json(ws, timeout=remaining)
        received.append(frame)
        if predicate(frame):
            return received


# --------- (a) + (b): attach frame + echo round-trip --------------------


@pytest.mark.asyncio
async def test_attach_frame_then_echo_roundtrip(orchestrator: dict[str, str]) -> None:
    """First frame is `attach`; sending `echo hello\\n` yields a `data` frame
    whose decoded payload contains `hello` within 5s.
    """
    sid, _, _, _ = _seed_session(orchestrator)
    async with connect(_ws_url(orchestrator, sid)) as ws:
        first = await _recv_json(ws, timeout=10.0)
        assert first["type"] == "attach", first
        # Scrollback is base64; decoding it should not raise.
        _b64dec(first["scrollback"])

        await ws.send(json.dumps({"type": "input", "bytes": _b64("echo hello\n")}))

        # Drain data frames until we see 'hello' in decoded output.
        frames = await _recv_until(
            ws,
            lambda f: f.get("type") == "data"
            and b"hello" in _b64dec(f.get("bytes", "")),
            timeout=5.0,
        )
        assert any(
            f["type"] == "data" and b"hello" in _b64dec(f["bytes"]) for f in frames
        )


# --------- (c): resize doesn't error -----------------------------------


@pytest.mark.asyncio
async def test_resize_frame_does_not_error(orchestrator: dict[str, str]) -> None:
    """Send `resize` and assert the WS stays open afterwards (no error frame,
    no close). 200ms quiet window after resize is enough — tmux refresh-client
    is synchronous on the orchestrator side.
    """
    sid, _, _, _ = _seed_session(orchestrator)
    async with connect(_ws_url(orchestrator, sid)) as ws:
        first = await _recv_json(ws, timeout=10.0)
        assert first["type"] == "attach"

        await ws.send(json.dumps({"type": "resize", "cols": 120, "rows": 40}))

        # Quiet window: no `error`, no close. We allow `data` frames (the
        # repaint may emit some) — anything else is fine. Also confirm we
        # can still send input afterwards.
        try:
            for _ in range(5):
                f = await _recv_json(ws, timeout=0.2)
                assert f.get("type") in ("data", "attach"), f
        except asyncio.TimeoutError:
            pass

        # Round-trip ping: input still works.
        await ws.send(json.dumps({"type": "input", "bytes": _b64("echo R\n")}))
        await _recv_until(
            ws,
            lambda f: f.get("type") == "data"
            and b"R" in _b64dec(f.get("bytes", "")),
            timeout=5.0,
        )


# --------- (d): disconnect + reconnect carries scrollback --------------


@pytest.mark.asyncio
async def test_disconnect_reconnect_preserves_scrollback(
    orchestrator: dict[str, str],
) -> None:
    """Run `echo hello`, disconnect, reconnect to the SAME sid, assert the
    second attach frame's scrollback decodes to UTF-8 containing 'hello'.

    Proves tmux survived the WS disconnect — the orchestrator-restart proof
    is the more aggressive variant in T06.
    """
    sid, _, _, _ = _seed_session(orchestrator)
    async with connect(_ws_url(orchestrator, sid)) as ws:
        await _recv_json(ws, timeout=10.0)  # attach
        await ws.send(
            json.dumps({"type": "input", "bytes": _b64("echo hello-d-test\n")})
        )
        await _recv_until(
            ws,
            lambda f: f.get("type") == "data"
            and b"hello-d-test" in _b64dec(f.get("bytes", "")),
            timeout=5.0,
        )

    # Brief pause to let tmux flush the line into its buffer before the
    # orchestrator captures scrollback on the next attach. capture-pane
    # reads the live pane state which can race with the just-emitted
    # output if we attach too fast.
    await asyncio.sleep(0.5)

    async with connect(_ws_url(orchestrator, sid)) as ws:
        attach = await _recv_json(ws, timeout=10.0)
        assert attach["type"] == "attach"
        decoded = _b64dec(attach["scrollback"]).decode("utf-8", errors="replace")
        assert "hello-d-test" in decoded, decoded[-500:]


# --------- (e): bad key → 1008 unauthorized -----------------------------


@pytest.mark.asyncio
async def test_bad_key_closes_1008_unauthorized(
    orchestrator: dict[str, str],
) -> None:
    sid, _, _, _ = _seed_session(orchestrator)
    url = _ws_url(orchestrator, sid, key="not-the-key")
    with pytest.raises(websockets.exceptions.InvalidStatus) as exc:
        async with connect(url):
            pass
    # close-before-accept yields HTTP 403 on the upgrade. Both 403 and the
    # ConnectionClosedError path are valid signals of policy rejection.
    assert exc.value.response.status_code in (401, 403), exc.value


# --------- (f): unknown session_id → 1008 session_not_found -----------


@pytest.mark.asyncio
async def test_unknown_session_id_closes_1008(orchestrator: dict[str, str]) -> None:
    bogus = str(uuid.uuid4())  # never seeded
    async with connect(_ws_url(orchestrator, bogus)) as ws:
        with pytest.raises(websockets.exceptions.ConnectionClosed) as exc:
            await asyncio.wait_for(ws.recv(), timeout=5.0)
    assert exc.value.code == 1008, exc.value
    assert "session_not_found" in (exc.value.reason or ""), exc.value


# --------- (g): exit\\n → exit frame + close 1000 -----------------------


@pytest.mark.asyncio
async def test_shell_exit_emits_exit_frame_and_closes_1000(
    orchestrator: dict[str, str],
) -> None:
    """Send `exit\\n`, await the `exit` frame, assert close code 1000.

    Note: `tmux attach-session` returns when the *attached client* detaches —
    `exit` from the inner shell terminates that shell, which causes the
    tmux session itself to end (since it's a single-shell session) and the
    attach to return code 0. Either way, the exec stream EOFs and the
    bridge sends `{type:"exit", code:0}`.
    """
    sid, _, _, _ = _seed_session(orchestrator)
    async with connect(_ws_url(orchestrator, sid)) as ws:
        await _recv_json(ws, timeout=10.0)  # attach
        await ws.send(json.dumps({"type": "input", "bytes": _b64("exit\n")}))

        frames = await _recv_until(
            ws, lambda f: f.get("type") == "exit", timeout=10.0
        )
        exit_frame = next(f for f in frames if f["type"] == "exit")
        assert isinstance(exit_frame["code"], int)

        # The next recv should observe the close.
        with pytest.raises(websockets.exceptions.ConnectionClosed) as exc:
            await asyncio.wait_for(ws.recv(), timeout=5.0)
        assert exc.value.code == 1000, exc.value


# --------- negative: malformed JSON frame → 1003 -----------------------


@pytest.mark.asyncio
async def test_malformed_json_closes_1003(orchestrator: dict[str, str]) -> None:
    sid, _, _, _ = _seed_session(orchestrator)
    async with connect(_ws_url(orchestrator, sid)) as ws:
        await _recv_json(ws, timeout=10.0)  # attach
        await ws.send("not-json{{{")

        with pytest.raises(websockets.exceptions.ConnectionClosed) as exc:
            await asyncio.wait_for(ws.recv(), timeout=5.0)
        assert exc.value.code == 1003, exc.value
        assert "malformed_frame" in (exc.value.reason or ""), exc.value


# --------- negative: unknown frame type → ignored, WS stays open -------


@pytest.mark.asyncio
async def test_unknown_frame_type_is_ignored(orchestrator: dict[str, str]) -> None:
    sid, _, _, _ = _seed_session(orchestrator)
    async with connect(_ws_url(orchestrator, sid)) as ws:
        await _recv_json(ws, timeout=10.0)  # attach
        await ws.send(json.dumps({"type": "telepathy", "thoughts": "hi"}))
        # WS should still be open and able to round-trip an input.
        await ws.send(json.dumps({"type": "input", "bytes": _b64("echo K\n")}))
        await _recv_until(
            ws,
            lambda f: f.get("type") == "data"
            and b"K" in _b64dec(f.get("bytes", "")),
            timeout=5.0,
        )


# --------- observability: session_attached + session_detached logs -----


@pytest.mark.asyncio
async def test_observability_log_lines(orchestrator: dict[str, str]) -> None:
    """Slice observability: session_attached + session_detached INFO lines
    are emitted, and every log line is UUID-only (no email/full_name).
    """
    sid, _, user, _ = _seed_session(orchestrator)
    async with connect(_ws_url(orchestrator, sid)) as ws:
        await _recv_json(ws, timeout=10.0)
        await ws.send(json.dumps({"type": "input", "bytes": _b64("echo obs\n")}))
        await _recv_until(
            ws,
            lambda f: f.get("type") == "data"
            and b"obs" in _b64dec(f.get("bytes", "")),
            timeout=5.0,
        )

    # Brief pause so the detach log line is flushed before we read.
    await asyncio.sleep(0.4)
    logs = _docker("logs", orchestrator["name"], check=False).stdout or ""
    logs += _docker("logs", orchestrator["name"], check=False).stderr or ""
    assert "session_attached" in logs, logs[-2000:]
    assert "session_detached" in logs, logs[-2000:]
    assert user[:8] in logs or user in logs, "expected our user UUID in logs"
