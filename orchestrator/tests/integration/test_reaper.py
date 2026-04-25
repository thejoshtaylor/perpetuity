"""Integration tests for the S04/T02 background idle reaper.

Each test boots a fresh ephemeral orchestrator container with
`REAPER_INTERVAL_SECONDS=1` so the reaper trips quickly. Tests then
either drive POST /v1/sessions to provision a real workspace container
or inject a Redis record directly to exercise edge cases (orphaned
container, no-attach idle session) without paying for a workspace boot.

Verification matrix from the task plan:
  1. test_reaper_kills_idle_session_with_no_attach
  2. test_reaper_skips_attached_session
  3. test_reaper_skips_non_idle_session
  4. test_reaper_reaps_container_when_last_session_killed
  5. test_reaper_keeps_container_with_surviving_session
  6. test_resolve_idle_timeout_seconds_reads_system_settings + fallback +
     invalid-value (mirrors S03's volume_size_gb tests)
  7. test_reaper_survives_redis_blip — reaper still alive after a
     transient scan_session_keys raise

Run from inside the compose network (psql + asyncpg need db DNS). The
canonical command (per the task plan):

    docker compose build orchestrator backend &&
    docker compose up -d --force-recreate orchestrator &&
    docker cp orchestrator/tests perpetuity-orchestrator-1:/app/tests &&
    docker compose exec orchestrator /app/.venv/bin/pytest \\
        tests/integration/test_reaper.py -v
"""

from __future__ import annotations

import json
import logging
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
API_KEY = "integration-test-reaper-key"


# ---------------------------------------------------------------------------
# Docker / pg / redis helpers — mirror test_ws_attach_map.py / test_volumes.py.
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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_for_health(base_url: str, *, timeout_s: float = 60.0) -> None:
    deadline = time.time() + timeout_s
    last_exc: Exception | None = None
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base_url}/v1/health", timeout=2.0)
            if r.status_code == 200 and r.json().get("image_present"):
                return
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
    """Insert a fresh (user, team) so workspace_volume FK constraints hold."""
    user_id = str(uuid.uuid4())
    team_id = str(uuid.uuid4())
    user_email = f"reaper-{uuid.uuid4().hex[:8]}@test.local"
    team_name = f"reaper-team-{uuid.uuid4().hex[:8]}"
    _psql_query(
        f"INSERT INTO \"user\" (id, email, hashed_password, is_active, role, full_name) "
        f"VALUES ('{user_id}', '{user_email}', 'x', true, 'user', 'Reaper Test')"
    )
    _psql_query(
        f"INSERT INTO team (id, name, slug, is_personal) "
        f"VALUES ('{team_id}', '{team_name}', '{team_name}', false)"
    )
    return user_id, team_id


def _cleanup_pg_user_team(user_id: str, team_id: str) -> None:
    _psql_query(f"DELETE FROM team WHERE id = '{team_id}'")
    _psql_query(f"DELETE FROM \"user\" WHERE id = '{user_id}'")


def _boot_orchestrator(
    *,
    reaper_interval_seconds: int = 1,
    extra_env: dict[str, str] | None = None,
) -> tuple[str, str]:
    name = f"orch-reaper-{uuid.uuid4().hex[:8]}"
    host_port = _free_port()
    redis_password = os.environ.get("REDIS_PASSWORD") or "changethis"
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
        "--network", NETWORK,
        "-p", f"{host_port}:8001",
        "-v", "/var/run/docker.sock:/var/run/docker.sock",
        "--mount",
        f"type=bind,source={host_workspace_root},target={host_workspace_root},bind-propagation=rshared",
        "-v", f"{host_vols_dir}:{host_vols_dir}",
        "--privileged",
        "-e", f"WORKSPACE_IMAGE={WORKSPACE_IMAGE}",
        "-e", f"ORCHESTRATOR_API_KEY={API_KEY}",
        "-e", "REDIS_HOST=redis",
        "-e", f"REDIS_PASSWORD={redis_password}",
        "-e", f"DATABASE_URL={database_url}",
        "-e", f"REAPER_INTERVAL_SECONDS={reaper_interval_seconds}",
    ]
    for k, v in (extra_env or {}).items():
        args.extend(["-e", f"{k}={v}"])
    args.append(ORCH_IMAGE)
    _docker(*args)
    base_url = f"http://localhost:{host_port}"
    try:
        _wait_for_health(base_url)
    except Exception:
        logs = _docker("logs", name, check=False).stdout or ""
        _docker("rm", "-f", name, check=False)
        raise AssertionError(f"orchestrator boot failed; logs:\n{logs}")
    return name, base_url


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
            "ps", "-aq",
            "--filter", "label=perpetuity.managed=true",
            check=False,
        )
        if ws.stdout.strip():
            _docker("rm", "-f", *ws.stdout.split(), check=False, timeout=120)
        _docker("rm", "-f", name, check=False)


