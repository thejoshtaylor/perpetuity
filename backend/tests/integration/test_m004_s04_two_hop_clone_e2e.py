"""M004 / S04 / T05 — full two-hop materialize + auto-push e2e.

Slice S04's authoritative integration proof. Single test against the live
compose db + redis + an ephemeral orchestrator + sibling backend + a mock-
github API sibling + a mock-github git-daemon sibling that hosts the
upstream bare repo.

Scenarios A-H walk the slice contract end-to-end:
  A. Setup: seed system_settings + GitHub App credentials, signup admin,
     INSERT a github_app_installations row, seed mirror_idle_timeout=86400.
     Drop a fixture bare repo `acme/widgets.git` into the git-daemon sibling.
  B. POST /api/v1/teams/{id}/projects → assert projects + default
     manual_workflow rule rows.
  C. PUT /api/v1/projects/{id}/push-rule mode=auto → assert rule.mode=auto.
  D. POST /api/v1/projects/{id}/open → assert mirror running, .git/config
     sanitized (no token), post-receive hook present, user container on
     perpetuity_default, user-side .git/config has bare git:// remote, and
     all four expected log lines fired.
  E. Idempotency: second POST /open → both hops report `result=reused`.
  F. Auto-push: docker-exec into user container, commit + push → mirror
     post-receive hook fires → orchestrator pushes to the upstream → fixture
     bare repo sees the new commit, projects.last_push_status='ok'.
  G. Failure path: a SECOND project pointing at acme/missing → user push
     fires → auto-push fails → auto_push_rejected_by_remote WARNING fires +
     last_push_status='failed' (and stderr scrubbed of any token substring).
  H. Redaction sweep: backend + orchestrator logs contain ZERO matches for
     gho_/ghs_/ghu_/ghr_/github_pat_/-----BEGIN.

The mock-github API sidecar is the same shape as S02's. The mock-github
git-daemon is a fresh sidecar serving `/srv/git/<owner>/<repo>.git` on port
9418 with `--enable=receive-pack`. We rewire the mirror to clone from that
git-daemon (instead of github.com) by setting `git config --global
url."git://...:9418/".insteadOf "https://github.com/"` inside the mirror
container — so every git operation that targets `https://github.com/` is
transparently rewritten to the mock at `git://`. This drops the token from
the URL (insteadOf rewrites the prefix), which exactly matches how a real
clone would behave (token in env, not in cmd) but bypasses the need for a
real TLS handshake against api.github.com.

How to run::

    docker compose build backend orchestrator
    docker compose up -d db redis
    cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e \\
        tests/integration/test_m004_s04_two_hop_clone_e2e.py -v
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

# Same Fernet key the conftest exposes; the sibling backend AND the
# ephemeral orchestrator must share it so encrypted system_settings rows
# round-trip across both.
SYSTEM_SETTINGS_ENCRYPTION_KEY_TEST = (
    "kfk5l7mPRFpBV7PzWJxYmO6LRRQAdZ4iGYZRG6xL0fY="
)

# Skip-guard probes — backend image needs T01's alembic revision; orchestrator
# image needs T02/T04's modules. Stale images would otherwise crash in a
# confusing way at prestart / first import.
S06D_REVISION = "s06d_projects_and_push_rules"
CLONE_MODULE = "clone.py"
AUTO_PUSH_MODULE = "auto_push.py"

# The fake installation token the mock-github API returns. Prefix `ghs_`
# matches GitHub's real installation-token shape so the redaction sweep
# exercises the real-world fingerprint.
_MOCK_FIXED_TOKEN = "ghs_M004S04E2EFAKEINSTALLATIONTOKEN0000000000"

# Fixed installation_id keeps the cache key deterministic.
_FIXED_INSTALLATION_ID = 4242

# All sensitive github_app_* keys we seed and wipe.
_GITHUB_APP_KEYS = (
    "github_app_id",
    "github_app_client_id",
    "github_app_private_key",
    "github_app_webhook_secret",
)

# Mirror reaper interval used for the ephemeral orchestrator. We push the
# idle timeout up to 24h (86400) via system_settings so the mirror cannot
# reap mid-test even on slow CI; the reaper interval being short is fine.
_REAPER_INTERVAL_SECONDS = 5
_MIRROR_IDLE_TIMEOUT_S = 86400


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


def _delete_state() -> None:
    """Wipe everything this test creates so re-runs start clean (MEM161)."""
    _psql_exec("DELETE FROM project_push_rules")
    _psql_exec("DELETE FROM projects")
    _psql_exec("DELETE FROM github_app_installations")
    keys_csv = ",".join(f"'{k}'" for k in _GITHUB_APP_KEYS)
    _psql_exec(
        f"DELETE FROM system_settings WHERE key IN ({keys_csv})"
    )
    _psql_exec("DELETE FROM team_mirror_volumes")
    _psql_exec(
        "DELETE FROM system_settings "
        "WHERE key='mirror_idle_timeout_seconds'"
    )

    # team-mirror-* containers
    ls = _docker(
        "ps", "-aq", "--filter", "label=perpetuity.team_mirror=true",
        check=False, timeout=15,
    )
    if (ls.stdout or "").strip():
        _docker(
            "rm", "-f", *ls.stdout.split(),
            check=False, timeout=120,
        )

    # workspace containers (perpetuity-ws-*)
    ws = _docker(
        "ps", "-aq", "--filter", "label=perpetuity.managed=true",
        check=False, timeout=15,
    )
    if (ws.stdout or "").strip():
        _docker(
            "rm", "-f", *ws.stdout.split(),
            check=False, timeout=120,
        )

    # Per-team docker volumes
    vol_ls = _docker(
        "volume", "ls", "-q", "--filter", "name=perpetuity-team-mirror-",
        check=False, timeout=15,
    )
    if (vol_ls.stdout or "").strip():
        _docker(
            "volume", "rm", *vol_ls.stdout.split(),
            check=False, timeout=60,
        )


# ----- image probes (skip-guards) ----------------------------------------


def _backend_image_has_s06d() -> bool:
    r = _docker(
        "run", "--rm", "--entrypoint", "ls", BACKEND_IMAGE,
        "/app/backend/app/alembic/versions/",
        check=False, timeout=15,
    )
    return f"{S06D_REVISION}.py" in (r.stdout or "")


def _orchestrator_image_has_clone_and_auto_push() -> bool:
    r = _docker(
        "run", "--rm", "--entrypoint", "ls", ORCH_IMAGE,
        "/app/orchestrator/",
        check=False, timeout=15,
    )
    return CLONE_MODULE in (r.stdout or "") and AUTO_PUSH_MODULE in (r.stdout or "")


@pytest.fixture(autouse=True)
def _require_baked_images() -> None:
    if not _backend_image_has_s06d():
        pytest.skip(
            f"backend:latest is missing the {S06D_REVISION!r} alembic "
            "revision — run `docker compose build backend orchestrator` so "
            "the images bake the current source tree."
        )
    if not _orchestrator_image_has_clone_and_auto_push():
        pytest.skip(
            f"orchestrator:latest is missing {CLONE_MODULE!r} or "
            f"{AUTO_PUSH_MODULE!r} — run `docker compose build "
            "backend orchestrator`."
        )


@pytest.fixture(autouse=True)
def _wipe_state_before_after() -> Iterator[None]:
    _delete_state()
    yield
    _delete_state()


# ----- key generation -----------------------------------------------------


def _generate_rsa_keypair() -> tuple[str, str]:
    """Fresh 2048-bit RSA keypair for the synthetic GitHub App."""
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


# ----- credential / signup helpers ---------------------------------------


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


def _user_id_from_cookies(base_url: str, cookies: httpx.Cookies) -> str:
    with httpx.Client(base_url=base_url, timeout=15.0, cookies=cookies) as c:
        r = c.get("/api/v1/users/me")
        assert r.status_code == 200, f"users/me: {r.status_code} {r.text}"
        return r.json()["id"]


def _seed_github_app_credentials(
    backend_url: str,
    admin_cookies: httpx.Cookies,
    *,
    private_key_pem: str,
    app_id: int,
    client_id: str,
) -> None:
    """PUT the three github_app_* settings via the admin route."""
    with httpx.Client(
        base_url=backend_url, timeout=30.0, cookies=admin_cookies
    ) as c:
        r = c.put(
            "/api/v1/admin/settings/github_app_id", json={"value": app_id}
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


def _seed_mirror_idle_timeout(
    backend_url: str, admin_cookies: httpx.Cookies, *, value: int
) -> None:
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


def _insert_github_app_installation_via_psql(
    *, team_id: str, installation_id: int
) -> None:
    """Insert a github_app_installations row directly (skips the install-handshake;
    S02 covers that path)."""
    sql = (
        "INSERT INTO github_app_installations "
        "(id, team_id, installation_id, account_login, account_type) "
        f"VALUES ('{uuid.uuid4()}', '{team_id}', {installation_id}, "
        "'test-org', 'Organization')"
    )
    r = _psql_exec(sql)
    assert r.returncode == 0, f"insert installation row failed: {r.stderr!r}"


# ----- mock-github API sidecar (token mint) ------------------------------


def _boot_mock_github_app_api(
    *, public_key_pem: str, fixed_token: str, app_id: int
) -> tuple[str, str]:
    """Same shape as S02's _boot_mock_github — FastAPI sidecar verifying RS256
    JWTs and minting the canned installation token. Returns (name, base_url)."""
    name = f"mock-gh-api-{uuid.uuid4().hex[:8]}"
    fixture_path = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__), "fixtures", "mock_github_app.py"
        )
    )
    assert os.path.exists(fixture_path), (
        f"mock_github_app.py missing at {fixture_path}"
    )

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
    deadline = time.time() + 60.0
    last_err = ""
    probe = (
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
            "exec", name, "python3", "-c", probe,
            check=False, timeout=5,
        )
        if r.returncode == 0:
            return name, base_url
        last_err = (r.stderr or "")[:200] + " | " + (r.stdout or "")[:200]
        time.sleep(1.0)

    logs = _docker("logs", name, check=False, timeout=10).stdout or ""
    _docker("rm", "-f", name, check=False)
    pytest.fail(
        f"mock-github-api {name!r} never became healthy; last={last_err!r}\n"
        f"logs:\n{logs[-2000:]}"
    )


# ----- mock-github git-daemon sidecar (upstream remote) ------------------


def _boot_mock_github_git_daemon() -> str:
    """Run alpine/git as a sibling, hosting `/srv/git/acme/widgets.git` (a
    bare repo with one initial commit) on port 9418 with `--enable=receive-pack`.

    Also creates an EMPTY `/srv/git/acme/missing.git` so scenario G can clone
    from a present-at-clone-time / remove-before-push remote to simulate a
    GitHub-side delete that surfaces auto_push_rejected_by_remote.

    Returns the container name (DNS alias on perpetuity_default).
    """
    name = f"mock-gh-git-{uuid.uuid4().hex[:8]}"

    # Initialize the two bare repos and seed widgets with one commit, then
    # exec git-daemon. The alpine/git image deliberately omits `git daemon`,
    # so we use the workspace image (ubuntu-based ships /usr/lib/git-core/
    # git-daemon). The initial commit is created in a transient working-
    # tree clone so the bare repo gets a real refs/heads/main → real commit.
    boot_cmd = (
        "set -e; "
        "mkdir -p /srv/git/acme/widgets.git /srv/git/acme/missing.git; "
        "git init --bare /srv/git/acme/widgets.git >/dev/null; "
        "git init --bare /srv/git/acme/missing.git >/dev/null; "
        # Make `main` the default HEAD on both bare repos so a `git clone`
        # from a sibling lands on `main` (not detached HEAD). `git init`
        # writes `ref: refs/heads/master` by default; we overwrite it.
        "echo 'ref: refs/heads/main' > /srv/git/acme/widgets.git/HEAD; "
        "echo 'ref: refs/heads/main' > /srv/git/acme/missing.git/HEAD; "
        # Seed widgets.git with one commit on `main`.
        "mkdir -p /tmp/seed-widgets && cd /tmp/seed-widgets; "
        "git init -b main >/dev/null 2>&1; "
        "git config user.email seed@example.com; "
        "git config user.name seed; "
        "echo 'initial' > README.md; "
        "git add README.md; "
        "git commit -m 'initial commit' >/dev/null 2>&1; "
        "git push /srv/git/acme/widgets.git main:main >/dev/null 2>&1; "
        "cd /; "
        # Permit pushes from anywhere on the network. `--reuseaddr` lets us
        # restart the daemon without TIME_WAIT trouble during teardown.
        "exec git daemon --base-path=/srv/git --export-all --reuseaddr "
        "--enable=receive-pack --listen=0.0.0.0 --port=9418 "
        "--verbose --informative-errors"
    )
    _docker(
        "run", "-d",
        "--name", name,
        "--network", NETWORK,
        "--network-alias", name,
        "--entrypoint", "bash",
        WORKSPACE_IMAGE,
        "-c", boot_cmd,
        timeout=60,
    )

    # Wait for the daemon to be listening. Probe via a sibling git ls-remote
    # using the same workspace image (which we already proved has `git`).
    deadline = time.time() + 30.0
    last_err = ""
    while time.time() < deadline:
        probe = _docker(
            "run", "--rm",
            "--network", NETWORK,
            "--entrypoint", "git",
            WORKSPACE_IMAGE,
            "ls-remote", f"git://{name}:9418/acme/widgets.git",
            check=False, timeout=10,
        )
        if probe.returncode == 0 and "refs/heads/main" in (probe.stdout or ""):
            return name
        last_err = (
            (probe.stderr or "")[:200] + " | " + (probe.stdout or "")[:200]
        )
        time.sleep(0.5)

    logs = _docker("logs", name, check=False, timeout=10).stdout or ""
    _docker("rm", "-f", name, check=False)
    pytest.fail(
        f"mock-github-git {name!r} never became reachable; last={last_err!r}\n"
        f"logs:\n{logs[-2000:]}"
    )


# ----- ephemeral orchestrator parameterized by mock-github API URL -------


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


def _boot_ephemeral_orchestrator(
    *,
    mock_github_api_url: str,
    mock_github_clone_base_url: str,
    redis_password: str,
    pg_password: str,
    api_key: str,
) -> str:
    """Stop the compose orchestrator and launch an ephemeral one with
    GITHUB_API_BASE_URL=<mock-api>, GITHUB_CLONE_BASE_URL=<mock-git-daemon>,
    MIRROR_REAPER_INTERVAL_SECONDS short."""
    name = f"orch-s04-twohop-{uuid.uuid4().hex[:8]}"
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
        "-e", f"GITHUB_API_BASE_URL={mock_github_api_url}",
        "-e", f"GITHUB_CLONE_BASE_URL={mock_github_clone_base_url}",
        "-e", f"MIRROR_REAPER_INTERVAL_SECONDS={_REAPER_INTERVAL_SECONDS}",
        ORCH_IMAGE,
    ]
    _docker(*args)
    return name


def _wait_for_orch_running_self(
    ephemeral_name: str, *, timeout_s: float = 60.0
) -> None:
    """MEM194 readiness probe — exec python3+urllib INSIDE the ephemeral."""
    deadline = time.time() + timeout_s
    last_err = ""
    probe = (
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
            "python3", "-c", probe,
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
        f"within {int(timeout_s)}s; last={last_err!r}\n"
        f"logs:\n{logs[-4000:]}"
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
    + pointed at the ephemeral orchestrator at http://orchestrator:8001."""
    name = f"perpetuity-backend-e2e-s04-{uuid.uuid4().hex[:8]}"
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


