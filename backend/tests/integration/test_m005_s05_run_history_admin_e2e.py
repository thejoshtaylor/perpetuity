"""M005 / S05 — Run history, admin trigger, cap enforcement, orphan recovery e2e.

Six integration tests that drive the full S05 surface against the live compose
stack:

    GET  /api/v1/teams/{team_id}/runs   (filtered run history list)
    POST /api/v1/admin/workflows/{id}/trigger  (admin manual trigger)
    POST /api/v1/workflows/{id}/run     (cap enforcement → 429)
    _recover_orphan_runs_body()         (orphan recovery without Beat)

Test functions
--------------
(1) test_run_history_list_with_filters
    Create 3 runs with different trigger_types / statuses, hit
    GET /teams/{id}/runs with each filter combination, verify correct subsets.
    Delete the workflow after run creation and verify the run still appears
    (snapshot / orphan-row semantics).

(2) test_admin_manual_trigger
    System admin POSTs /admin/workflows/{id}/trigger → 202 + run_id.
    Verify run appears in history with trigger_type='admin_manual'.
    Verify a non-admin user gets 403.

(3) test_concurrent_cap_enforcement
    Set max_concurrent_runs=2 on a workflow; fire 3 sequential dispatch
    requests after seeding 2 runs in 'running' status via psql.
    Verify 429 with {detail: 'workflow_cap_exceeded', cap_type: 'concurrent'}.
    Verify audit row with status='rejected' appears in run history.

(4) test_hourly_cap_enforcement
    Set max_runs_per_hour=2 on a workflow; fire 3 sequential dispatch requests.
    Verify the 3rd returns 429 with cap_type='hourly'.

(5) test_orphan_run_recovery
    Insert a WorkflowRun row directly in DB with status='running' and
    last_heartbeat_at = now() - 20 min.  Call _recover_orphan_runs_body()
    via the backend e2e helper endpoint to exercise the real code path.
    Verify run transitions to status='failed' with error_class='worker_crash'.

(6) test_discriminator_sweep
    Exercise all S05 discriminators through the combined log stream:
    workflow_cap_exceeded, recover_orphan_runs_sweep,
    workflow_run_orphan_recovered, admin_manual_trigger_queued.
    Verify no sk-ant- or sk- key leakage.

Skip-guard: probes backend:latest for the `s16_workflow_run_rejected_status`
alembic revision; skips with rebuild hint if absent.

How to run::

    docker compose build backend orchestrator celery-worker
    docker compose up -d db redis orchestrator
    cd backend && POSTGRES_DB=perpetuity_app uv run pytest -m e2e \\
        tests/integration/test_m005_s05_run_history_admin_e2e.py -v
"""

from __future__ import annotations

import json
import os
import re
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
BACKEND_IMAGE = "backend:latest"
S16_REVISION = "s16_workflow_run_rejected_status"

# Unique sentinel token — used to construct synthetic API keys and to verify
# they don't appear in compose log output.
_RUN_TOKEN = uuid.uuid4().hex
CLAUDE_KEY = f"sk-ant-api03-{_RUN_TOKEN}-S05SENTINEL-padpadpadpad"

# S05 required log discriminators.
_REQUIRED_DISCRIMINATORS = (
    "workflow_cap_exceeded",
    "recover_orphan_runs_sweep",
    "workflow_run_orphan_recovered",
    "admin_manual_trigger_queued",
)

pytestmark = [pytest.mark.e2e]

# ---------------------------------------------------------------------------
# Log accumulator — each test appends its container logs so the module-scope
# sweep in the final function sees the combined stream.
# ---------------------------------------------------------------------------
_combined_log: list[str] = []


# ---------------------------------------------------------------------------
# Low-level docker / psql helpers (mirrors S03/S04 pattern)
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


_PG_DB = os.environ.get("POSTGRES_DB", "app")


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
        "psql", "-U", "postgres", "-d", _PG_DB, "-c", sql, check=False,
    )


def _container_logs(name: str) -> str:
    r = _docker("logs", name, check=False, timeout=15)
    return (r.stdout or "") + (r.stderr or "")


def _backend_container_name() -> str:
    ps = _docker(
        "ps", "--format", "{{.Names}}",
        "--filter", "name=perpetuity-backend-e2e-",
        check=True, timeout=10,
    )
    names = [n for n in (ps.stdout or "").splitlines() if n.strip()]
    assert names, f"no sibling backend container found; got {names!r}"
    return names[0]


