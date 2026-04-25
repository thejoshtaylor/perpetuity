"""M002 / S05 / T02 — Two-key shared-secret rotation acceptance e2e.

Proves the D016/MEM096 zero-downtime rotation contract end-to-end:
when the orchestrator boots with BOTH `ORCHESTRATOR_API_KEY=key_current`
AND `ORCHESTRATOR_API_KEY_PREVIOUS=key_previous` set, two distinct
backends — one carrying `key_current`, the other carrying
`key_previous` — must BOTH succeed against the same orchestrator
endpoint on both the HTTP path (POST/DELETE /api/v1/sessions, which
the backend proxies with `X-Orchestrator-Key`) and the WS path
(/api/v1/ws/terminal/{sid}, which the backend proxies as
`?key=<orchestrator_api_key>`). A third backend carrying a
fully-random unrecognized key must fail closed (orchestrator returns
401, backend surfaces as 502 `orchestrator_rejected_create`).

Strategy mirrors S04/T04 + S05/T01:

  * Live-orchestrator-swap pattern (MEM149/MEM188): `docker compose
    rm -sf orchestrator` then `docker run --network-alias orchestrator`
    with both keys set, restore on teardown.
  * Module-local `_boot_sibling_backend(api_key=...)` parameterizes
    the conftest's `backend_url` fixture by key — the fixture itself
    is hard-wired to dotenv `ORCHESTRATOR_API_KEY` (conftest line 301)
    and explicitly empties `ORCHESTRATOR_API_KEY_PREVIOUS` (conftest
    line 324), so neither sibling backend in this test can use it.
  * Probe ephemeral orchestrator readiness via a python3 one-liner
    `docker exec`'d INSIDE the ephemeral container itself — /v1/health
    is unauthenticated so this sidesteps the chicken-and-egg between
    the test's randomly-generated secrets and readiness detection,
    and uses python3+urllib (already in the orchestrator image) since
    the compose `db` postgres image lacks wget/curl.
  * Capture `docker logs` for the ephemeral orchestrator + all three
    sibling backends BEFORE teardown so the milestone-wide redaction
    sweep has the right blob.

How to run:

    docker compose build backend orchestrator
    docker build -f orchestrator/tests/fixtures/Dockerfile.test \\
        -t perpetuity/workspace:test orchestrator/workspace-image/
    docker compose up -d db redis orchestrator
    cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e \\
        tests/integration/test_m002_s05_two_key_rotation_e2e.py -v

Wall-clock budget: ≤120 s on a warm compose stack (boot of the
ephemeral orchestrator + three sibling backends dominates).
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator

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

# M004/S01/T01: stable Fernet key for the e2e suite — same value as the
# conftest's SYSTEM_SETTINGS_ENCRYPTION_KEY_TEST so sibling backends and
# the ephemeral orchestrator share the same Fernet key (sensitive rows
# written by either side must round-trip). Test-only secret.
SYSTEM_SETTINGS_ENCRYPTION_KEY_TEST = (
    "kfk5l7mPRFpBV7PzWJxYmO6LRRQAdZ4iGYZRG6xL0fY="
)

# The most-recent alembic revision the suite depends on. The backend
# image bakes /app/backend/app/alembic/versions/ (MEM147), so a stale
# image would fail at prestart with "Can't locate revision". The
# autouse skip-guard converts that into an actionable skip.
S05_REVISION = "s05_system_settings"


pytestmark = [pytest.mark.e2e, pytest.mark.serial]


# ----- low-level helpers (module-local copies from S05/T01) --------------


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
    """Mirror compose's `workspace-mount-init`. Idempotent."""
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


def _http_to_ws(http_base: str) -> str:
    if http_base.startswith("https://"):
        return "wss://" + http_base[len("https://"):]
    if http_base.startswith("http://"):
        return "ws://" + http_base[len("http://"):]
    return "ws://" + http_base


# ----- ephemeral orchestrator with BOTH keys -----------------------------


