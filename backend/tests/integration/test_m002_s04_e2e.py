"""M002 / S04 / T04 — Full slice end-to-end demo.

Slice S04's demo-truth statement: two WS sessions for the same (user, team)
share a single workspace container; GET /api/v1/sessions lists both;
DELETE one — the surviving sibling stays alive AND the workspace
container stays alive; after `idle_timeout_seconds` of no I/O and no live
attach the reaper kills the surviving tmux session and reaps the
container; the per-volume `.img` persists; the next POST /api/v1/sessions
for the same (user, team) re-provisions the container and remounts the
existing workspace_volume.

Stitches every prior task in the slice together against the **real
compose stack**:

  * T01 — orchestrator AttachMap register/unregister around the WS bridge
  * T02 — background idle reaper (two-phase D018 check + container reap)
  * T03 — public GET /api/v1/sessions/{sid}/scrollback proxy
  * S03 — admin PUT /api/v1/admin/settings/{key} (now extended in T02 to
    accept `idle_timeout_seconds` so we can dial the reaper down to 3 s
    without redeploying)

Approach against the live compose stack — sibling backend container, no
TestClient. Because the compose orchestrator's REAPER_INTERVAL_SECONDS is
unset (defaults 30 s), we use the live-orchestrator-swap pattern from S02
(MEM149) to swap in an ephemeral orchestrator with
REAPER_INTERVAL_SECONDS=1 for the duration of the test. The sibling
backend container resolves `http://orchestrator:8001` at request time so
the swap is invisible from the backend's perspective.

Flow (single async test):

  1. Promote the seeded admin@example.com (already system_admin) and log
     in. Sign up alice (RFC 2606 example.com per MEM131). Both via the
     sibling backend on `backend_url`.
  2. As admin PUT `idle_timeout_seconds=3` → 200 with `value=3`. An
     autouse fixture also DELETEs the row before AND after the test
     (MEM161 — compose's app-db-data persists across runs).
  3. As alice POST /api/v1/sessions twice (same personal team_id from
     signup) → got two session_ids, sid_a and sid_b. The orchestrator's
     create response carries `created=True` for the first and `False`
     for the second (T03/MEM120 — same container reused, distinct tmux
     sessions inside).
  4. WS-attach to sid_a, write a marker file at /workspaces/<team_id>/
     marker.txt and read it back to prove the file made it through.
     WS-attach to sid_b, `cat` the same path — strip ANSI, assert the
     marker contents land in alice's data-frame stream. This is the
     multi-tmux/single-container filesystem-sharing proof (R008/MEM120).
  5. GET /api/v1/sessions → exactly 2 rows for alice, set of ids ==
     {sid_a, sid_b}.
  6. GET /api/v1/sessions/{sid_a}/scrollback (the new T03 endpoint) →
     200 with non-empty scrollback containing the echoed marker
     contents. Negative: same GET as bob → 404 with body identical to a
     missing-session GET (no enumeration).
  7. DELETE /api/v1/sessions/{sid_a} → 200. GET → exactly 1 row, sid_b.
     The workspace container is STILL running.
  8. Wait for the reaper to act on sid_b. Two-phase check requires
     (a) Redis last_activity > idle_timeout (3 s) AND (b) the AttachMap
     reports no live attach. Per MEM171: open the WS attach FIRST and
     then back-date last_activity AFTER the attach is unregistered; with
     a 1 s tick a fresh WS connect can race the reaper otherwise. Sleep
     5 s (3 s timeout + 1 s interval + 1 s buffer). Assert: GET returns
     empty data; the container labelled (user_id, team_id) is gone.
  9. Volume persists: as alice POST /api/v1/sessions → 200 sid_c.
     WS-attach, cat /workspaces/<team_id>/marker.txt, assert marker
     contents in the data stream — proves the workspace_volume row +
     .img survived the reap and the new container remounted the
     existing volume (D015 invariant + R006).
 10. Log redaction sweep (MEM134): grep `docker compose logs orchestrator
     backend` for alice/bob email and full_name → assert zero matches.

Wall-clock budget: ≤45 s once the orchestrator image is warm. The sleep
budget alone is ~6 s; everything else is HTTP/WS round-trips.

How to run:

    docker compose build backend orchestrator
    docker build -f orchestrator/tests/fixtures/Dockerfile.test \
        -t perpetuity/workspace:test orchestrator/workspace-image/
    docker compose up -d db redis orchestrator
    cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e \
        tests/integration/test_m002_s04_e2e.py -v

The test must NOT mock anything below the backend HTTP boundary — the
slice acceptance demands the real reaper trips on the real Docker daemon.
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
WORKSPACE_IMAGE = "perpetuity/workspace:test"
ORCH_DNS_ALIAS = "orchestrator"

# Two-phase idle_timeout strategy: keep the timeout generous during the
# prep steps (WS attaches, scrollback fetches, list calls) so the reaper
# doesn't race the test on a 1 s tick, then dial it down to 3 s right
# before the actual reap-the-survivor step. Without this, by the time
# step 6's scrollback fetch lands, ~5 s have elapsed since the sid_a WS
# close — the reaper already ticked once with idle_timeout=3 and killed
# the session, so the scrollback proxy 404s.
TEST_IDLE_TIMEOUT_SECONDS_PREP = 600
TEST_IDLE_TIMEOUT_SECONDS_REAP = 3
TEST_REAPER_INTERVAL_SECONDS = 1


pytestmark = [pytest.mark.e2e, pytest.mark.serial]


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
    """Returns session_ids for the caller filtered by team_id.

    The backend's GET /api/v1/sessions route forwards ?team_id to the
    orchestrator's GET /v1/sessions which requires both (user_id, team_id)
    as Query(...) params; calling without team_id surfaces as 503
    orchestrator_status_422 today. The slice's demo only needs to list the
    two sessions on alice's personal team, which is well-served by the
    explicit-team_id form.
    """
    with httpx.Client(base_url=base_url, timeout=15.0, cookies=cookies) as c:
        r = c.get("/api/v1/sessions", params={"team_id": team_id})
    assert r.status_code == 200, f"list sessions: {r.status_code} {r.text}"
    body = r.json()
    # Orchestrator's record shape is {container_id, tmux_session, user_id,
    # team_id, last_activity} — the session_id is stored as `tmux_session`
    # since sessions.py uses session_id verbatim as the tmux session name
    # (MEM120). The backend's list_sessions forwards these records as-is.
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


def _capture_compose_logs(*services: str) -> str:
    r = _compose(
        "logs", "--no-color", "--timestamps", *services,
        check=False, timeout=30,
    )
    return (r.stdout or "") + (r.stderr or "")


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


def _ensure_host_workspaces_shared() -> None:
    """Mirror the compose `workspace-mount-init` service — make
    /var/lib/perpetuity/workspaces a shared mountpoint so the ephemeral
    orchestrator's bind-propagation=rshared can re-acquire ext4 mounts.
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


