"""M006 / S02 / T04 — GitHub OAuth token persistence e2e integration test.

Cross-cutting invariant: GET install callback driven through a mock-GitHub
sidecar ends with a decryptable github_user_oauth_tokens row, no plaintext
anywhere in logs.

Eight test cases:

  (a) Happy path — GET /github/install-callback?code=<code>&state=<state>
      redirects to /teams, a github_app_installations row exists AND a
      github_user_oauth_tokens row exists for the initiating user.

  (b) Token row decrypts correctly — decrypted access_token == mocked value,
      decrypted refresh_token == mocked value.

  (c) github_user_id == 42 (mocked), scope matches mocked scope string.

  (d) access_token_expires_at within ±2s of now + MOCK_EXPIRES_IN.

  (e) refresh_token_expires_at within ±2s of now + MOCK_REFRESH_EXPIRES_IN.

  (f) Reinstall-overwrite path — second callback with same user updates the
      existing row (ON CONFLICT DO UPDATE); updated_at is strictly later.

  (g) Redaction sweep over backend container logs — zero matches for the
      literal mocked access_token or refresh_token strings.

  (h) No plaintext token appears in the DB row (ciphertext is not readable
      as the raw token string).

Stack-bringup discipline (MEM162): probes backend:latest for the
s17_github_user_oauth_tokens alembic revision before booting. Skips with
the canonical `docker compose build backend` hint if absent.

Requires a running compose stack (db, redis) — the orchestrator is booted
ephemerally so it can be pointed at the mock-github-oauth sidecar.

How to run::

    docker compose build backend orchestrator
    docker compose up -d db redis
    cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e \\
        tests/integration/test_github_oauth_token_persistence.py -v

Wall-clock budget ≤ 180 s on a warm compose stack.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

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

# Alembic revision skip-guard (MEM162).
S17_REVISION = "s17_github_user_oauth_tokens"

# Stable Fernet key for the e2e suite (same as conftest.py).
SYSTEM_SETTINGS_ENCRYPTION_KEY_TEST = (
    "kfk5l7mPRFpBV7PzWJxYmO6LRRQAdZ4iGYZRG6xL0fY="
)

# Mocked GitHub OAuth token values.  Suffixed with a run-unique token so the
# redaction sweep at case (g) can prove THIS run's values didn't leak — not a
# coincidental match from a prior run.
_RUN_TOKEN = uuid.uuid4().hex[:12]
MOCK_ACCESS_TOKEN = f"ghu_M006S02T04_access_{_RUN_TOKEN}"
MOCK_REFRESH_TOKEN = f"ghr_M006S02T04_refresh_{_RUN_TOKEN}"
MOCK_EXPIRES_IN = 28800          # 8 hours
MOCK_REFRESH_EXPIRES_IN = 15897600  # ~184 days
MOCK_SCOPE = "repo,read:user"
MOCK_INSTALLATION_ID = 77042
MOCK_GITHUB_USER_ID = 42
MOCK_GITHUB_LOGIN = "test-octocat"
MOCK_CODE = f"ghc_M006S02T04_code_{_RUN_TOKEN}"
MOCK_CLIENT_ID = "Iv1.T04MockClientId"
MOCK_CLIENT_SECRET = f"ghsec_M006S02T04_secret_{_RUN_TOKEN}"

# Fake GitHub app_id (integer) for the orchestrator's JWT minting.
MOCK_GITHUB_APP_ID = 12345

# system_settings keys for the backend
_GITHUB_SETTINGS_KEYS = (
    "github_app_client_id",
    "github_app_client_secret",
    "github_app_id",
    "github_app_private_key",
    "github_app_slug",
)

pytestmark = [pytest.mark.e2e]

_PG_DB = os.environ.get("POSTGRES_DB", "app")


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


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
                    value = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                    if value:
                        return value
    except OSError:
        pass
    return default


def _psql_one(sql: str) -> str:
    out = _docker(
        "exec", "perpetuity-db-1",
        "psql", "-U", "postgres", "-d", _PG_DB, "-A", "-t",
        "-c", sql, check=False,
    )
    return (out.stdout or "").strip()


def _psql_exec(sql: str) -> subprocess.CompletedProcess[str]:
    return _docker(
        "exec", "perpetuity-db-1",
        "psql", "-U", "postgres", "-d", _PG_DB, "-c", sql,
        check=False,
    )


def _backend_logs(container_name: str) -> str:
    r = _docker("logs", container_name, check=False, timeout=15)
    return (r.stdout or "") + (r.stderr or "")


# ---------------------------------------------------------------------------
# Image probes / skip-guards (MEM162)
# ---------------------------------------------------------------------------


def _backend_image_has_s17() -> bool:
    """Probe backend:latest for the s17_github_user_oauth_tokens revision."""
    r = _docker(
        "run", "--rm", "--entrypoint", "ls", BACKEND_IMAGE,
        "/app/backend/app/alembic/versions/",
        check=False, timeout=15,
    )
    return f"{S17_REVISION}.py" in (r.stdout or "")


@pytest.fixture(autouse=True)
def _require_s17_baked() -> None:
    """Skip if backend:latest is missing the s17 alembic revision."""
    if not _backend_image_has_s17():
        pytest.skip(
            f"backend:latest is missing the {S17_REVISION!r} alembic "
            "revision — run `docker compose build backend` so the image "
            "bakes the current /app/backend/app/alembic/versions/ tree."
        )


# ---------------------------------------------------------------------------
# RSA keypair for the orchestrator's App JWT signing
# ---------------------------------------------------------------------------


def _generate_rsa_keypair() -> tuple[str, str]:
    """Generate a fresh 2048-bit RSA keypair. Returns (private_pem, public_pem)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return private_pem, public_pem