def _boot_ephemeral_orchestrator_dual_key(
    *,
    redis_password: str,
    pg_password: str,
    key_current: str,
    key_previous: str,
) -> str:
    """Stop the compose orchestrator and launch an ephemeral one carrying
    BOTH ORCHESTRATOR_API_KEY=key_current AND ORCHESTRATOR_API_KEY_PREVIOUS
    =key_previous, on the compose network with the `orchestrator` DNS
    alias so siblings resolve it transparently. Returns the ephemeral
    container name.

    Reaper interval stays at the orchestrator default — this test never
    waits for the reaper. Workspace bind-mount + docker.sock follow the
    S05/T01 shape so any incidental session that POSTs through actually
    boots a workspace container correctly.
    """
    name = f"orch-s05-rot-{uuid.uuid4().hex[:8]}"
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
        "-e", f"ORCHESTRATOR_API_KEY={key_current}",
        "-e", f"ORCHESTRATOR_API_KEY_PREVIOUS={key_previous}",
        "-e", f"SYSTEM_SETTINGS_ENCRYPTION_KEY={SYSTEM_SETTINGS_ENCRYPTION_KEY_TEST}",
        "-e", "REDIS_HOST=redis",
        "-e", f"REDIS_PASSWORD={redis_password}",
        "-e",
        f"DATABASE_URL=postgresql://postgres:{pg_password}@db:5432/app",
        ORCH_IMAGE,
    ]
    _docker(*args)
    return name


def _wait_for_orch_running_self(
    ephemeral_name: str, *, timeout_s: float = 30.0
) -> None:
    """Probe the ephemeral orchestrator's /v1/health by running a tiny
    python3 client INSIDE the orchestrator container itself
    (`docker exec <eph_name> python3 ...`). Three reasons this beats
    probing from another container:

      1. The compose `db` postgres image lacks `wget` and `curl`, so a
         shell-based HTTP probe needs `python3` or `/dev/tcp`-bash —
         postgres' busybox sh has neither.
      2. The orchestrator image is built on python:3.12-slim and has
         a working python3 + urllib in the runtime PATH (matches the
         shape used by the orchestrator's own healthcheck).
      3. /v1/health doesn't require auth (`_PUBLIC_PATHS` in auth.py),
         so the probe doesn't need to know either of the two keys
         under test — sidesteps a chicken-and-egg between the test
         secrets and readiness detection.

    The probe still exercises the local container's HTTP stack the
    same way the real backends will, so a misconfigured ephemeral
    orchestrator (missing key, broken settings) will fail this probe
    just as visibly as it would fail a sibling-backend request.
    """
    deadline = time.time() + timeout_s
    last_err = ""
    probe_script = (
        "import sys, urllib.request\n"
        "try:\n"
        "    body = urllib.request.urlopen("
        "'http://127.0.0.1:8001/v1/health', timeout=2).read().decode()\n"
        "    print(body)\n"
        "    sys.exit(0 if 'image_present' in body else 2)\n"
        "except Exception as e:\n"
        "    print(repr(e)); sys.exit(3)\n"
    )
    while time.time() < deadline:
        r = _docker(
            "exec", ephemeral_name,
            "python3", "-c", probe_script,
            check=False, timeout=5,
        )
        if r.returncode == 0 and "image_present" in (r.stdout or ""):
            return
        last_err = (
            (r.stderr or "")[:200] + " | " + (r.stdout or "")[:200]
        )
        time.sleep(0.5)
    logs = _docker(
        "logs", ephemeral_name, check=False, timeout=10
    ).stdout or ""
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


# ----- parameterized sibling backend boot --------------------------------