# ----- mirror-side rewiring ----------------------------------------------


def _team_mirror_container_name(team_id: str) -> str:
    clean = team_id.replace("-", "")
    return f"team-mirror-{clean[:8]}"


def _wait_for_container_running(
    name: str, *, timeout_s: float = 30.0
) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = _docker(
            "inspect", "-f", "{{.State.Running}}", name,
            check=False, timeout=10,
        )
        if (r.stdout or "").strip() == "true":
            return True
        time.sleep(0.3)
    return False


def _install_mirror_url_rewrite(
    mirror_name: str, mock_git_daemon_name: str
) -> None:
    """Set `git config --global url."git://<mock>:9418/".insteadOf
    "https://github.com/"` inside the mirror container.

    insteadOf rewrites the URL prefix at git's call site BEFORE the network
    layer touches it — so the orchestrator's `git clone https://x-access-
    token:$TOKEN@github.com/<repo>.git` becomes `git clone git://<mock>:
    9418/<repo>.git` (the credential portion is dropped, which is fine for
    git daemon — it has no auth).

    The test runs this AFTER ensure_team_mirror creates the container but
    BEFORE materialize-mirror is called. The mirror is identified by name
    (deterministic from team_id).
    """
    rewrite_url = f"git://{mock_git_daemon_name}:9418/"
    # `git config --global` writes to /root/.gitconfig (root in the mirror).
    r = _docker(
        "exec", mirror_name,
        "git", "config", "--global",
        f"url.{rewrite_url}.insteadOf",
        "https://github.com/",
        check=False, timeout=10,
    )
    assert r.returncode == 0, (
        f"git config insteadOf install failed: rc={r.returncode} "
        f"stderr={r.stderr!r}"
    )
    # Also run with --system so any non-root caller would inherit the rule.
    # The orchestrator's docker exec runs as root in the mirror by default,
    # so --global is sufficient — we do --system as belt-and-suspenders.
    _docker(
        "exec", mirror_name,
        "git", "config", "--system",
        f"url.{rewrite_url}.insteadOf",
        "https://github.com/",
        check=False, timeout=10,
    )