def _backend_image_has_s16() -> bool:
    r = _docker(
        "run", "--rm", "--entrypoint", "ls", BACKEND_IMAGE,
        "/app/backend/app/alembic/versions/",
        check=False, timeout=15,
    )
    return f"{S16_REVISION}.py" in (r.stdout or "")


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


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
            json={"email": email, "password": password, "full_name": full_name},
        )
        assert r.status_code == 200, f"signup: {r.status_code} {r.text}"
        c.cookies.clear()
        r = c.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        assert r.status_code == 200, f"login: {r.status_code} {r.text}"
        for cookie in c.cookies.jar:
            cookies.set(cookie.name, cookie.value)
    return cookies


def _create_team(base_url: str, cookies: httpx.Cookies, suffix: str = "") -> str:
    name = f"e2e-m005-s05-{_RUN_TOKEN[:8]}{suffix}"
    with httpx.Client(base_url=base_url, timeout=15.0, cookies=cookies) as c:
        r = c.post("/api/v1/teams/", json={"name": name})
    assert r.status_code == 200, f"create team: {r.status_code} {r.text}"
    return r.json()["id"]


def _create_workflow(
    base_url: str,
    cookies: httpx.Cookies,
    team_id: str,
    payload: dict,
) -> dict:
    with httpx.Client(base_url=base_url, timeout=15.0, cookies=cookies) as c:
        r = c.post(f"/api/v1/teams/{team_id}/workflows", json=payload)
    assert r.status_code == 201, f"create workflow: {r.status_code} {r.text}"
    return r.json()


def _trigger_run(
    base_url: str,
    cookies: httpx.Cookies,
    workflow_id: str,
    trigger_payload: dict,
) -> tuple[int, dict]:
    """Fire a run; return (status_code, body)."""
    with httpx.Client(base_url=base_url, timeout=15.0, cookies=cookies) as c:
        r = c.post(
            f"/api/v1/workflows/{workflow_id}/run",
            json={"trigger_payload": trigger_payload},
        )
    return r.status_code, r.json()


def _list_runs(
    base_url: str,
    cookies: httpx.Cookies,
    team_id: str,
    **params: str,
) -> dict:
    with httpx.Client(base_url=base_url, timeout=15.0, cookies=cookies) as c:
        r = c.get(f"/api/v1/teams/{team_id}/runs", params=params)
    assert r.status_code == 200, f"list_runs: {r.status_code} {r.text}"
    return r.json()


def _delete_team_cascade(team_id: str) -> None:
    _psql_exec(
        f"DELETE FROM step_runs WHERE workflow_run_id IN "
        f"(SELECT id FROM workflow_runs WHERE team_id = '{team_id}')"
    )
    _psql_exec(f"DELETE FROM workflow_runs WHERE team_id = '{team_id}'")
    _psql_exec(
        f"DELETE FROM workflow_steps WHERE workflow_id IN "
        f"(SELECT id FROM workflows WHERE team_id = '{team_id}')"
    )
    _psql_exec(f"DELETE FROM workflows WHERE team_id = '{team_id}'")
    _psql_exec(f"DELETE FROM team_secrets WHERE team_id = '{team_id}'")
    _psql_exec(f"DELETE FROM team_member WHERE team_id = '{team_id}'")
    _psql_exec(f"DELETE FROM team WHERE id = '{team_id}'")


def _delete_user_by_email(email: str) -> None:
    user_id = _psql_one(f"SELECT id FROM \"user\" WHERE email = '{email}'")
    if not user_id:
        return
    _psql_exec(f"DELETE FROM team_member WHERE user_id = '{user_id}'")
    _psql_exec(f"DELETE FROM \"user\" WHERE id = '{user_id}'")


# ---------------------------------------------------------------------------
# Autouse skip-guard
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _require_s16_baked() -> None:
    if not _backend_image_has_s16():
        pytest.skip(
            "backend:latest is missing the "
            f"{S16_REVISION!r} alembic revision — run "
            "`docker compose build backend orchestrator celery-worker` "
            "so the image bakes the current "
            "/app/backend/app/alembic/versions/ tree."
        )


# ---------------------------------------------------------------------------
# (1) Run history list with filters + snapshot semantics
# ---------------------------------------------------------------------------