# ---------------------------------------------------------------------------
# Mock GitHub OAuth sidecar
# ---------------------------------------------------------------------------


def _boot_mock_github_oauth(
    *,
    mock_client_id: str,
    mock_client_secret: str,
    mock_code: str,
    mock_access_token: str,
    mock_refresh_token: str,
    mock_expires_in: int,
    mock_refresh_expires_in: int,
    mock_scope: str,
    mock_installation_id: int,
    mock_github_user_id: int,
    mock_github_login: str,
) -> tuple[str, str]:
    """Run a python:3.12-slim sibling container with mock_github_oauth.py.

    The mock serves GitHub OAuth token exchange, /user/installations, /user,
    and /app/installations/{id} (for the orchestrator's lookup). All on plain
    HTTP — the backend is configured to call this URL instead of github.com.

    Returns (container_name, base_url_on_docker_network).
    """
    name = f"mock-github-oauth-{uuid.uuid4().hex[:8]}"
    fixture_path = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__), "fixtures", "mock_github_oauth.py"
        )
    )
    assert os.path.exists(fixture_path), (
        f"mock_github_oauth.py missing at {fixture_path}"
    )

    boot_cmd = (
        "set -e; "
        "pip install --quiet --no-cache-dir "
        "'fastapi==0.115.*' 'uvicorn==0.32.*'; "
        "exec uvicorn mock_github_oauth:app --host 0.0.0.0 --port 8090"
    )

    _docker(
        "run", "-d",
        "--name", name,
        "--network", NETWORK,
        "--network-alias", name,
        "-v", f"{fixture_path}:/app/mock_github_oauth.py:ro",
        "-w", "/app",
        "-e", f"MOCK_CLIENT_ID={mock_client_id}",
        "-e", f"MOCK_CLIENT_SECRET={mock_client_secret}",
        "-e", f"MOCK_CODE={mock_code}",
        "-e", f"MOCK_ACCESS_TOKEN={mock_access_token}",
        "-e", f"MOCK_REFRESH_TOKEN={mock_refresh_token}",
        "-e", f"MOCK_EXPIRES_IN={mock_expires_in}",
        "-e", f"MOCK_REFRESH_EXPIRES_IN={mock_refresh_expires_in}",
        "-e", f"MOCK_SCOPE={mock_scope}",
        "-e", f"MOCK_INSTALLATION_ID={mock_installation_id}",
        "-e", f"MOCK_GITHUB_USER_ID={mock_github_user_id}",
        "-e", f"MOCK_GITHUB_LOGIN={mock_github_login}",
        "--entrypoint", "bash",
        "python:3.12-slim",
        "-c", boot_cmd,
        timeout=60,
    )

    base_url = f"http://{name}:8090"

    deadline = time.time() + 90.0
    last_err = ""
    probe_script = (
        "import sys, urllib.request\n"
        "try:\n"
        "    urllib.request.urlopen("
        "'http://127.0.0.1:8090/healthz', timeout=2).read()\n"
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
        f"mock-github-oauth {name!r} never became healthy; "
        f"last_probe={last_err!r}\n"
        f"docker logs:\n{os.linesep.join(logs.splitlines()[-60:])}"
    )