@pytest.fixture
def user_team() -> Iterator[tuple[str, str]]:
    user_id, team_id = _create_pg_user_team()
    try:
        yield (user_id, team_id)
    finally:
        _cleanup_pg_user_team(user_id, team_id)


def _http(base_url: str, api_key: str) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        headers={"X-Orchestrator-Key": api_key},
        timeout=httpx.Timeout(30.0, connect=5.0),
    )


def _logs(orch: dict[str, str]) -> str:
    p = _docker("logs", orch["name"], check=False)
    return (p.stdout or "") + (p.stderr or "")


def _wait_for_log(
    orch: dict[str, str], substring: str, *, timeout_s: float = 10.0
) -> str:
    deadline = time.time() + timeout_s
    last = ""
    while time.time() < deadline:
        last = _logs(orch)
        if substring in last:
            return last
        time.sleep(0.2)
    raise AssertionError(
        f"log line containing {substring!r} never appeared within "
        f"{timeout_s}s; tail=\n{last[-2500:]}"
    )


def _seed_session(orch: dict[str, str], user_id: str, team_id: str) -> tuple[str, str]:
    sid = str(uuid.uuid4())
    with _http(orch["base_url"], orch["api_key"]) as c:
        r = c.post(
            "/v1/sessions",
            json={"session_id": sid, "user_id": user_id, "team_id": team_id},
        )
        assert r.status_code == 200, r.text
        return sid, r.json()["container_id"]


# ---------------------------------------------------------------------------
# Direct Redis access — via `docker exec perpetuity-redis-1 redis-cli`.
#
# The compose redis is internal-network-only (no published host port), so
# the test process can't connect to it directly with a redis client. Going
# through `docker exec ... redis-cli` from the host process is the same
# pattern this module uses for psql and matches MEM137: tests run on the
# host but reach compose-internal services via `docker exec` shims.
# ---------------------------------------------------------------------------


REDIS_CONTAINER = "perpetuity-redis-1"


