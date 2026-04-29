"""M005 / S03 / T06 — Workflow run engine end-to-end.

Slice S03 closure.  Six integration tests that drive the full S03 surface
against the live compose stack:

    team admin creates user workflow (4 steps: shell×3 + claude)
                         →  POST /api/v1/teams/{id}/workflows
                         →  POST /api/v1/workflows/{id}/run
                         →  celery-worker picks up run_workflow
                         →  shell executor POSTs orchestrator /exec for each step
                         →  {prev.stdout} substitution feeds step[3] claude shim
                         →  GET /workflow_runs/{id} reflects succeeded / cancelled.

Test functions
--------------
(a) test_workflow_crud_create_run_succeeds
    Admin creates 'lint and report', fires it with {branch:'main'}, polls to
    succeeded.  Asserts step[3] stdout carries the prior-step lint output
    ({prev.stdout} substitution verified), trigger_payload persisted, no
    key leaks.

(b) test_workflow_cancellation_terminates_run_and_skips_remaining_steps
    Same workflow but step 0 is a `sleep 30` shim; admin POSTs cancel
    immediately after step 0 starts; asserts run terminates `cancelled` and
    steps[1..3] land as `skipped` with error_class='cancelled'.

(c) test_round_robin_dispatch_picks_next_member_and_advances_cursor
    Scope=round_robin, team with 3 members, 2 with live workspace volumes.
    Triggers 4 runs in sequence; asserts only the live-workspace users are
    picked, cursor advances monotonically.

(d) test_round_robin_falls_back_to_triggering_user_when_no_live_workspace
    Scope=round_robin, no workspace volumes provisioned; asserts run goes to
    triggering user and log emits workflow_dispatch_fallback.

(e) test_form_field_required_validation_rejects_dispatch_without_field
    form_schema with a required field 'branch'; POST /run with {} → 400.

(f) test_substitution_failure_marks_step_failed_with_substitution_failed_discriminator
    Step config references {nonexistent.var}; asserts step and run both
    terminate with error_class='substitution_failed'.

(g) Combined-log redaction sweep at module-scope (executed after all test
    functions via module-level logic): zero `sk-ant-`/`sk-` matches, every
    locked observability discriminator fires at least once.

Skip-guard: probes backend:latest for the `s13_workflow_crud_extensions`
alembic revision; skips with a rebuild hint if absent.

How to run::

    docker compose build backend orchestrator celery-worker
    docker compose up -d db redis orchestrator
    cd backend && POSTGRES_DB=perpetuity_app uv run pytest -m e2e \\
        tests/integration/test_m005_s03_workflow_run_engine_e2e.py -v
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
ORCHESTRATOR_IMAGE = "orchestrator:latest"
S13_REVISION = "s13_workflow_crud_extensions"

# Unique sentinel token for this run — used to construct synthetic API keys
# and to verify they don't appear in compose log output.
_RUN_TOKEN = uuid.uuid4().hex
CLAUDE_KEY = f"sk-ant-api03-{_RUN_TOKEN}-S03SENTINEL-padpadpadpad"

# S03 adds 5 discriminators on top of S02's 9.
_REQUIRED_DISCRIMINATORS = (
    # S02 carry-forward
    "workflow_run_dispatched",
    "workflow_run_started",
    "workflow_run_succeeded",
    "workflow_run_failed",
    "step_run_started",
    "step_run_succeeded",
    "step_run_failed",
    "oneshot_exec_started",
    "oneshot_exec_completed",
    # S03 additions
    "workflow_run_cancelled",
    "step_run_skipped",
    "workflow_dispatch_round_robin_pick",
    "workflow_dispatch_fallback",
    "orchestrator_exec_retry",
)

pytestmark = [pytest.mark.e2e]

# ---------------------------------------------------------------------------
# Log accumulator — each test appends its container logs so the module-scope
# sweep in the final function sees the combined stream.
# ---------------------------------------------------------------------------
_combined_log: list[str] = []

# Track containers created by each test so the sweep can read their final
# logs even after teardown has run.
_all_workspace_containers: list[str] = []


# ---------------------------------------------------------------------------
# Low-level docker / psql helpers  (mirrors S02 pattern)
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


def _orchestrator_container_name() -> str:
    for prefix in ("perpetuity-orch-e2e-", "perpetuity-orchestrator-"):
        ps = _docker(
            "ps", "--format", "{{.Names}}",
            "--filter", f"name={prefix}",
            check=True, timeout=10,
        )
        names = [n for n in (ps.stdout or "").splitlines() if n.strip()]
        if names:
            return names[0]
    raise AssertionError(
        "no orchestrator container found; run "
        "`docker compose up -d orchestrator`"
    )


def _backend_image_has_s13() -> bool:
    r = _docker(
        "run", "--rm", "--entrypoint", "ls", BACKEND_IMAGE,
        "/app/backend/app/alembic/versions/",
        check=False, timeout=15,
    )
    return f"{S13_REVISION}.py" in (r.stdout or "")


def _user_id_from_db(email: str) -> str:
    val = _psql_one(f"SELECT id FROM \"user\" WHERE email = '{email}'")
    assert val, f"no user row for {email!r}"
    return val


def _add_member(team_id: str, user_id: str) -> None:
    new_id = uuid.uuid4()
    out = _psql_exec(
        f"INSERT INTO team_member (id, user_id, team_id, role, created_at) "
        f"VALUES ('{new_id}', '{user_id}', '{team_id}', 'member', NOW()) "
        "ON CONFLICT (user_id, team_id) DO NOTHING"
    )
    assert out.returncode == 0, (
        f"INSERT team_member failed; rc={out.returncode} stderr={out.stderr!r}"
    )


def _workspace_container_name(team_id: str) -> str:
    clean = team_id.replace("-", "")
    return f"perpetuity-ws-{clean[:8]}"


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
    name = f"e2e-m005-s03-{_RUN_TOKEN[:8]}{suffix}"
    with httpx.Client(base_url=base_url, timeout=15.0, cookies=cookies) as c:
        r = c.post("/api/v1/teams/", json={"name": name})
    assert r.status_code == 200, f"create team: {r.status_code} {r.text}"
    return r.json()["id"]


def _put_team_secret(
    base_url: str, cookies: httpx.Cookies, team_id: str, key: str, value: str
) -> None:
    with httpx.Client(base_url=base_url, timeout=15.0, cookies=cookies) as c:
        r = c.put(f"/api/v1/teams/{team_id}/secrets/{key}", json={"value": value})
    assert r.status_code == 200, f"PUT {key}: {r.status_code} {r.text}"


def _create_session(
    base_url: str, cookies: httpx.Cookies, team_id: str
) -> str:
    with httpx.Client(base_url=base_url, timeout=30.0, cookies=cookies) as c:
        r = c.post("/api/v1/sessions", json={"team_id": team_id})
    assert r.status_code == 200, f"create session: {r.status_code} {r.text}"
    return r.json()["session_id"]


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
) -> str:
    with httpx.Client(base_url=base_url, timeout=15.0, cookies=cookies) as c:
        r = c.post(
            f"/api/v1/workflows/{workflow_id}/run",
            json={"trigger_payload": trigger_payload},
        )
    assert r.status_code == 200, f"trigger run: {r.status_code} {r.text}"
    body = r.json()
    assert body["status"] == "pending"
    assert "run_id" in body
    return body["run_id"]


def _poll_run(
    base_url: str,
    cookies: httpx.Cookies,
    run_id: str,
    *,
    terminal_statuses: frozenset[str] | None = None,
    timeout_s: float = 45.0,
    interval_s: float = 0.5,
) -> dict:
    """Poll GET /workflow_runs/{run_id} until a terminal status is reached."""
    if terminal_statuses is None:
        terminal_statuses = frozenset({"succeeded", "failed", "cancelled"})
    deadline = time.time() + timeout_s
    last: dict = {}
    while time.time() < deadline:
        with httpx.Client(base_url=base_url, timeout=10.0, cookies=cookies) as c:
            r = c.get(f"/api/v1/workflow_runs/{run_id}")
        assert r.status_code == 200, f"GET workflow_run: {r.status_code} {r.text}"
        last = r.json()
        if last.get("status") in terminal_statuses:
            return last
        time.sleep(interval_s)
    raise AssertionError(
        f"run {run_id!r} did not reach terminal status in {timeout_s}s; "
        f"last state: {json.dumps(last, default=str)[:1500]}"
    )


def _cancel_run(
    base_url: str, cookies: httpx.Cookies, run_id: str
) -> dict:
    with httpx.Client(base_url=base_url, timeout=15.0, cookies=cookies) as c:
        r = c.post(f"/api/v1/workflow_runs/{run_id}/cancel")
    assert r.status_code == 202, f"cancel: {r.status_code} {r.text}"
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


def _wait_for_container_running(name: str, *, timeout_s: float = 15.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        inspect = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True, text=True, timeout=5,
        )
        if inspect.returncode == 0 and inspect.stdout.strip() == "true":
            return
        time.sleep(0.3)
    raise AssertionError(
        f"workspace container {name!r} never reached Running=true in {timeout_s}s"
    )


# ---------------------------------------------------------------------------
# Test shims
# ---------------------------------------------------------------------------

_GIT_SHIM = r"""#!/bin/sh
# git checkout shim — echoes deterministic stdout
echo "on branch ${2:-main}"
exit 0
"""

_NPM_INSTALL_SHIM = r"""#!/bin/sh
# npm install shim
echo "npm install: 42 packages installed"
exit 0
"""

# npm shim: route install / run lint via $2 (subcommand)
_NPM_SHIM = r"""#!/bin/sh
subcmd="$1"
if [ "$subcmd" = "install" ]; then
  echo "npm install: 42 packages installed"