# ----- log-marker helpers ------------------------------------------------


def _container_logs_blob(name: str) -> str:
    r = _docker("logs", name, check=False, timeout=15)
    return (r.stdout or "") + (r.stderr or "")


def _wait_for_log_marker(
    name: str, marker: str, *, timeout_s: float
) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if marker in _container_logs_blob(name):
            return True
        time.sleep(0.5)
    return False


# ----- the test ----------------------------------------------------------


@pytest.fixture
def two_hop_stack(
    compose_stack_up: None,  # noqa: ARG001
    request: pytest.FixtureRequest,
) -> Iterator[dict[str, str]]:
    """Boot mock-github API + mock-github git-daemon + ephemeral orchestrator
    + sibling backend, all on perpetuity_default. Yield names + URLs.

    Teardown captures logs BEFORE removing containers, then removes everything
    and restores the compose orchestrator."""
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
    app_id = secrets.randbelow(900_000) + 100_000

    # 1. mock-github API sidecar (token mint).
    api_name, api_url = _boot_mock_github_app_api(
        public_key_pem=public_pem,
        fixed_token=_MOCK_FIXED_TOKEN,
        app_id=app_id,
    )

    created: list[str] = [api_name]
    captured: dict[str, str] = {}

    def _teardown() -> None:
        for n in created:
            try:
                blob = _docker("logs", n, check=False, timeout=15)
                captured[n] = (blob.stdout or "") + (blob.stderr or "")
            except Exception:  # noqa: BLE001
                captured[n] = ""
        for n in created:
            _docker("rm", "-f", n, check=False, timeout=30)
        # team-mirror-* + workspace-* containers spawned by the orchestrator.
        for label in (
            "label=perpetuity.team_mirror=true",
            "label=perpetuity.managed=true",
        ):
            ls = _docker(
                "ps", "-aq", "--filter", label,
                check=False, timeout=15,
            )
            if (ls.stdout or "").strip():
                _docker(
                    "rm", "-f", *ls.stdout.split(),
                    check=False, timeout=120,
                )
        _restore_compose_orchestrator()

    request.addfinalizer(_teardown)

    # 2. mock-github git-daemon sidecar (upstream remote).
    git_name = _boot_mock_github_git_daemon()
    created.append(git_name)

    # 3. Ephemeral orchestrator pointed at the API mock and git-daemon mock.
    #    The clone-base URL is a `git://` so the orchestrator's clone-to-
    #    mirror and auto-push paths target the credential-free fixture
    #    daemon instead of the public github.com host.
    eph_name = _boot_ephemeral_orchestrator(
        mock_github_api_url=api_url,
        mock_github_clone_base_url=f"git://{git_name}:9418",
        redis_password=redis_password,
        pg_password=pg_password,
        api_key=api_key,
    )
    created.append(eph_name)
    _wait_for_orch_running_self(eph_name, timeout_s=60.0)

    # 4. Sibling backend.
    bk_name, bk_url = _boot_sibling_backend(
        api_key=api_key,
        redis_password=redis_password,
        pg_password=pg_password,
        secret_key=secret_key,
    )
    created.append(bk_name)

    yield {
        "api_name": api_name,
        "git_name": git_name,
        "eph_name": eph_name,
        "backend_name": bk_name,
        "backend_url": bk_url,
        "api_key": api_key,
        "private_pem": private_pem,
        "app_id": str(app_id),
        "redis_password": redis_password,
        "pg_password": pg_password,
        "secret_key": secret_key,
    }