def _redis_cli(*args: str, check: bool = True) -> str:
    """Run a redis-cli command against the compose redis. Returns stdout
    stripped. Password defaults to 'changethis' (compose .env)."""
    password = os.environ.get("REDIS_PASSWORD") or "changethis"
    cmd = [
        "docker", "exec", REDIS_CONTAINER,
        "redis-cli", "-a", password, "--no-auth-warning",
        *args,
    ]
    out = subprocess.run(
        cmd,
        check=check,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return (out.stdout or "").strip()


def _put_redis_session(
    session_id: str,
    *,
    container_id: str,
    user_id: str,
    team_id: str,
    last_activity: float,
    tmux_session: str | None = None,
) -> None:
    """Write a session record + user_sessions index entry directly into
    Redis (mirrors RedisSessionRegistry.set_session's pipeline shape).
    """
    record = {
        "container_id": container_id,
        "tmux_session": tmux_session or session_id,
        "user_id": user_id,
        "team_id": team_id,
        "last_activity": last_activity,
    }
    _redis_cli("SET", f"session:{session_id}", json.dumps(record))
    _redis_cli("SADD", f"user_sessions:{user_id}:{team_id}", session_id)


def _get_redis_session(session_id: str) -> dict | None:
    raw = _redis_cli("GET", f"session:{session_id}", check=False)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _delete_redis_session(session_id: str) -> None:
    raw = _redis_cli("GET", f"session:{session_id}", check=False)
    if raw:
        try:
            data = json.loads(raw)
            _redis_cli(
                "SREM",
                f"user_sessions:{data['user_id']}:{data['team_id']}",
                session_id,
            )
        except (TypeError, ValueError):
            pass
    _redis_cli("DEL", f"session:{session_id}", check=False)


# ---------------------------------------------------------------------------
# (1) Idle + no attach → reaper kills tmux + deletes Redis row
# ---------------------------------------------------------------------------


def test_reaper_kills_idle_session_with_no_attach(
    orchestrator: dict[str, str],
    user_team: tuple[str, str],
) -> None:
    """An idle session (last_activity 1000s ago) with no attach is reaped.

    Provision via POST /v1/sessions so a real container + real tmux
    session exist; then back-date last_activity via direct Redis write.
    Wait for `reaper_killed_session` to land in the orchestrator logs.
    """
    user_id, team_id = user_team
    sid, container_id = _seed_session(orchestrator, user_id, team_id)

    # Back-date last_activity well past the test idle_timeout (5s).
    _put_redis_session(
        sid,
        container_id=container_id,
        user_id=user_id,
        team_id=team_id,
        last_activity=time.time() - 1000.0,
    )

    # PUT idle_timeout_seconds=5 via direct system_settings write so the
    # helper resolves to 5s instead of the boot-time 900s default.
    _psql_query(
        "INSERT INTO system_settings (key, value, updated_at) "
        "VALUES ('idle_timeout_seconds', '5'::jsonb, NOW()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()"
    )
    try:
        _wait_for_log(
            orchestrator,
            f"reaper_killed_session session_id={sid}",
            timeout_s=15.0,
        )
        # Redis row is gone.
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if _get_redis_session(sid) is None:
                break
            time.sleep(0.2)
        assert _get_redis_session(sid) is None, (
            "Redis session record should be deleted by reaper"
        )
    finally:
        _psql_query("DELETE FROM system_settings WHERE key = 'idle_timeout_seconds'")
        _delete_redis_session(sid)


# ---------------------------------------------------------------------------
# (2) Idle but attached → skipped (D018 two-phase)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reaper_skips_attached_session(
    orchestrator: dict[str, str],
    user_team: tuple[str, str],
) -> None:
    """An idle session with a live WS attach is NOT reaped.

    The attach map is process-local, so we can't register an attach from
    outside the orchestrator process — instead we open a real WS attach
    and hold it for the duration of the test.
    """
    from websockets.asyncio.client import connect

    user_id, team_id = user_team
    sid, container_id = _seed_session(orchestrator, user_id, team_id)

    # Connect WS FIRST so the AttachMap registers this session before we
    # back-date last_activity. If we back-dated first and the reaper tick
    # fired before the WS upgrade ran register(), the session would be
    # reaped (no attach) and the WS would close 1008 session_not_found.
    _psql_query(
        "INSERT INTO system_settings (key, value, updated_at) "
        "VALUES ('idle_timeout_seconds', '5'::jsonb, NOW()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()"
    )
    ws_url = (
        orchestrator["base_url"].replace("http://", "ws://")
        + f"/v1/sessions/{sid}/stream?key={orchestrator['api_key']}"
    )
    try:
        async with connect(ws_url) as ws:
            # Drain the attach frame — register_attach() runs after
            # __aenter__ on the exec stream; this recv() guarantees the
            # AttachMap has the session before we back-date.
            await ws.recv()
            _wait_for_log(
                orchestrator,
                f"attach_registered session_id={sid}",
                timeout_s=5.0,
            )

            # Now back-date last_activity past idle_timeout=5s. The
            # session is "live" purely because the AttachMap says so —
            # this is the D018 two-phase check under test.
            _put_redis_session(
                sid,
                container_id=container_id,
                user_id=user_id,
                team_id=team_id,
                last_activity=time.time() - 1000.0,
            )

            # Wait for at least 3 reaper ticks (interval=1s) so we know
            # the reaper SAW this session and chose not to reap it.
            time.sleep(4.0)

            logs = _logs(orchestrator)
            assert (
                f"reaper_killed_session session_id={sid}" not in logs
            ), f"attached session must NOT be reaped; logs:\n{logs[-2500:]}"
            # Redis row must still exist.
            assert _get_redis_session(sid) is not None, (
                "attached session record was deleted"
            )
    finally:
        _delete_redis_session(sid)
        _psql_query("DELETE FROM system_settings WHERE key = 'idle_timeout_seconds'")


