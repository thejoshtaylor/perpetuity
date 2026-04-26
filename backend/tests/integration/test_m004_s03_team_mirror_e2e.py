"""M004 / S03 / T04 — team-mirror end-to-end proof.

Slice S03's authoritative integration proof. Single test file, marked
@pytest.mark.e2e + @pytest.mark.serial, against the live compose db +
ephemeral orchestrator (parameterized with MIRROR_REAPER_INTERVAL_SECONDS=1
and `mirror_idle_timeout_seconds=60` — the validator's floor — combined
with manual psql back-dating of `last_idle_at` so the test stays under
30s wall-clock).

Scenarios A–E walk the slice contract:

  A. POST /v1/teams/{id}/mirror/ensure cold-start → 200 with
     {container_id, network_addr: 'team-mirror-<first8>:9418'};
     team_mirror_volumes row inserted with non-NULL container_id; container
     running with the expected labels; log line `team_mirror_started`.
  B. Second POST /v1/teams/{id}/mirror/ensure → 200 same container_id;
     no second container; log line `team_mirror_reused`.
  C. `git init --bare /repos/test.git` inside the mirror; sibling
     alpine/git container clones git://team-mirror-<first8>:9418/test.git
     and asserts `.git/HEAD` exists. (D023 transport proof.)
  D. PATCH /api/v1/teams/{id}/mirror with always_on=true; back-date
     last_idle_at by 120s; sleep 2× reaper_interval; container STILL
     running; log line `team_mirror_reap_skipped reason=always_on`.
  E. PATCH again with always_on=false; back-dated last_idle_at unchanged;
     sleep 2× reaper_interval; container NOT running; row's container_id
     NULL but volume_path persists; log lines `team_mirror_reaped
     reason=idle` and `mirror_idle_timeout_seconds_resolved value=60`.

How to run::

    docker compose build backend orchestrator
    docker compose up -d db redis
    cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e \\
        tests/integration/test_m004_s03_team_mirror_e2e.py -v
"""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator

import httpx
import pytest

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)

NETWORK = "perpetuity_default"
ORCH_IMAGE = "orchestrator:latest"
BACKEND_IMAGE = "backend:latest"
WORKSPACE_IMAGE = "perpetuity/workspace:test"
ORCH_DNS_ALIAS = "orchestrator"

# Shared Fernet key — same value as the conftest's
# SYSTEM_SETTINGS_ENCRYPTION_KEY_TEST so the sibling backend AND the
# ephemeral orchestrator can both decrypt rows the other side wrote.
SYSTEM_SETTINGS_ENCRYPTION_KEY_TEST = (
    "kfk5l7mPRFpBV7PzWJxYmO6LRRQAdZ4iGYZRG6xL0fY="
)

# T01 alembic revision filename — backend skip-guard probe.
S06C_REVISION = "s06c_team_mirror_volumes"

# T02 orchestrator module — orchestrator skip-guard probe.
TEAM_MIRROR_MODULE = "team_mirror.py"

# Reaper interval used for the ephemeral orchestrator. The reaper sleeps
# FIRST then ticks, so two intervals (= 2s) is a safe wait-window.
_REAPER_INTERVAL_SECONDS = 1
_REAPER_WAIT_S = _REAPER_INTERVAL_SECONDS * 3 + 1  # 4s belt-and-braces

# `mirror_idle_timeout_seconds` floor (T01 validator). Combined with
# manual back-dating of last_idle_at by > this many seconds.
_MIRROR_IDLE_TIMEOUT_S = 60
_BACKDATE_S = _MIRROR_IDLE_TIMEOUT_S + 60  # 120s past the deadline


pytestmark = [pytest.mark.e2e, pytest.mark.serial]


# ----- low-level helpers (module-local copies; MEM197) --------------------


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


def _psql_one(sql: str) -> str:
    out = _docker(
        "exec", "perpetuity-db-1",
        "psql", "-U", "postgres", "-d", "app", "-A", "-t",
        "-c", sql, check=False,
    )
    return (out.stdout or "").strip()


def _psql_exec(sql: str) -> subprocess.CompletedProcess[str]:
    return _docker(
        "exec", "perpetuity-db-1",
        "psql", "-U", "postgres", "-d", "app", "-c", sql,
        check=False,
    )


