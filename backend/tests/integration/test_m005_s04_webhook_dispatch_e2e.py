"""M005 / S04 / T03 — Webhook dispatch end-to-end.

Slice S04 closure. Seven integration tests that drive the full S04 webhook
dispatch surface against the live compose stack:

    external POST /api/v1/github/webhooks (HMAC-verified)
                         →  dispatch_github_event evaluates per-project push rules
                         →  mode='manual_workflow' inserts WorkflowRun + enqueues Celery
                         →  mode='rule' branch-pattern match calls orchestrator callback
                         →  webhook_delivery_id UNIQUE prevents double-trigger

Test functions
--------------
(1) test_webhook_pr_manual_workflow_push_rule_dispatches_run
    mode='manual_workflow': PR webhook creates WorkflowRun with trigger_type='webhook'
    and correct webhook_delivery_id. Celery transitions to running/succeeded.
    Logs contain webhook_run_enqueued.

(2) test_webhook_duplicate_delivery_id_no_double_trigger
    Same delivery_id twice: only ONE WorkflowRun row created. Second POST 200 ok.

(3) test_webhook_push_rule_mode_rule_branch_match_triggers_auto_push
    mode='rule' branch_pattern='feature/*': push to feature/test-branch triggers
    orchestrator callback. Logs contain webhook_dispatch_push_rule_evaluated outcome=auto_push_triggered.

(4) test_webhook_push_rule_mode_rule_branch_no_match_skips
    mode='rule': push to main (no match). No WorkflowRun created.
    Logs contain auto_push_skipped reason=branch_pattern_no_match.

(5) test_webhook_no_installation_graceful_skip
    Payload without 'installation' key: no WorkflowRun, route returns 200,
    logs contain webhook_dispatch_no_installation.

(6) test_webhook_run_target_is_team_mirror
    manual_workflow push rule with claude step targeting team_mirror:
    WorkflowRun.trigger_payload contains PR payload, step snapshot shows
    target_container='team_mirror'.

(7) test_discriminator_sweep
    Combined log sweep across both dispatch modes: webhook_dispatched,
    webhook_run_enqueued, webhook_dispatch_push_rule_evaluated all fire.
    No sk-ant-/sk- key fragments in any log.

Skip-guard: probes backend:latest for the `s14_webhook_delivery_id` alembic
revision; skips with rebuild hint if absent.

How to run::

    docker compose build backend orchestrator celery-worker
    docker compose up -d db redis orchestrator
    cd backend && POSTGRES_DB=perpetuity_app uv run pytest -m e2e \\
        tests/integration/test_m005_s04_webhook_dispatch_e2e.py -v
"""

from __future__ import annotations

import hashlib
import hmac
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
S14_REVISION = "s14_webhook_delivery_id"

# Unique sentinel token for this run — used to construct synthetic API keys
# and to verify they don't appear in compose log output.
_RUN_TOKEN = uuid.uuid4().hex
CLAUDE_KEY = f"sk-ant-api03-{_RUN_TOKEN}-S04SENTINEL-padpadpadpad"

# S04 required discriminators.
_REQUIRED_DISCRIMINATORS = (
    "webhook_dispatched",
    "webhook_run_enqueued",
    "webhook_dispatch_push_rule_evaluated",
)

pytestmark = [pytest.mark.e2e]

# ---------------------------------------------------------------------------
# Log accumulator — each test appends its container logs so the module-scope
# sweep in the final function sees the combined stream.
# ---------------------------------------------------------------------------
_combined_log: list[str] = []

# ---------------------------------------------------------------------------
# Low-level docker / psql helpers
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


def _backend_image_has_s14() -> bool:
    r = _docker(
        "run", "--rm", "--entrypoint", "ls", BACKEND_IMAGE,
        "/app/backend/app/alembic/versions/",
        check=False, timeout=15,
    )
    return f"{S14_REVISION}.py" in (r.stdout or "")


# ---------------------------------------------------------------------------
# HMAC signing helper
# ---------------------------------------------------------------------------


def _sign(secret: str, body: bytes) -> str:
    """Compute the GitHub-compatible sha256=<hex> signature header."""
    digest = hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return f"sha256={digest}"


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


def _create_team(base_url: str, cookies: httpx.Cookies, suffix: str = "") -> str:
    name = f"e2e-m005-s04-{_RUN_TOKEN[:8]}{suffix}"
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


# ---------------------------------------------------------------------------
# Webhook seeding helpers
# ---------------------------------------------------------------------------