# ---------------------------------------------------------------------------
# (3) Recently active → skipped
# ---------------------------------------------------------------------------


def test_reaper_skips_non_idle_session(
    orchestrator: dict[str, str],
    user_team: tuple[str, str],
) -> None:
    """A session with last_activity=now is NOT reaped even with no attach."""
    user_id, team_id = user_team
    sid, container_id = _seed_session(orchestrator, user_id, team_id)

    # last_activity=now, idle_timeout=5 — still well under threshold.
    _put_redis_session(
        sid,
        container_id=container_id,
        user_id=user_id,
        team_id=team_id,
        last_activity=time.time(),
    )
    _psql_query(
        "INSERT INTO system_settings (key, value, updated_at) "
        "VALUES ('idle_timeout_seconds', '5'::jsonb, NOW()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()"
    )
    try:
        time.sleep(3.0)  # ~3 reaper ticks at 1s interval

        logs = _logs(orchestrator)
        assert (
            f"reaper_killed_session session_id={sid}" not in logs
        ), f"non-idle session must NOT be reaped; logs:\n{logs[-2500:]}"
        assert _get_redis_session(sid) is not None, (
            "non-idle session record was deleted"
        )
    finally:
        _delete_redis_session(sid)
        _psql_query("DELETE FROM system_settings WHERE key = 'idle_timeout_seconds'")


# ---------------------------------------------------------------------------
# (4) Container reaped when its last tmux session dies
# ---------------------------------------------------------------------------


def test_reaper_reaps_container_when_last_session_killed(
    orchestrator: dict[str, str],
    user_team: tuple[str, str],
) -> None:
    """When the reaper kills the only Redis session for a container AND
    `tmux ls` returns zero sessions, the container is stopped+removed.
    """
    user_id, team_id = user_team
    sid, container_id = _seed_session(orchestrator, user_id, team_id)

    _put_redis_session(
        sid,
        container_id=container_id,
        user_id=user_id,
        team_id=team_id,
        last_activity=time.time() - 1000.0,
    )
    _psql_query(
        "INSERT INTO system_settings (key, value, updated_at) "
        "VALUES ('idle_timeout_seconds', '5'::jsonb, NOW()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()"
    )
    try:
        _wait_for_log(
            orchestrator,
            f"reaper_reaped_container container_id={container_id[:12]}",
            timeout_s=20.0,
        )
        # Container is gone (label scoped — looking for managed containers
        # owned by this user_team).
        ps = _docker(
            "ps", "-aq",
            "--filter", "label=perpetuity.managed=true",
            "--filter", f"label=user_id={user_id}",
            "--filter", f"label=team_id={team_id}",
            check=False,
        )
        assert not ps.stdout.strip(), (
            f"container should be removed; docker ps still lists: {ps.stdout!r}"
        )
    finally:
        _psql_query("DELETE FROM system_settings WHERE key = 'idle_timeout_seconds'")
        _delete_redis_session(sid)


# ---------------------------------------------------------------------------
# (5) Container survives when one of two sessions is reaped
# ---------------------------------------------------------------------------


