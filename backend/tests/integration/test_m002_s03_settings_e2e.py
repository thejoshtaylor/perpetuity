"""M002 / S03 / T04 — Admin settings PUT + dynamic workspace cap demo.

Slice S03's demo-truth statement: a system_admin can PUT
`/api/v1/admin/settings/workspace_volume_size_gb` to a smaller value than
the current default; existing workspace_volume rows keep their old cap
(D015 partial-apply rule) and the response surfaces a warnings list of
the affected rows; a freshly-signed-up user provisioned AFTER the PUT
gets the new (smaller) cap, kernel-enforced via ext4.

Flow against the live compose stack (sibling backend container — no
TestClient, no orchestrator swap):

  1. Log in as the FIRST_SUPERUSER seeded by prestart (admin@example.com,
     role=system_admin per init_db).
  2. Sign up alice; POST /api/v1/sessions → orchestrator provisions a
     4 GiB volume (system_settings is empty so the orchestrator's
     `_resolve_default_size_gb` falls back to settings.default_volume_size_gb
     baked into the compose orchestrator image as 4).
  3. As admin PUT workspace_volume_size_gb=1 → 200 with value=1, warnings
     non-empty containing alice's row (size_gb=4, usage_bytes=null).
     Backend stdout shows `system_setting_updated` and
     `system_setting_shrink_warnings_emitted`. Alice's row in DB still
     has size_gb=4 (partial-apply).
  4. Sign up bob; POST /api/v1/sessions → orchestrator provisions a 1 GiB
     volume (system_settings now governs). Orchestrator stdout shows
     `volume_size_gb_resolved source=system_settings value=1`.
  5. WS-attach as bob; df -BG reports ~1 GiB total; dd 1100 MB → ENOSPC.
  6. Idempotent PUT: re-PUT value=1; 200 with previous_value_present=true,
     warnings still list alice (size_gb=4 > 1 stays a warning).
  7. Negative cases: non-admin PUT 403; value=300 → 422 invalid_value_for_key;
     unknown key → 422 unknown_setting_key.
  8. Log redaction: alice/bob email and full_name never appear in compose
     logs (MEM134 invariant).

Test runs serial (-n 1). Wall-clock budget ≤ 60 s for the slice
acceptance — two fresh provisions (4 GiB + 1 GiB mkfs) plus admin API
calls and one dd is roughly 30 s.

How to run:

    docker compose build backend orchestrator
    docker build -f orchestrator/tests/fixtures/Dockerfile.test \\
        -t perpetuity/workspace:test orchestrator/workspace-image/
    docker compose up -d db redis orchestrator
    cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e \\
        tests/integration/test_m002_s03_settings_e2e.py -v
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
BACKEND_IMAGE = "backend:latest"
S05_REVISION = "s05_system_settings"

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
    """Log in an existing user (e.g. the FIRST_SUPERUSER seeded by prestart)."""
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


def _psql_one(sql: str) -> str:
    out = _docker(
        "exec", "perpetuity-db-1",
        "psql", "-U", "postgres", "-d", "app", "-A", "-t",
        "-c", sql, check=False,
    )
    return (out.stdout or "").strip()


def _user_id_from_db(email: str) -> str:
    val = _psql_one(f"SELECT id FROM \"user\" WHERE email = '{email}'")
    assert val, f"no user row for {email!r}"
    return val


def _delete_setting_row(key: str) -> None:
    """Wipe a system_settings row directly. Used to reset between test
    iterations so the test starts from a known empty state regardless of
    what other tests left behind."""
    _docker(
        "exec", "perpetuity-db-1",
        "psql", "-U", "postgres", "-d", "app", "-c",
        f"DELETE FROM system_settings WHERE key = '{key}'",
        check=False,
    )


def _backend_container_name() -> str:
    """Discover the sibling backend container spawned by the `backend_url`
    fixture. Naming policy: `perpetuity-backend-e2e-<8hex>`."""
    ps = _docker(
        "ps", "--format", "{{.Names}}",
        "--filter", "name=perpetuity-backend-e2e-",
        check=True, timeout=10,
    )
    names = [n for n in (ps.stdout or "").splitlines() if n.strip()]
    assert names, f"no sibling backend container found; got {names!r}"
    return names[0]


def _backend_logs(container_name: str) -> str:
    r = _docker("logs", container_name, check=False, timeout=15)
    return (r.stdout or "") + (r.stderr or "")


def _capture_compose_logs(*services: str) -> str:
    r = _compose(
        "logs", "--no-color", "--timestamps", *services,
        check=False, timeout=30,
    )
    return (r.stdout or "") + (r.stderr or "")


def _backend_image_has_s05() -> bool:
    """Probe `backend:latest` for the s05 alembic revision file. Per MEM147
    the image bakes /app/backend/app/alembic/versions/, so a stale image
    will fail to upgrade and the e2e will be misleading."""
    r = _docker(
        "run", "--rm", "--entrypoint", "ls", BACKEND_IMAGE,
        "/app/backend/app/alembic/versions/",
        check=False, timeout=15,
    )
    return f"{S05_REVISION}.py" in (r.stdout or "")


@pytest.fixture(autouse=True)
def _require_s05_baked() -> None:
    """Skip if the backend image lacks s05 — the test would fail in a
    confusing way at alembic upgrade. The skip message points the operator
    to the exact `docker compose build backend` command."""
    if not _backend_image_has_s05():
        pytest.skip(
            "backend:latest is missing the "
            f"{S05_REVISION!r} alembic revision — run "
            "`docker compose build backend` so the image bakes the "
            "current /app/backend/app/alembic/versions/ tree."
        )


@pytest.fixture(autouse=True)
def _wipe_system_settings_before() -> None:
    """Clear the workspace_volume_size_gb row before each test so the test
    starts from the documented empty-table state. The compose `db` service
    persists across tests via the `app-db-data` named volume; without this
    a previous run could leave a row that biases step 2's assertion."""
    _delete_setting_row("workspace_volume_size_gb")
    yield
    _delete_setting_row("workspace_volume_size_gb")