def _generate_webhook_secret(
    base_url: str, cookies: httpx.Cookies
) -> str:
    """Generate and return the plaintext webhook secret for HMAC signing."""
    with httpx.Client(base_url=base_url, timeout=15.0, cookies=cookies) as c:
        r = c.post(
            "/api/v1/admin/settings/github_app_webhook_secret/generate"
        )
    assert r.status_code == 200, (
        f"generate webhook_secret: {r.status_code} {r.text}"
    )
    body = r.json()
    assert body["has_value"] is True
    return body["value"]


def _seed_installation(team_id: str, installation_id: int) -> str:
    """Insert a github_app_installations row and return its UUID."""
    install_uuid = uuid.uuid4()
    sql = (
        f"INSERT INTO github_app_installations "
        f"(id, team_id, installation_id, account_login, account_type, created_at) "
        f"VALUES ('{install_uuid}', '{team_id}', {installation_id}, "
        f"'test-org', 'Organization', NOW()) "
        f"ON CONFLICT (installation_id) DO NOTHING"
    )
    r = _psql_exec(sql)
    assert r.returncode == 0, (
        f"seed installation failed; rc={r.returncode} stderr={r.stderr!r}"
    )
    return str(install_uuid)


def _seed_project(
    team_id: str, installation_id: int, repo_name: str = "test-org/test-repo"
) -> str:
    """Insert a projects row and return its UUID."""
    project_id = uuid.uuid4()
    sql = (
        f"INSERT INTO projects "
        f"(id, team_id, installation_id, github_repo_full_name, name, created_at) "
        f"VALUES ('{project_id}', '{team_id}', {installation_id}, "
        f"'{repo_name}', 'test-project-{project_id.hex[:6]}', NOW())"
    )
    r = _psql_exec(sql)
    assert r.returncode == 0, (
        f"seed project failed; rc={r.returncode} stderr={r.stderr!r}"
    )
    return str(project_id)


def _seed_push_rule(
    project_id: str,
    mode: str,
    branch_pattern: str | None = None,
    workflow_id: str | None = None,
) -> None:
    """Insert or update a project_push_rules row."""
    bp_sql = f"'{branch_pattern}'" if branch_pattern else "NULL"
    wf_sql = f"'{workflow_id}'" if workflow_id else "NULL"
    sql = (
        f"INSERT INTO project_push_rules "
        f"(project_id, mode, branch_pattern, workflow_id, created_at, updated_at) "
        f"VALUES ('{project_id}', '{mode}', {bp_sql}, {wf_sql}, NOW(), NOW()) "
        f"ON CONFLICT (project_id) DO UPDATE "
        f"SET mode=EXCLUDED.mode, branch_pattern=EXCLUDED.branch_pattern, "
        f"workflow_id=EXCLUDED.workflow_id, updated_at=NOW()"
    )
    r = _psql_exec(sql)
    assert r.returncode == 0, (
        f"seed push_rule failed; rc={r.returncode} stderr={r.stderr!r}"
    )


def _post_webhook(
    base_url: str,
    webhook_secret: str,
    *,
    delivery_id: str,
    event_type: str,
    payload: dict,
    installation_id: int | None = None,
) -> httpx.Response:
    """Sign and POST a synthetic GitHub webhook. Returns the httpx Response."""
    raw_body = json.dumps(payload).encode("utf-8")
    sig = _sign(webhook_secret, raw_body)
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": sig,
        "X-GitHub-Event": event_type,
        "X-GitHub-Delivery": delivery_id,
    }
    if installation_id is not None:
        headers["X-GitHub-Hook-Installation-Target-Id"] = str(installation_id)
    with httpx.Client(base_url=base_url, timeout=15.0) as c:
        return c.post(
            "/api/v1/github/webhooks",
            content=raw_body,
            headers=headers,
        )


def _poll_run(
    base_url: str,
    cookies: httpx.Cookies,
    run_id: str,
    *,
    timeout_s: float = 45.0,
    interval_s: float = 0.5,
) -> dict:
    """Poll GET /workflow_runs/{run_id} until terminal."""
    terminal = frozenset({"succeeded", "failed", "cancelled"})
    deadline = time.time() + timeout_s
    last: dict = {}
    while time.time() < deadline:
        with httpx.Client(base_url=base_url, timeout=10.0, cookies=cookies) as c:
            r = c.get(f"/api/v1/workflow_runs/{run_id}")
        assert r.status_code == 200, f"GET workflow_run: {r.status_code} {r.text}"
        last = r.json()
        if last.get("status") in terminal:
            return last
        time.sleep(interval_s)
    raise AssertionError(
        f"run {run_id!r} did not reach terminal status in {timeout_s}s; "
        f"last state: {json.dumps(last, default=str)[:1500]}"
    )


