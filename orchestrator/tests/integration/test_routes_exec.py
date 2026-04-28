"""Integration tests for the M005/S02 one-shot exec route.

POST /v1/sessions/{session_id}/exec wraps a cmd as
``script -q /dev/null sh -c '<shell-quoted cmd>'`` and runs it inside the
(user, team) workspace container with secrets passed via env. These tests
boot a fresh orchestrator + spawn a real workspace container and assert:

  1. Happy path: ``["echo", "$WHAT"]`` + ``env={"WHAT": "ok"}`` returns
     ``stdout`` containing "ok" and exit_code=0; duration_ms is plausible.
  2. Non-zero exit: ``sh -c "exit 7"`` returns exit_code=7 (CLI-nonzero
     error_class shape — distinct from a daemon failure).
  3. Secret discipline: api-key style env values do NOT appear in
     orchestrator logs (cmd, env values, stdout are all redacted by
     contract — only the action/session_id/exit/duration leak).
  4. Timeout: a 1s ``sleep 5`` invocation with timeout_seconds=2 returns
     a 504 with ``oneshot_exec_timeout`` rather than waiting for the
     full 5s.
  5. Auth: missing / bogus X-Orchestrator-Key → 401 (middleware contract).
  6. Validation: malformed cmd / oversized env → 422.
  7. Container reuse: a second exec call with the same (user, team)
     reuses the existing workspace container (no new container created).

Tests reuse the boot/teardown helpers from ``test_sessions_lifecycle.py``
where possible, with light copies where the imports cross-pollinate
fixtures with broader behavior than this suite needs.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator

import httpx
import pytest

ORCH_IMAGE = "orchestrator:latest"
WORKSPACE_IMAGE = "perpetuity/workspace:test"
NETWORK = "perpetuity_default"
API_KEY = "integration-test-routes-exec-key"


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


def _ensure_host_workspaces_shared() -> None:
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


def _boot_orchestrator() -> tuple[str, str]:
    name = f"orch-exec-{uuid.uuid4().hex[:8]}"
    label = f"perpetuity-routes-exec={uuid.uuid4().hex[:12]}"
    host_port = _free_port()
    redis_password = os.environ.get("REDIS_PASSWORD") or "changeme"
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

    args: list[str] = [
        "run", "-d",
        "--name", name,
        "--label", label,
        "--network", NETWORK,
        "-p", f"{host_port}:8001",
        "-v", "/var/run/docker.sock:/var/run/docker.sock",
        "--mount", f"type=bind,source={host_workspace_root},"
                   f"target={host_workspace_root},bind-propagation=rshared",
        "-v", f"{host_vols_dir}:{host_vols_dir}",
        "--privileged",
        "-e", f"WORKSPACE_IMAGE={WORKSPACE_IMAGE}",
        "-e", f"ORCHESTRATOR_API_KEY={API_KEY}",
        "-e", "REDIS_HOST=redis",
        "-e", f"REDIS_PASSWORD={redis_password}",
        "-e", f"DATABASE_URL={database_url}",
        ORCH_IMAGE,
    ]
    _docker(*args)
    base_url = f"http://localhost:{host_port}"
    try:
        _wait_for_health(base_url)
    except Exception:
        logs = _docker("logs", name, check=False).stdout or ""
        _docker("rm", "-f", name, check=False)
        raise AssertionError(f"orchestrator boot failed; logs:\n{logs}")
    return name, base_url


def _psql_query(sql: str) -> str:
    pg_user = os.environ.get("POSTGRES_USER") or "postgres"
    pg_db = os.environ.get("POSTGRES_DB") or "app"
    out = _docker(
        "exec", "perpetuity-db-1",
        "psql", "-U", pg_user, "-d", pg_db, "-A", "-t", "-c", sql,
        check=False,
    )
    return (out.stdout or "").strip()


def _create_pg_user_team() -> tuple[str, str]:
    user_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())
    user_email = f"exec-{uuid.uuid4().hex[:8]}@test.local"
    team_name = f"exec-team-{uuid.uuid4().hex[:8]}"
    _psql_query(
        f"INSERT INTO \"user\" (id, email, hashed_password, is_active, role, full_name) "
        f"VALUES ('{user_id}', '{user_email}', 'x', true, 'user', 'Exec Test')"
    )
    _psql_query(
        f"INSERT INTO team (id, name, slug, is_personal) "
        f"VALUES ('{team_id}', '{team_name}', '{team_name}', false)"
    )
    return user_id, team_id


def _cleanup_pg_user_team(user_id: str, team_id: str) -> None:
    _psql_query(f"DELETE FROM team WHERE id = '{team_id}'")
    _psql_query(f"DELETE FROM \"user\" WHERE id = '{user_id}'")


@pytest.fixture
def orchestrator() -> Iterator[dict[str, str]]:
    if not os.path.exists("/var/run/docker.sock"):
        pytest.skip("no docker socket on host")
    name, base_url = _boot_orchestrator()
    info = {"name": name, "base_url": base_url, "api_key": API_KEY}
    try:
        yield info
    finally:
        ws = _docker(
            "ps", "-aq", "--filter", "label=perpetuity.managed=true",
            check=False,
        )
        if ws.stdout.strip():
            _docker(
                "rm", "-f", *ws.stdout.split(), check=False, timeout=120,
            )
        _docker("rm", "-f", name, check=False)


@pytest.fixture
def user_team() -> Iterator[tuple[str, str]]:
    if not os.path.exists("/var/run/docker.sock"):
        pytest.skip("no docker socket on host")
    user_id, team_id = _create_pg_user_team()
    try:
        yield (user_id, team_id)
    finally:
        _cleanup_pg_user_team(user_id, team_id)


def _client(base_url: str, api_key: str) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        headers={"X-Orchestrator-Key": api_key},
        timeout=httpx.Timeout(30.0, connect=5.0),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_oneshot_exec_happy_path(
    orchestrator: dict[str, str], user_team: tuple[str, str]
) -> None:
    """``echo "$WHAT"`` with env ``WHAT=ok`` returns stdout containing 'ok',
    exit_code 0, plausible duration_ms.

    The cmd shape ``["echo", "$WHAT"]`` exercises the bare-var-ref path
    in routes_exec._build_script_cmd: the wrapper produces
    ``script -q /dev/null sh -c 'echo "$WHAT"'`` so the shell expands
    $WHAT against the env dict — secret-passing pattern (MEM274).
    """
    user_id, team_id = user_team
    sid = str(uuid.uuid4())

    with _client(orchestrator["base_url"], orchestrator["api_key"]) as c:
        r = c.post(
            f"/v1/sessions/{sid}/exec",
            json={
                "user_id": user_id,
                "team_id": team_id,
                "cmd": ["echo", "$WHAT"],
                "env": {"WHAT": "ok"},
                "timeout_seconds": 30,
                "action": "shell",
            },
            timeout=60.0,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["exit_code"] == 0, body
    # script -q wraps newlines as CRLF on Linux; tolerate either shape.
    assert "ok" in body["stdout"], f"stdout={body['stdout']!r}"
    assert isinstance(body["duration_ms"], int)
    assert body["duration_ms"] >= 0
    assert body["duration_ms"] < 30_000


def test_oneshot_exec_non_zero_exit(
    orchestrator: dict[str, str], user_team: tuple[str, str]
) -> None:
    """A CLI that exits 7 surfaces exit_code=7, not a 5xx. M005's executor
    distinguishes ``cli_nonzero`` from ``orchestrator_exec_failed`` based on
    HTTP status — a non-zero exit must be a clean 200.
    """
    user_id, team_id = user_team
    sid = str(uuid.uuid4())
    with _client(orchestrator["base_url"], orchestrator["api_key"]) as c:
        r = c.post(
            f"/v1/sessions/{sid}/exec",
            json={
                "user_id": user_id,
                "team_id": team_id,
                "cmd": ["sh", "-c", "exit 7"],
                "env": {},
                "timeout_seconds": 10,
                "action": "shell",
            },
            timeout=30.0,
        )
    assert r.status_code == 200, r.text
    assert r.json()["exit_code"] == 7


def test_oneshot_exec_secret_not_logged(
    orchestrator: dict[str, str], user_team: tuple[str, str]
) -> None:
    """An ``ANTHROPIC_API_KEY`` value passed via env never appears in the
    orchestrator log stream. The cmd, env values, and stdout are redacted
    by contract — only ``oneshot_exec_started`` / ``oneshot_exec_completed``
    with the action + session_id + exit + duration are emitted.
    """
    user_id, team_id = user_team
    sid = str(uuid.uuid4())
    secret_marker = f"sk-ant-DO-NOT-LOG-{uuid.uuid4().hex}"

    with _client(orchestrator["base_url"], orchestrator["api_key"]) as c:
        r = c.post(
            f"/v1/sessions/{sid}/exec",
            json={
                "user_id": user_id,
                "team_id": team_id,
                # Echo the env value as the body of stdout — this proves
                # the env-var passthrough but ALSO means the secret would
                # show in stdout. Critical: the orchestrator must not log
                # stdout (T03's step_runs persists it; the orch log line
                # carries only exit + duration).
                "cmd": ["echo", "$ANTHROPIC_API_KEY"],
                "env": {"ANTHROPIC_API_KEY": secret_marker},
                "timeout_seconds": 10,
                "action": "claude",
            },
            timeout=30.0,
        )
    assert r.status_code == 200, r.text
    # Sanity: stdout did receive the secret (env passthrough works).
    assert secret_marker in r.json()["stdout"]

    # Now scrape the orchestrator's docker logs for the secret marker.
    logs = _docker("logs", orchestrator["name"], check=False).stdout or ""
    err_logs = _docker("logs", orchestrator["name"], check=False).stderr or ""
    full_logs = logs + err_logs
    assert secret_marker not in full_logs, (
        "secret leaked into orchestrator log stream: "
        + full_logs[-2000:]
    )
    # And the structured INFO lines we DO expect.
    assert f"oneshot_exec_started session_id={sid}" in full_logs
    assert f"oneshot_exec_completed session_id={sid}" in full_logs


def test_oneshot_exec_timeout(
    orchestrator: dict[str, str], user_team: tuple[str, str]
) -> None:
    """A ``sleep 5`` with timeout_seconds=2 returns 504 ``oneshot_exec_timeout``
    well before the 5s.
    """
    user_id, team_id = user_team
    sid = str(uuid.uuid4())
    started = time.monotonic()
    with _client(orchestrator["base_url"], orchestrator["api_key"]) as c:
        r = c.post(
            f"/v1/sessions/{sid}/exec",
            json={
                "user_id": user_id,
                "team_id": team_id,
                "cmd": ["sh", "-c", "sleep 5"],
                "env": {},
                "timeout_seconds": 2,
                "action": "shell",
            },
            timeout=30.0,
        )
    elapsed = time.monotonic() - started
    assert r.status_code == 504, r.text
    body = r.json()
    assert body["detail"]["code"] == "oneshot_exec_timeout"
    assert body["detail"]["timeout_seconds"] == 2
    # 2s timeout + provision overhead < 5s sleep — anything close to 5
    # would mean we waited for the cmd rather than enforcing the cap.
    assert elapsed < 5.0, f"timeout did not fire promptly; elapsed={elapsed:.2f}s"


def test_oneshot_exec_unauthorized(orchestrator: dict[str, str]) -> None:
    """Missing X-Orchestrator-Key → 401 (middleware contract; no body leak)."""
    sid = str(uuid.uuid4())
    r = httpx.post(
        f"{orchestrator['base_url']}/v1/sessions/{sid}/exec",
        json={
            "user_id": str(uuid.uuid4()),
            "team_id": str(uuid.uuid4()),
            "cmd": ["echo", "x"],
            "env": {},
            "timeout_seconds": 10,
        },
        timeout=10.0,
    )
    assert r.status_code == 401


def test_oneshot_exec_validation_oversized_env(
    orchestrator: dict[str, str], user_team: tuple[str, str]
) -> None:
    """env with too many entries → 422.

    The bound is documented in routes_exec — protects the orchestrator's
    heap from a misbehaving caller.
    """
    user_id, team_id = user_team
    sid = str(uuid.uuid4())
    too_many_env = {f"K{i}": "v" for i in range(200)}  # > _MAX_ENV_ENTRIES (64)
    with _client(orchestrator["base_url"], orchestrator["api_key"]) as c:
        r = c.post(
            f"/v1/sessions/{sid}/exec",
            json={
                "user_id": user_id,
                "team_id": team_id,
                "cmd": ["echo", "x"],
                "env": too_many_env,
                "timeout_seconds": 10,
            },
            timeout=30.0,
        )
    assert r.status_code == 422, r.text


def test_oneshot_exec_validation_malformed_uuid(
    orchestrator: dict[str, str],
) -> None:
    """Malformed UUID in body → 422 (pydantic)."""
    sid = str(uuid.uuid4())
    with _client(orchestrator["base_url"], orchestrator["api_key"]) as c:
        r = c.post(
            f"/v1/sessions/{sid}/exec",
            json={
                "user_id": "not-a-uuid",
                "team_id": str(uuid.uuid4()),
                "cmd": ["echo", "x"],
                "env": {},
                "timeout_seconds": 10,
            },
            timeout=30.0,
        )
    assert r.status_code == 422, r.text


def test_oneshot_exec_reuses_workspace_container(
    orchestrator: dict[str, str], user_team: tuple[str, str]
) -> None:
    """Two exec calls for the same (user, team) reuse the same workspace
    container — `provision_container` is idempotent and the route must
    not race-create a duplicate.
    """
    user_id, team_id = user_team
    with _client(orchestrator["base_url"], orchestrator["api_key"]) as c:
        for _ in range(2):
            sid = str(uuid.uuid4())
            r = c.post(
                f"/v1/sessions/{sid}/exec",
                json={
                    "user_id": user_id,
                    "team_id": team_id,
                    "cmd": ["echo", "ok"],
                    "env": {},
                    "timeout_seconds": 10,
                    "action": "shell",
                },
                timeout=60.0,
            )
            assert r.status_code == 200, r.text
            assert r.json()["exit_code"] == 0

    # Exactly one workspace container exists for this (user, team) pair.
    out = _docker(
        "ps", "-aq",
        "--filter", f"label=user_id={user_id}",
        "--filter", f"label=team_id={team_id}",
        "--filter", "label=perpetuity.managed=true",
        check=False,
    )
    ids = [line for line in (out.stdout or "").split() if line]
    assert len(ids) == 1, f"expected 1 workspace container, got {len(ids)}: {ids!r}"