def _boot_sibling_backend(
    *,
    api_key: str,
    redis_password: str,
    pg_password: str,
    secret_key: str,
    name_prefix: str,
) -> tuple[str, str, int]:
    """Boot a sibling backend container parameterized by
    `ORCHESTRATOR_API_KEY=<api_key>`. Mirrors the conftest's
    `backend_url` fixture shape (lines 301-348) but takes the key as
    an argument.

    Returns (container_name, base_url, host_port).
    """
    name = f"{name_prefix}-{uuid.uuid4().hex[:8]}"
    host_port = _free_port()

    env_args = [
        "-e", "PROJECT_NAME=Perpetuity-e2e",
        "-e", "DOMAIN=localhost",
        "-e", "ENVIRONMENT=local",
        "-e", "FRONTEND_HOST=http://localhost:5173",
        "-e", "BACKEND_CORS_ORIGINS=http://localhost,http://localhost:5173",
        "-e", f"SECRET_KEY={secret_key}",
        "-e", "FIRST_SUPERUSER=admin@example.com",
        "-e", "FIRST_SUPERUSER_PASSWORD=changethis",
        "-e", "POSTGRES_SERVER=db",
        "-e", "POSTGRES_PORT=5432",
        "-e", "POSTGRES_DB=app",
        "-e", "POSTGRES_USER=postgres",
        "-e", f"POSTGRES_PASSWORD={pg_password}",
        "-e", "REDIS_HOST=redis",
        "-e", f"REDIS_PASSWORD={redis_password}",
        "-e", "ORCHESTRATOR_BASE_URL=http://orchestrator:8001",
        "-e", f"ORCHESTRATOR_API_KEY={api_key}",
        "-e", "ORCHESTRATOR_API_KEY_PREVIOUS=",
        "-e", f"SYSTEM_SETTINGS_ENCRYPTION_KEY={SYSTEM_SETTINGS_ENCRYPTION_KEY_TEST}",
        "-e", "EMAILS_FROM_EMAIL=noreply@example.com",
        "-e", "SMTP_HOST=",
        "-e", "SMTP_USER=",
        "-e", "SMTP_PASSWORD=",
        "-e", "SENTRY_DSN=",
    ]

    cmd = (
        "set -e; bash scripts/prestart.sh && "
        "exec fastapi run --host 0.0.0.0 --port 8000 app/main.py"
    )
    _docker(
        "run", "-d",
        "--name", name,
        "--network", NETWORK,
        "-p", f"{host_port}:8000",
        *env_args,
        "--entrypoint", "bash",
        BACKEND_IMAGE,
        "-c", cmd,
        timeout=60,
    )

    base_url = f"http://localhost:{host_port}"
    health_url = f"{base_url}/api/v1/utils/health-check/"

    deadline = time.time() + 90.0
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            r = httpx.get(health_url, timeout=2.0)
            if r.status_code == 200:
                break
        except (httpx.HTTPError, OSError) as exc:
            last_err = exc
        time.sleep(0.5)
    else:
        logs = _docker("logs", name, check=False).stdout or ""
        _docker("rm", "-f", name, check=False)
        raise AssertionError(
            f"sibling backend {name!r} never became healthy at "
            f"{health_url}; last_err={last_err!r}\n"
            f"logs:\n{logs[-4000:]}"
        )

    return name, base_url, host_port


# ----- autouse skip-guards (MEM162/MEM186) -------------------------------


def _backend_image_has_s05_revision() -> bool:
    r = _docker(
        "run", "--rm", "--entrypoint", "ls", BACKEND_IMAGE,
        "/app/backend/app/alembic/versions/",
        check=False, timeout=15,
    )
    return f"{S05_REVISION}.py" in (r.stdout or "")


def _orchestrator_image_present() -> bool:
    r = _docker(
        "image", "inspect", ORCH_IMAGE, check=False, timeout=10,
    )
    return r.returncode == 0