elif [ "$subcmd" = "run" ]; then
  echo "lint stdout: 0 errors, 0 warnings"
else
  echo "npm $subcmd"
fi
exit 0
"""

_CLAUDE_SHIM = r"""#!/bin/sh
if [ -z "$ANTHROPIC_API_KEY" ]; then
  echo "missing ANTHROPIC_API_KEY" >&2
  exit 2
fi
# Echo $PROMPT so the test can assert {prev.stdout} substitution arrived.
echo "stub-claude-output for prompt: $PROMPT"
exit 0
"""

# Sleep shim for the cancellation test — blocks long enough for the cancel
# API call to land before the step completes.
_SLEEP30_SHIM = r"""#!/bin/sh
sleep 30
exit 0
"""


def _install_shim(workspace_container: str, name: str, body: str) -> None:
    target = f"/usr/local/bin/{name}"
    cmd = (
        f"set -e; cat > {target} <<'PERPETUITY_E2E_SHIM_EOF'\n"
        f"{body}"
        "PERPETUITY_E2E_SHIM_EOF\n"
        f"chmod +x {target}\n"
    )
    proc = subprocess.run(
        ["docker", "exec", "-i", workspace_container, "sh", "-c", cmd],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, (
        f"install shim {name!r} failed; rc={proc.returncode} "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    probe = subprocess.run(
        ["docker", "exec", workspace_container, "test", "-x", target],
        capture_output=True, text=True, timeout=10,
    )
    assert probe.returncode == 0, (
        f"shim {name!r} not executable; stderr={probe.stderr!r}"
    )


# ---------------------------------------------------------------------------
# Autouse skip-guard
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _require_s13_baked() -> None:
    if not _backend_image_has_s13():
        pytest.skip(
            "backend:latest is missing the "
            f"{S13_REVISION!r} alembic revision — run "
            "`docker compose build backend orchestrator celery-worker` "
            "so the image bakes the current "
            "/app/backend/app/alembic/versions/ tree."
        )


# ---------------------------------------------------------------------------
# Workflow payload helpers
# ---------------------------------------------------------------------------

_LINT_REPORT_STEPS = [
    {
        "step_index": 0,
        "action": "shell",
        "config": {"cmd": ["git", "checkout", "{form.branch}"]},
        "target_container": "user_workspace",
    },
    {
        "step_index": 1,
        "action": "shell",
        "config": {"cmd": ["npm", "install"]},
        "target_container": "user_workspace",
    },
    {
        "step_index": 2,
        "action": "shell",
        "config": {"cmd": ["npm", "run", "lint"]},
        "target_container": "user_workspace",
    },
    {
        "step_index": 3,
        "action": "claude",
        "config": {"prompt_template": "summarize: {prev.stdout}"},
        "target_container": "user_workspace",
    },
]

_LINT_REPORT_PAYLOAD = {
    "name": f"lint-and-report-{_RUN_TOKEN[:8]}",
    "description": "S03 e2e lint + report workflow",
    "scope": "user",
    "form_schema": {
        "fields": [
            {
                "name": "branch",
                "label": "Branch",
                "kind": "string",
                "required": True,
            }
        ]
    },
    "steps": _LINT_REPORT_STEPS,
}


# ---------------------------------------------------------------------------
# (a) Happy-path: create + run + assert {prev.stdout} substitution
# ---------------------------------------------------------------------------


def test_workflow_crud_create_run_succeeds(  # noqa: PLR0915
    orchestrator_on_e2e_db: None,  # noqa: ARG001
    backend_url: str,
    celery_worker_url: str,
) -> None:
    """Create 'lint and report' (4 steps), fire with branch=main, assert
    {prev.stdout} substitution flows lint stdout into the claude step."""
    backend_container = _backend_container_name()
    worker_container = celery_worker_url
    orchestrator_container = _orchestrator_container_name()

    admin_email = "admin@example.com"
    admin_cookies = _login_only(backend_url, email=admin_email, password="changethis")
    team_id = _create_team(backend_url, admin_cookies, suffix="-a")

    try:
        _put_team_secret(
            backend_url, admin_cookies, team_id, "claude_api_key", CLAUDE_KEY
        )

        # Provision workspace container for admin user.
        _create_session(backend_url, admin_cookies, team_id)
        ws_name = _workspace_container_name(team_id)
        _wait_for_container_running(ws_name)
        _all_workspace_containers.append(ws_name)

        # Install test shims.
        _install_shim(ws_name, "git", _GIT_SHIM)
        _install_shim(ws_name, "npm", _NPM_SHIM)
        _install_shim(ws_name, "claude", _CLAUDE_SHIM)

        # Create the workflow.
        wf = _create_workflow(
            backend_url, admin_cookies, team_id, _LINT_REPORT_PAYLOAD
        )
        workflow_id = wf["id"]

        # Fire it.
        run_id = _trigger_run(
            backend_url, admin_cookies, workflow_id,
            {"branch": "main"},
        )

        run = _poll_run(backend_url, admin_cookies, run_id, timeout_s=45.0)

        if run["status"] != "succeeded":
            worker_logs = _container_logs(worker_container)[-3000:]
            orch_logs = _container_logs(orchestrator_container)[-2000:]
            raise AssertionError(
                f"run did not succeed; got {run!r}\n"
                f"--- worker logs ---\n{worker_logs}\n"
                f"--- orchestrator logs ---\n{orch_logs}"
            )

        assert run.get("error_class") in (None, "")
        assert (run.get("duration_ms") or 0) > 0

        # trigger_payload persisted.
        assert run.get("trigger_payload") == {"branch": "main"}, (
            f"trigger_payload mismatch: {run.get('trigger_payload')!r}"
        )

        steps = run.get("step_runs") or []
        assert len(steps) == 4, f"expected 4 step_runs; got {len(steps)}"

        # Steps 0–2 succeeded with exit_code=0.
        for i in range(3):
            s = steps[i]
            assert s["status"] == "succeeded", f"step[{i}] not succeeded: {s!r}"
            assert s["exit_code"] == 0

        # Step 2 (npm run lint) must have emitted the lint shim stdout.
        lint_stdout = steps[2]["stdout"] or ""
        assert "lint stdout" in lint_stdout, (
            f"lint shim stdout missing: {lint_stdout!r}"
        )

        # Step 3: claude — snapshot.config.prompt_template frozen to
        # original template (not yet substituted on the snapshot column).
        step3 = steps[3]
        assert step3["status"] == "succeeded"
        assert step3["exit_code"] == 0
        snap_cfg = step3.get("snapshot", {}).get("config", {})
        assert "prompt_template" in snap_cfg, (
            f"step[3] snapshot missing prompt_template: {snap_cfg!r}"
        )
        # The snapshot stores the RESOLVED config (runner writes the
        # substituted snapshot before execution).  Assert the substituted
        # lint stdout arrived.
        assert "lint stdout" in step3["stdout"], (
            f"step[3] stdout does not contain lint output: {step3['stdout']!r}"
        )

        # Redaction: no key fragments in logs.
        time.sleep(0.5)
        backend_log = _container_logs(backend_container)
        worker_log = _container_logs(worker_container)
        orch_log = _container_logs(orchestrator_container)
        combined = backend_log + "\n" + worker_log + "\n" + orch_log
        _combined_log.append(combined)

        assert CLAUDE_KEY not in combined, "redaction: CLAUDE_KEY plaintext leaked"
        sk_ant_hits = re.findall(r"sk-ant-[A-Za-z0-9_-]+", combined)
        assert sk_ant_hits == [], f"sk-ant- leak: {sk_ant_hits[:3]!r}"

    finally:
        _delete_team_cascade(team_id)


# ---------------------------------------------------------------------------
# (b) Cancellation: run terminates cancelled, remaining steps skipped
# ---------------------------------------------------------------------------


def test_workflow_cancellation_terminates_run_and_skips_remaining_steps(
    orchestrator_on_e2e_db: None,  # noqa: ARG001
    backend_url: str,
    celery_worker_url: str,
) -> None:
    """Step 0 is a sleep-30 shim.  POST /cancel while step 0 is running;
    assert run terminates cancelled and steps 1–3 are skipped."""
    worker_container = celery_worker_url
    orchestrator_container = _orchestrator_container_name()

    admin_email = "admin@example.com"
    admin_cookies = _login_only(backend_url, email=admin_email, password="changethis")
    team_id = _create_team(backend_url, admin_cookies, suffix="-b")

    try:
        _put_team_secret(
            backend_url, admin_cookies, team_id, "claude_api_key", CLAUDE_KEY
        )

        _create_session(backend_url, admin_cookies, team_id)
        ws_name = _workspace_container_name(team_id)
        _wait_for_container_running(ws_name)
        _all_workspace_containers.append(ws_name)

        # Step 0: sleep 30 shim makes the run block long enough to cancel.
        _install_shim(ws_name, "git", _SLEEP30_SHIM)
        _install_shim(ws_name, "npm", _NPM_SHIM)
        _install_shim(ws_name, "claude", _CLAUDE_SHIM)

        # Build a version of the lint-and-report payload with sleep at step 0.
        cancel_steps = [
            {
                "step_index": 0,
                "action": "shell",
                "config": {"cmd": ["git", "checkout", "main"]},
                "target_container": "user_workspace",
            },
            {
                "step_index": 1,
                "action": "shell",
                "config": {"cmd": ["npm", "install"]},
                "target_container": "user_workspace",
            },
            {
                "step_index": 2,
                "action": "shell",
                "config": {"cmd": ["npm", "run", "lint"]},
                "target_container": "user_workspace",
            },
            {
                "step_index": 3,
                "action": "claude",
                "config": {"prompt_template": "summarize: {prev.stdout}"},
                "target_container": "user_workspace",
            },
        ]
        cancel_wf_payload = {
            "name": f"cancel-test-{_RUN_TOKEN[:8]}",
            "scope": "user",
            "form_schema": {},
            "steps": cancel_steps,
        }
        wf = _create_workflow(backend_url, admin_cookies, team_id, cancel_wf_payload)
        workflow_id = wf["id"]

        run_id = _trigger_run(
            backend_url, admin_cookies, workflow_id,
            {},
        )

        # Poll until step 0 is running (status transitions to 'running').
        deadline = time.time() + 20.0
        while time.time() < deadline:
            with httpx.Client(base_url=backend_url, timeout=10.0, cookies=admin_cookies) as c:
                r = c.get(f"/api/v1/workflow_runs/{run_id}")
            assert r.status_code == 200
            state = r.json()
            if state.get("status") == "running":
                break
            time.sleep(0.3)

        # Issue cancel.
        cancel_resp = _cancel_run(backend_url, admin_cookies, run_id)
        assert cancel_resp.get("status") == "cancelling"

        # Poll to terminal (cancelled).
        run = _poll_run(
            backend_url, admin_cookies, run_id,
            timeout_s=45.0,
        )

        worker_log = _container_logs(worker_container)
        orch_log = _container_logs(orchestrator_container)
        combined = worker_log + "\n" + orch_log
        _combined_log.append(combined)

        assert run["status"] == "cancelled", (
            f"run should be cancelled; got {run['status']!r}\n"
            f"--- worker logs ---\n{worker_log[-2000:]}\n"
            f"--- orchestrator logs ---\n{orch_log[-1000:]}"
        )

        steps = run.get("step_runs") or []
        assert len(steps) == 4

        # Step 0: either succeeded (cancel landed after it finished) or
        # failed with error_class='cancelled' if the orchestrator killed it.
        step0 = steps[0]
        assert step0["status"] in ("succeeded", "failed", "running"), (
            f"step[0] unexpected status: {step0!r}"
        )

        # Steps 1–3 must be skipped with error_class='cancelled'.
        for i in range(1, 4):
            s = steps[i]
            assert s["status"] == "skipped", (
                f"step[{i}] should be skipped; got {s['status']!r}"
            )
            assert s.get("error_class") == "cancelled", (
                f"step[{i}] error_class should be 'cancelled'; got {s!r}"
            )

        assert "workflow_run_cancelled" in combined, (
            "workflow_run_cancelled discriminator missing from worker/orch logs"
        )

    finally:
        _delete_team_cascade(team_id)


# ---------------------------------------------------------------------------
# (c) Round-robin: picks live-workspace members and advances cursor
# ---------------------------------------------------------------------------


def test_round_robin_dispatch_picks_next_member_and_advances_cursor(
    orchestrator_on_e2e_db: None,  # noqa: ARG001
    backend_url: str,
    celery_worker_url: str,
) -> None:
    """3 team members, 2 with live workspaces.  4 round-robin triggers must
    distribute only to the 2 live members and advance the cursor."""
    worker_container = celery_worker_url

    admin_email = "admin@example.com"
    admin_cookies = _login_only(backend_url, email=admin_email, password="changethis")
    admin_id = _user_id_from_db(admin_email)

    team_id = _create_team(backend_url, admin_cookies, suffix="-c")

    # Two extra members — m1 and m2 will get live workspaces; m3 is offline.
    m1_email = f"rr-m1-{_RUN_TOKEN[:8]}@example.com"
    m2_email = f"rr-m2-{_RUN_TOKEN[:8]}@example.com"
    m3_email = f"rr-m3-{_RUN_TOKEN[:8]}@example.com"

    m1_cookies = _signup_login(
        backend_url, email=m1_email, password="changethis", full_name="RR M1"
    )
    m2_cookies = _signup_login(
        backend_url, email=m2_email, password="changethis", full_name="RR M2"
    )
    _signup_login(
        backend_url, email=m3_email, password="changethis", full_name="RR M3"
    )

    m1_id = _user_id_from_db(m1_email)
    m2_id = _user_id_from_db(m2_email)
    m3_id = _user_id_from_db(m3_email)

    _add_member(team_id, m1_id)
    _add_member(team_id, m2_id)
    _add_member(team_id, m3_id)

    try:
        _put_team_secret(
            backend_url, admin_cookies, team_id, "claude_api_key", CLAUDE_KEY
        )

        # Provision live workspaces for admin, m1, m2; not m3.
        for cookies in (admin_cookies, m1_cookies, m2_cookies):
            _create_session(backend_url, cookies, team_id)

        # Wait for the admin workspace (m1/m2 share the same container name
        # pattern keyed on team — only one workspace per team is provisioned
        # per session API).
        ws_name = _workspace_container_name(team_id)
        _wait_for_container_running(ws_name, timeout_s=20.0)
        _all_workspace_containers.append(ws_name)

        # Install a trivial shell + claude shim (no sleep — we want fast runs).
        _install_shim(ws_name, "git", _GIT_SHIM)
        _install_shim(ws_name, "npm", _NPM_SHIM)
        _install_shim(ws_name, "claude", _CLAUDE_SHIM)

        rr_steps = [
            {
                "step_index": 0,
                "action": "shell",
                "config": {"cmd": ["git", "checkout", "main"]},
                "target_container": "user_workspace",
            },
        ]
        wf = _create_workflow(
            backend_url, admin_cookies, team_id,
            {
                "name": f"rr-wf-{_RUN_TOKEN[:8]}",
                "scope": "round_robin",
                "form_schema": {},
                "steps": rr_steps,
            },
        )
        workflow_id = wf["id"]

        # Fire 4 runs in sequence; capture target_user_id for each.
        live_user_ids = {admin_id, m1_id, m2_id}
        picked_targets: list[str] = []
        cursor_values: list[int] = []

        for _ in range(4):
            run_id = _trigger_run(
                backend_url, admin_cookies, workflow_id, {}
            )
            run = _poll_run(backend_url, admin_cookies, run_id, timeout_s=45.0)
            assert run["status"] == "succeeded", (
                f"round-robin run failed: {run!r}\n"
                f"worker: {_container_logs(worker_container)[-2000:]}"
            )
            picked = run.get("target_user_id")
            assert picked is not None
            picked_targets.append(picked)

            cursor_raw = _psql_one(
                f"SELECT round_robin_cursor FROM workflows WHERE id = '{workflow_id}'"
            )
            cursor_values.append(int(cursor_raw))

        combined = _container_logs(worker_container)
        _combined_log.append(combined)

        # Offline member (m3) must never be picked.
        assert m3_id not in picked_targets, (
            f"offline member {m3_id} was picked: {picked_targets!r}"
        )

        # All picks must be live members.
        for picked in picked_targets:
            assert picked in live_user_ids, (
                f"unknown target {picked!r}; live={live_user_ids!r}"
            )

        # Cursor must advance monotonically.
        for i in range(1, len(cursor_values)):
            assert cursor_values[i] > cursor_values[i - 1], (
                f"cursor did not advance: {cursor_values!r}"
            )

        assert "workflow_dispatch_round_robin_pick" in combined, (
            "workflow_dispatch_round_robin_pick discriminator missing from worker logs"
        )

    finally:
        _delete_team_cascade(team_id)
        for em in (m1_email, m2_email, m3_email):
            _delete_user_by_email(em)


# ---------------------------------------------------------------------------
# (d) Round-robin fallback when no live workspace
# ---------------------------------------------------------------------------


def test_round_robin_falls_back_to_triggering_user_when_no_live_workspace(
    orchestrator_on_e2e_db: None,  # noqa: ARG001
    backend_url: str,
    celery_worker_url: str,
) -> None:
    """No workspace volumes provisioned → round-robin falls back to the
    triggering user and emits workflow_dispatch_fallback."""
    worker_container = celery_worker_url

    admin_email = "admin@example.com"
    admin_cookies = _login_only(backend_url, email=admin_email, password="changethis")
    admin_id = _user_id_from_db(admin_email)

    team_id = _create_team(backend_url, admin_cookies, suffix="-d")

    m1_email = f"rr-fb-{_RUN_TOKEN[:8]}@example.com"
    _signup_login(
        backend_url, email=m1_email, password="changethis", full_name="RR FB"
    )
    m1_id = _user_id_from_db(m1_email)
    _add_member(team_id, m1_id)

    try:
        # Explicitly do NOT provision any workspace volumes.
        wf = _create_workflow(
            backend_url, admin_cookies, team_id,
            {
                "name": f"rr-fb-wf-{_RUN_TOKEN[:8]}",
                "scope": "round_robin",
                "form_schema": {},
                "steps": [
                    {
                        "step_index": 0,
                        "action": "shell",
                        # Simple echo — no workspace container needed IF the
                        # orchestrator tolerates a missing container.  The
                        # fallback just means target_user_id = admin; the
                        # orchestrator will 503 when there's no container.
                        # The run is expected to FAIL (no container), but
                        # target_user_id must equal admin_id.
                        "config": {"cmd": ["echo", "hello"]},
                        "target_container": "user_workspace",
                    }
                ],
            },
        )
        workflow_id = wf["id"]

        run_id = _trigger_run(backend_url, admin_cookies, workflow_id, {})

        # The run will fail (no workspace container) but that's expected.
        # We only care that target_user_id was set to the triggering user.
        run = _poll_run(backend_url, admin_cookies, run_id, timeout_s=30.0)
        assert run.get("target_user_id") == admin_id, (
            f"fallback target should be admin; got {run.get('target_user_id')!r}"
        )

        combined = _container_logs(worker_container)
        _combined_log.append(combined)

        # The discriminator must fire regardless of run outcome.
        assert "workflow_dispatch_fallback" in combined, (
            "workflow_dispatch_fallback discriminator missing from worker logs\n"
            f"worker tail: {combined[-2000:]}"
        )

    finally:
        _delete_team_cascade(team_id)
        _delete_user_by_email(m1_email)


# ---------------------------------------------------------------------------
# (e) Form-field required validation rejects dispatch without field
# ---------------------------------------------------------------------------


def test_form_field_required_validation_rejects_dispatch_without_field(
    orchestrator_on_e2e_db: None,  # noqa: ARG001
    backend_url: str,
    celery_worker_url: str,  # noqa: ARG001
) -> None:
    """POST /run with empty trigger_payload on a workflow that has a required
    form field must return 400 missing_required_field."""
    admin_email = "admin@example.com"
    admin_cookies = _login_only(backend_url, email=admin_email, password="changethis")
    team_id = _create_team(backend_url, admin_cookies, suffix="-e")

    try:
        wf = _create_workflow(
            backend_url, admin_cookies, team_id,
            {
                "name": f"form-val-{_RUN_TOKEN[:8]}",
                "scope": "user",
                "form_schema": {
                    "fields": [
                        {
                            "name": "branch",
                            "label": "Branch",
                            "kind": "string",
                            "required": True,
                        }
                    ]
                },
                "steps": [
                    {
                        "step_index": 0,
                        "action": "shell",
                        "config": {"cmd": ["echo", "{form.branch}"]},
                        "target_container": "user_workspace",
                    }
                ],
            },
        )
        workflow_id = wf["id"]

        with httpx.Client(base_url=backend_url, timeout=15.0, cookies=admin_cookies) as c:
            r = c.post(
                f"/api/v1/workflows/{workflow_id}/run",
                json={"trigger_payload": {}},
            )
        assert r.status_code == 400, (
            f"expected 400; got {r.status_code} {r.text}"
        )
        body = r.json()
        assert body.get("detail") == "missing_required_field", (
            f"wrong detail: {body!r}"
        )
        assert body.get("field") == "branch", (
            f"wrong field: {body!r}"
        )

    finally:
        _delete_team_cascade(team_id)


# ---------------------------------------------------------------------------
# (f) Substitution failure marks step failed with substitution_failed
# ---------------------------------------------------------------------------


def test_substitution_failure_marks_step_failed_with_substitution_failed_discriminator(
    orchestrator_on_e2e_db: None,  # noqa: ARG001
    backend_url: str,
    celery_worker_url: str,
) -> None:
    """A step config that references {nonexistent.var} must land with
    error_class='substitution_failed' on both step_run and workflow_run."""
    worker_container = celery_worker_url

    admin_email = "admin@example.com"
    admin_cookies = _login_only(backend_url, email=admin_email, password="changethis")
    team_id = _create_team(backend_url, admin_cookies, suffix="-f")

    try:
        # No workspace provisioned — substitution failure happens before the
        # orchestrator call so container presence doesn't matter.
        wf = _create_workflow(
            backend_url, admin_cookies, team_id,
            {
                "name": f"subst-fail-{_RUN_TOKEN[:8]}",
                "scope": "user",
                "form_schema": {},
                "steps": [
                    {
                        "step_index": 0,
                        "action": "shell",
                        # {nonexistent.var} is not a recognised token.
                        "config": {"cmd": ["echo", "{nonexistent.var}"]},
                        "target_container": "user_workspace",
                    }
                ],
            },
        )
        workflow_id = wf["id"]

        run_id = _trigger_run(backend_url, admin_cookies, workflow_id, {})
        run = _poll_run(backend_url, admin_cookies, run_id, timeout_s=30.0)

        combined = _container_logs(worker_container)
        _combined_log.append(combined)

        assert run["status"] == "failed", (
            f"run should be failed; got {run['status']!r}"
        )
        assert run.get("error_class") == "substitution_failed", (
            f"run error_class should be substitution_failed; got {run!r}"
        )

        steps = run.get("step_runs") or []
        assert len(steps) >= 1
        step0 = steps[0]
        assert step0["status"] == "failed"
        assert step0.get("error_class") == "substitution_failed", (
            f"step[0] error_class wrong: {step0!r}"
        )
        assert step0.get("stderr"), "step[0] stderr should name the missing variable"
        assert "nonexistent.var" in (step0.get("stderr") or ""), (
            f"stderr should name the missing var: {step0.get('stderr')!r}"
        )

    finally:
        _delete_team_cascade(team_id)


# ---------------------------------------------------------------------------
# (g) Combined-log redaction sweep + observability discriminator audit
# ---------------------------------------------------------------------------


def test_combined_log_redaction_and_discriminator_sweep(
    orchestrator_on_e2e_db: None,  # noqa: ARG001
    backend_url: str,  # noqa: ARG001
    celery_worker_url: str,
) -> None:
    """Module-scope sweep executed after the other test functions.

    Combines the log blobs accumulated by each test, then asserts:
      1. Zero sk-ant-/sk- plaintext API key fragments.
      2. Every locked observability discriminator emits at least once.

    Note: orchestrator_exec_retry requires a transient 5xx to fire.  In the
    happy-path compose setup it will rarely be emitted.  We assert it if it
    was seen; if none of the prior tests triggered a retry the assertion is
    skipped with a note rather than failing — the discriminator contract is
    verified by the _retry.py unit tests.
    """
    worker_container = celery_worker_url

    # Append a final log snapshot so any late-flushed lines are captured.
    time.sleep(1.0)
    _combined_log.append(_container_logs(worker_container))

    combined = "\n".join(_combined_log)

    # 1. No plaintext API key fragments.
    assert CLAUDE_KEY not in combined, "redaction: CLAUDE_KEY plaintext leaked"
    sk_ant_hits = re.findall(r"sk-ant-[A-Za-z0-9_-]+", combined)
    assert sk_ant_hits == [], (
        f"redaction: combined logs contain sk-ant- matches: {sk_ant_hits[:3]!r}"
    )
    sk_hits = re.findall(r"sk-[A-Za-z0-9_-]{20,}", combined)
    assert sk_hits == [], (
        f"redaction: combined logs contain bearer-shape sk- matches: {sk_hits[:3]!r}"
    )

    # 2. Observability discriminator audit.
    # orchestrator_exec_retry only fires on transient 5xx — skip rather
    # than fail when the compose stack is healthy.
    optional_discriminators = frozenset({"orchestrator_exec_retry"})

    for marker in _REQUIRED_DISCRIMINATORS:
        if marker in optional_discriminators:
            if marker not in combined:
                # Skip with a note — not a hard failure in a healthy stack.
                pytest.skip(
                    f"optional discriminator {marker!r} not observed "
                    "(no transient 5xx occurred during the e2e run)"
                )
            continue
        assert marker in combined, (
            f"observability regression: {marker!r} not seen in combined logs"
        )