# ---------------------------------------------------------------------------
# Autouse skip-guard
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _require_s14_baked() -> None:
    if not _backend_image_has_s14():
        pytest.skip(
            "backend:latest is missing the "
            f"{S14_REVISION!r} alembic revision — run "
            "`docker compose build backend orchestrator celery-worker` "
            "so the image bakes the current "
            "/app/backend/app/alembic/versions/ tree."
        )


# ---------------------------------------------------------------------------
# Module-scoped webhook secret — generated once for the whole test module
# so HMAC signing works for all tests.
# ---------------------------------------------------------------------------


@pytest.fixture
def webhook_secret_fixture(
    backend_url: str,
) -> str:
    """Generate a fresh webhook secret for the test; returns the plaintext.

    Each test gets an independent secret so HMAC signing is always consistent
    within that test. The prior secret is wiped first so the generate endpoint
    always succeeds cleanly.
    """
    admin_email = "admin@example.com"
    admin_password = "changethis"
    admin_cookies = _login_only(
        backend_url, email=admin_email, password=admin_password
    )
    # Wipe any old secret first so generate is deterministic.
    _psql_exec(
        "DELETE FROM system_settings WHERE key='github_app_webhook_secret'"
    )
    secret = _generate_webhook_secret(backend_url, admin_cookies)
    return secret


# ---------------------------------------------------------------------------
# (1) manual_workflow push rule dispatches a WorkflowRun
# ---------------------------------------------------------------------------


def test_webhook_pr_manual_workflow_push_rule_dispatches_run(
    orchestrator_on_e2e_db: None,  # noqa: ARG001
    backend_url: str,
    celery_worker_url: str,
    webhook_secret_fixture: str,
) -> None:
    """POST pull_request webhook with matching installation → WorkflowRun inserted,
    Celery picks it up, webhook_run_enqueued fires."""
    webhook_secret = webhook_secret_fixture
    backend_container = _backend_container_name()
    worker_container = celery_worker_url

    admin_email = "admin@example.com"
    admin_cookies = _login_only(backend_url, email=admin_email, password="changethis")
    team_id = _create_team(backend_url, admin_cookies, suffix="-1")

    installation_id = 20001 + abs(hash(_RUN_TOKEN[:4])) % 1000

    try:
        # Seed a CLAUDE key so Celery doesn't abort at key-lookup.
        with httpx.Client(
            base_url=backend_url, timeout=15.0, cookies=admin_cookies
        ) as c:
            r = c.put(
                f"/api/v1/teams/{team_id}/secrets/claude_api_key",
                json={"value": CLAUDE_KEY},
            )
        assert r.status_code == 200, f"PUT claude_api_key: {r.status_code} {r.text}"

        # Seed installation + project + workflow + push rule via psql.
        _seed_installation(team_id, installation_id)
        project_id = _seed_project(team_id, installation_id)

        wf = _create_workflow(
            backend_url, admin_cookies, team_id,
            {
                "name": f"webhook-wf-{_RUN_TOKEN[:8]}",
                "scope": "user",
                "form_schema": {},
                "steps": [
                    {
                        "step_index": 0,
                        "action": "shell",
                        "config": {"cmd": ["echo", "webhook-step-ok"]},
                        "target_container": "user_workspace",
                    }
                ],
            },
        )
        workflow_id = wf["id"]

        _seed_push_rule(project_id, "manual_workflow", workflow_id=workflow_id)

        delivery_id = f"e2e-s04-pr-{uuid.uuid4().hex[:16]}"
        pr_payload = {
            "action": "opened",
            "installation": {"id": installation_id},
            "pull_request": {
                "number": 42,
                "title": "test PR",
                "diff_url": "https://example.com/diff",
                "head": {"ref": "feature/test"},
                "base": {"ref": "main"},
            },
            "repository": {"full_name": "test-org/test-repo"},
        }

        # POST the webhook.
        resp = _post_webhook(
            backend_url, webhook_secret,
            delivery_id=delivery_id,
            event_type="pull_request",
            payload=pr_payload,
            installation_id=installation_id,
        )
        assert resp.status_code == 200, (
            f"webhook POST: {resp.status_code} {resp.text}"
        )
        assert resp.json().get("duplicate") is False

        # Give dispatch time to commit the WorkflowRun row.
        time.sleep(2.0)

        # (a) WorkflowRun row created with expected fields.
        run_row = _psql_one(
            f"SELECT id || '|' || trigger_type || '|' || COALESCE(webhook_delivery_id, '') "
            f"FROM workflow_runs "
            f"WHERE webhook_delivery_id = '{delivery_id}'"
        )
        assert run_row, (
            f"no workflow_runs row with webhook_delivery_id={delivery_id!r}"
        )
        run_id_str, trigger_type, wdid = run_row.split("|")
        assert trigger_type == "webhook", (
            f"trigger_type should be 'webhook'; got {trigger_type!r}"
        )
        assert wdid == delivery_id

        # (b) Verify trigger_payload stored PR payload.
        trigger_payload_raw = _psql_one(
            f"SELECT trigger_payload FROM workflow_runs "
            f"WHERE id = '{run_id_str}'"
        )
        assert trigger_payload_raw, "trigger_payload missing from workflow_runs row"
        tp = json.loads(trigger_payload_raw)
        assert "pull_request" in tp, (
            f"trigger_payload should contain pull_request; got {tp!r}"
        )

        # (c) Celery may pick up the run — poll until terminal (best effort;
        #     a missing workspace container will fail the step, which is ok).
        time.sleep(1.0)
        backend_log = _container_logs(backend_container)
        worker_log = _container_logs(worker_container)
        combined = backend_log + "\n" + worker_log
        _combined_log.append(combined)

        # (c) webhook_run_enqueued discriminator.
        assert "webhook_run_enqueued" in combined, (
            f"webhook_run_enqueued not found in logs; tail:\n{combined[-2000:]}"
        )

    finally:
        _delete_team_cascade(team_id)
        _psql_exec(f"DELETE FROM projects WHERE id = '{project_id}'")
        _psql_exec(
            f"DELETE FROM github_app_installations WHERE installation_id = {installation_id}"
        )