# ---------------------------------------------------------------------------
# Ephemeral orchestrator
# ---------------------------------------------------------------------------


def _ensure_host_workspaces_shared() -> None:
    """Ensure /var/lib/perpetuity/workspaces is bind-shared (MEM136)."""
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
    """Stop the compose orchestrator and launch an ephemeral one pointing at
    the mock-github-oauth sidecar. Returns the ephemeral container name."""
    name = f"orch-t04-mock-{uuid.uuid4().hex[:8]}"
    _ensure_host_workspaces_shared()

    _compose("rm", "-sf", "orchestrator", check=False, timeout=60)

    args = [
        "run", "-d",
        "--name", name,
        "--network", NETWORK,
        "--network-alias", ORCH_DNS_ALIAS,
        "--privileged",
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
        "-e", f"DATABASE_URL=postgresql://postgres:{pg_password}@db:5432/{_PG_DB}",
        "-e", f"GITHUB_API_BASE_URL={mock_github_url}",
        ORCH_IMAGE,
    ]
    _docker(*args)
    return name


def _wait_orch_healthy(name: str, *, timeout_s: float = 60.0) -> None:
    deadline = time.time() + timeout_s
    last_err = ""
    probe = (
        "import sys, urllib.request\n"
        "try:\n"
        "    body = urllib.request.urlopen("
        "'http://127.0.0.1:8001/v1/health', timeout=2).read().decode()\n"
        "    sys.exit(0)\n"
        "except Exception as e:\n"
        "    print(repr(e)); sys.exit(3)\n"
    )
    while time.time() < deadline:
        r = _docker("exec", name, "python3", "-c", probe, check=False, timeout=5)
        if r.returncode == 0:
            return
        last_err = (r.stderr or "")[:200]
        time.sleep(0.5)
    logs = _docker("logs", name, check=False, timeout=10).stdout or ""
    raise AssertionError(
        f"ephemeral orchestrator {name!r} never healthy; "
        f"last_err={last_err!r}\nlogs:\n{logs[-2000:]}"
    )


# ---------------------------------------------------------------------------
# Sibling backend
# ---------------------------------------------------------------------------


def _boot_sibling_backend(
    *,
    api_key: str,
    redis_password: str,
    pg_password: str,
    secret_key: str,
    github_oauth_base_url: str,
    github_api_base_url: str,
) -> tuple[str, str]:
    """Boot a sibling backend:latest container with mock GitHub URLs.

    Returns (container_name, base_url).
    """
    name = f"perpetuity-backend-e2e-t04-{uuid.uuid4().hex[:8]}"
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
        "-e", f"POSTGRES_DB={_PG_DB}",
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
        # Override GitHub URLs to point at the mock sidecar.
        "-e", f"GITHUB_OAUTH_BASE_URL={github_oauth_base_url}",
        "-e", f"GITHUB_API_BASE_URL={github_api_base_url}",
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
                return name, base_url
        except (httpx.HTTPError, OSError) as exc:
            last_err = exc
        time.sleep(0.5)

    logs = _docker("logs", name, check=False).stdout or ""
    _docker("rm", "-f", name, check=False)
    raise AssertionError(
        f"backend container {name!r} never became healthy at {health_url}; "
        f"last_err={last_err!r}\nlogs:\n{logs[-4000:]}"
    )


# ---------------------------------------------------------------------------
# Auth + team helpers
# ---------------------------------------------------------------------------