# ----- image probes (skip-guards) ----------------------------------------


def _backend_image_has_s06c() -> bool:
    """Probe backend:latest for the s06c_team_mirror_volumes alembic
    revision — preempts the MEM137 stale-image trap with a clear skip
    message rather than a confusing prestart failure."""
    r = _docker(
        "run", "--rm", "--entrypoint", "ls", BACKEND_IMAGE,
        "/app/backend/app/alembic/versions/",
        check=False, timeout=15,
    )
    return f"{S06C_REVISION}.py" in (r.stdout or "")


def _orchestrator_image_has_team_mirror() -> bool:
    """Probe orchestrator:latest for orchestrator/team_mirror.py — the
    T02 module. The Dockerfile copies orchestrator/orchestrator → /app/
    orchestrator (S02 reference test confirms this layout)."""
    r = _docker(
        "run", "--rm", "--entrypoint", "ls", ORCH_IMAGE,
        "/app/orchestrator/",
        check=False, timeout=15,
    )
    return TEAM_MIRROR_MODULE in (r.stdout or "")


@pytest.fixture(autouse=True)
def _require_baked_images() -> None:
    if not _backend_image_has_s06c():
        pytest.skip(
            f"backend:latest is missing the {S06C_REVISION!r} alembic "
            "revision — run `docker compose build backend orchestrator` so "
            "the images bake the current source tree."
        )
    if not _orchestrator_image_has_team_mirror():
        pytest.skip(
            f"orchestrator:latest is missing orchestrator/{TEAM_MIRROR_MODULE} "
            "— run `docker compose build backend orchestrator`."
        )


# ----- cleanup (belt-and-suspenders, MEM246/S01 pattern) -----------------


def _cleanup_team_mirror_state() -> None:
    """Wipe team_mirror_volumes rows AND any team-mirror-* containers
    AND the mirror_idle_timeout_seconds setting AND any per-team docker
    volumes left from prior runs. Compose's app-db-data volume persists
    across runs (MEM161) so leftover rows from a crashed prior test
    would otherwise re-trigger the reaper."""
    _psql_exec("DELETE FROM team_mirror_volumes")
    _psql_exec(
        "DELETE FROM system_settings WHERE key='mirror_idle_timeout_seconds'"
    )
    # team-mirror-* containers (matched by labels — robust to name skew).
    ls = _docker(
        "ps", "-aq", "--filter", "label=perpetuity.team_mirror=true",
        check=False, timeout=15,
    )
    if (ls.stdout or "").strip():
        _docker(
            "rm", "-f", *ls.stdout.split(),
            check=False, timeout=120,
        )
    # Per-team named volumes — `docker volume rm` is idempotent on missing.
    vol_ls = _docker(
        "volume", "ls", "-q", "--filter", "name=perpetuity-team-mirror-",
        check=False, timeout=15,
    )
    if (vol_ls.stdout or "").strip():
        _docker(
            "volume", "rm", *vol_ls.stdout.split(),
            check=False, timeout=60,
        )


@pytest.fixture(autouse=True)
def _wipe_team_mirror_state_before_after() -> Iterator[None]:
    _cleanup_team_mirror_state()
    yield
    _cleanup_team_mirror_state()


# ----- credential / signup helpers --------------------------------------


def _login_only(
    base_url: str, *, email: str, password: str
) -> httpx.Cookies:
    cookies = httpx.Cookies()
    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        r = c.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        assert r.status_code == 200, f"login: {r.status_code} {r.text}"
        for cookie in c.cookies.jar:
            cookies.set(cookie.name, cookie.value)
    return cookies