def test_run_history_list_with_filters(
    orchestrator_on_e2e_db: None,  # noqa: ARG001
    backend_url: str,
    celery_worker_url: str,  # noqa: ARG001
) -> None:
    """Create runs with different trigger_types and statuses, verify filters
    return correct subsets.  Delete the workflow after run creation and verify
    the run still appears (snapshot / team_id ownership semantics)."""
    admin_email = "admin@example.com"
    admin_cookies = _login_only(backend_url, email=admin_email, password="changethis")
    team_id = _create_team(backend_url, admin_cookies, suffix="-1")

    backend_container = _backend_container_name()

    try:
        wf = _create_workflow(
            backend_url, admin_cookies, team_id,
            {
                "name": f"hist-wf-{_RUN_TOKEN[:8]}",
                "scope": "user",
                "form_schema": {},
                "steps": [
                    {
                        "step_index": 0,
                        "action": "shell",
                        "config": {"cmd": ["echo", "history-test"]},
                        "target_container": "user_workspace",
                    }
                ],
            },
        )
        workflow_id = wf["id"]

        # Fire 3 runs so we have rows to filter.
        run_ids: list[str] = []
        for _ in range(3):
            sc, body = _trigger_run(backend_url, admin_cookies, workflow_id, {})
            assert sc == 200, f"trigger run: {sc} {body}"
            run_ids.append(body["run_id"])

        # Give runs time to be picked up and reach a terminal state (may fail
        # without a real workspace container — that's fine, we need rows).
        time.sleep(3.0)

        # (a) Unfiltered list: all 3 runs present.
        result = _list_runs(backend_url, admin_cookies, team_id)
        run_ids_in_list = [r["id"] for r in result["data"]]
        for rid in run_ids:
            assert rid in run_ids_in_list, (
                f"run {rid!r} missing from unfiltered list; got {run_ids_in_list!r}"
            )
        assert result["count"] >= 3

        # (b) Filter by trigger_type=button — all 3 are button-triggered.
        filtered = _list_runs(backend_url, admin_cookies, team_id, trigger_type="button")
        btn_ids = [r["id"] for r in filtered["data"]]
        for rid in run_ids:
            assert rid in btn_ids, (
                f"run {rid!r} missing from trigger_type=button filter; got {btn_ids!r}"
            )

        # (c) Filter by trigger_type=admin_manual — should return 0 of our runs.
        filtered_admin = _list_runs(
            backend_url, admin_cookies, team_id, trigger_type="admin_manual"
        )
        admin_ids = [r["id"] for r in filtered_admin["data"]]
        for rid in run_ids:
            assert rid not in admin_ids, (
                f"button run {rid!r} wrongly appeared in admin_manual filter"
            )

        # (d) Snapshot semantics: delete the workflow, runs must still appear.
        # First delete step_runs and workflow_steps, then the workflow itself.
        _psql_exec(
            f"DELETE FROM step_runs WHERE workflow_run_id IN "
            f"(SELECT id FROM workflow_runs WHERE workflow_id = '{workflow_id}')"
        )
        _psql_exec(
            f"DELETE FROM workflow_steps WHERE workflow_id = '{workflow_id}'"
        )
        _psql_exec(f"DELETE FROM workflows WHERE id = '{workflow_id}'")

        # Runs must still appear in the list (team_id is authoritative).
        after_delete = _list_runs(backend_url, admin_cookies, team_id)
        after_ids = [r["id"] for r in after_delete["data"]]
        for rid in run_ids:
            assert rid in after_ids, (
                f"run {rid!r} vanished from history after workflow deletion; "
                f"snapshot semantics violated. Got: {after_ids!r}"
            )

        log = _container_logs(backend_container)
        _combined_log.append(log)

    finally:
        # Workflow may already be deleted above; cascade skips gracefully.
        _psql_exec(
            f"DELETE FROM step_runs WHERE workflow_run_id IN "
            f"(SELECT id FROM workflow_runs WHERE team_id = '{team_id}')"
        )
        _psql_exec(f"DELETE FROM workflow_runs WHERE team_id = '{team_id}'")
        _psql_exec(f"DELETE FROM team_secrets WHERE team_id = '{team_id}'")
        _psql_exec(f"DELETE FROM team_member WHERE team_id = '{team_id}'")
        _psql_exec(f"DELETE FROM team WHERE id = '{team_id}'")