def _login_only(base_url: str, *, email: str, password: str) -> httpx.Cookies:
    cookies = httpx.Cookies()
    with httpx.Client(base_url=base_url, timeout=30.0) as c:
        r = c.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, f"login: {r.status_code} {r.text}"
        for cookie in c.cookies.jar:
            cookies.set(cookie.name, cookie.value)
    return cookies


def _user_id_from_db(email: str) -> str:
    val = _psql_one(f"SELECT id FROM \"user\" WHERE email = '{email}'")
    assert val, f"no user row for {email!r}"
    return val


def _create_team(base_url: str, cookies: httpx.Cookies, name: str) -> str:
    with httpx.Client(base_url=base_url, timeout=15.0, cookies=cookies) as c:
        r = c.post("/api/v1/teams/", json={"name": name})
    assert r.status_code == 200, f"create team: {r.status_code} {r.text}"
    return r.json()["id"]


def _seed_github_settings(
    base_url: str,
    admin_cookies: httpx.Cookies,
    *,
    private_key_pem: str,
) -> None:
    """Seed github_app_* settings the backend and orchestrator need."""
    with httpx.Client(base_url=base_url, timeout=30.0, cookies=admin_cookies) as c:
        r = c.put(
            "/api/v1/admin/settings/github_app_id",
            json={"value": MOCK_GITHUB_APP_ID},
        )
        assert r.status_code == 200, f"PUT github_app_id: {r.status_code} {r.text}"
        r = c.put(
            "/api/v1/admin/settings/github_app_client_id",
            json={"value": MOCK_CLIENT_ID},
        )
        assert r.status_code == 200, (
            f"PUT github_app_client_id: {r.status_code} {r.text}"
        )
        r = c.put(
            "/api/v1/admin/settings/github_app_client_secret",
            json={"value": MOCK_CLIENT_SECRET},
        )
        assert r.status_code == 200, (
            f"PUT github_app_client_secret: {r.status_code} {r.text}"
        )
        r = c.put(
            "/api/v1/admin/settings/github_app_private_key",
            json={"value": private_key_pem},
        )
        assert r.status_code == 200, (
            f"PUT github_app_private_key: {r.status_code} {r.text}"
        )
        r = c.put(
            "/api/v1/admin/settings/github_app_slug",
            json={"value": "test-app"},
        )
        assert r.status_code == 200, f"PUT github_app_slug: {r.status_code} {r.text}"


def _mint_state_jwt(
    *,
    team_id: str,
    user_id: str,
    secret_key: str,
) -> str:
    """Mint a valid install-state JWT matching the backend's _mint_install_state logic."""
    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=600)
    import secrets as _secrets
    jti = _secrets.token_urlsafe(16)
    payload = {
        "team_id": team_id,
        "user_id": user_id,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "iss": "perpetuity-install",
        "aud": "github-install",
    }
    return jwt.encode(payload, secret_key, algorithm="HS256")


def _wipe_test_rows(user_id: str, team_id: str) -> None:
    """Clean up test rows from DB to allow re-runs."""
    _psql_exec(
        f"DELETE FROM github_user_oauth_tokens WHERE user_id = '{user_id}'"
    )
    _psql_exec(
        "DELETE FROM github_app_installations "
        f"WHERE installation_id = {MOCK_INSTALLATION_ID}"
    )
    _psql_exec(f"DELETE FROM team_member WHERE team_id = '{team_id}'")
    _psql_exec(f"DELETE FROM team WHERE id = '{team_id}'")
    keys_csv = ",".join(f"'{k}'" for k in _GITHUB_SETTINGS_KEYS)
    _psql_exec(f"DELETE FROM system_settings WHERE key IN ({keys_csv})")


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