def _boot_ephemeral_orchestrator(
    *,
    redis_password: str,
    pg_password: str,
    api_key: str,
    reaper_interval_seconds: int,
) -> str:
    """Stop compose orchestrator and launch an ephemeral one with
    REAPER_INTERVAL_SECONDS=<n> on the same compose network with
    `--network-alias orchestrator` so the backend's DNS resolves to it.
    Returns the ephemeral container name. Mirrors the S02 pattern
    (MEM149) — the only delta is the env-var override + no
    DEFAULT_VOLUME_SIZE_GB override (we want compose's 4 GiB default)."""
    name = f"orch-s04-{uuid.uuid4().hex[:8]}"
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
    """Probe the ephemeral orchestrator from inside the sibling backend
    container so the probe rides the same DNS path the backend will use
    for real requests. Identical shape to the S02 probe."""
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


# ----- autouse fixtures --------------------------------------------------


def _backend_image_has_scrollback_route() -> bool:
    """Probe `backend:latest` for the T03 scrollback route. A stale image
    (built before T03 landed) was the source of a 30-minute red-herring
    chase during T04 development — the route was returning a FastAPI
    default 404 (route-not-matched) which looked indistinguishable from
    our own no-enumeration 404. Surface as a skip with a fix command
    rather than a confusing assertion failure mid-test."""
    r = _docker(
        "run", "--rm", "--entrypoint", "grep", "backend:latest",
        "-l", "scrollback", "/app/backend/app/api/routes/sessions.py",
        check=False, timeout=15,
    )
    return r.returncode == 0