def test_reaper_keeps_container_with_surviving_session(
    orchestrator: dict[str, str],
    user_team: tuple[str, str],
) -> None:
    """Two sessions in one container, one idle, one fresh: only the idle
    one is killed; the container keeps running for the survivor.
    """
    user_id, team_id = user_team
    sid_idle, container_id = _seed_session(orchestrator, user_id, team_id)
    sid_fresh, container_id2 = _seed_session(orchestrator, user_id, team_id)
    assert container_id == container_id2, (
        "second POST should reuse the (user,team) container"
    )

    # Back-date sid_idle, leave sid_fresh at now.
    _put_redis_session(
        sid_idle,
        container_id=container_id,
        user_id=user_id,
        team_id=team_id,
        last_activity=time.time() - 1000.0,
    )
    _put_redis_session(
        sid_fresh,
        container_id=container_id,
        user_id=user_id,
        team_id=team_id,
        last_activity=time.time(),
    )
    _psql_query(
        "INSERT INTO system_settings (key, value, updated_at) "
        "VALUES ('idle_timeout_seconds', '5'::jsonb, NOW()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()"
    )
    try:
        _wait_for_log(
            orchestrator,
            f"reaper_killed_session session_id={sid_idle}",
            timeout_s=15.0,
        )
        # Wait two more ticks to give the container reap pass a chance to
        # (incorrectly) fire — but it must not, because sid_fresh survives.
        time.sleep(3.0)
        logs = _logs(orchestrator)
        assert (
            f"reaper_reaped_container container_id={container_id[:12]}"
            not in logs
        ), "container should NOT be reaped while sid_fresh survives"

        # Container is still running.
        ps = _docker(
            "ps", "-q", "--filter", f"id={container_id}", check=False
        )
        assert ps.stdout.strip(), "container should still be running"

        # sid_fresh's record is still in Redis.
        assert _get_redis_session(sid_fresh) is not None
    finally:
        _delete_redis_session(sid_fresh)
        _delete_redis_session(sid_idle)
        _psql_query("DELETE FROM system_settings WHERE key = 'idle_timeout_seconds'")


# ---------------------------------------------------------------------------
# (6) idle_timeout_seconds resolver — happy path + fallback + invalid
# ---------------------------------------------------------------------------


@pytest.fixture
async def pg_pool():
    import asyncpg

    from orchestrator.config import settings as orch_settings

    pool = await asyncpg.create_pool(
        dsn=orch_settings.database_url,
        min_size=1,
        max_size=2,
        command_timeout=5.0,
    )
    if pool is None:
        pytest.skip("could not open asyncpg pool against compose db")
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def clean_idle_timeout(pg_pool):
    delete = "DELETE FROM system_settings WHERE key = $1"
    async with pg_pool.acquire() as conn:
        await conn.execute(delete, "idle_timeout_seconds")
    try:
        yield
    finally:
        async with pg_pool.acquire() as conn:
            await conn.execute(delete, "idle_timeout_seconds")


async def _upsert_idle_timeout(pool, value) -> None:
    sql = (
        "INSERT INTO system_settings (key, value, updated_at) "
        "VALUES ($1, $2::jsonb, NOW()) "
        "ON CONFLICT (key) DO UPDATE "
        "SET value = EXCLUDED.value, updated_at = NOW()"
    )
    async with pool.acquire() as conn:
        await conn.execute(sql, "idle_timeout_seconds", json.dumps(value))


async def test_resolve_idle_timeout_seconds_reads_system_settings(
    pg_pool,
    clean_idle_timeout,  # noqa: ARG001 - autouse-shaped cleanup fixture
    caplog: pytest.LogCaptureFixture,
) -> None:
    """system_settings.idle_timeout_seconds=7 → helper returns 7."""
    from orchestrator.volume_store import _resolve_idle_timeout_seconds

    await _upsert_idle_timeout(pg_pool, 7)

    caplog.set_level(logging.INFO, logger="orchestrator")
    value = await _resolve_idle_timeout_seconds(pg_pool)

    assert value == 7
    msgs = [r.getMessage() for r in caplog.records]
    assert any(
        "idle_timeout_seconds_resolved source=system_settings value=7" in m
        for m in msgs
    ), msgs
    assert not any("system_settings_lookup_failed" in m for m in msgs), msgs