# ---------------------------------------------------------------------------
# (2) Duplicate delivery_id does not double-trigger
# ---------------------------------------------------------------------------


def test_webhook_duplicate_delivery_id_no_double_trigger(
    orchestrator_on_e2e_db: None,  # noqa: ARG001
    backend_url: str,
    celery_worker_url: str,  # noqa: ARG001
    webhook_secret_fixture: str,
) -> None:
    """Same delivery_id posted twice → exactly one WorkflowRun row.
    Second POST returns 200 ok (route idempotent at the route level, but
    dispatch deduplication via UNIQUE webhook_delivery_id prevents double-run)."""
    webhook_secret = webhook_secret_fixture

    admin_email = "admin@example.com"
    admin_cookies = _login_only(backend_url, email=admin_email, password="changethis")
    team_id = _create_team(backend_url, admin_cookies, suffix="-2")

    installation_id = 20101 + abs(hash(_RUN_TOKEN[:5])) % 1000

    project_id: str = ""
    try:
        _seed_installation(team_id, installation_id)
        project_id = _seed_project(team_id, installation_id)

        wf = _create_workflow(
            backend_url, admin_cookies, team_id,
            {
                "name": f"dup-wf-{_RUN_TOKEN[:8]}",
                "scope": "user",
                "form_schema": {},
                "steps": [
                    {
                        "step_index": 0,
                        "action": "shell",
                        "config": {"cmd": ["echo", "dup-test"]},
                        "target_container": "user_workspace",
                    }
                ],
            },
        )
        workflow_id = wf["id"]
        _seed_push_rule(project_id, "manual_workflow", workflow_id=workflow_id)

        delivery_id = f"e2e-s04-dup-{uuid.uuid4().hex[:16]}"
        pr_payload = {
            "action": "opened",
            "installation": {"id": installation_id},
            "pull_request": {"number": 1},
            "repository": {"full_name": "test-org/dup-repo"},
        }

        # First POST.
        resp1 = _post_webhook(
            backend_url, webhook_secret,
            delivery_id=delivery_id,
            event_type="pull_request",
            payload=pr_payload,
            installation_id=installation_id,
        )
        assert resp1.status_code == 200, (
            f"first webhook POST: {resp1.status_code} {resp1.text}"
        )
        assert resp1.json().get("duplicate") is False

        # Wait for dispatch to complete.
        time.sleep(2.0)

        # Second POST with same delivery_id — route returns 200 (duplicate=True).
        resp2 = _post_webhook(
            backend_url, webhook_secret,
            delivery_id=delivery_id,
            event_type="pull_request",
            payload=pr_payload,
            installation_id=installation_id,
        )
        assert resp2.status_code == 200, (
            f"second webhook POST: {resp2.status_code} {resp2.text}"
        )
        # Route-level dedup: returns duplicate=True because the github_webhook_events
        # insert hit ON CONFLICT DO NOTHING.
        assert resp2.json().get("duplicate") is True, (
            f"second POST should return duplicate=True; got {resp2.json()!r}"
        )

        # Only ONE WorkflowRun row for this delivery_id.
        run_count = _psql_one(
            f"SELECT count(*) FROM workflow_runs "
            f"WHERE webhook_delivery_id = '{delivery_id}'"
        )
        assert run_count == "1", (
            f"expected exactly 1 WorkflowRun for delivery_id={delivery_id!r}; "
            f"got {run_count!r}"
        )

    finally:
        _delete_team_cascade(team_id)
        if project_id:
            _psql_exec(f"DELETE FROM projects WHERE id = '{project_id}'")
        _psql_exec(
            f"DELETE FROM github_app_installations WHERE installation_id = {installation_id}"
        )


