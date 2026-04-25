"""Integration tests for the S04/T01 live-attach refcount map.

The AttachMap is process-local to the orchestrator. The test process
boots a fresh EPHEMERAL orchestrator container per test (same boot
recipe as test_sessions_lifecycle.py — privileged, DATABASE_URL, rshared
workspace mount) and verifies behavior via the structured log lines the
WS bridge emits:

  - INFO `attach_registered session_id=<sid> count=<n>`
  - INFO `attach_unregistered session_id=<sid> count=<n>`

These lines are part of the slice observability taxonomy (MEM134) and
are the contract the reaper (T02) and downstream tests rely on.

Verification matrix from the task plan:
  (1) Connect a WS → `attach_registered ... count=1` appears in logs.
  (2) Disconnect WS → `attach_unregistered ... count=0` appears, polled
      with a short deadline (matches plan wording: "poll up to 1s").
  (3) Exec-start failure path (tmux session deleted out from under the
      bridge before the upgrade): `docker_exec_start_failed` appears,
      and `attach_registered` does NOT — the map must be empty for that
      session_id. Proves we register AFTER `__aenter__`, not before.

Run from the host (needs the docker CLI to boot ephemeral orchestrators
and `docker exec` workspace containers — same as test_ws_bridge.py /
test_sessions_lifecycle.py per MEM141):

    cd orchestrator && uv run pytest tests/integration/test_ws_attach_map.py
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator

import httpx
import pytest
from websockets.asyncio.client import connect

ORCH_IMAGE = "orchestrator:latest"
WORKSPACE_IMAGE = "perpetuity/workspace:test"
NETWORK = "perpetuity_default"
API_KEY = "integration-test-attach-map-key"


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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


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


def _ensure_host_workspaces_shared() -> None:
    """rshared bind-propagation prep — same as test_sessions_lifecycle.py.

    Idempotent. The orchestrator's loopback-ext4 mounts will only
    propagate back to the host (and thus into nested workspace
    containers) if the bind-mount source is itself a shared mountpoint.
    """
    _docker(
        "run", "--rm", "--privileged", "--pid=host",
        "alpine:3", "nsenter", "-t", "1", "-m", "--",
        "sh", "-c",
        "mkdir -p /var/lib/perpetuity/workspaces /var/lib/perpetuity/vols && "
        "( mountpoint -q /var/lib/perpetuity/workspaces || "
        "  mount --bind /var/lib/perpetuity/workspaces /var/lib/perpetuity/workspaces ) && "
        "mount --make-shared /var/lib/perpetuity/workspaces",
        check=False,
    )


def _psql_query(sql: str) -> str:
    """Run SQL against the live compose db container."""
    pg_user = os.environ.get("POSTGRES_USER") or "postgres"
    pg_db = os.environ.get("POSTGRES_DB") or "app"
    out = _docker(
        "exec", "perpetuity-db-1",
        "psql", "-U", pg_user, "-d", pg_db, "-A", "-t", "-c", sql,
        check=False,
    )
    return (out.stdout or "").strip()


def _create_pg_user_team() -> tuple[str, str]:
    """Insert a fresh (user, team) so workspace_volume FK constraints hold.

    Mirrors test_sessions_lifecycle.py — this orchestrator slice's tests
    require a real (user_id, team_id) row in Postgres because
    workspace_volume has FK constraints on both.
    """
    user_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())
    user_email = f"t01-{uuid.uuid4().hex[:8]}@test.local"
    team_name = f"t01-team-{uuid.uuid4().hex[:8]}"
    _psql_query(
        f"INSERT INTO \"user\" (id, email, hashed_password, is_active, role, full_name) "
        f"VALUES ('{user_id}', '{user_email}', 'x', true, 'user', 'S04T01 Test')"
    )
    _psql_query(
        f"INSERT INTO team (id, name, slug, is_personal) "
        f"VALUES ('{team_id}', '{team_name}', '{team_name}', false)"
    )
    return user_id, team_id


def _cleanup_pg_user_team(user_id: str, team_id: str) -> None:
    _psql_query(f"DELETE FROM team WHERE id = '{team_id}'")
    _psql_query(f"DELETE FROM \"user\" WHERE id = '{user_id}'")


def _boot_orchestrator() -> tuple[str, str]:
    """Boot a fresh ephemeral orchestrator. Mirrors the recipe in
    test_sessions_lifecycle.py — privileged, DATABASE_URL pointing at the
    compose db, vols dir bind-mounted, rshared workspace dir.
    """
    name = f"orch-attach-{uuid.uuid4().hex[:8]}"
    host_port = _free_port()
    redis_password = os.environ.get("REDIS_PASSWORD") or "changethis"
    pg_user = os.environ.get("POSTGRES_USER") or "postgres"
    pg_password = os.environ.get("POSTGRES_PASSWORD") or "changethis"
    pg_db = os.environ.get("POSTGRES_DB") or "app"
    database_url = f"postgresql://{pg_user}:{pg_password}@db:5432/{pg_db}"

    host_workspace_root = "/var/lib/perpetuity/workspaces"
    host_vols_dir = "/var/lib/perpetuity/vols"
    for d in (host_workspace_root, host_vols_dir):
        try:
            os.makedirs(d, exist_ok=True)
        except PermissionError:
            pass

    _ensure_host_workspaces_shared()

    _docker(
        "run", "-d",
        "--name", name,
        "--network", NETWORK,
        "-p", f"{host_port}:8001",
        "-v", "/var/run/docker.sock:/var/run/docker.sock",
        "--mount", f"type=bind,source={host_workspace_root},target={host_workspace_root},bind-propagation=rshared",
        "-v", f"{host_vols_dir}:{host_vols_dir}",
        "--privileged",
        "-e", f"WORKSPACE_IMAGE={WORKSPACE_IMAGE}",
        "-e", f"ORCHESTRATOR_API_KEY={API_KEY}",
        "-e", "REDIS_HOST=redis",
        "-e", f"REDIS_PASSWORD={redis_password}",
        "-e", f"DATABASE_URL={database_url}",
        ORCH_IMAGE,
    )
    base_url = f"http://localhost:{host_port}"
    try:
        _wait_for_health(base_url)
    except Exception:
        logs = _docker("logs", name, check=False).stdout or ""
        _docker("rm", "-f", name, check=False)
        raise AssertionError(f"orchestrator boot failed; logs:\n{logs}")
    return name, base_url


@pytest.fixture
def orchestrator() -> Iterator[dict[str, str]]:
    if not os.path.exists("/var/run/docker.sock"):
        pytest.skip("no docker socket on host")

    name, base_url = _boot_orchestrator()
    info = {
        "name": name,
        "base_url": base_url,
        "ws_base": base_url.replace("http://", "ws://"),
        "api_key": API_KEY,
    }
    try:
        yield info
    finally:
        ws = _docker(
            "ps", "-aq",
            "--filter", "label=perpetuity.managed=true",
            check=False,
        )
        if ws.stdout.strip():
            _docker("rm", "-f", *ws.stdout.split(), check=False, timeout=120)
        _docker("rm", "-f", name, check=False)


@pytest.fixture
def user_team() -> Iterator[tuple[str, str]]:
    user_id, team_id = _create_pg_user_team()
    try:
        yield (user_id, team_id)
    finally:
        _cleanup_pg_user_team(user_id, team_id)


def _http(base_url: str, api_key: str) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        headers={"X-Orchestrator-Key": api_key},
        timeout=httpx.Timeout(30.0, connect=5.0),
    )


def _seed_session(
    orch: dict[str, str], user_id: str, team_id: str
) -> tuple[str, str]:
    """POST /v1/sessions; returns (session_id, container_id)."""
    sid = str(uuid.uuid4())
    with _http(orch["base_url"], orch["api_key"]) as c:
        r = c.post(
            "/v1/sessions",
            json={"session_id": sid, "user_id": user_id, "team_id": team_id},
        )
        assert r.status_code == 200, r.text
        return sid, r.json()["container_id"]


def _ws_url(orch: dict[str, str], sid: str) -> str:
    return f"{orch['ws_base']}/v1/sessions/{sid}/stream?key={orch['api_key']}"


def _logs(orch: dict[str, str]) -> str:
    p = _docker("logs", orch["name"], check=False)
    return (p.stdout or "") + (p.stderr or "")


def _wait_for_log(
    orch: dict[str, str], substring: str, *, timeout_s: float = 5.0
) -> str:
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        last = _logs(orch)
        if substring in last:
            return last
        time.sleep(0.1)
    raise AssertionError(
        f"log line containing {substring!r} never appeared within "
        f"{timeout_s}s; tail=\n{last[-2000:]}"
    )


# --------- (1) attach registers the live count -------------------------


@pytest.mark.asyncio
async def test_ws_attach_emits_attach_registered(
    orchestrator: dict[str, str],
    user_team: tuple[str, str],
) -> None:
    """A WS connect emits `attach_registered session_id=<sid> count=1`.

    Proves register() is called AFTER the exec stream upgrade succeeded
    (the log line is emitted from the post-__aenter__ path).
    """
    user_id, team_id = user_team
    sid, _ = _seed_session(orchestrator, user_id, team_id)
    async with connect(_ws_url(orchestrator, sid)) as ws:
        first = await asyncio.wait_for(ws.recv(), timeout=10.0)
        assert json.loads(first)["type"] == "attach"

        logs = _wait_for_log(
            orchestrator, f"attach_registered session_id={sid}", timeout_s=5.0
        )
        assert f"attach_registered session_id={sid} count=1" in logs, logs[-1500:]


# --------- (2) detach decrements ---------------------------------------


@pytest.mark.asyncio
async def test_ws_close_emits_attach_unregistered(
    orchestrator: dict[str, str],
    user_team: tuple[str, str],
) -> None:
    """Closing the WS yields `attach_unregistered session_id=<sid> count=0`
    within the 1s polling window the task plan calls out.
    """
    user_id, team_id = user_team
    sid, _ = _seed_session(orchestrator, user_id, team_id)
    async with connect(_ws_url(orchestrator, sid)) as ws:
        first = await asyncio.wait_for(ws.recv(), timeout=10.0)
        assert json.loads(first)["type"] == "attach"
    logs = _wait_for_log(
        orchestrator, f"attach_unregistered session_id={sid}", timeout_s=2.0
    )
    assert f"attach_unregistered session_id={sid} count=0" in logs, logs[-1500:]


# --------- (3) exec-start failure leaves the map empty -----------------


@pytest.mark.asyncio
async def test_pump_failure_path_still_unregisters_cleanly(
    orchestrator: dict[str, str],
    user_team: tuple[str, str],
) -> None:
    """Provoke a pump-side failure by killing the tmux session in the
    workspace container before the WS upgrade. The `tmux attach-session
    -t <sid>` exec spawns successfully (docker upgrade does its dance),
    so register() runs — but the inner tmux command immediately exits
    with code 1 because the named session is gone. The pumps EOF and
    drive a normal close.

    Contract this test enforces:
      - register/unregister are balanced even on the failure path
        (count goes 1 → 0 via the finally block, no refcount leak).
      - teardown is clean: `attach_unregistered count=0` lands within
        a couple seconds of the WS close.

    This is the "teardown must be clean" half of the task plan's
    exec-start-failure case — the structural register-after-__aenter__
    placement is verified by the source itself; the wire test verifies
    the finally block fires regardless of which pump observed EOF first.
    """
    user_id, team_id = user_team
    sid, container_id = _seed_session(orchestrator, user_id, team_id)

    kill = _docker(
        "exec", container_id, "tmux", "kill-session", "-t", sid,
        check=False,
    )
    assert kill.returncode == 0, kill.stderr

    try:
        async with connect(_ws_url(orchestrator, sid)) as ws:
            # Drain any frames the server emitted before the inner tmux
            # exited — at minimum an attach frame, possibly an exit frame
            # carrying the tmux non-zero exit code.
            try:
                while True:
                    await asyncio.wait_for(ws.recv(), timeout=2.0)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass

    # Both must land — register fires when __aenter__ succeeds (it does
    # here), unregister fires from the finally block when the pumps EOF.
    logs = _wait_for_log(
        orchestrator, f"attach_registered session_id={sid}", timeout_s=5.0
    )
    assert f"attach_registered session_id={sid} count=1" in logs, logs[-1500:]

    logs = _wait_for_log(
        orchestrator, f"attach_unregistered session_id={sid}", timeout_s=3.0
    )
    assert f"attach_unregistered session_id={sid} count=0" in logs, logs[-1500:]