def _workspace_image_present() -> bool:
    r = _docker(
        "image", "inspect", WORKSPACE_IMAGE, check=False, timeout=10,
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
def _require_orchestrator_image() -> None:
    if not _orchestrator_image_present():
        pytest.skip(
            f"image {ORCH_IMAGE!r} missing — run "
            "`docker compose build orchestrator`"
        )
    if not _workspace_image_present():
        pytest.skip(
            f"image {WORKSPACE_IMAGE!r} missing — run `docker build -f "
            f"orchestrator/tests/fixtures/Dockerfile.test "
            f"-t {WORKSPACE_IMAGE} orchestrator/workspace-image/`"
        )


# ----- the test ----------------------------------------------------------


@pytest.fixture
def two_key_stack(
    compose_stack_up: None,  # noqa: ARG001
    request: pytest.FixtureRequest,
) -> Iterator[dict[str, str]]:
    """Boot:
      * an ephemeral orchestrator with BOTH keys set
      * three sibling backends: backend_current (key_current),
        backend_previous (key_previous), backend_wrong (random key
        unknown to the orchestrator).

    Yield a dict of names + base_urls. Teardown removes all four
    containers AND restores the compose orchestrator. Container names
    + log-blob captures for the redaction sweep are stashed on the
    request for the test body to read.
    """
    redis_password = (
        os.environ.get("REDIS_PASSWORD")
        or _read_dotenv_value("REDIS_PASSWORD", "changethis")
    )
    pg_password = (
        os.environ.get("POSTGRES_PASSWORD")
        or _read_dotenv_value("POSTGRES_PASSWORD", "changethis")
    )
    secret_key = _read_dotenv_value("SECRET_KEY", "changethis")

    key_current = secrets.token_urlsafe(32)
    key_previous = secrets.token_urlsafe(32)
    key_wrong = secrets.token_urlsafe(32)
    # Sanity: three independent secrets (no token_urlsafe collision in
    # 32-bytes of os.urandom is overwhelmingly safe, but guard anyway).
    assert len({key_current, key_previous, key_wrong}) == 3

    eph_name = _boot_ephemeral_orchestrator_dual_key(
        redis_password=redis_password,
        pg_password=pg_password,
        key_current=key_current,
        key_previous=key_previous,
    )

    # Track everything we created so teardown can rm -f even if a
    # later boot raises mid-fixture.
    created: list[str] = [eph_name]

    def _teardown() -> None:
        for n in created:
            _docker("rm", "-f", n, check=False, timeout=30)
        # Reap any incidentally-created workspace containers.
        ws = _docker(
            "ps", "-aq", "--filter", "label=perpetuity.managed=true",
            check=False, timeout=15,
        )
        if ws.stdout.strip():
            _docker(
                "rm", "-f", *ws.stdout.split(),
                check=False, timeout=120,
            )
        _restore_compose_orchestrator()

    request.addfinalizer(_teardown)

    _wait_for_orch_running_self(eph_name, timeout_s=30.0)

    # Boot the three sibling backends sequentially. Parallelizing would
    # save ~30 s but adds complexity that the assertion budget doesn't
    # need — boot cost is dominated by the orch image being warm.
    bc_name, bc_url, _ = _boot_sibling_backend(
        api_key=key_current,
        redis_password=redis_password,
        pg_password=pg_password,
        secret_key=secret_key,
        name_prefix="perpetuity-backend-e2e-cur",
    )
    created.append(bc_name)
    bp_name, bp_url, _ = _boot_sibling_backend(
        api_key=key_previous,
        redis_password=redis_password,
        pg_password=pg_password,
        secret_key=secret_key,
        name_prefix="perpetuity-backend-e2e-prev",
    )
    created.append(bp_name)
    bw_name, bw_url, _ = _boot_sibling_backend(
        api_key=key_wrong,
        redis_password=redis_password,
        pg_password=pg_password,
        secret_key=secret_key,
        name_prefix="perpetuity-backend-e2e-wrong",
    )
    created.append(bw_name)

    yield {
        "eph_name": eph_name,
        "backend_current_name": bc_name,
        "backend_current_url": bc_url,
        "backend_previous_name": bp_name,
        "backend_previous_url": bp_url,
        "backend_wrong_name": bw_name,
        "backend_wrong_url": bw_url,
        "key_current": key_current,
        "key_previous": key_previous,
        "key_wrong": key_wrong,
    }


def _delete_session(
    base_url: str, cookies: httpx.Cookies, sid: str
) -> int:
    with httpx.Client(base_url=base_url, timeout=15.0, cookies=cookies) as c:
        r = c.delete(f"/api/v1/sessions/{sid}")
        return r.status_code


async def _ws_attach_assert_attach_frame(
    base_url: str, cookies: httpx.Cookies, sid: str
) -> dict[str, object]:
    ws_base = _http_to_ws(base_url)
    cookie_header = "; ".join(f"{n}={v}" for n, v in cookies.items())
    ws_url = f"{ws_base}/api/v1/ws/terminal/{sid}"
    async with aconnect_ws(
        ws_url, headers={"Cookie": cookie_header}
    ) as ws:
        first = json.loads(
            await asyncio.wait_for(ws.receive_text(), timeout=20.0)
        )
        assert first.get("type") == "attach", (
            f"expected attach frame, got {first!r}"
        )
        return first


def test_m002_s05_two_key_rotation(  # noqa: PLR0915
    two_key_stack: dict[str, str],
) -> None:
    """Both ORCHESTRATOR_API_KEY (current) and ORCHESTRATOR_API_KEY_PREVIOUS
    are accepted by the same orchestrator on both HTTP and WS paths;
    a fully unknown key is rejected.
    """
    suite_started = time.time()

    bc_url = two_key_stack["backend_current_url"]
    bp_url = two_key_stack["backend_previous_url"]
    bw_url = two_key_stack["backend_wrong_url"]
    eph_name = two_key_stack["eph_name"]
    bc_name = two_key_stack["backend_current_name"]
    bp_name = two_key_stack["backend_previous_name"]
    bw_name = two_key_stack["backend_wrong_name"]
    key_wrong = two_key_stack["key_wrong"]

    # ----- step 6: alice on backend_current, bob on backend_previous --
    suffix_a = uuid.uuid4().hex[:8]
    alice_email = f"m002-s05t02-alice-{suffix_a}@example.com"
    alice_password = "Sup3rs3cret-alice"
    alice_full_name = f"Alice {suffix_a}"
    alice_cookies_current = _signup_login(
        bc_url,
        email=alice_email,
        password=alice_password,
        full_name=alice_full_name,
    )
    alice_team = _personal_team_id(bc_url, alice_cookies_current)

    suffix_b = uuid.uuid4().hex[:8]
    bob_email = f"m002-s05t02-bob-{suffix_b}@example.com"
    bob_password = "Sup3rs3cret-bob"
    bob_full_name = f"Bob {suffix_b}"
    bob_cookies_previous = _signup_login(
        bp_url,
        email=bob_email,
        password=bob_password,
        full_name=bob_full_name,
    )
    bob_team = _personal_team_id(bp_url, bob_cookies_previous)
    # Sanity: alice and bob both got their own personal teams (proves
    # the second backend isn't accidentally talking to the first user's
    # session — they hit the same shared db, but auth is per-cookie).
    assert alice_team != bob_team, (
        "alice and bob accidentally share a team — signup helpers leaked"
    )

    # ----- step 7: HTTP path — alice POST via backend_current ---------
    # Backend_current sends X-Orchestrator-Key=key_current. The
    # orchestrator's _key_matches accepts because key_current is the
    # active key. 200 is the only acceptable shape.
    with httpx.Client(
        base_url=bc_url, timeout=60.0, cookies=alice_cookies_current
    ) as c:
        r = c.post("/api/v1/sessions", json={"team_id": alice_team})
    assert r.status_code == 200, (
        f"step 7: alice POST via backend_current must succeed (key_current "
        f"is the active key); got {r.status_code} {r.text}"
    )
    sid_a = str(r.json()["session_id"])
    assert uuid.UUID(sid_a)

    teardown_status_a = _delete_session(
        bc_url, alice_cookies_current, sid_a
    )
    assert teardown_status_a == 200, (
        f"step 7: alice DELETE sid_a → expected 200, got {teardown_status_a}"
    )

    # ----- step 8: HTTP path — bob POST via backend_previous ----------
    # Backend_previous sends X-Orchestrator-Key=key_previous. The
    # orchestrator's _key_matches accepts because key_previous is in
    # the candidates list (rotation acceptance — exactly the contract
    # under test).
    with httpx.Client(
        base_url=bp_url, timeout=60.0, cookies=bob_cookies_previous
    ) as c:
        r = c.post("/api/v1/sessions", json={"team_id": bob_team})
    assert r.status_code == 200, (
        f"step 8: bob POST via backend_previous must succeed (key_previous "
        f"in candidates); got {r.status_code} {r.text}"
    )
    sid_b = str(r.json()["session_id"])
    assert uuid.UUID(sid_b)

    teardown_status_b = _delete_session(
        bp_url, bob_cookies_previous, sid_b
    )
    assert teardown_status_b == 200, (
        f"step 8: bob DELETE sid_b → expected 200, got {teardown_status_b}"
    )

    # ----- step 9: WS path proof for both keys ------------------------
    # Reprovision since steps 7/8 deleted both sessions. The backend's
    # WS-bridge code in routes/sessions.py proxies to the orchestrator
    # with `?key=<settings.ORCHESTRATOR_API_KEY>` — backend_current
    # sends ?key=key_current, backend_previous sends ?key=key_previous.
    # Both must succeed.
    with httpx.Client(
        base_url=bc_url, timeout=60.0, cookies=alice_cookies_current
    ) as c:
        r = c.post("/api/v1/sessions", json={"team_id": alice_team})
    assert r.status_code == 200, (
        f"step 9: alice POST (reprovision) via backend_current: "
        f"{r.status_code} {r.text}"
    )
    sid_a2 = str(r.json()["session_id"])

    with httpx.Client(
        base_url=bp_url, timeout=60.0, cookies=bob_cookies_previous
    ) as c:
        r = c.post("/api/v1/sessions", json={"team_id": bob_team})
    assert r.status_code == 200, (
        f"step 9: bob POST (reprovision) via backend_previous: "
        f"{r.status_code} {r.text}"
    )
    sid_b2 = str(r.json()["session_id"])

    asyncio.run(
        _ws_attach_assert_attach_frame(
            bc_url, alice_cookies_current, sid_a2
        )
    )
    asyncio.run(
        _ws_attach_assert_attach_frame(
            bp_url, bob_cookies_previous, sid_b2
        )
    )

    # Clean up reprovisioned sessions (DELETE of sid_a2 also exercises
    # the HTTP path with key_current a second time, sid_b2 with
    # key_previous a second time — incidental redundancy, harmless).
    assert _delete_session(bc_url, alice_cookies_current, sid_a2) == 200
    assert _delete_session(bp_url, bob_cookies_previous, sid_b2) == 200

    # ----- step 10: negative case — backend_wrong gets rejected -------
    # backend_wrong sends X-Orchestrator-Key=<random_unknown_key>. The
    # orchestrator's middleware emits `orchestrator_http_unauthorized
    # path=/v1/sessions key_prefix=<first 4>...` and returns 401
    # `{"detail":"unauthorized"}`. The backend's POST /api/v1/sessions
    # handler treats any non-2xx, non-5xx orchestrator response as
    # 502 `orchestrator_rejected_create` (sessions.py L183-189).
    with httpx.Client(
        base_url=bw_url, timeout=60.0, cookies=alice_cookies_current
    ) as c:
        # Re-login on backend_wrong: cookies are SECRET_KEY-signed and
        # SECRET_KEY is the same across all three backends, but the
        # session cookie's user row was created on backend_current's
        # first signup — same shared db, so the cookie is valid here.
        # No re-login needed.
        r_wrong = c.post(
            "/api/v1/sessions", json={"team_id": alice_team}
        )
    assert r_wrong.status_code == 502, (
        f"step 10: alice POST via backend_wrong should fail with 502 "
        f"(orchestrator returned 401 → backend wraps as 502 "
        f"orchestrator_rejected_create). Got {r_wrong.status_code} "
        f"{r_wrong.text}"
    )
    # Body assertion is intentionally lenient — we assert the status
    # contract (502) and that the detail contains "orchestrator" (the
    # backend's wrapper string) without pinning the exact phrase, so a
    # future refactor of the message string doesn't break this test.
    body = r_wrong.json()
    assert "orchestrator" in str(body.get("detail", "")).lower(), (
        f"step 10: 502 body should mention orchestrator; got {body!r}"
    )

    # ----- step 11: capture logs BEFORE teardown ----------------------
    # The ephemeral orchestrator + three sibling backends all go away
    # in the fixture's finalizer; once `docker rm -f` runs we lose
    # everything they emitted. Capture now so the redaction sweep has
    # the full blob to grep.
    eph_logs = _docker(
        "logs", eph_name, check=False, timeout=15,
    )
    eph_blob = (eph_logs.stdout or "") + (eph_logs.stderr or "")

    bc_logs = _docker(
        "logs", bc_name, check=False, timeout=15,
    )
    bc_blob = (bc_logs.stdout or "") + (bc_logs.stderr or "")

    bp_logs = _docker(
        "logs", bp_name, check=False, timeout=15,
    )
    bp_blob = (bp_logs.stdout or "") + (bp_logs.stderr or "")

    bw_logs = _docker(
        "logs", bw_name, check=False, timeout=15,
    )
    bw_blob = (bw_logs.stdout or "") + (bw_logs.stderr or "")

    log_blob = "\n".join((eph_blob, bc_blob, bp_blob, bw_blob))

    # ----- step 10 (continued): orchestrator_http_unauthorized fired --
    # The wrong-key path MUST have produced the unauthorized log line
    # in the ephemeral orchestrator's logs. Asserts the existing log
    # key fires for the negative branch (slice observability contract).
    assert "orchestrator_http_unauthorized" in eph_blob, (
        "step 10: orchestrator_http_unauthorized log key did not fire on "
        "backend_wrong's POST — the rotation contract's negative branch "
        "is silent.\n"
        f"eph_logs tail (last 2000 chars):\n{eph_blob[-2000:]}"
    )
    # Confirm the key_prefix shape (first 4 chars of the wrong key,
    # then '...') without ever logging the full key — this guards
    # against a future regression that logs the full secret.
    expected_prefix = key_wrong[:4] + "..."
    assert f"key_prefix={expected_prefix}" in eph_blob, (
        "step 10: key_prefix log shape regression — expected "
        f"key_prefix={expected_prefix!r} in eph_blob.\n"
        f"eph_logs tail (last 2000 chars):\n{eph_blob[-2000:]}"
    )
    # The full wrong key MUST NOT appear in any log (only its 4-char
    # prefix is permitted by the slice observability rule).
    assert key_wrong not in log_blob, (
        "step 10: full wrong-key value leaked into logs — only "
        "key_prefix=<first 4>... is permitted (auth.py _key_prefix)"
    )

    # ----- step 11: milestone-wide redaction sweep --------------------
    # Same shape as S01/T05 + S04/T04 + S05/T01: zero email/full_name
    # leaks across captured logs. UUIDs only.
    for sentinel, label in (
        (alice_email, "alice email"),
        (alice_full_name, "alice full_name"),
        (bob_email, "bob email"),
        (bob_full_name, "bob full_name"),
    ):
        assert sentinel not in log_blob, (
            f"step 11: redaction sweep — {label} ({sentinel!r}) leaked "
            "into captured logs (UUID-only invariant violated)"
        )

    elapsed = time.time() - suite_started
    # Slice budget is ≤120 s on a warm stack. Defensive cap at 240 s
    # for cold-cache CI runs.
    assert elapsed < 240.0, (
        f"e2e suite took {elapsed:.1f}s — far over the 120s slice budget"
    )