async def test_resolve_idle_timeout_seconds_falls_back_when_missing(
    pg_pool,
    clean_idle_timeout,  # noqa: ARG001 - autouse-shaped cleanup fixture
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No row → fallback to settings.idle_timeout_seconds + RowMissing WARNING."""
    from orchestrator.config import settings as orch_settings
    from orchestrator.volume_store import _resolve_idle_timeout_seconds

    caplog.set_level(logging.DEBUG, logger="orchestrator")
    value = await _resolve_idle_timeout_seconds(pg_pool)

    assert value == orch_settings.idle_timeout_seconds
    msgs = [r.getMessage() for r in caplog.records]
    assert any(
        f"idle_timeout_seconds_resolved source=fallback value={value}" in m
        for m in msgs
    ), msgs
    assert any(
        "system_settings_lookup_failed key=idle_timeout_seconds reason=RowMissing"
        in m
        for m in msgs
    ), msgs


async def test_resolve_idle_timeout_seconds_falls_back_on_invalid_value(
    pg_pool,
    clean_idle_timeout,  # noqa: ARG001 - autouse-shaped cleanup fixture
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Non-int → InvalidValue WARNING + fallback."""
    from orchestrator.config import settings as orch_settings
    from orchestrator.volume_store import _resolve_idle_timeout_seconds

    await _upsert_idle_timeout(pg_pool, "banana")

    caplog.set_level(logging.DEBUG, logger="orchestrator")
    value = await _resolve_idle_timeout_seconds(pg_pool)

    assert value == orch_settings.idle_timeout_seconds
    msgs = [r.getMessage() for r in caplog.records]
    assert any(
        "system_settings_lookup_failed key=idle_timeout_seconds reason=InvalidValue"
        in m
        for m in msgs
    ), msgs


# ---------------------------------------------------------------------------
# (7) Reaper survives a transient scan_session_keys failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reaper_survives_redis_blip(monkeypatch) -> None:
    """A single raise from scan_session_keys must surface as a
    `reaper_tick_failed` WARNING but the next tick must still run.

    Drives `reaper_loop` directly (in-process) rather than booting an
    orchestrator container so we can patch scan_session_keys cleanly.
    Uses a fake AttachMap, fake Docker, and a tiny stub for the registry +
    pool so the loop's dependencies are all in-process.
    """
    import asyncio

    from orchestrator import attach_map as attach_map_mod
    from orchestrator import reaper as reaper_mod
    from orchestrator import redis_client as redis_mod
    from orchestrator import volume_store as vs_mod

    # Patch the interval to 0.05s so two ticks happen inside a small budget.
    monkeypatch.setattr(reaper_mod, "_REAPER_INTERVAL_MIN_SECONDS", 0)
    monkeypatch.setenv("REAPER_INTERVAL_SECONDS", "0")

    class FakeRegistry:
        def __init__(self) -> None:
            self.calls = 0

        async def scan_session_keys(self, *, count_hint: int = 100):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("simulated_redis_blip")
            # Empty AsyncIterator on subsequent ticks.
            if False:
                yield None
            return

        async def delete_session(self, sid: str) -> bool:
            return False

    registry = FakeRegistry()
    redis_mod.set_registry(registry)

    # _resolve_idle_timeout_seconds reads from a pool; stub it to avoid
    # any pg dependency for this in-process test.
    async def _stub_resolve(pool):  # noqa: ARG001 - signature mirrors real fn
        return 5

    monkeypatch.setattr(vs_mod, "_resolve_idle_timeout_seconds", _stub_resolve)
    monkeypatch.setattr(reaper_mod, "_resolve_idle_timeout_seconds", _stub_resolve)

    class FakePool:
        pass

    vs_mod.set_pool(FakePool())

    attach_map_mod.set_attach_map(attach_map_mod.AttachMap())

    class FakeApp:
        class state:
            docker = object()  # truthy; we never call into it because scan raises

    # Drive the loop manually for two ticks worth.
    task = asyncio.create_task(reaper_mod.reaper_loop(FakeApp()))
    try:
        await asyncio.sleep(0.5)  # ample for several ticks at min interval=1s
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # The first tick raised; the loop swallowed it and continued. By 0.5s
    # we expect at least one more (no-op) tick to have happened — the
    # FakeRegistry.calls counter proves scan_session_keys was hit ≥2x.
    assert registry.calls >= 2, (
        f"reaper did not survive the blip; scan calls={registry.calls}"
    )

    # Cleanup module globals so other tests don't see our fakes.
    redis_mod.set_registry(None)
    vs_mod.set_pool(None)
    attach_map_mod.set_attach_map(None)
