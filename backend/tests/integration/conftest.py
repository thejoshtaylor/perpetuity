"""Shared fixtures for `backend/tests/integration/` (M002/T06 e2e suite).

This conftest is **isolated** from the unit suite at `backend/tests/conftest.py`:
the unit conftest uses a session-scoped autouse `db` fixture that connects
to `localhost:55432` (the host-side mapping per MEM021/MEM114) and also
holds an implicit AccessShareLock on the `user` table (MEM016). Neither is
appropriate for the e2e suite: the e2e tests talk to a separate backend
process over HTTP, the local Postgres may not even be reachable, and we
never want to take row-locks while another process is mutating the
schema. We override `db` and `client` with no-op fixtures here so the
unit conftest's autouse never runs for tests in this directory.

The fixtures here:
  - `_e2e_env_check` — autouse skip guard. Skips the whole module when
    `SKIP_INTEGRATION=1`, when docker is unreachable, when the
    `perpetuity_default` network or its `redis`/`db` services are missing,
    or when the required images are not built.
  - `compose_stack_up` — ensures `docker compose up -d db redis orchestrator`
    has been run. Idempotent (no-op if already healthy).
  - `backend_url` — boots a fresh sibling `backend` container on the compose
    network with a published host port and yields the HTTP base URL.
    Teardown reaps any workspace containers spawned during the test.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator

import httpx
import pytest

NETWORK = "perpetuity_default"
ORCH_IMAGE = "orchestrator:latest"
WORKSPACE_IMAGE = "perpetuity/workspace:test"
BACKEND_IMAGE = "backend:latest"

# M004/S01/T01: stable Fernet key for the e2e suite. Same value across
# every sibling backend boot — sensitive system_settings rows written
# by one test must remain decryptable by the next, and rotating the key
# without re-encrypting every row breaks every read. This is a
# test-only secret (not derived from any production key); the .env's
# SYSTEM_SETTINGS_ENCRYPTION_KEY for local dev is independent.
SYSTEM_SETTINGS_ENCRYPTION_KEY_TEST = (
    "kfk5l7mPRFpBV7PzWJxYmO6LRRQAdZ4iGYZRG6xL0fY="
)

# Repo root resolved from this file: backend/tests/integration/conftest.py
REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)


# ----- low-level helpers --------------------------------------------------


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


def _docker_socket_reachable() -> bool:
    if shutil.which("docker") is None:
        return False
    if os.environ.get("DOCKER_HOST"):
        # Trust an explicit DOCKER_HOST — `docker info` will fail loudly if
        # it's wrong and the test will skip on the next probe.
        return True
    return os.path.exists("/var/run/docker.sock")


def _docker_info_ok() -> bool:
    try:
        r = _docker("info", check=False, timeout=10)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _network_exists(name: str) -> bool:
    try:
        r = _docker("network", "inspect", name, check=False, timeout=10)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _image_present(image: str) -> bool:
    try:
        r = _docker("image", "inspect", image, check=False, timeout=10)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _read_dotenv_value(key: str, default: str) -> str:
    """Pull a single value out of `<repo>/.env` without importing dotenv.

    Per MEM111 the password defaults differ between the example and live
    `.env`, so we read the live file first and fall back to the documented
    placeholder.
    """
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


def _service_healthy(service: str) -> bool:
    try:
        r = _compose(
            "ps", "--format", "{{.Service}}\t{{.Health}}", check=False, timeout=15
        )
        if r.returncode != 0:
            return False
        for line in r.stdout.splitlines():
            parts = line.strip().split("\t")
            if len(parts) == 2 and parts[0] == service and parts[1] == "healthy":
                return True
        return False
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _wait_for_service_healthy(
    service: str, *, timeout_s: float = 60.0
) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _service_healthy(service):
            return True
        time.sleep(1.0)
    return False


# ----- override unit-suite autouse fixtures -------------------------------
#
# The parent `backend/tests/conftest.py` defines a session-scoped autouse
# `db` fixture that opens a SQLAlchemy session against localhost:55432 (the
# host-side mapping). pytest's name-based override resolution lets us
# replace it with a no-op for everything under `tests/integration/`.


@pytest.fixture(scope="session", autouse=True)
def db() -> Iterator[None]:  # noqa: PT004
    """No-op replacement for the unit-suite autouse `db` fixture.

    The integration tests never touch the SQLAlchemy engine directly —
    they exercise the backend via HTTP. We must override the parent
    fixture before pytest tries to instantiate it (which would fail on
    the localhost:55432 connection when only the in-network db is up).
    """
    yield


@pytest.fixture(scope="module")
def client() -> Iterator[None]:  # noqa: PT004
    """No-op replacement for the unit-suite TestClient `client` fixture.

    Integration tests use httpx.Client against `backend_url` instead.
    Defined here so any accidental reference to `client` from imported
    helpers fails loudly with a clear pytest error, not a TestClient
    talking to an in-process app.
    """
    pytest.fail(
        "the integration suite uses httpx.Client(base_url=backend_url), "
        "not the in-process TestClient `client` fixture"
    )


# ----- env-check skip fixture --------------------------------------------


@pytest.fixture(autouse=True)
def _e2e_env_check() -> None:
    """Skip the whole e2e module when prerequisites are missing.

    These checks are intentionally cheap so unit-only runs never pay the
    cost of importing the test module's other heavy fixtures.
    """
    if os.environ.get("SKIP_INTEGRATION") == "1":
        pytest.skip("SKIP_INTEGRATION=1 set")
    if not _docker_socket_reachable():
        pytest.skip(
            "docker socket not reachable — set DOCKER_HOST or start Docker Desktop"
        )
    if not _docker_info_ok():
        pytest.skip("docker daemon not responding to `docker info`")
    if not _image_present(ORCH_IMAGE):
        pytest.skip(
            f"image {ORCH_IMAGE!r} missing — run `docker compose build orchestrator`"
        )
    if not _image_present(WORKSPACE_IMAGE):
        pytest.skip(
            f"image {WORKSPACE_IMAGE!r} missing — run `docker build -f "
            f"orchestrator/tests/fixtures/Dockerfile.test -t {WORKSPACE_IMAGE} "
            f"orchestrator/workspace-image/`"
        )


# ----- compose stack -----------------------------------------------------


@pytest.fixture(scope="session")
def compose_stack_up() -> Iterator[None]:
    """Ensure `db`, `redis`, and `orchestrator` are up and healthy.

    Idempotent: if the user already ran `docker compose up -d ...` we just
    poll for health. We do NOT bring `backend` up via compose because the
    compose `backend` service has no published host port; the e2e test
    spawns its own ephemeral backend container with a host port.
    """
    if not _docker_socket_reachable() or not _docker_info_ok():
        # The autouse fixture would have skipped already, but defending here
        # makes the fixture safe when invoked outside the marker selection.
        pytest.skip("docker not available")

    needs_up = []
    for svc in ("db", "redis", "orchestrator"):
        if not _service_healthy(svc):
            needs_up.append(svc)
    if needs_up:
        _compose("up", "-d", *needs_up, check=True, timeout=300)

    for svc in ("db", "redis", "orchestrator"):
        if not _wait_for_service_healthy(svc, timeout_s=90.0):
            logs = _compose("logs", "--tail=80", svc, check=False).stdout
            raise AssertionError(
                f"compose service {svc!r} did not become healthy; recent logs:\n{logs}"
            )

    if not _network_exists(NETWORK):
        raise AssertionError(
            f"compose network {NETWORK!r} not present after `docker compose up`"
        )
    yield


# ----- ephemeral sibling backend container -------------------------------


@pytest.fixture
def backend_url(
    compose_stack_up: None,  # noqa: ARG001
) -> Iterator[str]:
    """Boot a fresh `backend:latest` container on `perpetuity_default`.

    Uses the compose `db`, `redis`, `orchestrator` services internally
    (DNS-resolvable on the network) and publishes the backend's port 8000
    on a random host port so httpx can reach it.

    Per MEM114 the in-network db listens on 5432, not the `.env`'s 55432
    (which is a host-side leftover). Override POSTGRES_PORT explicitly.

    The fixture runs the prestart script (alembic + initial_data) inside
    the container before launching uvicorn — same shape as compose's
    prestart service.
    """
    if not _image_present(BACKEND_IMAGE):
        pytest.skip(
            f"image {BACKEND_IMAGE!r} missing — run `docker compose build backend`"
        )

    redis_password = (
        os.environ.get("REDIS_PASSWORD") or _read_dotenv_value("REDIS_PASSWORD", "changethis")
    )
    pg_password = (
        os.environ.get("POSTGRES_PASSWORD")
        or _read_dotenv_value("POSTGRES_PASSWORD", "changethis")
    )
    # MEM348: the shared `app` DB on `perpetuity-db-1` was contaminated by
    # an unrelated CRM schema (alembic version `z2y_*` / `z3b_*`). When that
    # happens the operator can stand up a clean `perpetuity_app` database
    # and route the e2e backend at it via POSTGRES_DB. Default stays at
    # `app` so existing happy-path runs are unaffected.
    pg_db = os.environ.get("POSTGRES_DB", "app")
    secret_key = _read_dotenv_value("SECRET_KEY", "changethis")
    api_key = _read_dotenv_value("ORCHESTRATOR_API_KEY", "changethis")

    name = f"perpetuity-backend-e2e-{uuid.uuid4().hex[:8]}"
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
        "-e", f"POSTGRES_DB={pg_db}",
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

    # Two-phase boot: prestart (migrations + seed) then fastapi run. Mirrors
    # the compose `prestart` + `backend` split inside one container so the
    # test never depends on the compose `backend` service being running.
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
            f"backend container {name!r} never became healthy at {health_url}; "
            f"last_err={last_err!r}\nlogs:\n{logs[-4000:]}"
        )

    try:
        yield base_url
    finally:
        # Reap any workspace containers spawned by the orchestrator during
        # this test (they outlive a backend restart but not the test run).
        ws = _docker(
            "ps", "-aq", "--filter", "label=perpetuity.managed=true",
            check=False, timeout=15,
        )
        if ws.stdout.strip():
            _docker(
                "rm", "-f", *ws.stdout.split(),
                check=False, timeout=120,
            )
        _docker("rm", "-f", name, check=False, timeout=30)


# ----- ephemeral orchestrator pointed at the e2e DB ----------------------
#
# The compose `orchestrator` service inherits POSTGRES_DB from .env, which
# defaults to `app`. When the e2e suite is run with POSTGRES_DB=perpetuity_app
# (the canonical M005 e2e shape — the bare `app` DB has been contaminated by
# an unrelated project per MEM348/MEM361), the compose orchestrator is on
# the wrong DB and POST /v1/sessions surfaces 503 with
# `pg_unreachable op=get_volume reason=UndefinedTableError`.
#
# Mirror the M002/S05 rotation pattern: stop the compose orchestrator and
# boot an ephemeral one carrying DATABASE_URL pointed at the test DB,
# attached to the compose network with the `orchestrator` DNS alias so
# the sibling backend / celery worker resolve it transparently. The
# fixture is a no-op when POSTGRES_DB is unset / set to `app`.
_ORCH_DNS_ALIAS = "orchestrator"


@pytest.fixture(scope="session")
def orchestrator_on_e2e_db(
    compose_stack_up: None,  # noqa: ARG001
) -> Iterator[None]:
    """Ensure the orchestrator on the network points at the e2e DB.

    Idempotent across the session — the first test that pulls this
    fixture swaps the compose orchestrator for an ephemeral one (or
    leaves the compose orchestrator alone if the DB matches).
    """
    pg_db = os.environ.get("POSTGRES_DB", "app")
    if pg_db == "app":
        # Compose orchestrator already on the right DB.
        yield
        return

    if not _docker_socket_reachable() or not _docker_info_ok():
        pytest.skip("docker not available")

    pg_password = (
        os.environ.get("POSTGRES_PASSWORD")
        or _read_dotenv_value("POSTGRES_PASSWORD", "changethis")
    )
    redis_password = (
        os.environ.get("REDIS_PASSWORD")
        or _read_dotenv_value("REDIS_PASSWORD", "changethis")
    )
    api_key = _read_dotenv_value("ORCHESTRATOR_API_KEY", "changethis")

    name = f"perpetuity-orch-e2e-{uuid.uuid4().hex[:8]}"

    # Stop + remove the compose orchestrator so its DNS alias frees up.
    subprocess.run(
        ["docker", "compose", "rm", "-sf", "orchestrator"],
        check=False, capture_output=True, text=True, cwd=REPO_ROOT,
        timeout=60,
    )

    args = [
        "run", "-d",
        "--name", name,
        "--network", NETWORK,
        "--network-alias", _ORCH_DNS_ALIAS,
        "--privileged",  # MEM136 — losetup inside the container
        "-v", "/var/run/docker.sock:/var/run/docker.sock",
        "--mount",
        "type=bind,"
        "source=/var/lib/perpetuity/workspaces,"
        "target=/var/lib/perpetuity/workspaces,bind-propagation=rshared",
        "-v", "/var/lib/perpetuity/vols:/var/lib/perpetuity/vols",
        "-e", f"WORKSPACE_IMAGE={WORKSPACE_IMAGE}",
        "-e", f"ORCHESTRATOR_API_KEY={api_key}",
        "-e", "ORCHESTRATOR_API_KEY_PREVIOUS=",
        "-e", f"SYSTEM_SETTINGS_ENCRYPTION_KEY={SYSTEM_SETTINGS_ENCRYPTION_KEY_TEST}",
        "-e", "REDIS_HOST=redis",
        "-e", f"REDIS_PASSWORD={redis_password}",
        "-e",
        f"DATABASE_URL=postgresql://postgres:{pg_password}@db:5432/{pg_db}",
        ORCH_IMAGE,
    ]
    _docker(*args, timeout=60)

    # Probe /v1/health from inside the ephemeral orchestrator (the
    # M002/S05 pattern — avoids cross-network probing complexity).
    deadline = time.time() + 30.0
    healthy = False
    while time.time() < deadline:
        probe = subprocess.run(
            ["docker", "exec", name, "python3", "-c",
             "import urllib.request; "
             "import sys; "
             "r=urllib.request.urlopen('http://127.0.0.1:8001/v1/health',timeout=2); "
             "sys.exit(0 if r.status==200 else 1)"],
            check=False, capture_output=True, text=True, timeout=10,
        )
        if probe.returncode == 0:
            healthy = True
            break
        time.sleep(0.5)
    if not healthy:
        logs = _docker("logs", name, check=False).stdout or ""
        _docker("rm", "-f", name, check=False)
        raise AssertionError(
            f"ephemeral orchestrator {name!r} did not become healthy in 30 s; "
            f"logs:\n{logs[-4000:]}"
        )

    try:
        yield
    finally:
        _docker("rm", "-f", name, check=False, timeout=30)


# ----- ephemeral sibling celery-worker container -------------------------
#
# M005/S02/T06: the AI-button e2e needs a Celery worker process that runs
# `app.workflows.tasks.run_workflow`. We boot a sibling container off the
# same `backend:latest` image that the `backend_url` fixture uses, on the
# same compose network, with the same Postgres + Redis + orchestrator env.
# Crucially we pin SYSTEM_SETTINGS_ENCRYPTION_KEY to the test Fernet key
# (above) so the worker's `get_team_secret` call decrypts what the API
# encrypted from inside the sibling backend.


@pytest.fixture
def celery_worker_url(
    backend_url: str,  # noqa: ARG001 — depend on backend so it boots first
) -> Iterator[str]:
    """Boot a fresh `backend:latest` container running celery worker.

    The fixture depends on `backend_url` so the prestart migrations have
    already run by the time the worker starts (the worker imports
    `app.workflows.tasks` which references the s10/s11/s12 tables — a
    fresh DB without those would crash at first task import).

    Yields the container name (callers usually only need its log stream
    for the redaction sweep). Teardown reaps any workspace containers
    spawned by the worker via the orchestrator.
    """
    if not _image_present(BACKEND_IMAGE):
        pytest.skip(
            f"image {BACKEND_IMAGE!r} missing — run `docker compose build backend`"
        )

    redis_password = (
        os.environ.get("REDIS_PASSWORD")
        or _read_dotenv_value("REDIS_PASSWORD", "changethis")
    )
    pg_password = (
        os.environ.get("POSTGRES_PASSWORD")
        or _read_dotenv_value("POSTGRES_PASSWORD", "changethis")
    )
    pg_db = os.environ.get("POSTGRES_DB", "app")
    secret_key = _read_dotenv_value("SECRET_KEY", "changethis")
    api_key = _read_dotenv_value("ORCHESTRATOR_API_KEY", "changethis")

    name = f"perpetuity-celery-e2e-{uuid.uuid4().hex[:8]}"

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
        "-e", f"POSTGRES_DB={pg_db}",
        "-e", "POSTGRES_USER=postgres",
        "-e", f"POSTGRES_PASSWORD={pg_password}",
        "-e", "REDIS_HOST=redis",
        "-e", f"REDIS_PASSWORD={redis_password}",
        "-e", "ORCHESTRATOR_BASE_URL=http://orchestrator:8001",
        "-e", f"ORCHESTRATOR_API_KEY={api_key}",
        "-e", "ORCHESTRATOR_API_KEY_PREVIOUS=",
        "-e", f"SYSTEM_SETTINGS_ENCRYPTION_KEY={SYSTEM_SETTINGS_ENCRYPTION_KEY_TEST}",
        "-e", "EMAILS_FROM_EMAIL=noreply@example.com",
        "-e", "SMTP_HOST=", "-e", "SMTP_USER=", "-e", "SMTP_PASSWORD=",
        "-e", "SENTRY_DSN=",
    ]

    cmd = (
        "celery -A app.workflows.tasks worker "
        "--loglevel=info --concurrency=1 -n e2e-worker@%h"
    )
    _docker(
        "run", "-d",
        "--name", name,
        "--network", NETWORK,
        *env_args,
        "--entrypoint", "bash",
        BACKEND_IMAGE,
        "-c", cmd,
        timeout=60,
    )

    # Wait until the worker has emitted the canonical "ready." line.
    # Celery prints `celery@<hostname> ready.` at the end of mingle/gossip
    # handshake. The hostname includes a docker-generated container short
    # id so we don't anchor on the worker name — we just look for the
    # literal " ready." suffix that always lands once boot completes.
    # 60 s budget is generous; a healthy worker reaches ready in ~5 s,
    # but mingle discovery can stretch when redis is loaded.
    deadline = time.time() + 60.0
    ready = False
    while time.time() < deadline:
        r = _docker("logs", name, check=False, timeout=10)
        log_blob = (r.stdout or "") + (r.stderr or "")
        # Celery's exact line ends with "<worker_name>@<host> ready."
        # We pass `-n e2e-worker@%h` so the worker_name segment is
        # `e2e-worker` (not the default `celery`). Match the suffix shape
        # rather than anchoring on the celery@ prefix.
        if re.search(r"e2e-worker@\S+ ready\.", log_blob):
            ready = True
            break
        time.sleep(0.5)
    if not ready:
        logs_text = (
            (_docker("logs", name, check=False).stdout or "")
            + "\n--- stderr ---\n"
            + (_docker("logs", name, check=False).stderr or "")
        )
        _docker("rm", "-f", name, check=False)
        raise AssertionError(
            f"celery worker {name!r} never reached ready state in 60 s; "
            f"last logs:\n{logs_text[-4000:]}"
        )

    try:
        yield name
    finally:
        _docker("rm", "-f", name, check=False, timeout=30)