def _signup_login(
    base_url: str, *, email: str, password: str, full_name: str
) -> httpx.Cookies:
    cookies = httpx.Cookies()
    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        r = c.post(
            "/api/v1/auth/signup",
            json={
                "email": email, "password": password, "full_name": full_name
            },
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


def _seed_mirror_idle_timeout(
    backend_url: str, admin_cookies: httpx.Cookies, *, value: int
) -> None:
    """Superuser PUT mirror_idle_timeout_seconds=<value>. The orchestrator
    reaper resolves this on every tick (volume_store._resolve_mirror_idle_
    timeout_seconds) so a freshly-PUT value biases the very next tick."""
    with httpx.Client(
        base_url=backend_url, timeout=30.0, cookies=admin_cookies
    ) as c:
        r = c.put(
            "/api/v1/admin/settings/mirror_idle_timeout_seconds",
            json={"value": value},
        )
        assert r.status_code == 200, (
            f"PUT mirror_idle_timeout_seconds: {r.status_code} {r.text}"
        )


# ----- ephemeral orchestrator parameterized by reaper interval -----------


def _ensure_host_workspaces_shared() -> None:
    """Make /var/lib/perpetuity/workspaces a rshared mount on the host
    so the orchestrator container can rshare-bind it (matches S02's
    pattern; the team_mirror containers don't actually need shared mounts
    but the orchestrator boots the same way regardless)."""
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
) -> str:
    """Stop the compose orchestrator and launch an ephemeral one with
    MIRROR_REAPER_INTERVAL_SECONDS=1 so the reap windows fit under 30s.

    The DNS alias `orchestrator` redirects backend → ephemeral. Returns
    the ephemeral container name."""
    name = f"orch-s03-mirror-{uuid.uuid4().hex[:8]}"
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
        "-e",
        f"SYSTEM_SETTINGS_ENCRYPTION_KEY={SYSTEM_SETTINGS_ENCRYPTION_KEY_TEST}",
        "-e", "REDIS_HOST=redis",
        "-e", f"REDIS_PASSWORD={redis_password}",
        "-e",
        f"DATABASE_URL=postgresql://postgres:{pg_password}@db:5432/app",
        # The contract under test — 1s tick keeps total wall-clock low.
        "-e", f"MIRROR_REAPER_INTERVAL_SECONDS={_REAPER_INTERVAL_SECONDS}",
        ORCH_IMAGE,
    ]
    _docker(*args)
    return name


def _wait_for_orch_running_self(
    ephemeral_name: str, *, timeout_s: float = 60.0
) -> None:
    """MEM194 readiness probe — exec python3+urllib INSIDE the ephemeral
    container itself. /v1/health is unauthenticated so the probe sidesteps
    the test's randomly-generated ORCHESTRATOR_API_KEY."""
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


# ----- sibling backend pointed at the ephemeral orchestrator -------------


def _boot_sibling_backend(
    *,
    api_key: str,
    redis_password: str,
    pg_password: str,
    secret_key: str,
) -> tuple[str, str]:
    """Boot a sibling backend container parameterized by ORCHESTRATOR_API_
    KEY and pointed at the ephemeral orchestrator at http://orchestrator:8001
    via the network-alias trick. Returns (container_name, base_url)."""
    name = f"perpetuity-backend-e2e-s03-{uuid.uuid4().hex[:8]}"
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
        "-e",
        f"SYSTEM_SETTINGS_ENCRYPTION_KEY={SYSTEM_SETTINGS_ENCRYPTION_KEY_TEST}",
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

    deadline = time.time() + 120.0
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            r = httpx.get(health_url, timeout=2.0)
            if r.status_code == 200:
                return name, base_url
        except (httpx.HTTPError, OSError) as exc:
            last_err = exc
        time.sleep(0.5)

    logs = _docker("logs", name, check=False).stdout or ""
    _docker("rm", "-f", name, check=False)
    raise AssertionError(
        f"sibling backend {name!r} never became healthy at "
        f"{health_url}; last_err={last_err!r}\n"
        f"logs:\n{logs[-4000:]}"
    )


# ----- orchestrator HTTP from inside its own container -------------------


def _http_orch(
    eph_name: str, path: str, *, api_key: str, method: str = "POST"
) -> tuple[int, str]:
    """Hit the ephemeral orchestrator's HTTP surface from inside its own
    container (MEM260) — sidesteps the host network since no port is
    published. Returns (status_code, body_text)."""
    # POST/PUT/PATCH/DELETE need data set so urllib emits Content-Length=0.
    body_kw = "" if method == "GET" else "data=b''"
    probe = (
        "import sys, urllib.request, urllib.error\n"
        f"req = urllib.request.Request('http://127.0.0.1:8001{path}', "
        f"headers={{'X-Orchestrator-Key': {api_key!r}}}, "
        f"method={method!r}{', ' + body_kw if body_kw else ''})\n"
        "try:\n"
        "    r = urllib.request.urlopen(req, timeout=20)\n"
        "    sys.stdout.write(str(r.status) + chr(10) + r.read().decode())\n"
        "except urllib.error.HTTPError as e:\n"
        "    sys.stdout.write(str(e.code) + chr(10) + e.read().decode())\n"
    )
    r = _docker(
        "exec", eph_name, "python3", "-c", probe,
        check=False, timeout=30,
    )
    out = (r.stdout or "").split("\n", 1)
    if len(out) != 2:
        return 0, (r.stdout or "") + (r.stderr or "")
    try:
        return int(out[0]), out[1]
    except ValueError:
        return 0, (r.stdout or "") + (r.stderr or "")