def test_m004_s04_two_hop_clone_e2e(  # noqa: PLR0912, PLR0915
    two_hop_stack: dict[str, str],
) -> None:
    """End-to-end materialize + auto-push + redaction sweep for slice S04."""
    suite_started = time.time()

    backend_url = two_hop_stack["backend_url"]
    backend_name = two_hop_stack["backend_name"]
    eph_name = two_hop_stack["eph_name"]
    git_name = two_hop_stack["git_name"]
    private_pem = two_hop_stack["private_pem"]
    app_id = int(two_hop_stack["app_id"])

    # ===== Scenario A: setup =====================================
    superuser_cookies = _login_only(
        backend_url, email="admin@example.com", password="changethis"
    )
    _seed_github_app_credentials(
        backend_url,
        superuser_cookies,
        private_key_pem=private_pem,
        app_id=app_id,
        client_id="perpetuity-test",
    )
    _seed_mirror_idle_timeout(
        backend_url, superuser_cookies, value=_MIRROR_IDLE_TIMEOUT_S
    )

    # Signup a team-admin user with a personal team.
    suffix = uuid.uuid4().hex[:8]
    admin_email = f"m004-s04-admin-{suffix}@example.com"
    admin_password = "Sup3rs3cret-team-admin"
    admin_full_name = f"M004S04Admin {suffix}"
    admin_cookies = _signup_login(
        backend_url,
        email=admin_email,
        password=admin_password,
        full_name=admin_full_name,
    )
    team_id = _personal_team_id(backend_url, admin_cookies)
    user_id = _user_id_from_cookies(backend_url, admin_cookies)

    # INSERT the github_app_installations row directly.
    _insert_github_app_installation_via_psql(
        team_id=team_id, installation_id=_FIXED_INSTALLATION_ID
    )

    # ===== Scenario B: create project ============================
    with httpx.Client(
        base_url=backend_url, timeout=30.0, cookies=admin_cookies
    ) as c:
        r_p = c.post(
            f"/api/v1/teams/{team_id}/projects",
            json={
                "installation_id": _FIXED_INSTALLATION_ID,
                "github_repo_full_name": "acme/widgets",
                "name": "widgets",
            },
        )
    assert r_p.status_code == 200, (
        f"POST projects: {r_p.status_code} {r_p.text}"
    )
    project_id = r_p.json()["id"]

    project_count = _psql_one(
        f"SELECT count(*) FROM projects WHERE id='{project_id}'"
    )
    assert project_count == "1", f"projects row missing: {project_count!r}"
    rule_mode = _psql_one(
        f"SELECT mode FROM project_push_rules WHERE project_id='{project_id}'"
    )
    assert rule_mode == "manual_workflow", (
        f"default push_rule.mode mismatch: {rule_mode!r}"
    )

    # ===== Scenario C: PUT push-rule mode=auto ===================
    with httpx.Client(
        base_url=backend_url, timeout=30.0, cookies=admin_cookies
    ) as c:
        r_pr = c.put(
            f"/api/v1/projects/{project_id}/push-rule",
            json={"mode": "auto"},
        )
    assert r_pr.status_code == 200, (
        f"PUT push-rule: {r_pr.status_code} {r_pr.text}"
    )
    rule_mode2 = _psql_one(
        f"SELECT mode FROM project_push_rules WHERE project_id='{project_id}'"
    )
    assert rule_mode2 == "auto", f"push_rule.mode after PUT: {rule_mode2!r}"

    # The orchestrator's clone path is parameterized by GITHUB_CLONE_BASE_URL
    # (set on the ephemeral above to `git://<mock-git-daemon>:9418`), so
    # there is no need for an explicit pre-ensure + URL rewrite hop. POST
    # /open will idempotently ensure the mirror, then clone-to-mirror will
    # target the mock-github git daemon directly.
    mirror_name = _team_mirror_container_name(team_id)

    # ===== Scenario D: POST /open ================================
    with httpx.Client(
        base_url=backend_url, timeout=120.0, cookies=admin_cookies
    ) as c:
        r_open = c.post(f"/api/v1/projects/{project_id}/open")
    if r_open.status_code != 200:
        orch_log_dump = "\n".join(
            _container_logs_blob(eph_name).splitlines()[-500:]
        )
        raise AssertionError(
            f"POST /open: {r_open.status_code} {r_open.text}\n"
            f"orch logs (last 500):\n{orch_log_dump}"
        )
    open_body = r_open.json()
    assert open_body["mirror_status"] == "created", (
        f"first /open mirror_status: {open_body!r}"
    )
    assert open_body["user_status"] == "created", (
        f"first /open user_status: {open_body!r}"
    )
    workspace_path = open_body["workspace_path"]

    # D.1: mirror container running with expected label.
    label_r = _docker(
        "inspect", "-f",
        '{{index .Config.Labels "perpetuity.team_mirror"}}',
        mirror_name, check=False, timeout=10,
    )
    assert (label_r.stdout or "").strip() == "true", (
        f"perpetuity.team_mirror label missing: {label_r.stdout!r}"
    )

    # D.2: mirror /repos/<id>.git/config sanitized — no token.
    cfg_r = _docker(
        "exec", mirror_name,
        "cat", f"/repos/{project_id}.git/config",
        check=False, timeout=10,
    )
    assert cfg_r.returncode == 0, (
        f"cat mirror config failed: {cfg_r.stderr!r}"
    )
    cfg_body = cfg_r.stdout or ""
    assert "https://github.com/acme/widgets" in cfg_body, (
        f"mirror config remote URL wrong: {cfg_body!r}"
    )
    for needle in ("x-access-token", "gho_", "ghs_", "ghu_", "ghr_", "github_pat_"):
        assert needle not in cfg_body, (
            f"mirror config carries leak fingerprint {needle!r}: {cfg_body!r}"
        )

    # D.3: post-receive hook present + executable.
    hook_r = _docker(
        "exec", mirror_name,
        "ls", "-l", f"/repos/{project_id}.git/hooks/post-receive",
        check=False, timeout=10,
    )
    assert hook_r.returncode == 0, (
        f"post-receive hook missing: rc={hook_r.returncode} "
        f"stderr={hook_r.stderr!r}"
    )
    # `-rwxr-xr-x` shape — the +x bits matter; be lenient on owner/group.
    assert "x" in (hook_r.stdout or "").split()[0], (
        f"post-receive hook not executable: {hook_r.stdout!r}"
    )

    # D.4: user-session container running and on perpetuity_default. The
    # orchestrator names the container `perpetuity-ws-<first8-team>` per
    # MEM098; we look it up by name and then inspect its NetworkMode.
    user_container_name = f"perpetuity-ws-{team_id.replace('-', '')[:8]}"
    assert _wait_for_container_running(user_container_name, timeout_s=10.0), (
        f"user-session container {user_container_name!r} not running after /open"
    )
    nm_r = _docker(
        "inspect", "-f", "{{.HostConfig.NetworkMode}}",
        user_container_name, check=False, timeout=10,
    )
    assert (nm_r.stdout or "").strip() == NETWORK, (
        f"user container NetworkMode: {nm_r.stdout!r} (want {NETWORK!r}) — "
        "MEM264 regression suspected"
    )
    user_container_id = user_container_name

    # D.5: user-side .git/config has bare git:// remote, no token, no https.
    user_cfg_r = _docker(
        "exec", user_container_id,
        "cat", f"{workspace_path}/.git/config",
        check=False, timeout=10,
    )
    assert user_cfg_r.returncode == 0, (
        f"cat user config failed: {user_cfg_r.stderr!r}"
    )
    user_cfg_body = user_cfg_r.stdout or ""
    expected_user_remote = f"git://{mirror_name}:9418/{project_id}.git"
    assert expected_user_remote in user_cfg_body, (
        f"user .git/config remote URL wrong: {user_cfg_body!r}"
    )
    assert "x-access-token" not in user_cfg_body, (
        f"user .git/config carries credentials: {user_cfg_body!r}"
    )
    assert "https://github.com" not in user_cfg_body, (
        f"user .git/config carries https://github.com (leak from mirror): "
        f"{user_cfg_body!r}"
    )

    # D.6: required log markers from D fired.
    orch_blob = _container_logs_blob(eph_name)
    for marker in (
        f"team_mirror_clone_started team_id={team_id} project_id={project_id}",
        f"team_mirror_clone_completed team_id={team_id} project_id={project_id}",
        f"user_clone_started user_id={user_id} team_id={team_id} project_id={project_id}",
        f"user_clone_completed user_id={user_id} team_id={team_id} project_id={project_id}",
        f"network_mode_attached_to_user_container",
        f"post_receive_hook_installed project_id={project_id}",
    ):
        assert marker in orch_blob, (
            f"missing log marker {marker!r} in orchestrator logs; "
            f"tail:\n{orch_blob[-3000:]}"
        )

    # ===== Scenario E: idempotency ===============================
    with httpx.Client(
        base_url=backend_url, timeout=120.0, cookies=admin_cookies
    ) as c:
        r_open2 = c.post(f"/api/v1/projects/{project_id}/open")
    assert r_open2.status_code == 200, (
        f"second /open: {r_open2.status_code} {r_open2.text}"
    )
    open2_body = r_open2.json()
    assert open2_body["mirror_status"] == "reused", (
        f"second /open mirror_status: {open2_body!r}"
    )
    assert open2_body["user_status"] == "reused", (
        f"second /open user_status: {open2_body!r}"
    )

    # ===== Scenario F: auto-push round-trip ======================
    push_cmd = (
        f"set -e; cd {workspace_path}; "
        "git config user.email t@example.com; "
        "git config user.name t; "
        "echo update > readme.md; "
        "git add readme.md; "
        "git commit -m 'test commit' >/dev/null; "
        "git push origin main"
    )
    push_r = _docker(
        "exec", user_container_id,
        "sh", "-c", push_cmd,
        check=False, timeout=60,
    )
    assert push_r.returncode == 0, (
        f"user-side push failed: rc={push_r.returncode} "
        f"stdout={push_r.stdout!r} stderr={push_r.stderr!r}"
    )

    # Within ~15s the orchestrator should have fired the auto-push hook
    # callback and completed the push.
    if not _wait_for_log_marker(
        eph_name,
        f"auto_push_started project_id={project_id} rule_mode=auto",
        timeout_s=15.0,
    ):
        # Diagnostics: dump push stdout/stderr, the mirror's hook script,
        # and the result of running the hook by hand to surface what
        # happened.
        hook_dump = _docker(
            "exec", mirror_name,
            "cat", f"/repos/{project_id}.git/hooks/post-receive",
            check=False, timeout=10,
        )
        manual_hook_r = _docker(
            "exec", "-e", f"GIT_DIR=/repos/{project_id}.git",
            mirror_name,
            "sh", f"/repos/{project_id}.git/hooks/post-receive",
            check=False, timeout=15,
        )
        env_dump = _docker(
            "exec", mirror_name,
            "sh", "-c", "echo PERPETUITY_ORCH_KEY=${PERPETUITY_ORCH_KEY:0:8}...",
            check=False, timeout=10,
        )
        raise AssertionError(
            "missing auto_push_started in orch logs.\n"
            f"push_r.returncode={push_r.returncode}\n"
            f"push_r.stdout={push_r.stdout!r}\n"
            f"push_r.stderr={push_r.stderr!r}\n"
            f"---hook script---\n{hook_dump.stdout}\n"
            f"---env in mirror (truncated key)---\n{env_dump.stdout}\n"
            f"---manual hook run rc={manual_hook_r.returncode}---\n"
            f"stdout={manual_hook_r.stdout}\n"
            f"stderr={manual_hook_r.stderr}\n"
            f"---orch logs tail---\n{_container_logs_blob(eph_name)[-3000:]}"
        )
    assert _wait_for_log_marker(
        eph_name,
        f"auto_push_completed project_id={project_id} result=ok",
        timeout_s=15.0,
    ), (
        "missing auto_push_completed result=ok in orch logs; "
        f"tail:\n{_container_logs_blob(eph_name)[-3000:]}"
    )

    # F.4: fixture upstream sees the new commit.
    deadline = time.time() + 10.0
    saw_commit = False
    upstream_log = ""
    while time.time() < deadline:
        log_r = _docker(
            "exec", git_name,
            "git", "--git-dir=/srv/git/acme/widgets.git",
            "log", "--oneline", "main",
            check=False, timeout=10,
        )
        upstream_log = log_r.stdout or ""
        if "test commit" in upstream_log:
            saw_commit = True
            break
        time.sleep(0.5)
    assert saw_commit, (
        f"fixture upstream missing 'test commit'; got:\n{upstream_log!r}"
    )

    # F.5: projects.last_push_status='ok'.
    deadline = time.time() + 10.0
    last_status = ""
    while time.time() < deadline:
        last_status = _psql_one(
            f"SELECT last_push_status FROM projects WHERE id='{project_id}'"
        )
        if last_status == "ok":
            break
        time.sleep(0.5)
    assert last_status == "ok", (
        f"projects.last_push_status: {last_status!r} (want 'ok')"
    )
    last_err = _psql_one(
        f"SELECT COALESCE(last_push_error, '') FROM projects "
        f"WHERE id='{project_id}'"
    )
    assert last_err == "", f"last_push_error not NULL: {last_err!r}"

    # ===== Scenario G: failure path =============================
    # Create a SECOND project pointing at acme/missing. The mock-github git
    # daemon has /srv/git/acme/missing.git as an empty bare repo at boot;
    # the clone-to-mirror succeeds against it (clone of empty bare → empty
    # local bare). Then we delete /srv/git/acme/missing.git from the
    # daemon, so the post-push to it fails with a non-zero exit. That gives
    # us the auto_push_rejected_by_remote contract.
    with httpx.Client(
        base_url=backend_url, timeout=30.0, cookies=admin_cookies
    ) as c:
        r_p2 = c.post(
            f"/api/v1/teams/{team_id}/projects",
            json={
                "installation_id": _FIXED_INSTALLATION_ID,
                "github_repo_full_name": "acme/missing",
                "name": "missing",
            },
        )
    assert r_p2.status_code == 200, (
        f"POST second project: {r_p2.status_code} {r_p2.text}"
    )
    project_id2 = r_p2.json()["id"]
    with httpx.Client(
        base_url=backend_url, timeout=30.0, cookies=admin_cookies
    ) as c:
        r_pr2 = c.put(
            f"/api/v1/projects/{project_id2}/push-rule",
            json={"mode": "auto"},
        )
    assert r_pr2.status_code == 200, (
        f"PUT push-rule (#2): {r_pr2.status_code} {r_pr2.text}"
    )

    # Open project #2 — mirror clone of an empty bare repo against
    # mock-github will succeed because the rewrite redirects to the
    # daemon's empty `acme/missing.git`.
    with httpx.Client(
        base_url=backend_url, timeout=120.0, cookies=admin_cookies
    ) as c:
        r_open_p2 = c.post(f"/api/v1/projects/{project_id2}/open")
    assert r_open_p2.status_code == 200, (
        f"POST /open (#2): {r_open_p2.status_code} {r_open_p2.text}"
    )
    workspace_path2 = r_open_p2.json()["workspace_path"]

    # NOW delete the upstream from the mock-github so the auto-push fails.
    rm_r = _docker(
        "exec", git_name,
        "rm", "-rf", "/srv/git/acme/missing.git",
        check=False, timeout=10,
    )
    assert rm_r.returncode == 0, (
        f"rm fixture upstream failed: {rm_r.stderr!r}"
    )

    # User push from project #2.
    push2_cmd = (
        f"set -e; cd {workspace_path2}; "
        "git config user.email t2@example.com; "
        "git config user.name t2; "
        "echo missing > readme.md; "
        "git add readme.md; "
        "git commit -m 'failing commit' >/dev/null; "
        "git push origin main"
    )
    push2_r = _docker(
        "exec", user_container_id,
        "sh", "-c", push2_cmd,
        check=False, timeout=60,
    )
    # The user-side push to the mirror succeeds (the mirror accepts the
    # ref); the failure is on the mirror-to-upstream auto-push, which fires
    # async via post-receive.
    assert push2_r.returncode == 0, (
        f"user-side push (#2) failed unexpectedly: rc={push2_r.returncode} "
        f"stdout={push2_r.stdout!r} stderr={push2_r.stderr!r}"
    )

    # Wait for auto_push_rejected_by_remote.
    assert _wait_for_log_marker(
        eph_name,
        f"auto_push_rejected_by_remote project_id={project_id2}",
        timeout_s=15.0,
    ), (
        "missing auto_push_rejected_by_remote (#2) in orch logs; "
        f"tail:\n{_container_logs_blob(eph_name)[-3000:]}"
    )

    # G.2: projects.last_push_status='failed' + last_push_error scrubbed.
    deadline = time.time() + 10.0
    last_status2 = ""
    while time.time() < deadline:
        last_status2 = _psql_one(
            f"SELECT last_push_status FROM projects WHERE id='{project_id2}'"
        )
        if last_status2 == "failed":
            break
        time.sleep(0.5)
    assert last_status2 == "failed", (
        f"projects.last_push_status (#2): {last_status2!r} (want 'failed')"
    )
    last_err2 = _psql_one(
        f"SELECT COALESCE(last_push_error, '') FROM projects "
        f"WHERE id='{project_id2}'"
    )
    # No token-prefix substring should land in the persisted error.
    for prefix in ("gho_", "ghu_", "ghr_", "github_pat_"):
        assert prefix not in last_err2, (
            f"last_push_error carries {prefix!r}: {last_err2!r}"
        )
    # `ghs_` is the mock-fixed-token prefix; it MUST NOT appear in the
    # scrubbed error either.
    assert "ghs_" not in last_err2, (
        f"last_push_error carries ghs_ prefix: {last_err2!r}"
    )

    # ===== Scenario H: redaction sweep ============================
    eph_blob = _container_logs_blob(eph_name)
    backend_blob = _container_logs_blob(backend_name)
    swept_blob = "\n".join((eph_blob, backend_blob))

    # The full mock token must NEVER appear in either log.
    assert _MOCK_FIXED_TOKEN not in swept_blob, (
        "redaction sweep — full installation token leaked into "
        "backend/orchestrator logs"
    )

    # GitHub token-prefix families MUST NOT appear except inside the
    # canonical `token_prefix=` log shape (4-char prefix).
    for prefix in ("gho_", "ghu_", "ghr_", "github_pat_"):
        assert prefix not in swept_blob, (
            f"redaction sweep — {prefix!r} appeared in logs"
        )
    # `ghs_` is the prefix family of installation tokens; the `_token_prefix`
    # helper emits the first 4 chars (e.g. `ghs_`) followed by '...' in the
    # canonical `token_prefix=` log. Anywhere else is a regression.
    for line in swept_blob.splitlines():
        if "ghs_" in line and "token_prefix=" not in line:
            raise AssertionError(
                f"redaction sweep — `ghs_` appeared outside token_prefix= "
                f"context: {line!r}"
            )

    # PEM armor must NEVER appear in either log.
    assert "-----BEGIN" not in swept_blob, (
        "redaction sweep — PEM armor `-----BEGIN` appeared in logs"
    )

    # Required positive log taxonomy.
    required_markers = (
        "team_mirror_clone_started",
        "team_mirror_clone_completed",
        "user_clone_started",
        "user_clone_completed",
        "auto_push_started",
        "auto_push_completed",
        "auto_push_rejected_by_remote",
        "network_mode_attached_to_user_container",
        "project_opened",
        "post_receive_hook_installed",
        "project_push_rule_updated",
    )
    for marker in required_markers:
        assert marker in swept_blob, (
            f"observability taxonomy regression: {marker!r} missing from "
            f"swept logs"
        )

    # Sanity: the wall-clock budget for the test (slice plan: <90s wall;
    # we allow 6x for cold caches and CI).
    elapsed = time.time() - suite_started
    assert elapsed < 540.0, (
        f"e2e suite took {elapsed:.1f}s — far over the 90s slice budget"
    )