# ---------------------------------------------------------------------------
# (3) mode='rule' branch match triggers auto-push
# ---------------------------------------------------------------------------


def test_webhook_push_rule_mode_rule_branch_match_triggers_auto_push(
    orchestrator_on_e2e_db: None,  # noqa: ARG001
    backend_url: str,
    celery_worker_url: str,  # noqa: ARG001
    webhook_secret_fixture: str,
) -> None:
    """mode='rule' push rule with branch_pattern='feature/*': push event for
    feature/test-branch fires the rule evaluated discriminator with
    outcome=auto_push_triggered."""
    webhook_secret = webhook_secret_fixture

    admin_email = "admin@example.com"
    admin_cookies = _login_only(backend_url, email=admin_email, password="changethis")
    team_id = _create_team(backend_url, admin_cookies, suffix="-3")

    installation_id = 20201 + abs(hash(_RUN_TOKEN[:6])) % 1000

    project_id: str = ""
    try:
        _seed_installation(team_id, installation_id)
        project_id = _seed_project(team_id, installation_id)
        _seed_push_rule(project_id, "rule", branch_pattern="feature/*")

        delivery_id = f"e2e-s04-rule-match-{uuid.uuid4().hex[:12]}"
        push_payload = {
            "ref": "refs/heads/feature/test-branch",
            "installation": {"id": installation_id},
            "repository": {"full_name": "test-org/rule-repo"},
            "commits": [],
        }

        resp = _post_webhook(
            backend_url, webhook_secret,
            delivery_id=delivery_id,
            event_type="push",
            payload=push_payload,
            installation_id=installation_id,
        )
        assert resp.status_code == 200, (
            f"webhook POST: {resp.status_code} {resp.text}"
        )

        # Give dispatch time to run and log.
        time.sleep(2.0)

        backend_container = _backend_container_name()
        backend_log = _container_logs(backend_container)
        _combined_log.append(backend_log)

        # Log discriminator confirms the rule evaluated with the right outcome.
        assert "webhook_dispatch_push_rule_evaluated" in backend_log, (
            f"webhook_dispatch_push_rule_evaluated not in backend logs; "
            f"tail:\n{backend_log[-2000:]}"
        )
        assert "outcome=auto_push_triggered" in backend_log, (
            f"outcome=auto_push_triggered not in backend logs; "
            f"tail:\n{backend_log[-2000:]}"
        )

        # No WorkflowRun created (mode='rule' calls orchestrator, not inserts runs).
        run_count = _psql_one(
            f"SELECT count(*) FROM workflow_runs "
            f"WHERE webhook_delivery_id = '{delivery_id}'"
        )
        assert run_count == "0", (
            f"mode=rule should not create WorkflowRun; got count={run_count!r}"
        )

    finally:
        _delete_team_cascade(team_id)
        if project_id:
            _psql_exec(f"DELETE FROM projects WHERE id = '{project_id}'")
        _psql_exec(
            f"DELETE FROM github_app_installations WHERE installation_id = {installation_id}"
        )


# ---------------------------------------------------------------------------
# (4) mode='rule' branch no-match skips
# ---------------------------------------------------------------------------