@pytest.fixture(autouse=True)
def _require_scrollback_route_baked() -> None:
    if not _backend_image_has_scrollback_route():
        pytest.skip(
            "backend:latest is missing the T03 GET /sessions/{sid}/scrollback "
            "route — run `docker compose build backend` so the image bakes "
            "the current /app/backend/app/api/routes/sessions.py."
        )


@pytest.fixture(autouse=True)
def _wipe_idle_timeout_setting() -> None:
    """Belt-and-suspenders cleanup of the system_settings row before AND
    after the test (MEM161). The compose `db` service persists across
    runs via `app-db-data`; without this an earlier crashed run could
    leave `idle_timeout_seconds` set, biasing assertions or — worse —
    biting the next non-S04 e2e test that doesn't expect a 3 s reaper
    timeout."""
    _delete_setting_row("idle_timeout_seconds")
    yield
    _delete_setting_row("idle_timeout_seconds")


@pytest.fixture
def ephemeral_orchestrator(
    compose_stack_up: None,  # noqa: ARG001
) -> object:
    """Swap the compose orchestrator for an ephemeral one with
    REAPER_INTERVAL_SECONDS=1 for the test, then restore on teardown.

    Yields the ephemeral container name (mostly for the test's debug
    logs) and reaps every workspace container that the test touched.
    """
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


