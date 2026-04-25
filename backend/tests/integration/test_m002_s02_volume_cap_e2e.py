"""M002 / S02 / T04 — Volume hard-cap end-to-end acceptance test.

Slice S02's demo-truth statement: a workspace with size_gb=1 honors a
kernel-enforced hard cap, neighbors are isolated, the workspace_volume row
matches disk, container resource limits hold, and observability logs do
not leak PII.

Stitches the slice together against the **real compose stack** (NOT
TestClient). Two users on two teams provision sessions through the
backend; alice's container is backed by a 1 GiB ext4 .img and bob's by
a 4 GiB ext4 .img. The test then writes past alice's cap and confirms
ENOSPC at the kernel boundary, while bob's workspace is untouched.

Architecture note — DEFAULT_VOLUME_SIZE_GB is a boot-time env on the
orchestrator (T03). To exercise both 1 GiB and 4 GiB caps in a single
test, we swap the live orchestrator container twice:

    Phase A: stop compose orchestrator → launch an ephemeral orchestrator
             on `perpetuity_default` with network-alias `orchestrator`
             (same DNS the backend uses) and DEFAULT_VOLUME_SIZE_GB=1.
             Provision alice through the backend → 1 GiB cap. Run dd.
             Kill ephemeral orchestrator.
    Phase B: `docker compose up -d orchestrator` → default 4 GiB cap.
             Provision bob through the backend → 4 GiB cap. Run df.

The sibling backend container keeps running across the swap because it
resolves `http://orchestrator:8001` at request time, and Docker DNS
re-points to whichever container holds the alias.

How to run:

    docker compose build orchestrator backend
    docker build -f orchestrator/tests/fixtures/Dockerfile.test \\
        -t perpetuity/workspace:test orchestrator/workspace-image/
    docker compose up -d db redis orchestrator
    cd backend && uv run pytest -m e2e \\
        tests/integration/test_m002_s02_volume_cap_e2e.py -v

The test must run with `-n 1` (default serial). Concurrent runs would
exhaust kernel loop devices on a 32-loop budget at ~4× concurrency.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import socket
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
WORKSPACE_IMAGE = "perpetuity/workspace:test"
ORCH_DNS_ALIAS = "orchestrator"


pytestmark = [pytest.mark.e2e]


# ----- helpers -----------------------------------------------------------


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
    """Remove ANSI escape sequences so substring matches survive a real PTY."""
    csi = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
    osc = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
    return osc.sub("", csi.sub("", text))


async def _drain_data(
    ws: object, *, timeout_s: float, until_substring: str | None = None
) -> str:
    """Read frames until `until_substring` shows up in decoded data, or timeout."""
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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_orch_health_via_backend(
    base_url: str, *, timeout_s: float = 60.0
) -> None:
    """Poll backend until orchestrator behind it accepts a session create.

    The backend's `/api/v1/sessions` POST is the cheapest way to confirm
    the live orchestrator is healthy AND reachable from the backend's
    perspective, which is what we actually care about. We can't curl the
    orchestrator port directly because the compose service does not
    publish one. We do NOT actually create a session here — we just wait
    for the backend's HTTP health-check to flip.
    """
    deadline = time.time() + timeout_s
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base_url}/api/v1/utils/health-check/", timeout=2.0)
            if r.status_code == 200:
                return
        except (httpx.HTTPError, OSError) as exc:
            last_err = exc
        time.sleep(0.5)
    raise AssertionError(
        f"backend never reported healthy at {base_url}; last_err={last_err!r}"
    )


def _orch_dns_resolves_inside_backend(
    backend_container_name: str, *, timeout_s: float = 30.0
) -> None:
    """Wait until `getent hosts orchestrator` from inside the backend
    resolves and the orchestrator port is reachable. Used after the swap
    so the test does not race the new orchestrator's DNS publish.
    """
    deadline = time.time() + timeout_s
    last_stderr = ""
    while time.time() < deadline:
        r = _docker(
            "exec", backend_container_name,
            "python3", "-c",
            "import socket,sys; "
            "s=socket.socket(); s.settimeout(1.0); "
            "s.connect(('orchestrator', 8001)); s.close(); print('ok')",
            check=False, timeout=5,
        )
        if r.returncode == 0 and "ok" in (r.stdout or ""):
            return
        last_stderr = (r.stderr or "")[:200]
        time.sleep(0.5)
    raise AssertionError(
        f"orchestrator DNS/port not reachable from backend within "
        f"{int(timeout_s)}s; last_stderr={last_stderr!r}"
    )


def _signup_login(
    base_url: str, *, email: str, password: str, full_name: str
) -> httpx.Cookies:
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


def _personal_team_id(base_url: str, cookies: httpx.Cookies) -> str:
    with httpx.Client(base_url=base_url, timeout=15.0, cookies=cookies) as c:
        r = c.get("/api/v1/teams/")
        assert r.status_code == 200, f"teams list: {r.status_code} {r.text}"
        rows = r.json()["data"]
    personal = next((t for t in rows if t["is_personal"]), None)
    assert personal is not None, f"no personal team in {rows!r}"
    return personal["id"]


def _create_session(
    base_url: str, cookies: httpx.Cookies, team_id: str
) -> str:
    with httpx.Client(base_url=base_url, timeout=60.0, cookies=cookies) as c:
        r = c.post("/api/v1/sessions", json={"team_id": team_id})
        assert r.status_code == 200, (
            f"create session: {r.status_code} {r.text}"
        )
        return r.json()["session_id"]


def _delete_session(
    base_url: str, cookies: httpx.Cookies, session_id: str
) -> int:
    with httpx.Client(base_url=base_url, timeout=15.0, cookies=cookies) as c:
        r = c.delete(f"/api/v1/sessions/{session_id}")
        return r.status_code


def _user_id_from_db(email: str) -> str:
    """Read the user.id row Postgres assigned for `email`. Used to query
    workspace_volume by user_id when the test only knows the email."""
    out = _docker(
        "exec", "perpetuity-db-1",
        "psql", "-U", "postgres", "-d", "app", "-A", "-t",
        "-c", f"SELECT id FROM \"user\" WHERE email = '{email}'",
        check=False,
    )
    val = (out.stdout or "").strip()
    assert val, f"no user row for {email!r}; psql stderr={out.stderr!r}"
    return val


def _psql_one(sql: str) -> str:
    out = _docker(
        "exec", "perpetuity-db-1",
        "psql", "-U", "postgres", "-d", "app", "-A", "-t",
        "-c", sql, check=False,
    )
    return (out.stdout or "").strip()


def _ensure_host_workspaces_shared() -> None:
    """Same logic as the compose `workspace-mount-init` service —
    convert /var/lib/perpetuity/workspaces into a shared mountpoint on
    the host so bind-propagation=rshared works on the orchestrator.
    Idempotent."""
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


def _read_dotenv_value(key: str, default: str) -> str:
    env_path = os.path.join(REPO_ROOT, ".env")
    try:
        with open(env_path) as fp:
            for line in fp:
                stripped = line.strip()
                if stripped.startswith(f"{key}="):
                    value = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                    if value:
                        return value
    except OSError:
        pass
    return default


def _boot_ephemeral_orchestrator(
    *, default_volume_size_gb: int, redis_password: str, pg_password: str,
    api_key: str,
) -> str:
    """Stop compose orchestrator and launch an ephemeral one on the
    compose network with `--network-alias orchestrator` so the backend's
    DNS resolution picks it up. Returns the ephemeral container name."""
    name = f"orch-t04-{uuid.uuid4().hex[:8]}"
    _ensure_host_workspaces_shared()

    # Ensure the compose orchestrator is gone so the alias is free.
    # `docker compose stop` keeps the container around (which holds the
    # name); `docker compose rm -sf` removes it cleanly.
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
        "-e", f"DEFAULT_VOLUME_SIZE_GB={default_volume_size_gb}",
        ORCH_IMAGE,
    ]
    _docker(*args)
    return name


def _wait_for_orch_running(
    name: str, *, backend_container: str, timeout_s: float = 30.0,
) -> None:
    """Poll until the ephemeral orchestrator's /v1/health responds with
    `image_present`. We probe from inside the sibling backend container
    (Python is guaranteed there) so the probe rides the same DNS path
    the backend will use for real requests."""
    deadline = time.time() + timeout_s
    last_err = ""
    probe_script = (
        "import json,sys,urllib.request\n"
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
    logs = _docker("logs", name, check=False, timeout=10).stdout or ""
    raise AssertionError(
        f"ephemeral orchestrator {name!r} never became healthy within "
        f"{int(timeout_s)}s; last_probe={last_err!r}\n"
        f"orchestrator logs (last 80 lines):\n"
        f"{os.linesep.join(logs.splitlines()[-80:])}"
    )


def _restore_compose_orchestrator(*, timeout_s: float = 60.0) -> None:
    """Bring the compose orchestrator back (default 4 GiB cap). Wait for
    it to report healthy via compose health probe."""
    _compose("up", "-d", "orchestrator", check=True, timeout=180)
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
    raise AssertionError(
        f"compose orchestrator did not become healthy within {int(timeout_s)}s"
    )


def _capture_compose_logs(*services: str) -> str:
    """Concatenate `docker compose logs` for the named services."""
    r = _compose(
        "logs", "--no-color", "--timestamps", *services,
        check=False, timeout=30,
    )
    return (r.stdout or "") + (r.stderr or "")


def _docker_inspect_json(container_id: str) -> dict:
    out = _docker("inspect", container_id, check=True, timeout=15)
    arr = json.loads(out.stdout)
    assert arr and isinstance(arr, list), f"bad inspect output: {out.stdout!r}"
    return arr[0]


# ----- the test ----------------------------------------------------------


def test_m002_s02_volume_cap_e2e(  # noqa: PLR0915
    backend_url: str, request: pytest.FixtureRequest,
) -> None:
    """Slice S02 demo: per-volume kernel-enforced hard cap, neighbor
    isolation, DB row matches disk, container resource limits hold,
    log redaction holds."""
    suite_started = time.time()

    # The `backend_url` fixture spawned a sibling backend container we'll
    # exec against to test in-network DNS after the orchestrator swap.
    # The container name is `perpetuity-backend-e2e-<8hex>` per the
    # fixture's naming policy. We discover it from `docker ps` rather
    # than re-deriving the name.
    ps = _docker(
        "ps", "--format", "{{.Names}}",
        "--filter", "name=perpetuity-backend-e2e-",
        check=True, timeout=10,
    )
    backend_names = [n for n in (ps.stdout or "").splitlines() if n.strip()]
    assert len(backend_names) >= 1, (
        f"no sibling backend container found; got {backend_names!r}"
    )
    # Pick the most recently created one — the fixture launches one per test.
    backend_container = backend_names[0]

    # ----- env passthrough used by both phases --------------------------
    redis_password = (
        os.environ.get("REDIS_PASSWORD")
        or _read_dotenv_value("REDIS_PASSWORD", "changethis")
    )
    pg_password = (
        os.environ.get("POSTGRES_PASSWORD")
        or _read_dotenv_value("POSTGRES_PASSWORD", "changethis")
    )
    api_key = _read_dotenv_value("ORCHESTRATOR_API_KEY", "changethis")

    # Track resources we created so the cleanup step can reap them even
    # if an assertion fails partway through.
    spawned_workspace_label = "perpetuity.managed=true"
    cleanup_state: dict[str, object] = {
        "ephemeral_orch": None,
        "alice": None,
        "bob": None,
    }

    def _cleanup() -> None:
        # Best-effort: kill ephemeral orchestrator, restore compose orchestrator,
        # remove workspace containers spawned during the test (label scoped).
        ephem = cleanup_state.get("ephemeral_orch")
        if isinstance(ephem, str):
            _docker("rm", "-f", ephem, check=False, timeout=30)
        try:
            _restore_compose_orchestrator(timeout_s=60.0)
        except AssertionError:
            pass
        ws = _docker(
            "ps", "-aq", "--filter", f"label={spawned_workspace_label}",
            check=False, timeout=15,
        )
        if ws.stdout.strip():
            _docker(
                "rm", "-f", *ws.stdout.split(),
                check=False, timeout=120,
            )

    request.addfinalizer(_cleanup)

    # ----- Phase A: alice on a 1 GiB-cap orchestrator -------------------
    ephemeral = _boot_ephemeral_orchestrator(
        default_volume_size_gb=1,
        redis_password=redis_password,
        pg_password=pg_password,
        api_key=api_key,
    )
    cleanup_state["ephemeral_orch"] = ephemeral
    _wait_for_orch_running(
        ephemeral, backend_container=backend_container, timeout_s=60.0,
    )
    _orch_dns_resolves_inside_backend(backend_container, timeout_s=30.0)

    suffix_a = uuid.uuid4().hex[:8]
    alice_email = f"m002-s02-alice-{suffix_a}@example.com"
    alice_password = "Sup3rs3cret-alice"
    alice_full_name = f"Alice {suffix_a}"
    alice_cookies = _signup_login(
        backend_url,
        email=alice_email, password=alice_password, full_name=alice_full_name,
    )
    alice_team = _personal_team_id(backend_url, alice_cookies)
    alice_user_id = _user_id_from_db(alice_email)
    alice_session = _create_session(backend_url, alice_cookies, alice_team)
    assert uuid.UUID(alice_session)

    # Capture alice's container_id by docker label (S02 container reuse
    # path: the orchestrator labels every container with user_id and
    # team_id so we can find it deterministically).
    alice_ps = _docker(
        "ps", "-q",
        "--filter", f"label=user_id={alice_user_id}",
        "--filter", f"label=team_id={alice_team}",
        check=True, timeout=10,
    )
    alice_container_id = (alice_ps.stdout or "").strip().splitlines()[0]
    assert alice_container_id, (
        "no alice workspace container found by label "
        f"user_id={alice_user_id} team_id={alice_team}"
    )
    cleanup_state["alice"] = alice_container_id

    # ----- Phase A WS attach + dd hits ENOSPC ---------------------------
    ws_base = _http_to_ws(backend_url)
    alice_ws_url = f"{ws_base}/api/v1/ws/terminal/{alice_session}"
    alice_cookie_header = "; ".join(
        f"{n}={v}" for n, v in alice_cookies.items()
    )

    async def _alice_dd() -> tuple[str, str]:
        """Open WS, dd 1100 MB into /workspaces/<team>/big, capture
        the dd output and a stat reading. Both as decoded plain text."""
        async with aconnect_ws(
            alice_ws_url, headers={"Cookie": alice_cookie_header}
        ) as ws:
            first_text = await asyncio.wait_for(ws.receive_text(), timeout=15.0)
            first_frame = json.loads(first_text)
            assert first_frame["type"] == "attach", (
                f"expected attach frame, got {first_frame!r}"
            )
            target = f"/workspaces/{alice_team}/big"
            # Stream dd's stderr to a tmpfile to avoid flooding the WS
            # with per-block progress messages, then cat the tail of it
            # after dd exits. The end-marker is built dynamically with
            # printf so the literal sentinel string never appears in the
            # typed command line — otherwise tmux would echo our own
            # input and `_drain_data` would race the actual completion.
            end_token = uuid.uuid4().hex
            # Build "ENDOK_" + uuid via printf concatenation so the
            # full string ENDOK_<uuid> only appears once stdout flushes.
            cmd = (
                f"dd if=/dev/zero of={target} bs=1M count=1100 "
                f"2>/tmp/dd.err; "
                f"printf 'DDRC=%d\\n' $?; "
                f"tail -n 5 /tmp/dd.err; "
                f"printf 'BYTES=%d\\n' $(stat -c '%s' {target}); "
                f"printf 'EN%sOK_%s\\n' D {end_token}\n"
            )
            await ws.send_text(_input_frame(cmd))
            buf = await _drain_data(
                ws, timeout_s=180.0,
                until_substring=f"ENDOK_{end_token}",
            )
            return buf, target

    alice_buf, alice_target = asyncio.run(_alice_dd())

    assert "no space left on device" in alice_buf.lower(), (
        f"alice dd should hit ENOSPC; saw:\n{alice_buf}"
    )
    rc_match = re.search(r"DDRC=(\d+)", alice_buf)
    assert rc_match and int(rc_match.group(1)) != 0, (
        f"dd should exit non-zero (ENOSPC); rc-section={rc_match!r} "
        f"buf={alice_buf!r}"
    )
    stat_match = re.search(r"BYTES=(\d+)", alice_buf)
    assert stat_match, f"no BYTES in alice buf:\n{alice_buf}"
    alice_bytes = int(stat_match.group(1))
    assert alice_bytes <= int(1.05 * 1024 * 1024 * 1024), (
        f"alice's `big` file exceeded 1.05 GiB cap: {alice_bytes} bytes"
    )
    assert alice_bytes >= int(0.90 * 1024 * 1024 * 1024), (
        f"alice's `big` file far smaller than expected (~1 GiB): "
        f"{alice_bytes} bytes — cap may not be wired"
    )

    # Inspect alice's workspace_volume row BEFORE swapping orchestrators.
    alice_row = _psql_one(
        "SELECT size_gb || '|' || img_path FROM workspace_volume "
        f"WHERE user_id = '{alice_user_id}' AND team_id = '{alice_team}'"
    )
    assert alice_row, "no workspace_volume row for alice"
    alice_size_gb_str, alice_img_path = alice_row.split("|", 1)
    assert int(alice_size_gb_str) == 1, (
        f"alice size_gb expected 1, got {alice_size_gb_str}"
    )
    assert alice_img_path.startswith("/var/lib/perpetuity/vols/"), (
        f"alice img_path malformed: {alice_img_path!r}"
    )
    assert alice_img_path.endswith(".img"), (
        f"alice img_path should end .img: {alice_img_path!r}"
    )
    # The img_path should be uuid-keyed (no PII); check it does NOT
    # contain alice's email or full name.
    assert alice_email not in alice_img_path, (
        f"alice email leaked into img_path: {alice_img_path!r}"
    )
    assert alice_full_name not in alice_img_path

    # Container resource limits on alice's container.
    alice_inspect = _docker_inspect_json(alice_container_id)
    a_host = alice_inspect.get("HostConfig", {})
    assert a_host.get("Memory") == 2 * 1024 * 1024 * 1024, (
        f"alice Memory={a_host.get('Memory')} (expected 2 GiB)"
    )
    assert a_host.get("PidsLimit") == 512, (
        f"alice PidsLimit={a_host.get('PidsLimit')} (expected 512)"
    )
    assert a_host.get("NanoCpus") == 1_000_000_000, (
        f"alice NanoCpus={a_host.get('NanoCpus')} (expected 1e9)"
    )

    # ----- Phase B: bob on the restored 4 GiB compose orchestrator ------
    # Kill ephemeral orchestrator first so the alias is free.
    _docker("rm", "-f", ephemeral, check=False, timeout=30)
    cleanup_state["ephemeral_orch"] = None
    _restore_compose_orchestrator(timeout_s=90.0)
    _orch_dns_resolves_inside_backend(backend_container, timeout_s=30.0)

    suffix_b = uuid.uuid4().hex[:8]
    bob_email = f"m002-s02-bob-{suffix_b}@example.com"
    bob_password = "Sup3rs3cret-bob"
    bob_full_name = f"Bob {suffix_b}"
    bob_cookies = _signup_login(
        backend_url,
        email=bob_email, password=bob_password, full_name=bob_full_name,
    )
    bob_team = _personal_team_id(backend_url, bob_cookies)
    bob_user_id = _user_id_from_db(bob_email)
    assert bob_team != alice_team, (
        f"bob and alice should have distinct teams; both got {bob_team}"
    )
    bob_session = _create_session(backend_url, bob_cookies, bob_team)
    assert uuid.UUID(bob_session)

    bob_ps = _docker(
        "ps", "-q",
        "--filter", f"label=user_id={bob_user_id}",
        "--filter", f"label=team_id={bob_team}",
        check=True, timeout=10,
    )
    bob_container_id = (bob_ps.stdout or "").strip().splitlines()[0]
    assert bob_container_id, (
        f"no bob workspace container found by label "
        f"user_id={bob_user_id} team_id={bob_team}"
    )
    cleanup_state["bob"] = bob_container_id

    bob_ws_url = f"{ws_base}/api/v1/ws/terminal/{bob_session}"
    bob_cookie_header = "; ".join(f"{n}={v}" for n, v in bob_cookies.items())

    async def _bob_inspect() -> tuple[str, str]:
        """Run df + ls inside bob's workspace; return decoded buffer
        plus the end-token used to demarcate the ls section."""
        async with aconnect_ws(
            bob_ws_url, headers={"Cookie": bob_cookie_header}
        ) as ws:
            first_text = await asyncio.wait_for(ws.receive_text(), timeout=15.0)
            first_frame = json.loads(first_text)
            assert first_frame["type"] == "attach", (
                f"expected attach frame for bob, got {first_frame!r}"
            )
            df_token = uuid.uuid4().hex
            ls_token = uuid.uuid4().hex
            # Same printf-split trick as alice's dd: build the marker
            # via printf so the literal substring is not present in
            # the input echoed back by tmux.
            cmd = (
                f"df -BG /workspaces/{bob_team} | tail -n +2; "
                f"printf 'D%sFOK_%s\\n' F {df_token}; "
                f"ls -la /workspaces/{bob_team}/; "
                f"printf 'L%sOK_%s\\n' SD {ls_token}\n"
            )
            await ws.send_text(_input_frame(cmd))
            buf = await _drain_data(
                ws, timeout_s=30.0,
                until_substring=f"LSDOK_{ls_token}",
            )
            return buf, df_token + "|" + ls_token

    bob_buf, bob_tokens = asyncio.run(_bob_inspect())
    bob_df_token, bob_ls_token = bob_tokens.split("|", 1)

    # df total — line is "<dev> NG NG NG <use%> <mount>". Find any
    # 4G-shaped total token; allow ±5% (ext4 metadata overhead).
    df_match = re.search(
        r"\s(\d+)G\s+(\d+)G\s+(\d+)G\s+(\d+)%\s+/workspaces", bob_buf
    )
    assert df_match, (
        f"could not parse df output for bob; saw:\n{bob_buf}"
    )
    bob_total_gb = int(df_match.group(1))
    bob_use_pct = int(df_match.group(4))
    assert 3 <= bob_total_gb <= 4, (
        f"bob df total expected ~4G (3-4 after ext4 overhead), got "
        f"{bob_total_gb}G"
    )
    assert bob_use_pct < 10, (
        f"bob workspace should be near-empty; Use%={bob_use_pct}"
    )

    # Isolation: bob's workspace must NOT contain alice's `big` file.
    # Scan the section between the df-done and ls-done markers — that
    # is the ls -la output. A standalone "big" filename at end-of-line
    # would mean the .img files leaked across volumes.
    df_marker = f"DFFOK_{bob_df_token}"
    ls_marker = f"LSDOK_{bob_ls_token}"
    ls_section = ""
    if df_marker in bob_buf and ls_marker in bob_buf:
        ls_section = bob_buf.split(df_marker, 1)[1].split(ls_marker, 1)[0]
    assert not re.search(r"(?m)\s+big\s*$", ls_section), (
        f"alice's `big` file leaked into bob's workspace; "
        f"ls section:\n{ls_section!r}"
    )

    # Bob's volume row.
    bob_row = _psql_one(
        "SELECT size_gb || '|' || img_path FROM workspace_volume "
        f"WHERE user_id = '{bob_user_id}' AND team_id = '{bob_team}'"
    )
    assert bob_row, "no workspace_volume row for bob"
    bob_size_gb_str, bob_img_path = bob_row.split("|", 1)
    assert int(bob_size_gb_str) == 4, (
        f"bob size_gb expected 4 (default), got {bob_size_gb_str}"
    )
    assert bob_img_path != alice_img_path, (
        "alice and bob share an img_path — isolation broken"
    )

    # Container resource limits on bob's container.
    bob_inspect = _docker_inspect_json(bob_container_id)
    b_host = bob_inspect.get("HostConfig", {})
    assert b_host.get("Memory") == 2 * 1024 * 1024 * 1024
    assert b_host.get("PidsLimit") == 512
    assert b_host.get("NanoCpus") == 1_000_000_000

    # ----- Cross-volume: alice's .img is still on disk, bob's too -------
    # `stat` from inside the db container (which has /var/lib/perpetuity
    # NOT mounted) — so we use `docker exec` against the orchestrator
    # which DOES have it bind-mounted. The orchestrator was restarted so
    # alice's .img must survive.
    a_stat = _docker(
        "exec", "perpetuity-orchestrator-1", "stat", "-c", "%s",
        alice_img_path, check=False, timeout=10,
    )
    assert a_stat.returncode == 0, (
        f"alice's .img missing after orchestrator swap: "
        f"stderr={a_stat.stderr!r}"
    )
    assert int(a_stat.stdout.strip()) >= 1024 * 1024 * 1024, (
        f"alice's .img unexpectedly small: {a_stat.stdout!r}"
    )

    # ----- Tear down sessions -------------------------------------------
    a_del = _delete_session(backend_url, alice_cookies, alice_session)
    b_del = _delete_session(backend_url, bob_cookies, bob_session)
    assert a_del == 200, f"DELETE alice {alice_session}: {a_del}"
    assert b_del == 200, f"DELETE bob {bob_session}: {b_del}"

    # ----- Log redaction sweep ------------------------------------------
    log_blob = _capture_compose_logs("orchestrator", "backend")
    log_path = "/tmp/m002_s02.log"
    try:
        with open(log_path, "w") as fp:
            fp.write(log_blob)
    except OSError:
        pass

    for sentinel, label in (
        (alice_email, "alice email"),
        (alice_full_name, "alice full_name"),
        (bob_email, "bob email"),
        (bob_full_name, "bob full_name"),
    ):
        assert sentinel not in log_blob, (
            f"redaction sweep: {label} ({sentinel!r}) leaked to compose logs"
        )

    # Smoke: the slice's observability taxonomy keys actually fired.
    for key in (
        "volume_provisioned",
        "volume_mounted",
    ):
        assert key in log_blob, (
            f"observability taxonomy regression: {key!r} not seen in logs"
        )

    elapsed = time.time() - suite_started
    # Slice budget is ≤60 s; we tolerate up to 180 s defensively because
    # `docker compose up -d --force-recreate orchestrator` can be slow on
    # cold compose.
    assert elapsed < 180.0, (
        f"e2e suite took {elapsed:.1f}s — far over the 60s slice budget"
    )