# ----- the test ----------------------------------------------------------


def test_m002_s03_admin_settings_partial_apply_e2e(  # noqa: PLR0915
    backend_url: str, request: pytest.FixtureRequest,
) -> None:
    """Slice S03 demo: admin PUT shrinks the workspace_volume_size_gb cap,
    existing rows keep their old cap (partial-apply), next signup gets
    the new cap, kernel-enforced via ext4."""
    suite_started = time.time()

    backend_container = _backend_container_name()

    # ----- cleanup state -----------------------------------------------
    spawned_workspace_label = "perpetuity.managed=true"
    cleanup_state: dict[str, object] = {
        "alice": None,
        "bob": None,
        "alice_session": None,
        "bob_session": None,
    }

    def _cleanup() -> None:
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

    # ----- Step 1: log in as the seeded system_admin -------------------
    admin_email = "admin@example.com"
    admin_password = "changethis"
    admin_cookies = _login_only(
        backend_url, email=admin_email, password=admin_password
    )
    admin_user_id = _user_id_from_db(admin_email)
    admin_role = _psql_one(
        f"SELECT role FROM \"user\" WHERE email = '{admin_email}'"
    )
    assert admin_role == "system_admin", (
        f"FIRST_SUPERUSER role expected system_admin, got {admin_role!r}"
    )

    # ----- Step 2: alice signs up; provisions a 4 GiB volume (fallback) -
    suffix_a = uuid.uuid4().hex[:8]
    alice_email = f"m002-s03-alice-{suffix_a}@example.com"
    alice_password = "Sup3rs3cret-alice"
    alice_full_name = f"Alice {suffix_a}"
    alice_cookies = _signup_login(
        backend_url,
        email=alice_email, password=alice_password, full_name=alice_full_name,
    )
    alice_team = _personal_team_id(backend_url, alice_cookies)
    alice_user_id = _user_id_from_db(alice_email)

    alice_session = _create_session(backend_url, alice_cookies, alice_team)
    cleanup_state["alice_session"] = alice_session
    assert uuid.UUID(alice_session)

    alice_ps = _docker(
        "ps", "-q",
        "--filter", f"label=user_id={alice_user_id}",
        "--filter", f"label=team_id={alice_team}",
        check=True, timeout=10,
    )
    alice_container_id = (alice_ps.stdout or "").strip().splitlines()[0]
    assert alice_container_id, "no alice workspace container found by label"
    cleanup_state["alice"] = alice_container_id

    alice_size_gb_str = _psql_one(
        "SELECT size_gb FROM workspace_volume "
        f"WHERE user_id = '{alice_user_id}' AND team_id = '{alice_team}'"
    )
    assert int(alice_size_gb_str) == 4, (
        f"alice (system_settings empty → fallback) expected size_gb=4, "
        f"got {alice_size_gb_str!r}"
    )

    # ----- Step 3: admin PUT to 1 → partial-apply warnings -------------
    with httpx.Client(
        base_url=backend_url, timeout=30.0, cookies=admin_cookies
    ) as c:
        r = c.put(
            "/api/v1/admin/settings/workspace_volume_size_gb",
            json={"value": 1},
        )
    assert r.status_code == 200, f"admin PUT: {r.status_code} {r.text}"
    body = r.json()
    assert body["key"] == "workspace_volume_size_gb"
    assert body["value"] == 1
    warnings = body.get("warnings") or []
    assert isinstance(warnings, list) and len(warnings) >= 1, (
        f"expected non-empty warnings after shrink, got {warnings!r}"
    )
    alice_warning = next(
        (w for w in warnings if w["user_id"] == alice_user_id), None
    )
    assert alice_warning is not None, (
        f"alice's warning row not in payload; warnings={warnings!r}"
    )
    assert alice_warning["team_id"] == alice_team
    assert alice_warning["size_gb"] == 4
    assert alice_warning["usage_bytes"] is None, (
        "usage_bytes should be null in S03 — backend has no workspace mount"
    )

    # Backend stdout should carry the slice's observability taxonomy keys.
    # `docker compose logs` doesn't always flush instantly; give it a moment.
    time.sleep(1.0)
    backend_log = _backend_logs(backend_container)
    assert (
        f"system_setting_updated actor_id={admin_user_id} "
        f"key=workspace_volume_size_gb previous_value_present=false"
        in backend_log
    ), (
        "missing `system_setting_updated ... previous_value_present=false` "
        f"line in backend logs; tail:\n{backend_log[-2000:]}"
    )
    assert (
        f"system_setting_shrink_warnings_emitted "
        f"key=workspace_volume_size_gb actor_id={admin_user_id} "
        f"affected={len(warnings)}"
        in backend_log
    ), (
        "missing `system_setting_shrink_warnings_emitted` line in backend "
        f"logs; tail:\n{backend_log[-2000:]}"
    )

    # Partial-apply: alice's row is unchanged.
    alice_size_after_put = _psql_one(
        "SELECT size_gb FROM workspace_volume "
        f"WHERE user_id = '{alice_user_id}' AND team_id = '{alice_team}'"
    )
    assert int(alice_size_after_put) == 4, (
        f"D015 partial-apply violated — alice's size_gb changed to "
        f"{alice_size_after_put!r}"
    )

    # ----- Step 4: bob signs up; provisions a 1 GiB volume (system_settings) -
    # Note: alice's earlier provision logged
    # `volume_size_gb_resolved source=fallback value=4` (system_settings was
    # empty at the time), so any later match for `source=system_settings
    # value=1` must come from bob's provision specifically — no ambiguity.

    suffix_b = uuid.uuid4().hex[:8]
    bob_email = f"m002-s03-bob-{suffix_b}@example.com"
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
    cleanup_state["bob_session"] = bob_session
    assert uuid.UUID(bob_session)

    bob_ps = _docker(
        "ps", "-q",
        "--filter", f"label=user_id={bob_user_id}",
        "--filter", f"label=team_id={bob_team}",
        check=True, timeout=10,
    )
    bob_container_id = (bob_ps.stdout or "").strip().splitlines()[0]
    assert bob_container_id, "no bob workspace container found by label"
    cleanup_state["bob"] = bob_container_id

    bob_size_gb_str = _psql_one(
        "SELECT size_gb FROM workspace_volume "
        f"WHERE user_id = '{bob_user_id}' AND team_id = '{bob_team}'"
    )
    assert int(bob_size_gb_str) == 1, (
        f"bob (system_settings governs) expected size_gb=1, "
        f"got {bob_size_gb_str!r}"
    )

    time.sleep(1.0)
    orch_log_full = _capture_compose_logs("orchestrator")
    assert (
        "volume_size_gb_resolved source=system_settings value=1"
        in orch_log_full
    ), (
        "orchestrator should log resolve source=system_settings value=1 "
        f"for bob's provision; tail:\n{orch_log_full[-2000:]}"
    )

    # ----- Step 5: WS-attach as bob; df + dd hits ENOSPC ---------------
    ws_base = _http_to_ws(backend_url)
    bob_ws_url = f"{ws_base}/api/v1/ws/terminal/{bob_session}"
    bob_cookie_header = "; ".join(f"{n}={v}" for n, v in bob_cookies.items())

    async def _bob_dd_and_df() -> str:
        async with aconnect_ws(
            bob_ws_url, headers={"Cookie": bob_cookie_header}
        ) as ws:
            first_text = await asyncio.wait_for(ws.receive_text(), timeout=15.0)
            first_frame = json.loads(first_text)
            assert first_frame["type"] == "attach", (
                f"expected attach frame, got {first_frame!r}"
            )
            target = f"/workspaces/{bob_team}/big"
            end_token = uuid.uuid4().hex
            df_token = uuid.uuid4().hex
            # printf-split sentinels so the literal substring isn't echoed
            # by tmux on the input line — same trick as S02/T04.
            cmd = (
                f"df -BG /workspaces/{bob_team} | tail -n +2; "
                f"printf 'D%sFOK_%s\\n' F {df_token}; "
                f"dd if=/dev/zero of={target} bs=1M count=1100 "
                f"2>/tmp/dd.err; "
                f"printf 'DDRC=%d\\n' $?; "
                f"tail -n 5 /tmp/dd.err; "
                f"printf 'EN%sOK_%s\\n' D {end_token}\n"
            )
            await ws.send_text(_input_frame(cmd))
            return await _drain_data(
                ws, timeout_s=180.0,
                until_substring=f"ENDOK_{end_token}",
            )

    bob_buf = asyncio.run(_bob_dd_and_df())

    df_match = re.search(
        r"\s(\d+)G\s+(\d+)G\s+(\d+)G\s+(\d+)%\s+/workspaces", bob_buf
    )
    assert df_match, f"could not parse df output for bob; saw:\n{bob_buf}"
    bob_total_gb = int(df_match.group(1))
    # ext4 metadata overhead: 1 GiB raw → df reports ~1G total, occasionally
    # 0G after rounding (du-style integer rounding on a near-empty fs).
    assert bob_total_gb <= 1, (
        f"bob df total expected ≤1G (admin-driven 1 GiB cap), got "
        f"{bob_total_gb}G — admin shrink may not be biting"
    )

    assert "no space left on device" in bob_buf.lower(), (
        f"bob dd should hit ENOSPC at the 1 GiB admin-driven cap; "
        f"saw:\n{bob_buf}"
    )
    rc_match = re.search(r"DDRC=(\d+)", bob_buf)
    assert rc_match and int(rc_match.group(1)) != 0, (
        f"dd should exit non-zero (ENOSPC); rc={rc_match!r} buf={bob_buf!r}"
    )

    # ----- Step 6: idempotent PUT --------------------------------------
    with httpx.Client(
        base_url=backend_url, timeout=30.0, cookies=admin_cookies
    ) as c:
        r2 = c.put(
            "/api/v1/admin/settings/workspace_volume_size_gb",
            json={"value": 1},
        )
    assert r2.status_code == 200, f"idempotent PUT: {r2.status_code} {r2.text}"
    body2 = r2.json()
    assert body2["value"] == 1
    # Per the slice plan: warnings are emitted whenever a row has size_gb >
    # new_value, regardless of whether the value actually changed. Alice
    # still has size_gb=4 > 1, so the list stays non-empty.
    warnings2 = body2.get("warnings") or []
    assert any(
        w["user_id"] == alice_user_id and w["size_gb"] == 4
        for w in warnings2
    ), (
        f"idempotent PUT should still warn about alice's size_gb=4 row; "
        f"got {warnings2!r}"
    )

    time.sleep(1.0)
    backend_log_after = _backend_logs(backend_container)
    assert (
        f"system_setting_updated actor_id={admin_user_id} "
        f"key=workspace_volume_size_gb previous_value_present=true"
        in backend_log_after
    ), (
        "missing `previous_value_present=true` line on the second PUT; "
        f"tail:\n{backend_log_after[-2000:]}"
    )

    # ----- Step 7: negative cases --------------------------------------
    # Non-admin (alice) PUT → 403.
    with httpx.Client(
        base_url=backend_url, timeout=15.0, cookies=alice_cookies
    ) as c:
        r403 = c.put(
            "/api/v1/admin/settings/workspace_volume_size_gb",
            json={"value": 2},
        )
    assert r403.status_code == 403, (
        f"non-admin PUT should be 403, got {r403.status_code} {r403.text}"
    )

    # Out-of-range value → 422 invalid_value_for_key.
    with httpx.Client(
        base_url=backend_url, timeout=15.0, cookies=admin_cookies
    ) as c:
        r422_range = c.put(
            "/api/v1/admin/settings/workspace_volume_size_gb",
            json={"value": 300},
        )
    assert r422_range.status_code == 422, (
        f"out-of-range PUT expected 422, got {r422_range.status_code} "
        f"{r422_range.text}"
    )
    detail_range = r422_range.json().get("detail") or {}
    # FastAPI wraps detail dicts as-is for HTTPException(detail={...}).
    assert detail_range.get("detail") == "invalid_value_for_key", (
        f"422 body shape unexpected: {r422_range.json()!r}"
    )

    # Unknown key → 422 unknown_setting_key.
    with httpx.Client(
        base_url=backend_url, timeout=15.0, cookies=admin_cookies
    ) as c:
        r422_unknown = c.put(
            "/api/v1/admin/settings/bogus_key",
            json={"value": 1},
        )
    assert r422_unknown.status_code == 422, (
        f"unknown key PUT expected 422, got {r422_unknown.status_code} "
        f"{r422_unknown.text}"
    )
    detail_unknown = r422_unknown.json().get("detail") or {}
    assert detail_unknown.get("detail") == "unknown_setting_key", (
        f"unknown-key body shape unexpected: {r422_unknown.json()!r}"
    )
    assert detail_unknown.get("key") == "bogus_key"

    # ----- Tear down sessions ------------------------------------------
    a_del = _delete_session(backend_url, alice_cookies, alice_session)
    b_del = _delete_session(backend_url, bob_cookies, bob_session)
    assert a_del == 200, f"DELETE alice: {a_del}"
    assert b_del == 200, f"DELETE bob: {b_del}"

    # ----- Step 8: log redaction sweep ---------------------------------
    backend_log_final = _backend_logs(backend_container)
    orch_log_final = _capture_compose_logs("orchestrator")
    log_blob = backend_log_final + "\n" + orch_log_final

    for sentinel, label in (
        (alice_email, "alice email"),
        (alice_full_name, "alice full_name"),
        (bob_email, "bob email"),
        (bob_full_name, "bob full_name"),
    ):
        assert sentinel not in log_blob, (
            f"redaction sweep: {label} ({sentinel!r}) leaked into logs"
        )

    # Smoke: the new slice's observability taxonomy actually fired.
    for key in (
        "system_setting_updated",
        "system_setting_shrink_warnings_emitted",
        "volume_size_gb_resolved",
    ):
        assert key in log_blob, (
            f"observability taxonomy regression: {key!r} not seen in logs"
        )

    elapsed = time.time() - suite_started
    # Slice budget is ≤60 s; we tolerate up to 180 s defensively because
    # cold compose + two fresh ext4 mkfs runs can stretch on slow hosts.
    assert elapsed < 180.0, (
        f"e2e suite took {elapsed:.1f}s — far over the 60s slice budget"
    )