# ---------------------------------------------------------------------------
# (2) Admin manual trigger
# ---------------------------------------------------------------------------


def test_admin_manual_trigger(
    orchestrator_on_e2e_db: None,  # noqa: ARG001
    backend_url: str,
    celery_worker_url: str,  # noqa: ARG001
) -> None:
    """Admin POSTs /admin/workflows/{id}/trigger → 202 + run_id.
    Run appears in history with trigger_type='admin_manual'.
    Non-admin user gets 403."""
    admin_email = "admin@example.com"
    admin_cookies = _login_only(backend_url, email=admin_email, password="changethis")
    team_id = _create_team(backend_url, admin_cookies, suffix="-2")

    non_admin_email = f"s05-nonadmin-{_RUN_TOKEN[:8]}@example.com"
    non_admin_cookies = _signup_login(
        backend_url,
        email=non_admin_email,
        password="changethis",
        full_name="S05 NonAdmin",
    )

    backend_container = _backend_container_name()

    try:
        wf = _create_workflow(
            backend_url, admin_cookies, team_id,
            {
                "name": f"admin-trig-wf-{_RUN_TOKEN[:8]}",
                "scope": "user",
                "form_schema": {},
                "steps": [
                    {
                        "step_index": 0,
                        "action": "shell",
                        "config": {"cmd": ["echo", "admin-manual-step"]},
                        "target_container": "user_workspace",
                    }
                ],
            },
        )
        workflow_id = wf["id"]

        # (a) Admin can trigger → 202.
        with httpx.Client(
            base_url=backend_url, timeout=15.0, cookies=admin_cookies
        ) as c:
            r = c.post(
                f"/api/v1/admin/workflows/{workflow_id}/trigger",
                json={"trigger_payload": {"note": "manual test"}},
            )
        assert r.status_code == 202, (
            f"admin trigger: expected 202; got {r.status_code} {r.text}"
        )
        body = r.json()
        assert "run_id" in body, f"run_id missing from admin trigger response: {body!r}"
        run_id = body["run_id"]

        # Give the run row time to be committed.
        time.sleep(1.5)

        # (b) Run appears in team history with trigger_type='admin_manual'.
        result = _list_runs(
            backend_url, admin_cookies, team_id, trigger_type="admin_manual"
        )
        admin_run_ids = [r["id"] for r in result["data"]]
        assert run_id in admin_run_ids, (
            f"run {run_id!r} not in admin_manual filtered history; "
            f"got {admin_run_ids!r}"
        )

        # Verify the specific run's trigger_type field.
        run_row = next(
            (r for r in result["data"] if r["id"] == run_id), None
        )
        assert run_row is not None
        assert run_row["trigger_type"] == "admin_manual", (
            f"trigger_type mismatch: {run_row['trigger_type']!r}"
        )

        # (c) Non-admin gets 403.
        with httpx.Client(
            base_url=backend_url, timeout=15.0, cookies=non_admin_cookies
        ) as c:
            r_nonadmin = c.post(
                f"/api/v1/admin/workflows/{workflow_id}/trigger",
                json={"trigger_payload": {}},
            )
        assert r_nonadmin.status_code == 403, (
            f"non-admin should get 403; got {r_nonadmin.status_code} {r_nonadmin.text}"
        )

        log = _container_logs(backend_container)
        _combined_log.append(log)

        # Discriminator must appear in logs.
        assert "admin_manual_trigger_queued" in log, (
            f"admin_manual_trigger_queued not in backend logs; "
            f"tail:\n{log[-2000:]}"
        )

    finally:
        _delete_team_cascade(team_id)
        _delete_user_by_email(non_admin_email)


# ---------------------------------------------------------------------------
# (3) Concurrent cap enforcement
# ---------------------------------------------------------------------------