# ----- the test ----------------------------------------------------------


@pytest.fixture
def mirror_stack(
    compose_stack_up: None,  # noqa: ARG001
    request: pytest.FixtureRequest,
) -> Iterator[dict[str, str]]:
    """Boot ephemeral orchestrator (MIRROR_REAPER_INTERVAL_SECONDS=1) +
    sibling backend pointed at it. Yield a dict of names + base_urls.

    Teardown captures logs BEFORE removing containers, then removes all
    test-spawned containers and restores the compose orchestrator."""
    redis_password = (
        os.environ.get("REDIS_PASSWORD")
        or _read_dotenv_value("REDIS_PASSWORD", "changethis")
    )
    pg_password = (
        os.environ.get("POSTGRES_PASSWORD")
        or _read_dotenv_value("POSTGRES_PASSWORD", "changethis")
    )
    secret_key = _read_dotenv_value("SECRET_KEY", "changethis")
    api_key = secrets.token_urlsafe(32)

    created: list[str] = []
    captured: dict[str, str] = {}

    def _teardown() -> None:
        # Capture logs first (rm -f drops them), then rm + restore.
        for n in created:
            try:
                blob = _docker("logs", n, check=False, timeout=15)
                captured[n] = (blob.stdout or "") + (blob.stderr or "")
            except Exception:  # noqa: BLE001 — best-effort
                captured[n] = ""
        for n in created:
            _docker("rm", "-f", n, check=False, timeout=30)
        # team-mirror-* containers spawned by the orchestrator on ensure.
        ls = _docker(
            "ps", "-aq", "--filter", "label=perpetuity.team_mirror=true",
            check=False, timeout=15,
        )
        if (ls.stdout or "").strip():
            _docker(
                "rm", "-f", *ls.stdout.split(),
                check=False, timeout=120,
            )
        # Sibling clone + bare-init helper containers.
        helpers = _docker(
            "ps", "-aq", "--filter", "name=s03-mirror-clone-",
            check=False, timeout=15,
        )
        if (helpers.stdout or "").strip():
            _docker(
                "rm", "-f", *helpers.stdout.split(),
                check=False, timeout=60,
            )
        _restore_compose_orchestrator()

    request.addfinalizer(_teardown)

    eph_name = _boot_ephemeral_orchestrator(
        redis_password=redis_password,
        pg_password=pg_password,
        api_key=api_key,
    )
    created.append(eph_name)
    _wait_for_orch_running_self(eph_name, timeout_s=60.0)

    bk_name, bk_url = _boot_sibling_backend(
        api_key=api_key,
        redis_password=redis_password,
        pg_password=pg_password,
        secret_key=secret_key,
    )
    created.append(bk_name)

    yield {
        "eph_name": eph_name,
        "backend_name": bk_name,
        "backend_url": bk_url,
        "api_key": api_key,
        "redis_password": redis_password,
        "pg_password": pg_password,
        "secret_key": secret_key,
    }


def _team_mirror_container_name(team_id: str) -> str:
    """Mirror of orchestrator.team_mirror._team_mirror_container_name —
    duplicated locally so the test stays self-contained (MEM197)."""
    clean = team_id.replace("-", "")
    return f"team-mirror-{clean[:8]}"


def _container_running(name: str) -> bool:
    r = _docker(
        "inspect", "-f", "{{.State.Running}}", name,
        check=False, timeout=10,
    )
    return (r.stdout or "").strip() == "true"


def _container_exists(name: str) -> bool:
    r = _docker(
        "inspect", name,
        check=False, timeout=10,
    )
    return r.returncode == 0