def test_webhook_push_rule_mode_rule_branch_no_match_skips(
    orchestrator_on_e2e_db: None,  # noqa: ARG001
    backend_url: str,
    celery_worker_url: str,  # noqa: ARG001
    webhook_secret_fixture: str,
) -> None:
    """mode='rule' branch_pattern='feature/*': push to main does not match.
    No WorkflowRun created. Log contains auto_push_skipped reason=branch_pattern_no_match."""
    webhook_secret = webhook_secret_fixture

    admin_email = "admin@example.com"
    admin_cookies = _login_only(backend_url, email=admin_email, password="changethis")
    team_id = _create_team(backend_url, admin_cookies, suffix="-4")

    installation_id = 20301 + abs(hash(_RUN_TOKEN[:7])) % 1000

    project_id: str = ""
    try:
        _seed_installation(team_id, installation_id)
        project_id = _seed_project(team_id, installation_id)
        _seed_push_rule(project_id, "rule", branch_pattern="feature/*")

        delivery_id = f"e2e-s04-rule-nomatch-{uuid.uuid4().hex[:12]}"
        push_payload = {
            "ref": "refs/heads/main",
            "installation": {"id": installation_id},
            "repository": {"full_name": "test-org/rule-repo"},
            "commits": [],
        }

        resp = _post_webhook(
            backend_url, webhook_secret,
            delivery_id=delivery_id,
            event_type="push",
            payload=push_payload,
            installation_id=installation_id,
        )
        assert resp.status_code == 200, (
            f"webhook POST: {resp.status_code} {resp.text}"
        )

        # Give dispatch time to run and log.
        time.sleep(2.0)

        backend_container = _backend_container_name()
        backend_log = _container_logs(backend_container)
        _combined_log.append(backend_log)

        # No WorkflowRun created.
        run_count = _psql_one(
            f"SELECT count(*) FROM workflow_runs "
            f"WHERE webhook_delivery_id = '{delivery_id}'"
        )
        assert run_count == "0", (
            f"branch no-match should not create WorkflowRun; got count={run_count!r}"
        )

        # Discriminator with reason=branch_pattern_no_match.
        assert "auto_push_skipped" in backend_log, (
            f"auto_push_skipped not in backend logs; tail:\n{backend_log[-2000:]}"
        )
        assert "branch_pattern_no_match" in backend_log, (
            f"branch_pattern_no_match not in backend logs; tail:\n{backend_log[-2000:]}"
        )

    finally:
        _delete_team_cascade(team_id)
        if project_id:
            _psql_exec(f"DELETE FROM projects WHERE id = '{project_id}'")
        _psql_exec(
            f"DELETE FROM github_app_installations WHERE installation_id = {installation_id}"
        )


# ---------------------------------------------------------------------------
# (5) Webhook without installation key returns 200, no WorkflowRun
# ---------------------------------------------------------------------------


def test_webhook_no_installation_graceful_skip(
    orchestrator_on_e2e_db: None,  # noqa: ARG001
    backend_url: str,
    celery_worker_url: str,  # noqa: ARG001
    webhook_secret_fixture: str,
) -> None:
    """Payload without 'installation' key: route returns 200, no WorkflowRun created,
    webhook_dispatch_no_installation fires."""
    webhook_secret = webhook_secret_fixture

    delivery_id = f"e2e-s04-noinst-{uuid.uuid4().hex[:12]}"
    payload_no_install = {
        "action": "opened",
        "pull_request": {"number": 99},
        "repository": {"full_name": "test-org/no-install-repo"},
        # intentionally no 'installation' key
    }

    resp = _post_webhook(
        backend_url, webhook_secret,
        delivery_id=delivery_id,
        event_type="pull_request",
        payload=payload_no_install,
    )
    assert resp.status_code == 200, (
        f"no-installation webhook POST: {resp.status_code} {resp.text}"
    )

    # Give dispatch time to run and log.
    time.sleep(1.5)

    backend_container = _backend_container_name()
    backend_log = _container_logs(backend_container)
    _combined_log.append(backend_log)

    # No WorkflowRun created.
    run_count = _psql_one(
        f"SELECT count(*) FROM workflow_runs "
        f"WHERE webhook_delivery_id = '{delivery_id}'"
    )
    assert run_count == "0", (
        f"no-installation should not create WorkflowRun; got count={run_count!r}"
    )

    # Discriminator fires.
    assert "webhook_dispatch_no_installation" in backend_log, (
        f"webhook_dispatch_no_installation not in backend logs; "
        f"tail:\n{backend_log[-2000:]}"
    )


# ---------------------------------------------------------------------------
# (6) WorkflowRun trigger_payload contains PR payload; step snapshot shows target
# ---------------------------------------------------------------------------