def test_github_oauth_token_persistence_e2e(  # noqa: PLR0915
    compose_stack_up: None,  # noqa: ARG001
) -> None:
    """T04 demo: install callback persists token row; decrypt+redaction sweep pass."""
    suite_started = time.time()

    redis_password = (
        os.environ.get("REDIS_PASSWORD")
        or _read_dotenv_value("REDIS_PASSWORD", "changethis")
    )
    pg_password = (
        os.environ.get("POSTGRES_PASSWORD")
        or _read_dotenv_value("POSTGRES_PASSWORD", "changethis")
    )
    secret_key = _read_dotenv_value("SECRET_KEY", "changethis")
    api_key = _read_dotenv_value("ORCHESTRATOR_API_KEY", "changethis")

    private_key_pem, _public_key_pem = _generate_rsa_keypair()

    # Boot the mock-github-oauth sidecar.
    mock_name, mock_base_url = _boot_mock_github_oauth(
        mock_client_id=MOCK_CLIENT_ID,
        mock_client_secret=MOCK_CLIENT_SECRET,
        mock_code=MOCK_CODE,
        mock_access_token=MOCK_ACCESS_TOKEN,
        mock_refresh_token=MOCK_REFRESH_TOKEN,
        mock_expires_in=MOCK_EXPIRES_IN,
        mock_refresh_expires_in=MOCK_REFRESH_EXPIRES_IN,
        mock_scope=MOCK_SCOPE,
        mock_installation_id=MOCK_INSTALLATION_ID,
        mock_github_user_id=MOCK_GITHUB_USER_ID,
        mock_github_login=MOCK_GITHUB_LOGIN,
    )

    # Boot ephemeral orchestrator pointed at mock (replaces compose orchestrator).
    orch_name = _boot_orch_with_mock(
        mock_github_url=mock_base_url,
        redis_password=redis_password,
        pg_password=pg_password,
        api_key=api_key,
    )
    try:
        _wait_orch_healthy(orch_name, timeout_s=60.0)
    except AssertionError:
        _docker("rm", "-f", mock_name, check=False)
        _docker("rm", "-f", orch_name, check=False)
        _compose("up", "-d", "orchestrator", check=False, timeout=120)
        raise

    # Boot the sibling backend with mock GitHub URLs.
    try:
        backend_name, backend_url = _boot_sibling_backend(
            api_key=api_key,
            redis_password=redis_password,
            pg_password=pg_password,
            secret_key=secret_key,
            github_oauth_base_url=mock_base_url,
            github_api_base_url=mock_base_url,
        )
    except AssertionError:
        _docker("rm", "-f", mock_name, check=False)
        _docker("rm", "-f", orch_name, check=False)
        _compose("up", "-d", "orchestrator", check=False, timeout=120)
        raise

    admin_email = "admin@example.com"
    admin_password = "changethis"
    admin_cookies = _login_only(
        backend_url, email=admin_email, password=admin_password
    )
    admin_user_id = _user_id_from_db(admin_email)
    team_id = _create_team(backend_url, admin_cookies, f"t04-test-{_RUN_TOKEN[:6]}")

    try:
        # Seed the GitHub App settings (client_id, client_secret, private_key, app_id).
        _seed_github_settings(
            backend_url,
            admin_cookies,
            private_key_pem=private_key_pem,
        )

        # =========================================================================
        # Case (a) + (b) + (c) + (d) + (e) — happy path: GET callback persists row
        # =========================================================================
        state_jwt = _mint_state_jwt(
            team_id=team_id,
            user_id=admin_user_id,
            secret_key=secret_key,
        )

        callback_time = datetime.now(timezone.utc)
        with httpx.Client(
            base_url=backend_url, timeout=30.0, follow_redirects=False
        ) as c:
            r = c.get(
                "/api/v1/github/install-callback",
                params={"code": MOCK_CODE, "state": state_jwt},
            )

        # (a) Redirects to /teams — no error param.
        assert r.status_code in (302, 303), (
            f"callback expected redirect; got {r.status_code} {r.text[:300]}"
        )
        location = r.headers.get("location", "")
        assert "github_install_error" not in location, (
            f"callback redirected with error: {location!r}"
        )
        assert "/teams" in location, (
            f"expected redirect to /teams; got {location!r}"
        )

        # (a) github_app_installations row exists.
        install_row_count = _psql_one(
            f"SELECT count(*) FROM github_app_installations "
            f"WHERE installation_id = {MOCK_INSTALLATION_ID}"
        )
        assert install_row_count == "1", (
            f"expected 1 github_app_installations row; got {install_row_count!r}"
        )

        # (a) github_user_oauth_tokens row exists for admin_user_id.
        token_row_count = _psql_one(
            f"SELECT count(*) FROM github_user_oauth_tokens "
            f"WHERE user_id = '{admin_user_id}'"
        )
        assert token_row_count == "1", (
            f"expected 1 github_user_oauth_tokens row; got {token_row_count!r}"
        )

        # (c) github_user_id == MOCK_GITHUB_USER_ID.
        db_github_user_id = _psql_one(
            f"SELECT github_user_id FROM github_user_oauth_tokens "
            f"WHERE user_id = '{admin_user_id}'"
        )
        assert db_github_user_id == str(MOCK_GITHUB_USER_ID), (
            f"expected github_user_id={MOCK_GITHUB_USER_ID}; got {db_github_user_id!r}"
        )

        # (c) scope matches.
        db_scope = _psql_one(
            f"SELECT scope FROM github_user_oauth_tokens "
            f"WHERE user_id = '{admin_user_id}'"
        )
        assert db_scope == MOCK_SCOPE, (
            f"expected scope={MOCK_SCOPE!r}; got {db_scope!r}"
        )

        # (d) access_token_expires_at within ±2s of callback_time + MOCK_EXPIRES_IN.
        expected_access_exp = callback_time + timedelta(seconds=MOCK_EXPIRES_IN)
        db_access_exp_str = _psql_one(
            "SELECT access_token_expires_at AT TIME ZONE 'UTC' "
            f"FROM github_user_oauth_tokens WHERE user_id = '{admin_user_id}'"
        )
        assert db_access_exp_str, "access_token_expires_at is NULL or missing"
        db_access_exp = datetime.fromisoformat(
            db_access_exp_str.replace(" ", "T")
        ).replace(tzinfo=timezone.utc)
        delta_access = abs((db_access_exp - expected_access_exp).total_seconds())
        assert delta_access <= 2.0, (
            f"access_token_expires_at off by {delta_access:.1f}s; "
            f"expected ~{expected_access_exp.isoformat()}, "
            f"got {db_access_exp.isoformat()}"
        )

        # (e) refresh_token_expires_at within ±2s of callback_time + MOCK_REFRESH_EXPIRES_IN.
        expected_refresh_exp = callback_time + timedelta(seconds=MOCK_REFRESH_EXPIRES_IN)
        db_refresh_exp_str = _psql_one(
            "SELECT refresh_token_expires_at AT TIME ZONE 'UTC' "
            f"FROM github_user_oauth_tokens WHERE user_id = '{admin_user_id}'"
        )
        assert db_refresh_exp_str, "refresh_token_expires_at is NULL or missing"
        db_refresh_exp = datetime.fromisoformat(
            db_refresh_exp_str.replace(" ", "T")
        ).replace(tzinfo=timezone.utc)
        delta_refresh = abs((db_refresh_exp - expected_refresh_exp).total_seconds())
        assert delta_refresh <= 2.0, (
            f"refresh_token_expires_at off by {delta_refresh:.1f}s; "
            f"expected ~{expected_refresh_exp.isoformat()}, "
            f"got {db_refresh_exp.isoformat()}"
        )

        # (b) Decrypt and verify the token values round-trip correctly.
        # We read the raw ciphertext from DB and decrypt with the same Fernet key
        # the backend used. The test Fernet key is SYSTEM_SETTINGS_ENCRYPTION_KEY_TEST.
        from cryptography.fernet import Fernet

        fernet = Fernet(SYSTEM_SETTINGS_ENCRYPTION_KEY_TEST.encode())

        # Read access_token_encrypted as hex from psql.
        access_enc_hex = _psql_one(
            "SELECT encode(access_token_encrypted, 'hex') "
            f"FROM github_user_oauth_tokens WHERE user_id = '{admin_user_id}'"
        )
        assert access_enc_hex, "access_token_encrypted is NULL or missing"
        access_ciphertext = bytes.fromhex(access_enc_hex)
        decrypted_access = fernet.decrypt(access_ciphertext).decode()
        assert decrypted_access == MOCK_ACCESS_TOKEN, (
            "decrypted access_token does not match mocked value"
        )

        refresh_enc_hex = _psql_one(
            "SELECT encode(refresh_token_encrypted, 'hex') "
            f"FROM github_user_oauth_tokens WHERE user_id = '{admin_user_id}'"
        )
        assert refresh_enc_hex, "refresh_token_encrypted is NULL or missing"
        refresh_ciphertext = bytes.fromhex(refresh_enc_hex)
        decrypted_refresh = fernet.decrypt(refresh_ciphertext).decode()
        assert decrypted_refresh == MOCK_REFRESH_TOKEN, (
            "decrypted refresh_token does not match mocked value"
        )

        # (h) The DB stores ciphertext, not plaintext.
        assert access_enc_hex != MOCK_ACCESS_TOKEN.encode().hex(), (
            "access_token_encrypted is not actually encrypted"
        )
        assert refresh_enc_hex != MOCK_REFRESH_TOKEN.encode().hex(), (
            "refresh_token_encrypted is not actually encrypted"
        )

        # =========================================================================
        # Case (f) — reinstall-overwrite: second callback updates the same user row
        # =========================================================================
        first_updated_at = _psql_one(
            "SELECT updated_at AT TIME ZONE 'UTC' FROM github_user_oauth_tokens "
            f"WHERE user_id = '{admin_user_id}'"
        )

        # Sleep 1s so updated_at changes measurably.
        time.sleep(1.1)

        state_jwt2 = _mint_state_jwt(
            team_id=team_id,
            user_id=admin_user_id,
            secret_key=secret_key,
        )
        with httpx.Client(
            base_url=backend_url, timeout=30.0, follow_redirects=False
        ) as c:
            r2 = c.get(
                "/api/v1/github/install-callback",
                params={"code": MOCK_CODE, "state": state_jwt2},
            )

        assert r2.status_code in (302, 303), (
            f"reinstall callback expected redirect; got {r2.status_code} {r2.text[:300]}"
        )
        location2 = r2.headers.get("location", "")
        assert "github_install_error" not in location2, (
            f"reinstall redirected with error: {location2!r}"
        )

        # Still exactly one row (upsert, not insert).
        token_count2 = _psql_one(
            f"SELECT count(*) FROM github_user_oauth_tokens "
            f"WHERE user_id = '{admin_user_id}'"
        )
        assert token_count2 == "1", (
            f"expected still 1 token row after reinstall; got {token_count2!r}"
        )

        second_updated_at = _psql_one(
            "SELECT updated_at AT TIME ZONE 'UTC' FROM github_user_oauth_tokens "
            f"WHERE user_id = '{admin_user_id}'"
        )
        assert second_updated_at > first_updated_at, (
            "reinstall did not bump updated_at; "
            f"first={first_updated_at!r} second={second_updated_at!r}"
        )

        # =========================================================================
        # Case (g) — redaction sweep: backend logs must not contain plaintext tokens
        # =========================================================================
        time.sleep(0.5)
        backend_log = _backend_logs(backend_name)

        assert MOCK_ACCESS_TOKEN not in backend_log, (
            f"redaction sweep: access token plaintext leaked into backend logs"
        )
        assert MOCK_REFRESH_TOKEN not in backend_log, (
            f"redaction sweep: refresh token plaintext leaked into backend logs"
        )
        assert MOCK_CLIENT_SECRET not in backend_log, (
            f"redaction sweep: client_secret leaked into backend logs"
        )

    finally:
        _wipe_test_rows(admin_user_id, team_id)
        _docker("rm", "-f", backend_name, check=False, timeout=30)
        _docker("rm", "-f", mock_name, check=False, timeout=30)
        _docker("rm", "-f", orch_name, check=False, timeout=30)
        _compose("up", "-d", "orchestrator", check=False, timeout=180)

    elapsed = time.time() - suite_started
    assert elapsed < 180.0, (
        f"T04 e2e took {elapsed:.1f}s — over 180s budget"
    )