def _wait_for_log_marker(
    container_name: str, marker: str, *, timeout_s: float
) -> bool:
    """Tail container logs up to timeout_s waiting for `marker` to appear.
    Returns True on hit, False on timeout. Cheap polling — the marker
    appears once per matching event so we can shortcut on first hit."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        blob = _docker(
            "logs", container_name, check=False, timeout=10
        )
        text = (blob.stdout or "") + (blob.stderr or "")
        if marker in text:
            return True
        time.sleep(0.5)
    return False


def test_m004_s03_team_mirror_e2e(  # noqa: PLR0912, PLR0915
    mirror_stack: dict[str, str],
) -> None:
    """End-to-end ensure / clone / reap / always_on contract for slice S03."""
    suite_started = time.time()

    backend_url = mirror_stack["backend_url"]
    backend_name = mirror_stack["backend_name"]
    eph_name = mirror_stack["eph_name"]
    api_key = mirror_stack["api_key"]

    # ----- prelude: superuser seeds mirror_idle_timeout_seconds=60 ------
    superuser_cookies = _login_only(
        backend_url, email="admin@example.com", password="changethis"
    )
    _seed_mirror_idle_timeout(
        backend_url, superuser_cookies, value=_MIRROR_IDLE_TIMEOUT_S
    )

    # ----- prelude: signup team-admin user with personal team -----------
    suffix = uuid.uuid4().hex[:8]
    admin_email = f"m004-s03-admin-{suffix}@example.com"
    admin_password = "Sup3rs3cret-team-admin"
    admin_full_name = f"M004S03Admin {suffix}"
    admin_cookies = _signup_login(
        backend_url,
        email=admin_email,
        password=admin_password,
        full_name=admin_full_name,
    )
    team_id = _personal_team_id(backend_url, admin_cookies)
    expected_container = _team_mirror_container_name(team_id)

    # ===== Scenario A: cold-start ensure ================================
    code_a, body_a = _http_orch(
        eph_name,
        f"/v1/teams/{team_id}/mirror/ensure",
        api_key=api_key,
    )
    assert code_a == 200, f"ensure cold-start: {code_a} {body_a}"
    import json as _json
    body_a_json = _json.loads(body_a)
    assert "container_id" in body_a_json and body_a_json["container_id"], (
        f"ensure cold-start body: {body_a_json!r}"
    )
    container_id_a: str = body_a_json["container_id"]
    network_addr_a: str = body_a_json["network_addr"]
    # network_addr shape: team-mirror-<first8>:9418
    expected_addr = f"{expected_container}:9418"
    assert network_addr_a == expected_addr, (
        f"unexpected network_addr: {network_addr_a!r} (want {expected_addr!r})"
    )
    assert body_a_json["reused"] is False, (
        f"cold-start should not be reused: {body_a_json!r}"
    )

    # team_mirror_volumes row inserted with non-NULL container_id.
    row_count = _psql_one(
        f"SELECT count(*) FROM team_mirror_volumes "
        f"WHERE team_id='{team_id}' AND container_id IS NOT NULL"
    )
    assert row_count == "1", (
        f"team_mirror_volumes row missing or container_id NULL: {row_count!r}"
    )

    # Container is running with expected labels.
    assert _container_running(expected_container), (
        f"team-mirror container {expected_container!r} not running after ensure"
    )
    label_q = _docker(
        "inspect", "-f",
        '{{index .Config.Labels "perpetuity.team_mirror"}}',
        expected_container,
        check=False, timeout=10,
    )
    assert (label_q.stdout or "").strip() == "true", (
        f"perpetuity.team_mirror label missing: {label_q.stdout!r}"
    )

    # Log marker.
    assert _wait_for_log_marker(
        eph_name, "team_mirror_started", timeout_s=10.0
    ), (
        "missing 'team_mirror_started' in orchestrator logs after ensure; "
        f"tail:\n{(_docker('logs', '--tail=80', eph_name, check=False).stdout or '')}"
    )

    # ===== Scenario B: idempotent ensure ================================
    code_b, body_b = _http_orch(
        eph_name,
        f"/v1/teams/{team_id}/mirror/ensure",
        api_key=api_key,
    )
    assert code_b == 200, f"ensure idempotent: {code_b} {body_b}"
    body_b_json = _json.loads(body_b)
    assert body_b_json["container_id"] == container_id_a, (
        f"second ensure returned different container_id: "
        f"{body_b_json['container_id']!r} vs {container_id_a!r}"
    )
    assert body_b_json["reused"] is True, (
        f"second ensure should be reused: {body_b_json!r}"
    )
    # Exactly one team-mirror container for this team.
    ps = _docker(
        "ps", "-q", "--filter", f"label=team_id={team_id}",
        "--filter", "label=perpetuity.team_mirror=true",
        check=False, timeout=10,
    )
    container_ids = [s for s in (ps.stdout or "").split() if s]
    assert len(container_ids) == 1, (
        f"expected exactly 1 mirror container; got {container_ids!r}"
    )
    assert _wait_for_log_marker(
        eph_name, "team_mirror_reused", timeout_s=10.0
    ), "missing 'team_mirror_reused' in orchestrator logs after second ensure"

    # ===== Scenario C: sibling clone over git://...:9418 ================
    # Create a bare repo inside the mirror.
    init_r = _docker(
        "exec", expected_container,
        "git", "init", "--bare", "/repos/test.git",
        check=False, timeout=20,
    )
    assert init_r.returncode == 0, (
        f"git init --bare failed inside mirror: rc={init_r.returncode} "
        f"stderr={init_r.stderr!r} stdout={init_r.stdout!r}"
    )

    # alpine/git sibling clones via compose-DNS.
    clone_name = f"s03-mirror-clone-{uuid.uuid4().hex[:8]}"
    clone_r = _docker(
        "run", "--rm",
        "--name", clone_name,
        "--network", NETWORK,
        "alpine/git",
        "clone", f"git://{expected_container}:9418/test.git", "/tmp/clone",
        check=False, timeout=60,
    )
    assert clone_r.returncode == 0, (
        f"sibling git clone failed: rc={clone_r.returncode} "
        f"stderr={clone_r.stderr!r} stdout={clone_r.stdout!r}"
    )
    # `git clone` of an empty bare repo yields a working tree with .git/HEAD.
    # Re-run to verify HEAD by cloning again into a different path with the
    # entrypoint overridden to ls; alpine/git has sh + ls.
    verify_name = f"s03-mirror-clone-{uuid.uuid4().hex[:8]}"
    verify_r = _docker(
        "run", "--rm",
        "--name", verify_name,
        "--network", NETWORK,
        "--entrypoint", "sh",
        "alpine/git",
        "-c",
        f"git clone git://{expected_container}:9418/test.git /tmp/c "
        "&& test -f /tmp/c/.git/HEAD && echo HEAD_OK",
        check=False, timeout=60,
    )
    assert verify_r.returncode == 0 and "HEAD_OK" in (verify_r.stdout or ""), (
        f"sibling clone HEAD verify failed: rc={verify_r.returncode} "
        f"stderr={verify_r.stderr!r} stdout={verify_r.stdout!r}"
    )

    # ===== Scenario D: always_on=true bypasses idle reap ================
    with httpx.Client(
        base_url=backend_url, timeout=15.0, cookies=admin_cookies
    ) as c:
        r_on = c.patch(
            f"/api/v1/teams/{team_id}/mirror",
            json={"always_on": True},
        )
    assert r_on.status_code == 200, (
        f"PATCH always_on=true: {r_on.status_code} {r_on.text}"
    )

    # Back-date last_idle_at by >> idle deadline so the next reaper tick
    # would normally reap, but always_on must suppress it.
    upd = _psql_exec(
        f"UPDATE team_mirror_volumes "
        f"SET last_idle_at = NOW() - INTERVAL '{_BACKDATE_S} seconds' "
        f"WHERE team_id='{team_id}'"
    )
    assert upd.returncode == 0, (
        f"backdate last_idle_at failed; stderr={upd.stderr!r}"
    )

    # Wait for the *always_on* reap-skip line specifically. The reaper
    # may have already logged `reason=recent_activity` for this row on a
    # prior tick (before we back-dated last_idle_at), so we cannot match
    # on the bare `team_mirror_reap_skipped` token — we must wait for the
    # team_id + reason=always_on combination after the back-date.
    always_on_marker = (
        f"team_mirror_reap_skipped team_id={team_id} reason=always_on"
    )
    saw_skip = _wait_for_log_marker(
        eph_name, always_on_marker, timeout_s=_REAPER_WAIT_S * 2
    )
    assert saw_skip, (
        f"missing {always_on_marker!r} in orchestrator logs after "
        "backdating last_idle_at with always_on=true; tail:\n"
        f"{(_docker('logs', '--tail=80', eph_name, check=False).stdout or '')}"
    )
    # Container must still be running.
    assert _container_running(expected_container), (
        f"team-mirror container {expected_container!r} was reaped "
        "despite always_on=true"
    )

    # ===== Scenario E: always_on=false re-enables reap ==================
    with httpx.Client(
        base_url=backend_url, timeout=15.0, cookies=admin_cookies
    ) as c:
        r_off = c.patch(
            f"/api/v1/teams/{team_id}/mirror",
            json={"always_on": False},
        )
    assert r_off.status_code == 200, (
        f"PATCH always_on=false: {r_off.status_code} {r_off.text}"
    )

    # The PATCH does NOT touch last_idle_at (T03 only updates always_on),
    # so the back-dated value remains in place. Wait for the reaper to
    # pick the row up on its next tick.
    saw_reap = _wait_for_log_marker(
        eph_name,
        f"team_mirror_reaped team_id={team_id}",
        timeout_s=_REAPER_WAIT_S * 2,  # extra slack — first tick may skip
    )
    assert saw_reap, (
        f"missing 'team_mirror_reaped team_id={team_id}' in orchestrator "
        "logs after flipping always_on=false"
    )

    # Container is gone.
    # The reap path stops + force-deletes; `docker inspect` returns non-zero.
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if not _container_exists(expected_container):
            break
        time.sleep(0.5)
    assert not _container_exists(expected_container), (
        f"team-mirror container {expected_container!r} still present after reap"
    )

    # team_mirror_volumes row: container_id NULL, volume_path persists.
    cid_after = _psql_one(
        f"SELECT container_id FROM team_mirror_volumes "
        f"WHERE team_id='{team_id}'"
    )
    # psql -A -t emits an empty string for SQL NULL.
    assert cid_after == "", (
        f"container_id should be NULL after reap; got {cid_after!r}"
    )
    vp_after = _psql_one(
        f"SELECT volume_path FROM team_mirror_volumes "
        f"WHERE team_id='{team_id}'"
    )
    assert vp_after, (
        f"volume_path should persist after reap; got {vp_after!r}"
    )

    # ===== Final structural log marker assertions =======================
    # Capture once more so all six markers are visible in the same blob.
    blob_final_orch = _docker(
        "logs", eph_name, check=False, timeout=15
    )
    orch_blob = (
        (blob_final_orch.stdout or "") + (blob_final_orch.stderr or "")
    )
    blob_final_backend = _docker(
        "logs", backend_name, check=False, timeout=15
    )
    backend_blob = (
        (blob_final_backend.stdout or "")
        + (blob_final_backend.stderr or "")
    )
    swept_blob = "\n".join((orch_blob, backend_blob))

    required_markers = (
        "team_mirror_started",
        "team_mirror_reused",
        "team_mirror_reaped",
        "team_mirror_reap_skipped",
        f"mirror_idle_timeout_seconds_resolved value={_MIRROR_IDLE_TIMEOUT_S}",
        "team_mirror_always_on_toggled",
    )
    for marker in required_markers:
        assert marker in swept_blob, (
            f"observability taxonomy regression: {marker!r} not seen in "
            f"backend/orchestrator logs"
        )

    # Reaper should NOT have hit `team_mirror_reaper_tick_failed` —
    # WARNING surface present in slice plan but only on real failures.
    assert "team_mirror_reaper_tick_failed" not in swept_blob, (
        "team_mirror_reaper_tick_failed appeared — reaper hit an unexpected "
        "exception during the test"
    )

    elapsed = time.time() - suite_started
    # Slice budget: orchestrator boot (~10s) + backend boot (~30s) +
    # ensure (~1s) + clone (~5s) + two reap windows (~10s).
    assert elapsed < 240.0, (
        f"e2e suite took {elapsed:.1f}s — far over the budget"
    )