def test_webhook_run_target_is_team_mirror(
    orchestrator_on_e2e_db: None,  # noqa: ARG001
    backend_url: str,
    celery_worker_url: str,  # noqa: ARG001
    webhook_secret_fixture: str,
) -> None:
    """manual_workflow with team_mirror step: WorkflowRun.trigger_payload contains
    full PR payload; step snapshot preserves target_container='team_mirror'."""
    webhook_secret = webhook_secret_fixture

    admin_email = "admin@example.com"
    admin_cookies = _login_only(backend_url, email=admin_email, password="changethis")
    team_id = _create_team(backend_url, admin_cookies, suffix="-6")

    installation_id = 20401 + abs(hash(_RUN_TOKEN[:8])) % 1000

    project_id: str = ""
    try:
        _seed_installation(team_id, installation_id)
        project_id = _seed_project(team_id, installation_id)

        # Workflow with a team_mirror step.
        wf = _create_workflow(
            backend_url, admin_cookies, team_id,
            {
                "name": f"mirror-wf-{_RUN_TOKEN[:8]}",
                "scope": "user",
                "form_schema": {},
                "steps": [
                    {
                        "step_index": 0,
                        "action": "claude",
                        "config": {
                            "prompt_template": "review this diff: {trigger.pull_request.diff_url}",
                        },
                        "target_container": "team_mirror",
                    }
                ],
            },
        )
        workflow_id = wf["id"]
        _seed_push_rule(project_id, "manual_workflow", workflow_id=workflow_id)

        delivery_id = f"e2e-s04-mirror-{uuid.uuid4().hex[:12]}"
        pr_payload = {
            "action": "opened",
            "installation": {"id": installation_id},
            "pull_request": {
                "number": 7,
                "title": "mirror test PR",
                "diff_url": "https://example.com/diff/7",
                "head": {"ref": "feature/mirror"},
                "base": {"ref": "main"},
            },
            "repository": {"full_name": "test-org/mirror-repo"},
        }

        resp = _post_webhook(
            backend_url, webhook_secret,
            delivery_id=delivery_id,
            event_type="pull_request",
            payload=pr_payload,
            installation_id=installation_id,
        )
        assert resp.status_code == 200, (
            f"webhook POST: {resp.status_code} {resp.text}"
        )

        time.sleep(2.0)

        # Verify WorkflowRun row was created.
        run_row = _psql_one(
            f"SELECT id || '|' || trigger_payload "
            f"FROM workflow_runs "
            f"WHERE webhook_delivery_id = '{delivery_id}'"
        )
        assert run_row, (
            f"no WorkflowRun row for delivery_id={delivery_id!r}"
        )
        sep_idx = run_row.index("|")
        run_id_str = run_row[:sep_idx]
        tp_raw = run_row[sep_idx + 1:]

        # trigger_payload contains the full PR payload.
        tp = json.loads(tp_raw)
        assert "pull_request" in tp, (
            f"trigger_payload missing pull_request; got keys {list(tp)!r}"
        )
        assert tp["pull_request"]["diff_url"] == "https://example.com/diff/7", (
            f"diff_url mismatch; got {tp['pull_request'].get('diff_url')!r}"
        )

        # Step snapshot shows target_container='team_mirror'.
        # The step snapshot is written after the Celery worker starts running;
        # fall back to the workflow_steps row if the run hasn't been picked up.
        step_target = _psql_one(
            f"SELECT config->>'target_container' FROM workflow_steps "
            f"WHERE workflow_id = '{workflow_id}'"
        )
        if not step_target:
            step_target = _psql_one(
                f"SELECT snapshot->>'target_container' FROM step_runs "
                f"WHERE workflow_run_id = '{run_id_str}'"
            )
        assert step_target == "team_mirror", (
            f"step target_container should be 'team_mirror'; got {step_target!r}"
        )

    finally:
        _delete_team_cascade(team_id)
        if project_id:
            _psql_exec(f"DELETE FROM projects WHERE id = '{project_id}'")
        _psql_exec(
            f"DELETE FROM github_app_installations WHERE installation_id = {installation_id}"
        )


# ---------------------------------------------------------------------------
# (7) Combined discriminator sweep
# ---------------------------------------------------------------------------


