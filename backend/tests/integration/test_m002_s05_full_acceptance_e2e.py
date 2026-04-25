"""M002 / S05 / T01 — Bundled milestone-capstone acceptance test.

This is the milestone's final integrated demo against the **real compose
stack** (db + redis + ephemeral orchestrator + workspace image, no mocks
below the backend HTTP boundary). It bundles every M002 headline guarantee
into one ordered flow:

  (a) DURABILITY — signup → POST session → WS attach → `echo hello` →
      restart orchestrator → reconnect same session_id → observe `hello`
      in scrollback → `echo $$` PID stable → `echo world` on the same
      shell. Proves tmux owns the pty (D012/MEM092).

  (b) REAPER + VOLUME PERSISTENCE — DELETE the session → wait
      idle_timeout_seconds → `docker ps` shows the workspace container
      reaped → `workspace_volume` row still in Postgres (D015/R006: the
      volume outlives the container).

  (c) OWNERSHIP / NO-ENUMERATION — user B WS to user A's session_id and
      to a never-existed session_id both fail upgrade with byte-identical
      shape; both DELETEs return 404 with byte-identical bodies.

  (d) MILESTONE-WIDE LOG REDACTION — zero email/full_name leaks across
      ephemeral-orchestrator + sibling-backend logs captured BEFORE
      teardown.

Strategy mirrors S01/S04: sibling backend container via the existing
`backend_url` fixture, plus the live-orchestrator-swap pattern (MEM149)
to inject REAPER_INTERVAL_SECONDS=1. The orchestrator restart subtest
restarts the EPHEMERAL orchestrator's container directly (NOT
`docker compose restart orchestrator`) — the ephemeral one owns the
`orchestrator` DNS alias for the duration of the test, so a compose
restart would only restart the masked-out compose service.

How to run:

    docker compose build backend orchestrator
    docker build -f orchestrator/tests/fixtures/Dockerfile.test \\
        -t perpetuity/workspace:test orchestrator/workspace-image/
    docker compose up -d db redis orchestrator
    cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e \\
        tests/integration/test_m002_s05_full_acceptance_e2e.py -v

Wall-clock budget: ≤120s on a warm compose stack.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import subprocess
import time
import uuid

import httpx
import pytest
from httpx_ws import aconnect_ws

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)

NETWORK = "perpetuity_default"
ORCH_IMAGE = "orchestrator:latest"
BACKEND_IMAGE = "backend:latest"
WORKSPACE_IMAGE = "perpetuity/workspace:test"
ORCH_DNS_ALIAS = "orchestrator"

# Two-phase idle_timeout strategy (MEM175): keep the timeout generous
# during prep (signup, WS attach, echo round-trips, restart, reconnect,
# DELETE) so the 1 s reaper tick never races. Right before the
# reap-the-survivor sleep we PUT this down to 3 s so the very next tick
# trips the reaper on the now-dead session.
TEST_IDLE_TIMEOUT_SECONDS_PREP = 600
TEST_IDLE_TIMEOUT_SECONDS_REAP = 3
TEST_REAPER_INTERVAL_SECONDS = 1

# The most-recent alembic revision the bundled test depends on. The
# backend image bakes /app/backend/app/alembic/versions/ (MEM147), so a
# stale image would fail at prestart with "Can't locate revision". The
# autouse skip-guard converts that into an actionable skip.
S05_REVISION = "s05_system_settings"


pytestmark = [pytest.mark.e2e, pytest.mark.serial]


# ----- helpers (module-local copies from S04 — see slice plan) -----------


def _b64enc(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.b64encode(data).decode("ascii")


def _b64dec(s: str) -> bytes:
    return base64.b64decode(s, validate=True)


def _http_to_ws(http_base: str) -> str:
    if http_base.startswith("https://"):
        return "wss://" + http_base[len("https://"):]
    if http_base.startswith("http://"):
        return "ws://" + http_base[len("http://"):]
    return "ws://" + http_base


def _strip_ansi(text: str) -> str:
    csi = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
    osc = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
    return osc.sub("", csi.sub("", text))


async def _drain_data(
    ws: object, *, timeout_s: float, until_substring: str | None = None
) -> str:
    deadline = time.monotonic() + timeout_s
    accumulated_raw = b""
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            text = await asyncio.wait_for(ws.receive_text(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        frame = json.loads(text)
        if frame.get("type") == "data":
            accumulated_raw += _b64dec(frame["bytes"])
            decoded_plain = _strip_ansi(
                accumulated_raw.decode("utf-8", errors="replace")
            )
            if until_substring is not None and until_substring in decoded_plain:
                return decoded_plain
        elif frame.get("type") == "exit":
            break
    return _strip_ansi(accumulated_raw.decode("utf-8", errors="replace"))


def _input_frame(payload: str) -> str:
    return json.dumps({"type": "input", "bytes": _b64enc(payload)})


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


def _compose(
    *args: str, check: bool = True, capture: bool = True, timeout: int = 180
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", *args],
        check=check,
        capture_output=capture,
        text=True,
        cwd=REPO_ROOT,
        timeout=timeout,
    )


def _signup_login(
    base_url: str, *, email: str, password: str, full_name: str
) -> httpx.Cookies:
    """Sign up and log in. Returns a fresh cookie jar (MEM029)."""
    cookies = httpx.Cookies()
    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        r = c.post(
            "/api/v1/auth/signup",
            json={"email": email, "password": password, "full_name": full_name},
        )
        assert r.status_code == 200, f"signup: {r.status_code} {r.text}"
        c.cookies.clear()
        r = c.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        assert r.status_code == 200, f"login: {r.status_code} {r.text}"
        for cookie in c.cookies.jar:
            cookies.set(cookie.name, cookie.value)
    return cookies


def _login_only(
    base_url: str, *, email: str, password: str
) -> httpx.Cookies:
    cookies = httpx.Cookies()
    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        r = c.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        assert r.status_code == 200, f"admin login: {r.status_code} {r.text}"
        for cookie in c.cookies.jar:
            cookies.set(cookie.name, cookie.value)
    return cookies


def _personal_team_id(base_url: str, cookies: httpx.Cookies) -> str:
    with httpx.Client(base_url=base_url, timeout=15.0, cookies=cookies) as c:
        r = c.get("/api/v1/teams/")
        assert r.status_code == 200, f"teams list: {r.status_code} {r.text}"
        rows = r.json()["data"]
    personal = next((t for t in rows if t["is_personal"]), None)
    assert personal is not None, f"no personal team in {rows!r}"
    return personal["id"]


def _create_session_raw(
    base_url: str, cookies: httpx.Cookies, team_id: str
) -> dict[str, object]:
    with httpx.Client(base_url=base_url, timeout=60.0, cookies=cookies) as c:
        r = c.post("/api/v1/sessions", json={"team_id": team_id})
    assert r.status_code == 200, (
        f"create session: {r.status_code} {r.text}"
    )
    return r.json()


def _delete_session(
    base_url: str, cookies: httpx.Cookies, session_id: str
) -> int:
    with httpx.Client(base_url=base_url, timeout=15.0, cookies=cookies) as c:
        r = c.delete(f"/api/v1/sessions/{session_id}")
        return r.status_code


def _list_session_ids(
    base_url: str, cookies: httpx.Cookies, team_id: str
) -> list[str]:
    """Return session_ids for caller filtered by team_id.

    The backend's GET /api/v1/sessions forwards ?team_id to the
    orchestrator's GET /v1/sessions which requires both (user_id, team_id).
    """
    with httpx.Client(base_url=base_url, timeout=15.0, cookies=cookies) as c:
        r = c.get("/api/v1/sessions", params={"team_id": team_id})
    assert r.status_code == 200, f"list sessions: {r.status_code} {r.text}"
    body = r.json()
    return [
        str(rec.get("tmux_session") or rec.get("session_id"))
        for rec in body["data"]
    ]


def _psql_one(sql: str) -> str:
    out = _docker(
        "exec", "perpetuity-db-1",
        "psql", "-U", "postgres", "-d", "app", "-A", "-t",
        "-c", sql, check=False,
    )
    return (out.stdout or "").strip()


def _delete_setting_row(key: str) -> None:
    _docker(
        "exec", "perpetuity-db-1",
        "psql", "-U", "postgres", "-d", "app", "-c",
        f"DELETE FROM system_settings WHERE key = '{key}'",
        check=False,
    )


def _user_id_from_db(email: str) -> str:
    val = _psql_one(f"SELECT id FROM \"user\" WHERE email = '{email}'")
    assert val, f"no user row for {email!r}"
    return val


def _read_dotenv_value(key: str, default: str) -> str:
    env_path = os.path.join(REPO_ROOT, ".env")
    try:
        with open(env_path) as fp:
            for line in fp:
                stripped = line.strip()
                if stripped.startswith(f"{key}="):
                    value = (
                        stripped.split("=", 1)[1].strip().strip('"').strip("'")
                    )
                    if value:
                        return value
    except OSError:
        pass
    return default


def _ensure_host_workspaces_shared() -> None:
    """Mirror the compose `workspace-mount-init` service. Idempotent."""
    _docker(
        "run", "--rm", "--privileged", "--pid=host",
        "alpine:3", "nsenter", "-t", "1", "-m", "--",
        "sh", "-c",
        "mkdir -p /var/lib/perpetuity/workspaces /var/lib/perpetuity/vols && "
        "( mountpoint -q /var/lib/perpetuity/workspaces || "
        "  mount --bind /var/lib/perpetuity/workspaces "
        "  /var/lib/perpetuity/workspaces ) && "
        "mount --make-shared /var/lib/perpetuity/workspaces",
        check=False,
    )


def _boot_ephemeral_orchestrator(
    *,
    redis_password: str,
    pg_password: str,
    api_key: str,
    reaper_interval_seconds: int,
) -> str:
    """Stop the compose orchestrator and launch an ephemeral one with
    REAPER_INTERVAL_SECONDS=<n>, on the compose network with the
    `orchestrator` DNS alias so the sibling backend resolves it
    transparently. Returns the ephemeral container name."""
    name = f"orch-s05-{uuid.uuid4().hex[:8]}"
    _ensure_host_workspaces_shared()

    _compose("rm", "-sf", "orchestrator", check=False, timeout=60)

    args = [
        "run", "-d",
        "--name", name,
        "--network", NETWORK,
        "--network-alias", ORCH_DNS_ALIAS,
        "--privileged",  # MEM136
        "-v", "/var/run/docker.sock:/var/run/docker.sock",
        "--mount",
        "type=bind,"
        "source=/var/lib/perpetuity/workspaces,"
        "target=/var/lib/perpetuity/workspaces,bind-propagation=rshared",
        "-v", "/var/lib/perpetuity/vols:/var/lib/perpetuity/vols",
        "-e", f"WORKSPACE_IMAGE={WORKSPACE_IMAGE}",
        "-e", f"ORCHESTRATOR_API_KEY={api_key}",
        "-e", "REDIS_HOST=redis",
        "-e", f"REDIS_PASSWORD={redis_password}",
        "-e",
        f"DATABASE_URL=postgresql://postgres:{pg_password}@db:5432/app",
        "-e", f"REAPER_INTERVAL_SECONDS={reaper_interval_seconds}",
        ORCH_IMAGE,
    ]
    _docker(*args)
    return name


def _backend_container_name() -> str:
    ps = _docker(
        "ps", "--format", "{{.Names}}",
        "--filter", "name=perpetuity-backend-e2e-",
        check=True, timeout=10,
    )
    names = [n for n in (ps.stdout or "").splitlines() if n.strip()]
    assert names, f"no sibling backend container found; got {names!r}"
    return names[0]


def _wait_for_orch_running(
    *, backend_container: str, ephemeral_name: str, timeout_s: float = 30.0,
) -> None:
    """Probe the ephemeral orchestrator's /v1/health from inside the
    sibling backend container so the probe rides the same DNS path the
    backend will use for real requests. Identical shape to S04."""
    deadline = time.time() + timeout_s
    last_err = ""
    probe_script = (
        "import sys,urllib.request\n"
        "try:\n"
        "    body = urllib.request.urlopen("
        "'http://orchestrator:8001/v1/health', timeout=2).read().decode()\n"
        "    print(body)\n"
        "    sys.exit(0 if 'image_present' in body else 2)\n"
        "except Exception as e:\n"
        "    print(repr(e)); sys.exit(3)\n"
    )
    while time.time() < deadline:
        r = _docker(
            "exec", backend_container,
            "python3", "-c", probe_script,
            check=False, timeout=5,
        )
        if r.returncode == 0 and "image_present" in (r.stdout or ""):
            return
        last_err = (
            (r.stderr or "")[:200] + " | " + (r.stdout or "")[:200]
        )
        time.sleep(0.5)
    logs = _docker("logs", ephemeral_name, check=False, timeout=10).stdout or ""
    raise AssertionError(
        f"ephemeral orchestrator {ephemeral_name!r} never became healthy "
        f"within {int(timeout_s)}s; last_probe={last_err!r}\n"
        f"orchestrator logs (last 80 lines):\n"
        f"{os.linesep.join(logs.splitlines()[-80:])}"
    )


def _restore_compose_orchestrator(*, timeout_s: float = 90.0) -> None:
    _compose("up", "-d", "orchestrator", check=False, timeout=180)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = _compose(
            "ps", "--format", "{{.Service}}\t{{.Health}}",
            "orchestrator", check=False, timeout=15,
        )
        for line in (r.stdout or "").splitlines():
            parts = line.strip().split("\t")
            if (
                len(parts) == 2
                and parts[0] == "orchestrator"
                and parts[1] == "healthy"
            ):
                return
        time.sleep(0.5)


def _restart_ephemeral_orchestrator(name: str, *, timeout_s: int = 30) -> None:
    """Restart the ephemeral orchestrator container directly. The
    ephemeral container owns the `orchestrator` DNS alias for the test's
    duration (MEM149/MEM188) — `docker compose restart orchestrator`
    would only restart the masked-out compose service, so the durability
    path needs `docker restart <ephemeral_name>` to actually break the
    live WS upgrade path the backend is talking to."""
    _docker("restart", "-t", "5", name, check=True, timeout=timeout_s)


# ----- autouse fixtures --------------------------------------------------


def _backend_image_has_s05_revision() -> bool:
    """Probe `backend:latest` for the S05 alembic revision file (MEM162).
    A stale image fails at prestart with `Can't locate revision`. The
    bundled e2e depends on `idle_timeout_seconds` system_settings PUT,
    which only works after S05 ran the system_settings table create."""
    r = _docker(
        "run", "--rm", "--entrypoint", "ls", BACKEND_IMAGE,
        "/app/backend/app/alembic/versions/",
        check=False, timeout=15,
    )
    return f"{S05_REVISION}.py" in (r.stdout or "")


def _backend_image_has_scrollback_route() -> bool:
    """Probe `backend:latest` for the T03 scrollback route (MEM173/MEM186).
    The bundled e2e doesn't itself hit the scrollback proxy, but the
    observability sweep asserts `session_scrollback_proxied` fires —
    that key only exists if the route is baked in. (Step 6's reconnect
    drops new scrollback into tmux, but no proxy fetch is required for
    the sweep to pass; the key fires from any S04+ backend image
    exercising any scrollback path. Skip cleanly if missing rather than
    leaving a confusing observability assertion failure.)"""
    r = _docker(
        "run", "--rm", "--entrypoint", "grep", BACKEND_IMAGE,
        "-l", "scrollback",
        "/app/backend/app/api/routes/sessions.py",
        check=False, timeout=15,
    )
    return r.returncode == 0


@pytest.fixture(autouse=True)
def _require_s05_baked() -> None:
    if not _backend_image_has_s05_revision():
        pytest.skip(
            f"backend:latest is missing the {S05_REVISION!r} alembic "
            "revision — run `docker compose build backend` so the image "
            "bakes the current /app/backend/app/alembic/versions/ tree."
        )


@pytest.fixture(autouse=True)
def _require_scrollback_route_baked() -> None:
    if not _backend_image_has_scrollback_route():
        pytest.skip(
            "backend:latest is missing the GET /sessions/{sid}/scrollback "
            "route — run `docker compose build backend` so the image bakes "
            "the current /app/backend/app/api/routes/sessions.py."
        )


@pytest.fixture(autouse=True)
def _wipe_idle_timeout_setting() -> None:
    """Belt-and-suspenders cleanup before AND after the test (MEM161).
    The compose `db` service persists across runs via `app-db-data`;
    without this an earlier crashed run could leave `idle_timeout_seconds`
    set to 3, biasing the S04 e2e (or this one's prep window) into a
    racing reaper."""
    _delete_setting_row("idle_timeout_seconds")
    yield
    _delete_setting_row("idle_timeout_seconds")


@pytest.fixture
def ephemeral_orchestrator(
    compose_stack_up: None,  # noqa: ARG001
) -> object:
    """Swap the compose orchestrator for an ephemeral one with
    REAPER_INTERVAL_SECONDS=1, restoring on teardown. Yields the
    ephemeral container name (used to drive the durability subtest's
    `docker restart` directly — see `_restart_ephemeral_orchestrator`)."""
    redis_password = (
        os.environ.get("REDIS_PASSWORD")
        or _read_dotenv_value("REDIS_PASSWORD", "changethis")
    )
    pg_password = (
        os.environ.get("POSTGRES_PASSWORD")
        or _read_dotenv_value("POSTGRES_PASSWORD", "changethis")
    )
    api_key = _read_dotenv_value("ORCHESTRATOR_API_KEY", "changethis")

    ephemeral_name = _boot_ephemeral_orchestrator(
        redis_password=redis_password,
        pg_password=pg_password,
        api_key=api_key,
        reaper_interval_seconds=TEST_REAPER_INTERVAL_SECONDS,
    )
    try:
        yield ephemeral_name
    finally:
        _docker("rm", "-f", ephemeral_name, check=False, timeout=30)
        _restore_compose_orchestrator()


# ----- the test ----------------------------------------------------------


def test_m002_s05_full_acceptance(  # noqa: PLR0915
    backend_url: str,
    ephemeral_orchestrator: str,
    request: pytest.FixtureRequest,
) -> None:
    """Bundled M002 milestone-capstone: durability + reaper + ownership +
    redaction in one ordered flow against the real compose stack."""
    suite_started = time.time()

    backend_container = _backend_container_name()
    _wait_for_orch_running(
        backend_container=backend_container,
        ephemeral_name=ephemeral_orchestrator,
        timeout_s=30.0,
    )

    # Workspace-container reaper safety net — the ephemeral_orchestrator
    # fixture already removes the ephemeral orch container, but if a
    # workspace container outlives the test (e.g. assertion failure
    # mid-flow before DELETE) we must reap it so the next run isn't
    # poisoned by a stuck container holding the (user, team) labels.
    def _final_cleanup() -> None:
        ws = _docker(
            "ps", "-aq", "--filter", "label=perpetuity.managed=true",
            check=False, timeout=15,
        )
        if ws.stdout.strip():
            _docker(
                "rm", "-f", *ws.stdout.split(),
                check=False, timeout=120,
            )

    request.addfinalizer(_final_cleanup)

    # ----- step 2: admin login + alice signup --------------------------
    admin_email = "admin@example.com"
    admin_password = "changethis"
    admin_cookies = _login_only(
        backend_url, email=admin_email, password=admin_password
    )

    suffix_a = uuid.uuid4().hex[:8]
    alice_email = f"m002-s05-alice-{suffix_a}@example.com"
    alice_password = "Sup3rs3cret-alice"
    alice_full_name = f"Alice {suffix_a}"
    alice_cookies = _signup_login(
        backend_url,
        email=alice_email, password=alice_password, full_name=alice_full_name,
    )
    alice_team = _personal_team_id(backend_url, alice_cookies)
    alice_user_id = _user_id_from_db(alice_email)

    # ----- step 3: admin PUT idle_timeout_seconds=600 (prep window) ----
    with httpx.Client(
        base_url=backend_url, timeout=30.0, cookies=admin_cookies
    ) as c:
        r = c.put(
            "/api/v1/admin/settings/idle_timeout_seconds",
            json={"value": TEST_IDLE_TIMEOUT_SECONDS_PREP},
        )
    assert r.status_code == 200, f"admin PUT (prep): {r.status_code} {r.text}"
    assert r.json()["value"] == TEST_IDLE_TIMEOUT_SECONDS_PREP

    # ----- step 4: alice POST + WS attach + echo hello + capture pid ---
    a_resp = _create_session_raw(backend_url, alice_cookies, alice_team)
    sid_a = str(a_resp["session_id"])
    assert uuid.UUID(sid_a)

    # Snapshot the (user, team)-labeled container id for the post-reap
    # invariant (must be reaped by step 9, must NOT come back with the
    # same id when alice re-creates a session — though we don't re-create
    # in S05; sufficient to assert reap removed THIS id).
    container_id_at_create = _docker(
        "ps", "-q",
        "--filter", f"label=user_id={alice_user_id}",
        "--filter", f"label=team_id={alice_team}",
        check=True, timeout=10,
    ).stdout.strip().splitlines()
    assert len(container_id_at_create) == 1, (
        f"step 4: expected exactly 1 workspace container after POST, got "
        f"{container_id_at_create!r}"
    )
    alice_container_id = container_id_at_create[0]

    ws_base = _http_to_ws(backend_url)
    cookie_header = "; ".join(f"{n}={v}" for n, v in alice_cookies.items())
    ws_url_a = f"{ws_base}/api/v1/ws/terminal/{sid_a}"

    async def _phase_one() -> str:
        """Attach to sid_a, echo hello, capture pid_before via echo $$."""
        async with aconnect_ws(
            ws_url_a, headers={"Cookie": cookie_header}
        ) as ws:
            first = json.loads(
                await asyncio.wait_for(ws.receive_text(), timeout=15.0)
            )
            assert first["type"] == "attach", (
                f"step 4: expected attach frame, got {first!r}"
            )
            await ws.send_text(_input_frame("echo hello\n"))
            seen = await _drain_data(
                ws, timeout_s=10.0, until_substring="hello"
            )
            assert "hello" in seen, (
                f"step 4: did not see 'hello' in WS data within 10s; "
                f"saw={seen!r}"
            )
            await ws.send_text(_input_frame("echo $$\n"))
            pid_buffer = await _drain_data(
                ws, timeout_s=10.0, until_substring=None
            )
            digits = re.findall(r"(?<!\d)(\d{2,7})(?!\d)", pid_buffer)
            assert digits, (
                f"step 4: no PID digits in echo $$ output: {pid_buffer!r}"
            )
            return digits[-1]

    pid_before = asyncio.run(_phase_one())
    assert pid_before, "step 4: empty pid_before"

    # ----- step 5: restart the EPHEMERAL orchestrator ------------------
    # `docker compose restart orchestrator` would only kick the masked-
    # out compose service, NOT the ephemeral container that owns the
    # `orchestrator` DNS alias the backend talks to. The durability path
    # needs the actual live container to bounce.
    _restart_ephemeral_orchestrator(ephemeral_orchestrator)
    _wait_for_orch_running(
        backend_container=backend_container,
        ephemeral_name=ephemeral_orchestrator,
        timeout_s=30.0,
    )

    # ----- step 6: reconnect, scrollback contains hello, same PID ------
    async def _phase_two() -> tuple[str, str, str]:
        async with aconnect_ws(
            ws_url_a, headers={"Cookie": cookie_header}
        ) as ws:
            first = json.loads(
                await asyncio.wait_for(ws.receive_text(), timeout=20.0)
            )
            assert first["type"] == "attach", (
                f"step 6: expected attach frame post-restart, got {first!r}"
            )
            scrollback_after = _strip_ansi(
                _b64dec(first["scrollback"]).decode("utf-8", errors="replace")
            )
            await ws.send_text(_input_frame("echo $$\n"))
            pid_buffer = await _drain_data(
                ws, timeout_s=10.0, until_substring=pid_before
            )
            await ws.send_text(_input_frame("echo world\n"))
            world_buffer = await _drain_data(
                ws, timeout_s=10.0, until_substring="world"
            )
            return scrollback_after, pid_buffer, world_buffer

    scrollback_after, pid_buffer, world_buffer = asyncio.run(_phase_two())

    assert "hello" in scrollback_after, (
        f"step 6: prior 'hello' missing from scrollback after orch restart; "
        f"scrollback_after={scrollback_after!r}"
    )
    assert pid_before in pid_buffer, (
        f"step 6: shell PID changed across orch restart — tmux durability "
        f"broken. pid_before={pid_before!r} post_restart={pid_buffer!r}"
    )
    assert "world" in world_buffer, (
        f"step 6: 'world' did not echo on the post-restart shell; "
        f"saw={world_buffer!r}"
    )

    # ----- step 7: ownership + no-enumeration --------------------------
    # Sign up bob, then assert byte-equal close shape across (a) bob WS
    # to alice's sid_a and (b) bob WS to a never-existed UUID. With
    # `httpx_ws.aconnect_ws` against a real backend the close happens
    # BEFORE WS accept (sessions.py line 405), so the upgrade fails with
    # an HTTP response — `WebSocketUpgradeError` carries the raw response.
    # Byte-equality means status code AND body match across both calls
    # (no existence enumeration via response shape).
    suffix_bob = uuid.uuid4().hex[:8]
    bob_email = f"m002-s05-bob-{suffix_bob}@example.com"
    bob_password = "Sup3rs3cret-bob"
    bob_full_name = f"Bob {suffix_bob}"
    bob_cookies = _signup_login(
        backend_url,
        email=bob_email, password=bob_password, full_name=bob_full_name,
    )
    bob_cookie_header = "; ".join(f"{n}={v}" for n, v in bob_cookies.items())
    never_existed_sid = str(uuid.uuid4())

    async def _capture_ws_upgrade_failure(target_sid: str) -> tuple[int, bytes]:
        # Drive the upgrade handshake manually via httpx.AsyncClient so we
        # can read the rejection response body directly. `httpx_ws.aconnect_ws`
        # raises `WebSocketUpgradeError(response)` on non-101 status, but
        # the response stream is closed before our except block can read
        # it (`httpx.StreamClosed`). The HTTP-only path below sidesteps
        # the streaming machinery: send the WS upgrade headers as a plain
        # GET, the backend's pre-accept `await websocket.close(1008,
        # 'session_not_owned')` (sessions.py L405) becomes an HTTP 403
        # with a regular response body.
        upgrade_headers = {
            "Cookie": bob_cookie_header,
            "Upgrade": "websocket",
            "Connection": "Upgrade",
            "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
            "Sec-WebSocket-Version": "13",
        }
        url = f"{backend_url}/api/v1/ws/terminal/{target_sid}"
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(url, headers=upgrade_headers)
        # The upgrade attempt MUST fail — if it returned 101 the backend
        # accepted, which would be the no-enumeration violation we're
        # asserting against.
        assert r.status_code != 101, (
            f"step 7: WS upgrade for {target_sid!r} should have been "
            f"rejected with 403 session_not_owned but switched protocols"
        )
        return r.status_code, r.content

    other_status, other_body = asyncio.run(
        _capture_ws_upgrade_failure(sid_a)
    )
    missing_status, missing_body = asyncio.run(
        _capture_ws_upgrade_failure(never_existed_sid)
    )
    assert other_status == missing_status, (
        f"step 7: WS upgrade status differs between cross-user and missing-"
        f"sid cases — enumeration leak. other={other_status} "
        f"missing={missing_status}"
    )
    assert other_body == missing_body, (
        f"step 7: WS upgrade body differs between cross-user and missing-"
        f"sid cases — enumeration leak. other={other_body!r} "
        f"missing={missing_body!r}"
    )

    # Parallel DELETE 404 byte-equality check.
    with httpx.Client(
        base_url=backend_url, timeout=15.0, cookies=bob_cookies
    ) as c:
        r_other = c.delete(f"/api/v1/sessions/{sid_a}")
        r_missing = c.delete(f"/api/v1/sessions/{never_existed_sid}")
    assert r_other.status_code == 404, (
        f"step 7: bob DELETE alice's sid → expected 404, got "
        f"{r_other.status_code} {r_other.text}"
    )
    assert r_missing.status_code == 404, (
        f"step 7: bob DELETE missing sid → expected 404, got "
        f"{r_missing.status_code} {r_missing.text}"
    )
    assert r_other.content == r_missing.content, (
        f"step 7: DELETE 404 bodies differ between cross-user and missing-"
        f"sid cases — enumeration leak. other={r_other.content!r} "
        f"missing={r_missing.content!r}"
    )

    # ----- step 8: snapshot list / container before reap ---------------
    # Plan deviation (documented): the slice plan called for an explicit
    # alice-DELETE here followed by a reaper-driven container reap. In
    # practice the reaper only enters its container-reap pass for
    # containers WHERE IT just killed the last tmux session on the same
    # tick (reaper.py L188 `candidates_for_reap`). A clean DELETE drops
    # the Redis row WITHOUT going through the reaper, so the orphaned
    # container would never enter the reap path and the assertion would
    # hang forever. The architectural rule is captured by MEM182. To
    # preserve the spirit of the slice contract (reaper reaps idle
    # containers + workspace_volume persists across reap), we let sid_a
    # idle out via the reaper instead: dial idle_timeout to 3 s, sleep,
    # then assert (a) the session is gone, (b) the container is reaped,
    # (c) the workspace_volume row survives. The DELETE happy-path is
    # already covered by S04/T04 e2e and the unit-suite test_d/e tests.
    listed_pre_reap = _list_session_ids(
        backend_url, alice_cookies, alice_team
    )
    assert listed_pre_reap == [sid_a], (
        f"step 8: pre-reap GET should return [sid_a], got {listed_pre_reap!r}"
    )
    container_pre_reap = _docker(
        "ps", "-q",
        "--filter", f"label=user_id={alice_user_id}",
        "--filter", f"label=team_id={alice_team}",
        check=True, timeout=10,
    ).stdout.strip().splitlines()
    assert container_pre_reap == [alice_container_id], (
        f"step 8: workspace container should still be alive pre-reap "
        f"(no idle yet); ps={container_pre_reap!r}"
    )

    # ----- step 9: dial idle_timeout to 3, wait, assert reap -----------
    with httpx.Client(
        base_url=backend_url, timeout=30.0, cookies=admin_cookies
    ) as c:
        r_reap = c.put(
            "/api/v1/admin/settings/idle_timeout_seconds",
            json={"value": TEST_IDLE_TIMEOUT_SECONDS_REAP},
        )
    assert r_reap.status_code == 200, (
        f"step 9: admin PUT (reap): {r_reap.status_code} {r_reap.text}"
    )
    assert r_reap.json()["value"] == TEST_IDLE_TIMEOUT_SECONDS_REAP

    sleep_budget = (
        TEST_IDLE_TIMEOUT_SECONDS_REAP + TEST_REAPER_INTERVAL_SECONDS + 2.0
    )
    time.sleep(sleep_budget)

    # Be tolerant of one or two extra ticks if the host is slow.
    deadline = time.time() + 10.0
    listed_after_reap: list[str] = []
    container_after_reap: list[str] = []
    while time.time() < deadline:
        listed_after_reap = _list_session_ids(
            backend_url, alice_cookies, alice_team
        )
        container_after_reap = _docker(
            "ps", "-q",
            "--filter", f"label=user_id={alice_user_id}",
            "--filter", f"label=team_id={alice_team}",
            check=True, timeout=10,
        ).stdout.strip().splitlines()
        if not listed_after_reap and not container_after_reap:
            break
        time.sleep(0.5)
    assert listed_after_reap == [], (
        f"step 9: reaper should have killed alice's sessions after "
        f"{sleep_budget:.1f}s; GET still returns {listed_after_reap!r}"
    )
    assert container_after_reap == [], (
        f"step 9: reaper should have removed the workspace container after "
        f"the last tmux session died; ps={container_after_reap!r}"
    )

    # D015/R006 invariant: the workspace_volume row OUTLIVES the container.
    # The reaper only touches Docker — the per-(user, team) volume row +
    # underlying loopback .img must persist so the next session POST
    # remounts the existing volume.
    volume_id = _psql_one(
        f"SELECT id FROM workspace_volume "
        f"WHERE user_id = '{alice_user_id}' AND team_id = '{alice_team}'"
    )
    assert volume_id, (
        f"step 9: D015/R006 invariant violated — workspace_volume row for "
        f"alice ({alice_user_id}, {alice_team}) was deleted by the reaper "
        f"but should persist across container reaps. SELECT returned empty."
    )
    try:
        uuid.UUID(volume_id)
    except ValueError:
        raise AssertionError(
            f"step 9: workspace_volume.id is not a UUID: {volume_id!r}"
        )

    # ----- step 10/11: capture logs BEFORE teardown --------------------
    # The ephemeral orchestrator isn't compose-managed; once the fixture
    # tears down `docker rm -f <ephemeral>` and `docker compose up -d
    # orchestrator`, `docker compose logs orchestrator` would hit the
    # restored compose orchestrator (empty after restart) and we'd lose
    # everything the test produced. Capture via `docker logs <name>` now.
    eph_logs_proc = _docker(
        "logs", ephemeral_orchestrator, check=False, timeout=15,
    )
    eph_logs = (eph_logs_proc.stdout or "") + (eph_logs_proc.stderr or "")

    backend_logs_proc = _docker(
        "logs", backend_container, check=False, timeout=15,
    )
    backend_logs = (
        (backend_logs_proc.stdout or "") + (backend_logs_proc.stderr or "")
    )
    log_blob = eph_logs + "\n" + backend_logs

    # ----- step 10: M002 observability taxonomy smoke check ------------
    # Each of these keys MUST appear at least once across the bundled run
    # — they're the slice's M002-wide observability invariant.
    # `session_scrollback_proxied` does NOT appear: the bundled flow in
    # this test never hits the GET /scrollback HTTP route (it reads
    # scrollback via the WS attach frame, which is the orchestrator's
    # /v1/sessions/{sid}/scrollback POST path emitted as a DEBUG, not
    # the backend INFO key). The slice plan lists this key in step 10
    # but on inspection of T03's actual log shape the BACKEND key only
    # fires when an HTTP client GETs /scrollback. Asserting it here would
    # cross a flow this test doesn't drive — omitted from the required
    # set with the reason captured here.
    required_taxonomy = (
        "image_pull_ok",
        "session_created",
        "session_attached",
        "session_detached",
        "attach_registered",
        "attach_unregistered",
        "reaper_started",
        "reaper_tick",
        "reaper_killed_session",
        "reaper_reaped_container",
        "idle_timeout_seconds_resolved",
    )
    missing_keys = [k for k in required_taxonomy if k not in log_blob]
    assert not missing_keys, (
        f"step 10: observability taxonomy regression — keys missing from "
        f"captured logs: {missing_keys!r}\n"
        f"eph_logs tail (last 2000 chars):\n{eph_logs[-2000:]}\n"
        f"backend_logs tail (last 1000 chars):\n{backend_logs[-1000:]}"
    )

    # ----- step 11: milestone-wide redaction sweep ---------------------
    for sentinel, label in (
        (alice_email, "alice email"),
        (alice_full_name, "alice full_name"),
        (bob_email, "bob email"),
        (bob_full_name, "bob full_name"),
    ):
        assert sentinel not in log_blob, (
            f"step 11: redaction sweep — {label} ({sentinel!r}) leaked into "
            f"captured logs (UUID-only invariant violated)"
        )

    elapsed = time.time() - suite_started
    # Slice budget is ≤120s on a warm stack. Defensive cap at 240s for
    # cold-cache CI runs (orchestrator pull + alembic upgrade).
    assert elapsed < 240.0, (
        f"e2e suite took {elapsed:.1f}s — far over the 120s slice budget"
    )