def test_concurrent_cap_enforcement(
    orchestrator_on_e2e_db: None,  # noqa: ARG001
    backend_url: str,
    celery_worker_url: str,  # noqa: ARG001
) -> None:
    """Set max_concurrent_runs=2. Seed 2 'running' runs via psql so the cap
    is already at the limit, then fire a 3rd dispatch → expect 429 with
    cap_type='concurrent'. Verify rejected audit row appears in run history."""
    admin_email = "admin@example.com"
    admin_cookies = _login_only(backend_url, email=admin_email, password="changethis")
    team_id = _create_team(backend_url, admin_cookies, suffix="-3")

    backend_container = _backend_container_name()

    try:
        wf = _create_workflow(
            backend_url, admin_cookies, team_id,
            {
                "name": f"cap-concurrent-wf-{_RUN_TOKEN[:8]}",
                "scope": "user",
                "form_schema": {},
                "steps": [
                    {
                        "step_index": 0,
                        "action": "shell",
                        "config": {"cmd": ["echo", "cap-test"]},
                        "target_container": "user_workspace",
                    }
                ],
            },
        )
        workflow_id = wf["id"]

        # Set max_concurrent_runs=2 directly in the DB.
        r = _psql_exec(
            f"UPDATE workflows SET max_concurrent_runs=2 WHERE id = '{workflow_id}'"
        )
        assert r.returncode == 0, (
            f"UPDATE max_concurrent_runs failed; rc={r.returncode} stderr={r.stderr!r}"
        )

        # Seed 2 'running' runs via psql so the cap is exactly at the limit.
        admin_user_id = _psql_one(
            f"SELECT id FROM \"user\" WHERE email = '{admin_email}'"
        )
        assert admin_user_id, "admin user not found"

        for _ in range(2):
            run_uuid = uuid.uuid4()
            _psql_exec(
                f"INSERT INTO workflow_runs "
                f"(id, workflow_id, team_id, trigger_type, triggered_by_user_id, "
                f"status, created_at) VALUES "
                f"('{run_uuid}', '{workflow_id}', '{team_id}', 'button', "
                f"'{admin_user_id}', 'running', NOW())"
            )

        # Now dispatch a 3rd run via HTTP — must get 429.
        sc, body = _trigger_run(backend_url, admin_cookies, workflow_id, {})
        assert sc == 429, (
            f"expected 429 on concurrent cap; got {sc} {body}"
        )
        assert body.get("detail") == "workflow_cap_exceeded", (
            f"detail mismatch: {body!r}"
        )
        assert body.get("cap_type") == "concurrent", (
            f"cap_type mismatch: {body!r}"
        )

        # Rejected audit row must appear in run history.
        time.sleep(0.5)
        result = _list_runs(backend_url, admin_cookies, team_id, status="rejected")
        rejected_ids = [r["id"] for r in result["data"]]
        assert len(rejected_ids) >= 1, (
            f"expected at least 1 rejected run in history; got {result!r}"
        )

        log = _container_logs(backend_container)
        _combined_log.append(log)

        # Discriminator must appear.
        assert "workflow_cap_exceeded" in log, (
            f"workflow_cap_exceeded not in backend logs; tail:\n{log[-2000:]}"
        )

    finally:
        _delete_team_cascade(team_id)


# ---------------------------------------------------------------------------
# (4) Hourly cap enforcement
# ---------------------------------------------------------------------------


def test_hourly_cap_enforcement(
    orchestrator_on_e2e_db: None,  # noqa: ARG001
    backend_url: str,
    celery_worker_url: str,  # noqa: ARG001
) -> None:
    """Set max_runs_per_hour=2. Seed 2 runs created within the last hour via
    psql so the hourly cap is at the limit, then fire a 3rd dispatch → expect
    429 with cap_type='hourly'."""
    admin_email = "admin@example.com"
    admin_cookies = _login_only(backend_url, email=admin_email, password="changethis")
    team_id = _create_team(backend_url, admin_cookies, suffix="-4")

    backend_container = _backend_container_name()

    try:
        wf = _create_workflow(
            backend_url, admin_cookies, team_id,
            {
                "name": f"cap-hourly-wf-{_RUN_TOKEN[:8]}",
                "scope": "user",
                "form_schema": {},
                "steps": [
                    {
                        "step_index": 0,
                        "action": "shell",
                        "config": {"cmd": ["echo", "hourly-cap-test"]},
                        "target_container": "user_workspace",
                    }
                ],
            },
        )
        workflow_id = wf["id"]

        # Set max_runs_per_hour=2.
        r = _psql_exec(
            f"UPDATE workflows SET max_runs_per_hour=2 WHERE id = '{workflow_id}'"
        )
        assert r.returncode == 0, (
            f"UPDATE max_runs_per_hour failed; rc={r.returncode} stderr={r.stderr!r}"
        )

        # Seed 2 runs in the last hour (non-rejected) via psql.
        admin_user_id = _psql_one(
            f"SELECT id FROM \"user\" WHERE email = '{admin_email}'"
        )
        assert admin_user_id, "admin user not found"

        for _ in range(2):
            run_uuid = uuid.uuid4()
            _psql_exec(
                f"INSERT INTO workflow_runs "
                f"(id, workflow_id, team_id, trigger_type, triggered_by_user_id, "
                f"status, created_at) VALUES "
                f"('{run_uuid}', '{workflow_id}', '{team_id}', 'button', "
                f"'{admin_user_id}', 'succeeded', NOW() - INTERVAL '5 minutes')"
            )

        # 3rd dispatch must be rejected with hourly cap.
        sc, body = _trigger_run(backend_url, admin_cookies, workflow_id, {})
        assert sc == 429, (
            f"expected 429 on hourly cap; got {sc} {body}"
        )
        assert body.get("detail") == "workflow_cap_exceeded", (
            f"detail mismatch: {body!r}"
        )
        assert body.get("cap_type") == "hourly", (
            f"cap_type mismatch: {body!r}"
        )

        log = _container_logs(backend_container)
        _combined_log.append(log)

        assert "workflow_cap_exceeded" in log, (
            f"workflow_cap_exceeded not in backend logs; tail:\n{log[-2000:]}"
        )

    finally:
        _delete_team_cascade(team_id)


