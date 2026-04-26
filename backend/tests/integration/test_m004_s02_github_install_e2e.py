"""M004 / S02 / T04 — GitHub App install + token-cache e2e.

Slice S02's demo-truth statement: a team admin retrieves a signed install URL
from the backend, GitHub round-trips back to /api/v1/github/install-callback
with the signed state, the callback validates the state and persists a
github_app_installations row scoped to the team, the team-settings list shows
the row, and the orchestrator can mint installation tokens on demand with a
50-minute Redis cache.

Strategy: end-to-end against the live compose stack (db, redis), with a
mock-github sidecar replacing api.github.com, an ephemeral orchestrator
parameterized to talk to that mock-github, and a sibling backend pointed at
the ephemeral orchestrator. We never touch the real GitHub API.

Pieces:

  * Skip-guards probing baked images for the s06b alembic revision (backend)
    and the orchestrator/orchestrator/github_tokens.py module (orchestrator).
  * Autouse cleanup fixture wiping the four github_app_* system_settings rows
    AND every github_app_installations row before AND after the test
    (mirrors MEM246/S01).
  * Module-local helpers for: synthetic RSA keypair, seeding github_app_*
    settings, booting mock-github, booting an ephemeral orchestrator pointed
    at it, booting a sibling backend pointed at the ephemeral orchestrator.
  * Six scenarios A-F covering install URL + state, callback round-trip,
    duplicate idempotency, token mint + cache, expired state, decrypt-failure
    503; plus a final redaction sweep across backend+orchestrator logs.

How to run::

    docker compose build backend orchestrator
    docker compose up -d db redis orchestrator
    cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e \\
        tests/integration/test_m004_s02_github_install_e2e.py -v

Wall-clock budget: ≤180 s on a warm compose stack (mock-github boot +
ephemeral orchestrator boot + sibling backend boot dominate).
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)

NETWORK = "perpetuity_default"
ORCH_IMAGE = "orchestrator:latest"
BACKEND_IMAGE = "backend:latest"
WORKSPACE_IMAGE = "perpetuity/workspace:test"
ORCH_DNS_ALIAS = "orchestrator"

# Sentinel substring that uniquely identifies the synthetic PEM body for the
# end-of-test redaction sweep — the body sliced into the middle of the PEM
# so any leak into logs is unambiguous.
_PEM_SENTINEL_PREFIX = "PEMS02SENTINEL"

# Shared Fernet key — same value as the conftest's SYSTEM_SETTINGS_ENCRYPTION
# _KEY_TEST so the sibling backend AND the ephemeral orchestrator can both
# decrypt rows the other side wrote.
SYSTEM_SETTINGS_ENCRYPTION_KEY_TEST = (
    "kfk5l7mPRFpBV7PzWJxYmO6LRRQAdZ4iGYZRG6xL0fY="
)

# The github_app_installations migration revision. The skip-guard probes
# backend:latest for `s06b_github_app_installations.py`; missing means the
# image predates T01 and the test would fail in a confusing way at prestart.
S06B_REVISION = "s06b_github_app_installations"

# The four sensitive/non-sensitive github_app_* keys that the test seeds and
# the autouse cleanup wipes. github_app_webhook_secret isn't exercised by
# this test but the cleanup wipes it anyway to keep the table empty between
# runs (compose's app-db-data volume persists, MEM161).
_GITHUB_APP_KEYS = (
    "github_app_id",
    "github_app_client_id",
    "github_app_private_key",
    "github_app_webhook_secret",
)

# Fixed installation_id keeps the cache key deterministic for KEYS / TTL
# probes (see scenario D). 42 has no GitHub significance — just a stable
# small int the test can hardcode.
_FIXED_INSTALLATION_ID = 42

# The fake installation token the mock-github returns. Prefix `ghs_` to
# match GitHub's real installation-token shape; the redaction sweep at the
# end of the test asserts this string never appears in backend/orchestrator
# logs (the mock-github container's logs DO contain it by design — only
# backend+orchestrator logs are swept).
_MOCK_FIXED_TOKEN = "ghs_M004S02E2EFAKEINSTALLATIONTOKEN0000000000"


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


def _redis_cli(*args: str, redis_password: str) -> str:
    """Run `redis-cli -a <pw> <args...>` inside the compose redis container."""
    r = _docker(
        "exec", "perpetuity-redis-1",
        "redis-cli", "-a", redis_password, "--no-auth-warning",
        *args,
        check=False, timeout=15,
    )
    return (r.stdout or "").strip()


def _delete_github_app_settings_and_installations() -> None:
    """Belt-and-suspenders cleanup. Wipe github_app_installations rows AND
    the four github_app_* system_settings rows so the test starts and ends
    with a known-empty surface regardless of leftover state from prior
    runs (MEM161 — compose's `app-db-data` named volume persists)."""
    # Installations first — they FK to team_id, but the github_app_* settings
    # are independent rows, so order between the two DELETEs doesn't matter.
    # We do installations first only because the slice's mental model has the
    # row depending on the settings being seeded.
    _psql_exec("DELETE FROM github_app_installations")
    keys_csv = ",".join(f"'{k}'" for k in _GITHUB_APP_KEYS)
    _psql_exec(f"DELETE FROM system_settings WHERE key IN ({keys_csv})")


# ----- image probes (skip-guards) ----------------------------------------


def _backend_image_has_s06b() -> bool:
    r = _docker(
        "run", "--rm", "--entrypoint", "ls", BACKEND_IMAGE,
        "/app/backend/app/alembic/versions/",
        check=False, timeout=15,
    )
    return f"{S06B_REVISION}.py" in (r.stdout or "")


def _orchestrator_image_has_github_tokens() -> bool:
    """Probe orchestrator:latest for the github_tokens.py module that T03
    introduced. The Dockerfile copies orchestrator/orchestrator -> /app/
    orchestrator (Dockerfile line `COPY orchestrator/orchestrator /app/
    orchestrator`), so the module lives at /app/orchestrator/github_tokens.py
    inside the image. A stale image without the file would break booting
    the ephemeral orchestrator in a confusing way."""
    r = _docker(
        "run", "--rm", "--entrypoint", "ls", ORCH_IMAGE,
        "/app/orchestrator/",
        check=False, timeout=15,
    )
    return "github_tokens.py" in (r.stdout or "")


@pytest.fixture(autouse=True)
def _require_baked_images() -> None:
    if not _backend_image_has_s06b():
        pytest.skip(
            f"backend:latest is missing the {S06B_REVISION!r} alembic "
            "revision — run `docker compose build backend orchestrator` so "
            "the images bake the current source tree."
        )
    if not _orchestrator_image_has_github_tokens():
        pytest.skip(
            "orchestrator:latest is missing orchestrator/github_tokens.py — "
            "run `docker compose build backend orchestrator`."
        )


@pytest.fixture(autouse=True)
def _wipe_github_state_before_after() -> Iterator[None]:
    """Wipe github_app_installations + github_app_* settings rows before AND
    after each test (mirrors MEM246 / S01 pattern). Belt-and-suspenders
    cleanup against the persistent app-db-data named volume."""
    _delete_github_app_settings_and_installations()
    yield
    _delete_github_app_settings_and_installations()


# ----- key generation + PEM helper ---------------------------------------


def _generate_rsa_keypair() -> tuple[str, str]:
    """Generate a fresh 2048-bit RSA keypair for the test.

    Returns (private_key_pem, public_key_pem). The private key is what the
    test seeds into `system_settings.github_app_private_key`; the public
    key is mounted into the mock-github container so it can verify the App
    JWTs the orchestrator mints. The PEM body contains a unique sentinel
    substring (`_PEM_SENTINEL_PREFIX`) so a leak into backend/orchestrator
    logs is unambiguous in the redaction sweep at the end of the test.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem_raw = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")

    # Splice a sentinel into a non-armor line so the body stays parseable
    # by both jwt.encode() (the orchestrator) and jwt.decode() (the mock).
    # We embed it as a comment-shaped line BETWEEN the BEGIN armor and the
    # base64 body — PyCA's PEM parser tolerates leading comment lines.
    sentinel = f"{_PEM_SENTINEL_PREFIX}{uuid.uuid4().hex}"
    lines = private_pem_raw.splitlines()
    # Validate shape: line[0] must be the BEGIN armor.
    assert lines[0].startswith("-----BEGIN"), (
        f"unexpected PEM shape — first line {lines[0]!r}"
    )
    # The sentinel goes into the SECOND line (an inert leading line); PyCA
    # treats lines before the first base64 as a header block and the first
    # all-base64 line as the body start. To avoid breaking the parse we
    # actually return TWO PEMs: one with the sentinel for the redaction
    # check, and the canonical one for actual cryptographic use. The route
    # validator only requires `-----BEGIN` armor + length, so the sentinel
    # version passes the structural validator AND is a valid signing key
    # because PyCA's PEM parser tolerates `Comment:` headers.
    sentinel_line = f"Comment: {sentinel}"
    private_pem_with_sentinel = "\n".join(
        [lines[0], sentinel_line, "", *lines[1:]]
    )
    return private_pem_with_sentinel, public_pem


def _pem_sentinel_value(private_pem: str) -> str:
    """Pull the sentinel substring back out of a sentineled PEM. Used by
    the redaction sweep to assert the substring never appeared in logs."""
    for line in private_pem.splitlines():
        if line.startswith("Comment: ") and _PEM_SENTINEL_PREFIX in line:
            return line[len("Comment: "):]
    raise AssertionError("sentinel not found in private_pem")


# ----- credential seeding -------------------------------------------------


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


def _seed_github_app_credentials(
    backend_url: str,
    admin_cookies: httpx.Cookies,
    *,
    private_key_pem: str,
    app_id: int,
    client_id: str,
) -> None:
    """PUT the three github_app_* settings the slice depends on.

    Order: id, client_id, private_key. The first two are non-sensitive
    JSONB scalars; the private key is a Fernet-encrypted PEM. The admin
    PUT validators enforce shape (int 1..2**63-1; ASCII ≤255; PEM armor +
    length) and the route persists ciphertext for the sensitive row only.
    """
    with httpx.Client(
        base_url=backend_url, timeout=30.0, cookies=admin_cookies
    ) as c:
        r = c.put(
            "/api/v1/admin/settings/github_app_id",
            json={"value": app_id},
        )
        assert r.status_code == 200, (
            f"PUT github_app_id: {r.status_code} {r.text}"
        )
        r = c.put(
            "/api/v1/admin/settings/github_app_client_id",
            json={"value": client_id},
        )
        assert r.status_code == 200, (
            f"PUT github_app_client_id: {r.status_code} {r.text}"
        )
        r = c.put(
            "/api/v1/admin/settings/github_app_private_key",
            json={"value": private_key_pem},
        )
        assert r.status_code == 200, (
            f"PUT github_app_private_key: {r.status_code} {r.text}"
        )


# ----- mock-github sidecar -----------------------------------------------


def _boot_mock_github(
    *,
    public_key_pem: str,
    fixed_token: str,
    app_id: int,
) -> tuple[str, str]:
    """Run a python:3.12-slim sibling container with mock_github_app.py
    mounted in, install fastapi+pyjwt+uvicorn, and start uvicorn on :8080.

    Returns (container_name, base_url). base_url is the compose-DNS name
    `http://<container>:8080` — only resolvable from other containers on
    `perpetuity_default`.

    The mount path is the absolute host path to mock_github_app.py. The
    public key is passed via env (multi-line PEMs are awkward to pass
    through `-e`, but docker tolerates literal newlines so we keep it
    simple — same trick the M002/S05 tests use).
    """
    name = f"mock-github-{uuid.uuid4().hex[:8]}"
    fixture_path = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__), "fixtures", "mock_github_app.py"
        )
    )
    assert os.path.exists(fixture_path), (
        f"mock_github_app.py missing at {fixture_path}"
    )

    # Inline-install fastapi + uvicorn + pyjwt + cryptography. We run pip
    # install at container start because python:3.12-slim has no preinstalled
    # libs; this adds ~10s to first-run boot but keeps the test self-contained
    # (no separate Dockerfile to build and bake).
    boot_cmd = (
        "set -e; "
        "pip install --quiet --no-cache-dir "
        "'fastapi==0.115.*' 'uvicorn==0.32.*' "
        "'pyjwt[crypto]==2.9.*' 'cryptography>=43,<46'; "
        "exec uvicorn mock_github_app:app --host 0.0.0.0 --port 8080"
    )

    _docker(
        "run", "-d",
        "--name", name,
        "--network", NETWORK,
        "--network-alias", name,
        "-v", f"{fixture_path}:/app/mock_github_app.py:ro",
        "-w", "/app",
        "-e", f"PUBLIC_KEY_PEM={public_key_pem}",
        "-e", f"FIXED_TOKEN={fixed_token}",
        "-e", f"GITHUB_APP_ID={app_id}",
        "--entrypoint", "bash",
        "python:3.12-slim",
        "-c", boot_cmd,
        timeout=60,
    )

    base_url = f"http://{name}:8080"

    # Probe readiness from inside the mock container itself — python3 +
    # urllib is available out of the box on python:3.12-slim. The /healthz
    # endpoint deliberately does no work so a 200 means uvicorn finished
    # booting and pip install completed.
    deadline = time.time() + 60.0
    last_err = ""
    probe_script = (
        "import sys, urllib.request\n"
        "try:\n"
        "    urllib.request.urlopen("
        "'http://127.0.0.1:8080/healthz', timeout=2).read()\n"
        "    sys.exit(0)\n"
        "except Exception as e:\n"
        "    print(repr(e)); sys.exit(3)\n"
    )
    while time.time() < deadline:
        r = _docker(
            "exec", name, "python3", "-c", probe_script,
            check=False, timeout=5,
        )
        if r.returncode == 0:
            return name, base_url
        last_err = (r.stderr or "")[:200] + " | " + (r.stdout or "")[:200]
        time.sleep(1.0)

    logs = _docker("logs", name, check=False, timeout=10).stdout or ""
    _docker("rm", "-f", name, check=False)
    pytest.fail(
        f"mock-github {name!r} never became healthy at {base_url!r}; "
        f"last_probe={last_err!r}\n"
        f"docker logs (last 80 lines):\n"
        f"{os.linesep.join(logs.splitlines()[-80:])}"
    )


# ----- ephemeral orchestrator parameterized by mock-github URL -----------


def _ensure_host_workspaces_shared() -> None:
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


def _boot_orch_with_mock(
    *,
    mock_github_url: str,
    redis_password: str,
    pg_password: str,
    api_key: str,
) -> str:
    """Stop the compose orchestrator and launch an ephemeral one whose
    `github_api_base_url` points at our mock-github sidecar. Returns the
    ephemeral container name."""
    name = f"orch-s02-mock-{uuid.uuid4().hex[:8]}"
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
        "-e", f"SYSTEM_SETTINGS_ENCRYPTION_KEY={SYSTEM_SETTINGS_ENCRYPTION_KEY_TEST}",
        "-e", "REDIS_HOST=redis",
        "-e", f"REDIS_PASSWORD={redis_password}",
        "-e",
        f"DATABASE_URL=postgresql://postgres:{pg_password}@db:5432/app",
        "-e", f"GITHUB_API_BASE_URL={mock_github_url}",
        ORCH_IMAGE,
    ]
    _docker(*args)
    return name


def _wait_for_orch_running_self(
    ephemeral_name: str, *, timeout_s: float = 60.0
) -> None:
    """MEM198 readiness probe — exec python3+urllib INSIDE the ephemeral
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
    """Boot a sibling backend container parameterized by ORCHESTRATOR_API_KEY
    and pointed at the ephemeral orchestrator at http://orchestrator:8001
    (which the network-alias trick redirects to our ephemeral container).

    Returns (container_name, base_url).
    """
    name = f"perpetuity-backend-e2e-s02-{uuid.uuid4().hex[:8]}"
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


# ----- the test ----------------------------------------------------------


@pytest.fixture
def install_stack(
    compose_stack_up: None,  # noqa: ARG001
    request: pytest.FixtureRequest,
) -> Iterator[dict[str, str]]:
    """Boot mock-github, ephemeral orchestrator pointed at it, sibling backend
    pointed at the ephemeral orchestrator. Yield a dict of names + base_urls.

    Teardown captures logs BEFORE removing containers (once `docker rm -f`
    runs, logs are gone), then removes everything and restores the compose
    orchestrator.
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
    api_key = secrets.token_urlsafe(32)

    private_pem, public_pem = _generate_rsa_keypair()
    app_id = secrets.randbelow(900_000) + 100_000  # 6-digit-ish app id

    mock_name, mock_url = _boot_mock_github(
        public_key_pem=public_pem,
        fixed_token=_MOCK_FIXED_TOKEN,
        app_id=app_id,
    )
    created: list[str] = [mock_name]

    captured: dict[str, str] = {}

    def _teardown() -> None:
        # Capture logs first, then rm -f.
        for n in created:
            try:
                blob = _docker("logs", n, check=False, timeout=15)
                captured[n] = (blob.stdout or "") + (blob.stderr or "")
            except Exception:  # noqa: BLE001 — best-effort
                captured[n] = ""
        for n in created:
            _docker("rm", "-f", n, check=False, timeout=30)
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

    eph_name = _boot_orch_with_mock(
        mock_github_url=mock_url,
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
        "mock_name": mock_name,
        "mock_url": mock_url,
        "eph_name": eph_name,
        "backend_name": bk_name,
        "backend_url": bk_url,
        "api_key": api_key,
        "private_pem": private_pem,
        "public_pem": public_pem,
        "app_id": str(app_id),
        "redis_password": redis_password,
        "pg_password": pg_password,
        "secret_key": secret_key,
    }


def _http_orch(eph_name: str, path: str, *, api_key: str) -> tuple[int, str]:
    """Hit the ephemeral orchestrator's HTTP surface from inside its own
    container (sidesteps the host network — no port published).

    Returns (status_code, body_text).
    """
    probe = (
        "import sys, urllib.request, urllib.error\n"
        f"req = urllib.request.Request('http://127.0.0.1:8001{path}', "
        f"headers={{'X-Orchestrator-Key': {api_key!r}}})\n"
        "try:\n"
        "    r = urllib.request.urlopen(req, timeout=10)\n"
        "    sys.stdout.write(str(r.status) + chr(10) + r.read().decode())\n"
        "except urllib.error.HTTPError as e:\n"
        "    sys.stdout.write(str(e.code) + chr(10) + e.read().decode())\n"
    )
    r = _docker(
        "exec", eph_name, "python3", "-c", probe,
        check=False, timeout=20,
    )
    out = (r.stdout or "").split("\n", 1)
    if len(out) != 2:
        return 0, (r.stdout or "") + (r.stderr or "")
    try:
        return int(out[0]), out[1]
    except ValueError:
        return 0, (r.stdout or "") + (r.stderr or "")


def test_m004_s02_github_install_e2e(  # noqa: PLR0912, PLR0915
    install_stack: dict[str, str],
) -> None:
    """End-to-end install + token-mint + cache contract for slice S02."""
    suite_started = time.time()

    backend_url = install_stack["backend_url"]
    backend_name = install_stack["backend_name"]
    eph_name = install_stack["eph_name"]
    mock_name = install_stack["mock_name"]
    api_key = install_stack["api_key"]
    private_pem = install_stack["private_pem"]
    app_id = int(install_stack["app_id"])
    redis_password = install_stack["redis_password"]

    # ----- prelude: log in as superuser, seed credentials ----------------
    admin_cookies = _login_only(
        backend_url, email="admin@example.com", password="changethis"
    )
    _seed_github_app_credentials(
        backend_url,
        admin_cookies,
        private_key_pem=private_pem,
        app_id=app_id,
        client_id="perpetuity-test",
    )

    # Sanity: settings landed.
    pem_count = _psql_one(
        "SELECT count(*) FROM system_settings "
        "WHERE key='github_app_private_key' AND value_encrypted IS NOT NULL"
    )
    assert pem_count == "1", (
        f"PEM not encrypted into system_settings; got count={pem_count!r}"
    )

    # ----- prelude: signup team-admin user with a personal team ----------
    suffix = uuid.uuid4().hex[:8]
    admin2_email = f"m004-s02-admin-{suffix}@example.com"
    admin2_password = "Sup3rs3cret-team-admin"
    admin2_full_name = f"M004S02Admin {suffix}"
    admin2_cookies = _signup_login(
        backend_url,
        email=admin2_email,
        password=admin2_password,
        full_name=admin2_full_name,
    )
    team_id = _personal_team_id(backend_url, admin2_cookies)

    # ===== Scenario A: install URL + state JWT shape =====================
    with httpx.Client(
        base_url=backend_url, timeout=15.0, cookies=admin2_cookies
    ) as c:
        r_url = c.get(f"/api/v1/teams/{team_id}/github/install-url")
    assert r_url.status_code == 200, (
        f"install-url: {r_url.status_code} {r_url.text}"
    )
    url_body = r_url.json()
    assert "install_url" in url_body and "state" in url_body, (
        f"install-url body shape: {url_body!r}"
    )
    state_token: str = url_body["state"]
    # Decode the state JWT against SECRET_KEY in-test — proves the route
    # signed it correctly. audience='github-install', iss='perpetuity-install'.
    secret_key = install_stack["secret_key"]
    decoded = jwt.decode(
        state_token,
        secret_key,
        algorithms=["HS256"],
        audience="github-install",
        issuer="perpetuity-install",
    )
    assert decoded["team_id"] == team_id, (
        f"state team_id mismatch: {decoded!r} vs team_id={team_id!r}"
    )
    now_ts = int(time.time())
    exp = decoded["exp"]
    # Slice spec says 10-minute exp; tolerate 8..12 minutes for clock skew
    # between this process and the backend container.
    assert (now_ts + 8 * 60) <= exp <= (now_ts + 12 * 60), (
        f"state exp out of expected window: now={now_ts} exp={exp} "
        f"(want now+~600s)"
    )
    # The install URL must reference the seeded client_id.
    assert "perpetuity-test" in url_body["install_url"], (
        f"install_url missing client_id: {url_body['install_url']!r}"
    )

    # ===== Scenario B: install-callback round-trip =======================
    with httpx.Client(base_url=backend_url, timeout=30.0) as c:
        # Public route — no cookies. The state JWT IS the auth.
        c.cookies.clear()
        r_cb = c.post(
            "/api/v1/github/install-callback",
            json={
                "installation_id": _FIXED_INSTALLATION_ID,
                "setup_action": "install",
                "state": state_token,
            },
        )
    assert r_cb.status_code == 200, (
        f"install-callback: {r_cb.status_code} {r_cb.text}"
    )
    cb_body = r_cb.json()
    assert cb_body["installation_id"] == _FIXED_INSTALLATION_ID
    assert cb_body["account_login"] == "test-org"
    assert cb_body["account_type"] == "Organization"
    assert cb_body["team_id"] == team_id

    with httpx.Client(
        base_url=backend_url, timeout=15.0, cookies=admin2_cookies
    ) as c:
        r_list = c.get(f"/api/v1/teams/{team_id}/github/installations")
    assert r_list.status_code == 200, (
        f"installations list: {r_list.status_code} {r_list.text}"
    )
    list_body = r_list.json()
    assert list_body["count"] == 1, f"installations count: {list_body!r}"
    assert list_body["data"][0]["installation_id"] == _FIXED_INSTALLATION_ID

    # ===== Scenario C: duplicate install-callback is idempotent ==========
    # Mint a fresh state JWT (the prior one is still valid but using a fresh
    # one models the realistic case where the operator refreshes the page).
    with httpx.Client(
        base_url=backend_url, timeout=15.0, cookies=admin2_cookies
    ) as c:
        r_url2 = c.get(f"/api/v1/teams/{team_id}/github/install-url")
    state_token2 = r_url2.json()["state"]
    with httpx.Client(base_url=backend_url, timeout=30.0) as c:
        c.cookies.clear()
        r_cb2 = c.post(
            "/api/v1/github/install-callback",
            json={
                "installation_id": _FIXED_INSTALLATION_ID,
                "setup_action": "install",
                "state": state_token2,
            },
        )
    assert r_cb2.status_code == 200, (
        f"duplicate install-callback: {r_cb2.status_code} {r_cb2.text}"
    )
    with httpx.Client(
        base_url=backend_url, timeout=15.0, cookies=admin2_cookies
    ) as c:
        r_list2 = c.get(f"/api/v1/teams/{team_id}/github/installations")
    assert r_list2.json()["count"] == 1, (
        "duplicate install-callback created a second row — UPSERT broken"
    )

    # ===== Scenario D: installation token mint + cache ===================
    # Pre-flush any stale cache key from a prior incomplete run.
    _redis_cli(
        "DEL", f"gh:installtok:{_FIXED_INSTALLATION_ID}",
        redis_password=redis_password,
    )

    code1, body1 = _http_orch(
        eph_name,
        f"/v1/installations/{_FIXED_INSTALLATION_ID}/token",
        api_key=api_key,
    )
    assert code1 == 200, f"first token mint: {code1} {body1}"
    body1_json = json.loads(body1)
    assert body1_json["source"] == "mint", (
        f"expected source=mint on first call; got {body1_json!r}"
    )
    assert body1_json["token"] == _MOCK_FIXED_TOKEN, (
        f"unexpected token: {body1_json['token']!r}"
    )

    code2, body2 = _http_orch(
        eph_name,
        f"/v1/installations/{_FIXED_INSTALLATION_ID}/token",
        api_key=api_key,
    )
    assert code2 == 200, f"second token call: {code2} {body2}"
    body2_json = json.loads(body2)
    assert body2_json["source"] == "cache", (
        f"expected source=cache on second call; got {body2_json!r}"
    )
    assert body2_json["token"] == _MOCK_FIXED_TOKEN

    # Redis introspection — exactly one cache key, TTL near 50 minutes.
    keys_out = _redis_cli(
        "KEYS", "gh:installtok:*", redis_password=redis_password
    )
    keys_lines = [k for k in keys_out.splitlines() if k.strip()]
    assert keys_lines == [f"gh:installtok:{_FIXED_INSTALLATION_ID}"], (
        f"expected exactly one gh:installtok:* key; got {keys_lines!r}"
    )
    ttl_str = _redis_cli(
        "TTL", f"gh:installtok:{_FIXED_INSTALLATION_ID}",
        redis_password=redis_password,
    )
    ttl_int = int(ttl_str)
    assert 1 < ttl_int <= 3001, (
        f"TTL out of expected range (1, 3001]: got {ttl_int}"
    )

    # ===== Scenario E: expired state JWT → 400 install_state_expired =====
    expired_payload = {
        "team_id": team_id,
        "jti": secrets.token_urlsafe(16),
        "iat": int(time.time()) - 700,
        "exp": int(time.time()) - 60,
        "iss": "perpetuity-install",
        "aud": "github-install",
    }
    expired_state = jwt.encode(
        expired_payload, secret_key, algorithm="HS256"
    )
    with httpx.Client(base_url=backend_url, timeout=15.0) as c:
        c.cookies.clear()
        r_exp = c.post(
            "/api/v1/github/install-callback",
            json={
                "installation_id": _FIXED_INSTALLATION_ID,
                "setup_action": "install",
                "state": expired_state,
            },
        )
    assert r_exp.status_code == 400, (
        f"expired-state callback: {r_exp.status_code} {r_exp.text}"
    )
    assert r_exp.json().get("detail") == "install_state_expired", (
        f"expired-state body: {r_exp.json()!r}"
    )

    # ===== Scenario F: decrypt-failure surfaces 503 over HTTP ============
    # Corrupt the stored ciphertext directly via psql, then flush the cache
    # so the next /token call goes through _load_github_app_credentials,
    # which calls Fernet.decrypt and raises SystemSettingDecryptError.
    upd = _psql_exec(
        "UPDATE system_settings "
        "SET value_encrypted = E'\\\\xdeadbeef' "
        "WHERE key='github_app_private_key'"
    )
    assert upd.returncode == 0, (
        f"psql UPDATE failed; rc={upd.returncode} stderr={upd.stderr!r}"
    )
    _redis_cli(
        "DEL", f"gh:installtok:{_FIXED_INSTALLATION_ID}",
        redis_password=redis_password,
    )

    code_f, body_f = _http_orch(
        eph_name,
        f"/v1/installations/{_FIXED_INSTALLATION_ID}/token",
        api_key=api_key,
    )
    assert code_f == 503, (
        f"decrypt-failure should return 503; got {code_f} {body_f!r}"
    )
    body_f_json = json.loads(body_f)
    assert body_f_json.get("detail") == "system_settings_decrypt_failed", (
        f"decrypt-failure body: {body_f_json!r}"
    )
    assert body_f_json.get("key") == "github_app_private_key", (
        f"decrypt-failure key: {body_f_json!r}"
    )
    # Wait for log flush.
    time.sleep(1.0)
    eph_logs = _docker(
        "logs", eph_name, check=False, timeout=15
    )
    eph_blob_partial = (eph_logs.stdout or "") + (eph_logs.stderr or "")
    expected_decrypt_line = (
        "system_settings_decrypt_failed key=github_app_private_key"
    )
    assert expected_decrypt_line in eph_blob_partial, (
        f"missing {expected_decrypt_line!r} in orchestrator logs; "
        f"tail:\n{eph_blob_partial[-2000:]}"
    )

    # ===== Final redaction sweep + log marker assertions ================
    # Capture backend + orchestrator logs ONE more time after every scenario
    # has fired so the marker assertions see the full taxonomy.
    eph_logs_final = _docker(
        "logs", eph_name, check=False, timeout=15
    )
    eph_blob = (eph_logs_final.stdout or "") + (eph_logs_final.stderr or "")
    backend_logs_final = _docker(
        "logs", backend_name, check=False, timeout=15
    )
    backend_blob = (
        (backend_logs_final.stdout or "") + (backend_logs_final.stderr or "")
    )
    swept_blob = "\n".join((eph_blob, backend_blob))

    # Token plaintext must NEVER appear in backend or orchestrator logs (the
    # mock-github container's logs DO contain it by design — only our two
    # services' logs are swept).
    assert _MOCK_FIXED_TOKEN not in swept_blob, (
        "redaction sweep — installation token leaked into "
        "backend/orchestrator logs"
    )
    # Generic GitHub token prefixes — defends against a future regression
    # that emits a real-shaped token via repr/repr-of-dict.
    for prefix in ("gho_", "ghs_", "ghu_", "ghr_", "github_pat_"):
        # The fixed mock token starts with `ghs_` so a literal `ghs_` token
        # in our private fake is the FIXED_TOKEN we already checked above.
        # Allow the prefix to appear ONLY if it's the explicit token-prefix
        # log shape `token_prefix=ghs_...` — we verified above that no full
        # token leaked, so any other occurrence would be a regression.
        if prefix == "ghs_":
            # Only `token_prefix=ghs_...` is permitted (4-char prefix log).
            for line in swept_blob.splitlines():
                if prefix in line and "token_prefix=" not in line:
                    raise AssertionError(
                        f"redaction sweep — `{prefix}` appeared in non-prefix "
                        f"context: {line!r}"
                    )
        else:
            assert prefix not in swept_blob, (
                f"redaction sweep — `{prefix}` appeared in logs"
            )

    # PEM body sentinel must NEVER appear in either log.
    pem_sentinel = _pem_sentinel_value(private_pem)
    assert pem_sentinel not in swept_blob, (
        "redaction sweep — PEM body sentinel leaked into "
        "backend/orchestrator logs"
    )
    # No PEM armor either — that string only ever appears inside a PEM body.
    assert "-----BEGIN" not in swept_blob, (
        "redaction sweep — PEM armor `-----BEGIN` appeared in logs"
    )

    # Required positive markers — full slice observability taxonomy.
    required_markers = (
        "github_install_url_issued",
        "github_install_callback_accepted",
        "installation_token_minted",
        "installation_token_cache_hit",
        "system_settings_decrypt_failed key=github_app_private_key",
    )
    for marker in required_markers:
        assert marker in swept_blob, (
            f"observability taxonomy regression: {marker!r} not seen "
            f"in backend/orchestrator logs"
        )

    # Sanity: mock-github container is still running (proves we exercised
    # the JWT-verify path; if mock-github died early, the orchestrator
    # would have surfaced 502 instead of 200 on the mint scenario).
    inspect = _docker(
        "inspect", "-f", "{{.State.Running}}", mock_name,
        check=False, timeout=10,
    )
    assert (inspect.stdout or "").strip() == "true", (
        f"mock-github died before test end; inspect={inspect.stdout!r}"
    )

    elapsed = time.time() - suite_started
    # Slice budget is ≤180 s on a warm stack (boot dominates). Cold-cache
    # pip-install for mock-github can stretch to ~60s extra.
    assert elapsed < 360.0, (
        f"e2e suite took {elapsed:.1f}s — far over the 180s slice budget"
    )