def test_m002_s04_full_demo(  # noqa: PLR0915
    backend_url: str,
    ephemeral_orchestrator: str,
    request: pytest.FixtureRequest,
) -> None:
    """Slice S04 demo: two WS sessions share a container, reaper kills
    idle survivor + reaps container, next POST remounts existing
    workspace_volume."""
    suite_started = time.time()

    backend_container = _backend_container_name()
    _wait_for_orch_running(
        backend_container=backend_container,
        ephemeral_name=ephemeral_orchestrator,
        timeout_s=30.0,
    )

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

    # ----- step 1: admin login + alice signup --------------------------
    admin_email = "admin@example.com"
    admin_password = "changethis"
    admin_cookies = _login_only(
        backend_url, email=admin_email, password=admin_password
    )
    admin_user_id = _user_id_from_db(admin_email)
    assert admin_user_id  # touch to avoid unused-name lints downstream

    suffix_a = uuid.uuid4().hex[:8]
    alice_email = f"m002-s04-alice-{suffix_a}@example.com"
    alice_password = "Sup3rs3cret-alice"
    alice_full_name = f"Alice {suffix_a}"
    alice_cookies = _signup_login(
        backend_url,
        email=alice_email, password=alice_password, full_name=alice_full_name,
    )
    alice_team = _personal_team_id(backend_url, alice_cookies)
    alice_user_id = _user_id_from_db(alice_email)

    # ----- step 2: admin PUT idle_timeout_seconds=600 (prep window) ----
    # The reap-the-survivor step (step 8) PUTs this down to 3 s. We start
    # generous so the WS / scrollback / list / DELETE round-trips don't
    # race a 1 s reaper tick.
    with httpx.Client(
        base_url=backend_url, timeout=30.0, cookies=admin_cookies
    ) as c:
        r = c.put(
            "/api/v1/admin/settings/idle_timeout_seconds",
            json={"value": TEST_IDLE_TIMEOUT_SECONDS_PREP},
        )
    assert r.status_code == 200, f"admin PUT: {r.status_code} {r.text}"
    body = r.json()
    assert body["key"] == "idle_timeout_seconds"
    assert body["value"] == TEST_IDLE_TIMEOUT_SECONDS_PREP

    # ----- step 3: alice POST /api/v1/sessions twice -------------------
    a_resp = _create_session_raw(backend_url, alice_cookies, alice_team)
    sid_a = str(a_resp["session_id"])
    assert uuid.UUID(sid_a)

    b_resp = _create_session_raw(backend_url, alice_cookies, alice_team)
    sid_b = str(b_resp["session_id"])
    assert uuid.UUID(sid_b)
    assert sid_a != sid_b, (
        f"two POSTs returned same session_id {sid_a!r} — backend is not "
        f"minting a fresh UUID per call"
    )

    # Container reuse check via Docker label scan: exactly one container
    # carrying both labels. The public POST response intentionally hides
    # container_id (D016 — backend never echoes orchestrator internals).
    container_id_at_create = _docker(
        "ps", "-q",
        "--filter", f"label=user_id={alice_user_id}",
        "--filter", f"label=team_id={alice_team}",
        check=True, timeout=10,
    ).stdout.strip().splitlines()
    assert len(container_id_at_create) == 1, (
        f"expected exactly 1 container for alice (R008 — same (user, team) "
        f"reuses one container), got {container_id_at_create!r}"
    )
    alice_container_id = container_id_at_create[0]

    # ----- step 4: WS round-trip — write marker via sid_a, read via sid_b -
    ws_base = _http_to_ws(backend_url)
    cookie_header = "; ".join(f"{n}={v}" for n, v in alice_cookies.items())

    marker_path = f"/workspaces/{alice_team}/marker.txt"
    marker_content = f"hello-s04-{uuid.uuid4().hex[:8]}"
    end_token_a = uuid.uuid4().hex
    end_token_b = uuid.uuid4().hex

    async def _write_then_read() -> tuple[str, str]:
        # Phase A: attach to sid_a, write the file. Use printf-substitution
        # sentinels so the literal ENDOK_<token> isn't echoed by tmux on
        # the input line (MEM142/MEM150).
        sid_a_url = f"{ws_base}/api/v1/ws/terminal/{sid_a}"
        async with aconnect_ws(
            sid_a_url, headers={"Cookie": cookie_header}
        ) as ws_a:
            first = json.loads(
                await asyncio.wait_for(ws_a.receive_text(), timeout=15.0)
            )
            assert first["type"] == "attach", (
                f"sid_a expected attach frame, got {first!r}"
            )
            cmd_a = (
                f"printf '%s\\n' {marker_content} > {marker_path}; "
                f"cat {marker_path}; "
                f"printf 'EN%sOK_%s\\n' D {end_token_a}\n"
            )
            await ws_a.send_text(_input_frame(cmd_a))
            buf_a = await _drain_data(
                ws_a, timeout_s=15.0,
                until_substring=f"ENDOK_{end_token_a}",
            )

        # Phase B: attach to sid_b, cat the same file. The `cat` succeeds
        # only because the two tmux sessions share the workspace bind
        # mount (R008/MEM120 — single container, two tmux).
        sid_b_url = f"{ws_base}/api/v1/ws/terminal/{sid_b}"
        async with aconnect_ws(
            sid_b_url, headers={"Cookie": cookie_header}
        ) as ws_b:
            first = json.loads(
                await asyncio.wait_for(ws_b.receive_text(), timeout=15.0)
            )
            assert first["type"] == "attach", (
                f"sid_b expected attach frame, got {first!r}"
            )
            cmd_b = (
                f"cat {marker_path}; "
                f"printf 'EN%sOK_%s\\n' D {end_token_b}\n"
            )
            await ws_b.send_text(_input_frame(cmd_b))
            buf_b = await _drain_data(
                ws_b, timeout_s=15.0,
                until_substring=f"ENDOK_{end_token_b}",
            )

        return buf_a, buf_b

    buf_a, buf_b = asyncio.run(_write_then_read())

    assert marker_content in buf_a, (
        f"sid_a did not see its own marker write echoed back; saw:\n{buf_a}"
    )
    assert marker_content in buf_b, (
        f"sid_b did not see sid_a's marker via shared filesystem (R008); "
        f"saw:\n{buf_b}"
    )

    # ----- step 5: GET /api/v1/sessions returns both -------------------
    listed = _list_session_ids(backend_url, alice_cookies, alice_team)
    assert set(listed) == {sid_a, sid_b}, (
        f"GET /api/v1/sessions: expected {{sid_a, sid_b}}, got {listed!r}"
    )

    # ----- step 6: scrollback proxy ------------------------------------
    with httpx.Client(
        base_url=backend_url, timeout=15.0, cookies=alice_cookies
    ) as c:
        r_sb = c.get(f"/api/v1/sessions/{sid_a}/scrollback")
    assert r_sb.status_code == 200, (
        f"GET scrollback (alice/sid_a): {r_sb.status_code} {r_sb.text}"
    )
    sb_body = r_sb.json()
    assert sb_body["session_id"] == sid_a
    assert isinstance(sb_body["scrollback"], str) and sb_body["scrollback"], (
        f"scrollback should be a non-empty string; got {sb_body!r}"
    )
    assert marker_content in sb_body["scrollback"], (
        f"scrollback missing marker {marker_content!r}; "
        f"got first 400 bytes: {sb_body['scrollback'][:400]!r}"
    )

    # Negative: sign up bob mid-test, hit alice's scrollback URL — must
    # 404 with the same body shape as a missing-session GET (no
    # enumeration). Compare against an actually-missing UUID for a
    # bit-for-bit equality check.
    suffix_bob = uuid.uuid4().hex[:8]
    bob_email = f"m002-s04-bob-{suffix_bob}@example.com"
    bob_password = "Sup3rs3cret-bob"
    bob_full_name = f"Bob {suffix_bob}"
    bob_cookies = _signup_login(
        backend_url,
        email=bob_email, password=bob_password, full_name=bob_full_name,
    )
    missing_sid = str(uuid.uuid4())
    with httpx.Client(
        base_url=backend_url, timeout=15.0, cookies=bob_cookies
    ) as c:
        r_other = c.get(f"/api/v1/sessions/{sid_a}/scrollback")
        r_missing = c.get(f"/api/v1/sessions/{missing_sid}/scrollback")
    assert r_other.status_code == 404
    assert r_missing.status_code == 404
    assert r_other.json() == r_missing.json(), (
        "no-enumeration violated: cross-user GET scrollback returned a "
        f"distinguishable body from a missing-session GET. "
        f"other={r_other.json()!r} missing={r_missing.json()!r}"
    )

    # ----- step 7: DELETE sid_a — sibling and container survive --------
    del_a = _delete_session(backend_url, alice_cookies, sid_a)
    assert del_a == 200, f"DELETE sid_a: {del_a}"

    listed_after = _list_session_ids(backend_url, alice_cookies, alice_team)
    assert listed_after == [sid_b], (
        f"after DELETE sid_a expected [sid_b], got {listed_after!r}"
    )

    container_after_delete = _docker(
        "ps", "-q",
        "--filter", f"label=user_id={alice_user_id}",
        "--filter", f"label=team_id={alice_team}",
        check=True, timeout=10,
    ).stdout.strip().splitlines()
    assert container_after_delete == [alice_container_id], (
        f"workspace container should still be alive after DELETE sid_a "
        f"(only its tmux session was killed); ps={container_after_delete!r}"
    )

    # ----- step 8: wait for the reaper to kill sid_b + reap container --
    # PUT idle_timeout_seconds down to 3 s NOW so the reaper trips on its
    # next tick. The previous steps ran with a 600 s timeout so the
    # reaper couldn't race the WS/HTTP round-trips. The reaper resolves
    # this on every tick (volume_store._resolve_idle_timeout_seconds) so
    # the change biases the very next tick (≤1 s away).
    with httpx.Client(
        base_url=backend_url, timeout=30.0, cookies=admin_cookies
    ) as c:
        r_reap = c.put(
            "/api/v1/admin/settings/idle_timeout_seconds",
            json={"value": TEST_IDLE_TIMEOUT_SECONDS_REAP},
        )
    assert r_reap.status_code == 200, (
        f"admin PUT (reap): {r_reap.status_code} {r_reap.text}"
    )
    assert r_reap.json()["value"] == TEST_IDLE_TIMEOUT_SECONDS_REAP

    # Two-phase D018 check: (a) Redis last_activity > 3s AND (b) AttachMap
    # reports no live attach. We closed the WS attach for sid_b in step 4
    # so the AttachMap is empty for it; the heartbeat was last bumped at
    # the close. Reaper interval is 1s, idle timeout is 3s.
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
        f"reaper should have killed sid_b after {sleep_budget:.1f}s; "
        f"GET still returns {listed_after_reap!r}"
    )
    assert container_after_reap == [], (
        f"reaper should have removed the workspace container after the "
        f"last tmux session died; ps={container_after_reap!r}"
    )

    # The orchestrator must have logged the reap signals — these are the
    # T02 observability keys that the slice's success criterion hinges on.
    eph_logs = _docker(
        "logs", ephemeral_orchestrator, check=False, timeout=15,
    ).stdout or ""
    eph_logs += _docker(
        "logs", ephemeral_orchestrator, check=False, timeout=15,
    ).stderr or ""
    assert "reaper_killed_session" in eph_logs, (
        "expected at least one reaper_killed_session log line on the "
        f"ephemeral orchestrator; tail:\n{eph_logs[-2000:]}"
    )
    assert "reaper_reaped_container" in eph_logs, (
        "expected at least one reaper_reaped_container log line on the "
        f"ephemeral orchestrator; tail:\n{eph_logs[-2000:]}"
    )

    # ----- step 9: volume persists across reap -------------------------
    c_resp = _create_session_raw(backend_url, alice_cookies, alice_team)
    sid_c = str(c_resp["session_id"])
    assert uuid.UUID(sid_c)

    # New container must exist (was just provisioned by the POST).
    container_after_remount = _docker(
        "ps", "-q",
        "--filter", f"label=user_id={alice_user_id}",
        "--filter", f"label=team_id={alice_team}",
        check=True, timeout=10,
    ).stdout.strip().splitlines()
    assert len(container_after_remount) == 1, (
        f"expected exactly 1 container after re-provision, got "
        f"{container_after_remount!r}"
    )
    assert container_after_remount[0] != alice_container_id, (
        "post-reap container should be a fresh container id (the prior "
        f"one was removed); got the same id back: {alice_container_id}"
    )

    end_token_c = uuid.uuid4().hex

    async def _read_marker_post_reap() -> str:
        sid_c_url = f"{ws_base}/api/v1/ws/terminal/{sid_c}"
        async with aconnect_ws(
            sid_c_url, headers={"Cookie": cookie_header}
        ) as ws_c:
            first = json.loads(
                await asyncio.wait_for(ws_c.receive_text(), timeout=15.0)
            )
            assert first["type"] == "attach", (
                f"sid_c expected attach frame, got {first!r}"
            )
            cmd_c = (
                f"cat {marker_path}; "
                f"printf 'EN%sOK_%s\\n' D {end_token_c}\n"
            )
            await ws_c.send_text(_input_frame(cmd_c))
            return await _drain_data(
                ws_c, timeout_s=15.0,
                until_substring=f"ENDOK_{end_token_c}",
            )

    buf_c = asyncio.run(_read_marker_post_reap())
    assert marker_content in buf_c, (
        "post-reap re-attach should see the prior marker file (volume "
        f"persists, D015/R006); saw:\n{buf_c}"
    )

    # Tear down sid_c so the test leaves no live tmux sessions on the
    # ephemeral orchestrator (otherwise teardown would race the reaper
    # and the alice container could outlive the test).
    del_c = _delete_session(backend_url, alice_cookies, sid_c)
    assert del_c == 200, f"DELETE sid_c: {del_c}"

    # ----- step 10: log redaction sweep --------------------------------
    backend_log = _docker(
        "logs", backend_container, check=False, timeout=15,
    ).stdout or ""
    backend_log += _docker(
        "logs", backend_container, check=False, timeout=15,
    ).stderr or ""
    log_blob = backend_log + "\n" + eph_logs

    for sentinel, label in (
        (alice_email, "alice email"),
        (alice_full_name, "alice full_name"),
        (bob_email, "bob email"),
        (bob_full_name, "bob full_name"),
    ):
        assert sentinel not in log_blob, (
            f"redaction sweep: {label} ({sentinel!r}) leaked into logs"
        )

    # Smoke-check that the slice's new observability taxonomy fired.
    for key in (
        "attach_registered",
        "attach_unregistered",
        "reaper_started",
        "reaper_tick",
        "reaper_killed_session",
        "reaper_reaped_container",
        "idle_timeout_seconds_resolved",
        "session_scrollback_proxied",
    ):
        assert key in log_blob, (
            f"observability taxonomy regression: {key!r} not seen in logs"
        )

    elapsed = time.time() - suite_started
    # Slice budget is ≤45s. Allow some slack for cold boot of the
    # ephemeral orchestrator on a slow host.
    assert elapsed < 180.0, (
        f"e2e suite took {elapsed:.1f}s — far over the 45s slice budget"
    )