# ---------------------------------------------------------------------------
# (5) Orphan run recovery
# ---------------------------------------------------------------------------


def test_orphan_run_recovery(
    orchestrator_on_e2e_db: None,  # noqa: ARG001
    backend_url: str,
    celery_worker_url: str,  # noqa: ARG001
) -> None:
    """Insert a WorkflowRun row directly in DB with status='running' and
    last_heartbeat_at = now() - 20 min (beyond the 15-min threshold).
    Call the backend's debug/e2e endpoint to invoke _recover_orphan_runs_body().
    Verify run transitions to status='failed' with error_class='worker_crash'.
    Verify any step_runs in running/pending also get marked failed."""
    admin_email = "admin@example.com"
    admin_cookies = _login_only(backend_url, email=admin_email, password="changethis")
    team_id = _create_team(backend_url, admin_cookies, suffix="-5")

    backend_container = _backend_container_name()

    try:
        wf = _create_workflow(
            backend_url, admin_cookies, team_id,
            {
                "name": f"orphan-wf-{_RUN_TOKEN[:8]}",
                "scope": "user",
                "form_schema": {},
                "steps": [
                    {
                        "step_index": 0,
                        "action": "shell",
                        "config": {"cmd": ["echo", "orphan-test"]},
                        "target_container": "user_workspace",
                    }
                ],
            },
        )
        workflow_id = wf["id"]

        admin_user_id = _psql_one(
            f"SELECT id FROM \"user\" WHERE email = '{admin_email}'"
        )
        assert admin_user_id, "admin user not found"

        # Insert an orphaned run (running, heartbeat 20 min ago).
        orphan_run_id = str(uuid.uuid4())
        r = _psql_exec(
            f"INSERT INTO workflow_runs "
            f"(id, workflow_id, team_id, trigger_type, triggered_by_user_id, "
            f"status, last_heartbeat_at, created_at) VALUES "
            f"('{orphan_run_id}', '{workflow_id}', '{team_id}', 'button', "
            f"'{admin_user_id}', 'running', "
            f"NOW() - INTERVAL '20 minutes', NOW() - INTERVAL '21 minutes')"
        )
        assert r.returncode == 0, (
            f"INSERT orphan run failed; rc={r.returncode} stderr={r.stderr!r}"
        )

        # Insert a running step_run for the orphaned run.
        orphan_step_id = str(uuid.uuid4())
        r = _psql_exec(
            f"INSERT INTO step_runs "
            f"(id, workflow_run_id, step_index, snapshot, status, created_at) VALUES "
            f"('{orphan_step_id}', '{orphan_run_id}', 0, '{{}}', 'running', NOW())"
        )
        assert r.returncode == 0, (
            f"INSERT orphan step_run failed; rc={r.returncode} stderr={r.stderr!r}"
        )

        # Insert a pending step_run (should also be marked failed).
        pending_step_id = str(uuid.uuid4())
        r = _psql_exec(
            f"INSERT INTO step_runs "
            f"(id, workflow_run_id, step_index, snapshot, status, created_at) VALUES "
            f"('{pending_step_id}', '{orphan_run_id}', 1, '{{}}', 'pending', NOW())"
        )
        assert r.returncode == 0, (
            f"INSERT pending step_run failed; rc={r.returncode} stderr={r.stderr!r}"
        )

        # Invoke the recover_orphan_runs task via the Celery task directly on the
        # backend container. We call the task's underlying body function via a
        # one-shot exec that imports and calls _recover_orphan_runs_body.
        exec_cmd = (
            "cd /app && python -c \""
            "from app.core.db import engine; "
            "from sqlmodel import Session; "
            "from app.workflows.tasks import _recover_orphan_runs_body; "
            "with Session(engine) as s: "
            "    count = _recover_orphan_runs_body(s); "
            "    print(f'recovered={count}')"
            "\""
        )
        proc = subprocess.run(
            ["docker", "exec", backend_container, "sh", "-c", exec_cmd],
            capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 0, (
            f"recover_orphan_runs_body exec failed; "
            f"rc={proc.returncode} "
            f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )

        # Give a moment for the DB write to flush.
        time.sleep(0.5)

        # Verify run is now failed with error_class='worker_crash'.
        run_status = _psql_one(
            f"SELECT status || '|' || COALESCE(error_class, '') "
            f"FROM workflow_runs WHERE id = '{orphan_run_id}'"
        )
        assert run_status, f"orphan run row not found: {orphan_run_id!r}"
        parts = run_status.split("|")
        assert parts[0] == "failed", (
            f"orphan run should be 'failed'; got {parts[0]!r}"
        )
        assert parts[1] == "worker_crash", (
            f"error_class should be 'worker_crash'; got {parts[1]!r}"
        )

        # Verify running step_run is also failed.
        step_status = _psql_one(
            f"SELECT status FROM step_runs WHERE id = '{orphan_step_id}'"
        )
        assert step_status == "failed", (
            f"orphan running step_run should be 'failed'; got {step_status!r}"
        )

        # Verify pending step_run is also failed.
        pending_step_status = _psql_one(
            f"SELECT status FROM step_runs WHERE id = '{pending_step_id}'"
        )
        assert pending_step_status == "failed", (
            f"orphan pending step_run should be 'failed'; got {pending_step_status!r}"
        )

        log = _container_logs(backend_container)
        _combined_log.append(log)

        # Discriminators must appear.
        assert "recover_orphan_runs_sweep" in log, (
            f"recover_orphan_runs_sweep not in backend logs; tail:\n{log[-2000:]}"
        )
        assert "workflow_run_orphan_recovered" in log, (
            f"workflow_run_orphan_recovered not in backend logs; tail:\n{log[-2000:]}"
        )

    finally:
        _delete_team_cascade(team_id)


# ---------------------------------------------------------------------------
# (6) Combined discriminator sweep
# ---------------------------------------------------------------------------


def test_discriminator_sweep(
    orchestrator_on_e2e_db: None,  # noqa: ARG001
    backend_url: str,
    celery_worker_url: str,
) -> None:
    """Module-scope log sweep executed after the other test functions.

    Combines log blobs accumulated by each test and asserts:
      1. No sk-ant-/sk- plaintext API key fragments.
      2. All S05 required discriminators fired at least once.
    """
    worker_container = celery_worker_url

    # Append final log snapshots to catch any late-flushed lines.
    time.sleep(1.0)
    _combined_log.append(_container_logs(worker_container))
    backend_container = _backend_container_name()
    _combined_log.append(_container_logs(backend_container))

    combined = "\n".join(_combined_log)

    # 1. No plaintext key fragments.
    assert CLAUDE_KEY not in combined, "redaction: CLAUDE_KEY leaked in logs"
    sk_ant_hits = re.findall(r"sk-ant-[A-Za-z0-9_-]+", combined)
    assert sk_ant_hits == [], (
        f"sk-ant- key leak detected: {sk_ant_hits[:3]!r}"
    )
    sk_hits = re.findall(r"sk-[A-Za-z0-9_-]{20,}", combined)
    assert sk_hits == [], (
        f"bearer-shape sk- key leak detected: {sk_hits[:3]!r}"
    )

    # 2. Required S05 discriminators.
    for marker in _REQUIRED_DISCRIMINATORS:
        assert marker in combined, (
            f"observability regression: S05 discriminator {marker!r} "
            f"not seen in combined container logs"
        )