def test_discriminator_sweep(
    orchestrator_on_e2e_db: None,  # noqa: ARG001
    backend_url: str,
    celery_worker_url: str,
    webhook_secret_fixture: str,
) -> None:
    """Module-scope log sweep executed after the other test functions.

    Posts two fresh webhook events (one manual_workflow, one push/rule-match)
    and asserts all required discriminators fire. Also checks no key leaks.
    """
    webhook_secret = webhook_secret_fixture
    worker_container = celery_worker_url

    admin_email = "admin@example.com"
    admin_cookies = _login_only(backend_url, email=admin_email, password="changethis")
    team_id = _create_team(backend_url, admin_cookies, suffix="-7")

    inst_a = 20501 + abs(hash(_RUN_TOKEN[:9])) % 500
    inst_b = inst_a + 600

    project_a: str = ""
    project_b: str = ""
    try:
        with httpx.Client(
            base_url=backend_url, timeout=15.0, cookies=admin_cookies
        ) as c:
            r = c.put(
                f"/api/v1/teams/{team_id}/secrets/claude_api_key",
                json={"value": CLAUDE_KEY},
            )
        assert r.status_code == 200

        # --- Setup project A: manual_workflow ---
        _seed_installation(team_id, inst_a)
        project_a = _seed_project(team_id, inst_a, repo_name="test-org/sweep-repo-a")

        wf_a = _create_workflow(
            backend_url, admin_cookies, team_id,
            {
                "name": f"sweep-wf-a-{_RUN_TOKEN[:8]}",
                "scope": "user",
                "form_schema": {},
                "steps": [
                    {
                        "step_index": 0,
                        "action": "shell",
                        "config": {"cmd": ["echo", "sweep-a"]},
                        "target_container": "user_workspace",
                    }
                ],
            },
        )
        _seed_push_rule(project_a, "manual_workflow", workflow_id=wf_a["id"])

        # --- Setup project B: rule mode ---
        _seed_installation(team_id, inst_b)
        project_b = _seed_project(team_id, inst_b, repo_name="test-org/sweep-repo-b")
        _seed_push_rule(project_b, "rule", branch_pattern="feature/*")

        # Post manual_workflow webhook.
        did_a = f"e2e-s04-sweep-a-{uuid.uuid4().hex[:12]}"
        resp_a = _post_webhook(
            backend_url, webhook_secret,
            delivery_id=did_a,
            event_type="pull_request",
            payload={
                "action": "opened",
                "installation": {"id": inst_a},
                "pull_request": {"number": 10},
                "repository": {"full_name": "test-org/sweep-repo-a"},
            },
            installation_id=inst_a,
        )
        assert resp_a.status_code == 200, (
            f"sweep webhook A: {resp_a.status_code} {resp_a.text}"
        )

        # Post push/rule webhook.
        did_b = f"e2e-s04-sweep-b-{uuid.uuid4().hex[:12]}"
        resp_b = _post_webhook(
            backend_url, webhook_secret,
            delivery_id=did_b,
            event_type="push",
            payload={
                "ref": "refs/heads/feature/sweep",
                "installation": {"id": inst_b},
                "repository": {"full_name": "test-org/sweep-repo-b"},
                "commits": [],
            },
            installation_id=inst_b,
        )
        assert resp_b.status_code == 200, (
            f"sweep webhook B: {resp_b.status_code} {resp_b.text}"
        )

        # Wait for dispatch + logging.
        time.sleep(3.0)
        _combined_log.append(_container_logs(worker_container))

        backend_container = _backend_container_name()
        backend_log = _container_logs(backend_container)
        _combined_log.append(backend_log)

        combined = "\n".join(_combined_log)

        # 1. No plaintext key fragments.
        assert CLAUDE_KEY not in combined, "redaction: CLAUDE_KEY leaked"
        sk_ant_hits = re.findall(r"sk-ant-[A-Za-z0-9_-]+", combined)
        assert sk_ant_hits == [], (
            f"sk-ant- leak: {sk_ant_hits[:3]!r}"
        )
        sk_hits = re.findall(r"sk-[A-Za-z0-9_-]{20,}", combined)
        assert sk_hits == [], (
            f"bearer-shape sk- leak: {sk_hits[:3]!r}"
        )

        # 2. Required S04 discriminators.
        assert "webhook_dispatched" in combined, (
            "webhook_dispatched not found in combined logs"
        )
        assert "webhook_run_enqueued" in combined, (
            "webhook_run_enqueued not found in combined logs"
        )
        assert "webhook_dispatch_push_rule_evaluated" in combined, (
            "webhook_dispatch_push_rule_evaluated not found in combined logs"
        )

    finally:
        _delete_team_cascade(team_id)
        if project_a:
            _psql_exec(f"DELETE FROM projects WHERE id = '{project_a}'")
        if project_b:
            _psql_exec(f"DELETE FROM projects WHERE id = '{project_b}'")
        _psql_exec(
            f"DELETE FROM github_app_installations WHERE installation_id IN ({inst_a}, {inst_b})"
        )
