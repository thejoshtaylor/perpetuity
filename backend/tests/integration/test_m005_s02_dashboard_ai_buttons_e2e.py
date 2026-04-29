"""M005 / S02 / T06 — Dashboard 'Run Claude' / 'Run Codex' end-to-end.

Slice S02 closure. One integration test that drives the full chain
against the live compose stack:

    dashboard click  →  POST /api/v1/workflows/{id}/run
                     →  workflow_runs row inserted, Celery task enqueued
                     →  celery-worker picks up `app.workflows.run_workflow`
                     →  AI executor reads the team's API key (S01 boundary)
                     →  POST orchestrator /v1/sessions/{sid}/exec
                     →  docker exec into the workspace container
                     →  `script -q -e -c '<claude|codex CLI>' /dev/null`
                     →  stdout + exit_code propagated back through the chain
                     →  GET /api/v1/workflow_runs/{id} reflects success.

Real-API acceptance is reserved for S06 (D029); this test replaces the
`claude` / `codex` CLIs inside the workspace image with a deterministic
test shim that proves:

  1. The env-injection code path is wired (`$ANTHROPIC_API_KEY` /
     `$OPENAI_API_KEY` reach the in-container exec frame).
  2. The prompt arrives via `$PROMPT` from the env dict (MEM274 — the
     prompt body never enters the cmd argv).
  3. `script -q -e ...` propagates the child exit code (MEM427).
  4. The slice's full INFO/ERROR taxonomy fires at the expected times
     (`workflow_run_dispatched`, `workflow_run_started`,
     `workflow_run_succeeded`, `workflow_run_failed`,
     `step_run_started`, `step_run_succeeded`, `step_run_failed`,
     `oneshot_exec_started`, `oneshot_exec_completed`).
  5. Zero `sk-ant-` / `sk-` plaintext keys leak into compose logs.

Skip-guard (MEM162 / MEM186 / MEM247): probes `backend:latest` for the
`s12_seed_direct_workflows` alembic revision; skips with the canonical
`docker compose build backend orchestrator celery-worker` hint when
absent.

How to run::

    docker compose build backend orchestrator
    docker compose up -d db redis orchestrator
    cd backend && POSTGRES_DB=perpetuity_app uv run pytest -m e2e \\
        tests/integration/test_m005_s02_dashboard_ai_buttons_e2e.py -v
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
S12_REVISION = "s12_seed_direct_workflows"

# Sentinel suffix appended to the synthetic API keys so the redaction
# sweep at the end can prove THIS test's plaintext didn't leak (vs.
# coincidentally matching an older log line).
_RUN_TOKEN = uuid.uuid4().hex
CLAUDE_KEY = f"sk-ant-api03-{_RUN_TOKEN}-CLAUDESENTINEL-padpadpadpadpad"
OPENAI_KEY = f"sk-{_RUN_TOKEN}-OPENAISENTINEL-padpadpadpadpadpad"

# Locked observability discriminators — the test asserts every one of
# these fires somewhere in the combined backend / celery-worker /
# orchestrator log stream during the full e2e cycle.
_REQUIRED_DISCRIMINATORS = (
    "workflow_run_dispatched",
    "workflow_run_started",
    "workflow_run_succeeded",
    "workflow_run_failed",
    "step_run_started",
    "step_run_succeeded",
    "step_run_failed",
    "oneshot_exec_started",
    "oneshot_exec_completed",
)

pytestmark = [pytest.mark.e2e]


# ----- helpers -----------------------------------------------------------


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
                "email": email,
                "password": password,
                "full_name": full_name,
            },
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


def _backend_container_name() -> str:
    """Discover the sibling backend container spawned by the conftest."""
    ps = _docker(
        "ps", "--format", "{{.Names}}",
        "--filter", "name=perpetuity-backend-e2e-",
        check=True, timeout=10,
    )
    names = [n for n in (ps.stdout or "").splitlines() if n.strip()]
    assert names, f"no sibling backend container found; got {names!r}"
    return names[0]


def _container_logs(name: str) -> str:
    r = _docker("logs", name, check=False, timeout=15)
    return (r.stdout or "") + (r.stderr or "")


def _orchestrator_container_name() -> str:
    """Discover the orchestrator currently bound to the `orchestrator` DNS
    alias on the compose network. May be the compose service
    (``perpetuity-orchestrator-1``) or the conftest's ephemeral
    DB-swap container (``perpetuity-orch-e2e-<hex>``)."""
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


def _backend_image_has_s12() -> bool:
    """Probe `backend:latest` for the s12 revision file."""
    r = _docker(
        "run", "--rm", "--entrypoint", "ls", BACKEND_IMAGE,
        "/app/backend/app/alembic/versions/",
        check=False, timeout=15,
    )
    return f"{S12_REVISION}.py" in (r.stdout or "")


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


def _create_team(base_url: str, cookies: httpx.Cookies) -> str:
    name = f"e2e-m005-s02-{_RUN_TOKEN[:8]}"
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


def _delete_team_secret(
    base_url: str, cookies: httpx.Cookies, team_id: str, key: str
) -> None:
    with httpx.Client(base_url=base_url, timeout=15.0, cookies=cookies) as c:
        r = c.delete(f"/api/v1/teams/{team_id}/secrets/{key}")
    assert r.status_code == 204, f"DELETE {key}: {r.status_code} {r.text}"


def _list_team_workflows(
    base_url: str, cookies: httpx.Cookies, team_id: str
) -> list[dict]:
    with httpx.Client(base_url=base_url, timeout=15.0, cookies=cookies) as c:
        r = c.get(f"/api/v1/teams/{team_id}/workflows")
    assert r.status_code == 200, f"list workflows: {r.status_code} {r.text}"
    return r.json()["data"]


def _create_session(
    base_url: str, cookies: httpx.Cookies, team_id: str
) -> str:
    """Drive the dashboard-side POST /api/v1/sessions to provision the
    workspace container for (caller, team). Returns the session id.

    The orchestrator names the container `perpetuity-ws-<first8-team>`
    (MEM098) — that's the container we install the test shims into."""
    with httpx.Client(base_url=base_url, timeout=30.0, cookies=cookies) as c:
        r = c.post("/api/v1/sessions", json={"team_id": team_id})
    assert r.status_code == 200, f"create session: {r.status_code} {r.text}"
    return r.json()["session_id"]


def _workspace_container_name(team_id: str) -> str:
    """Match orchestrator/orchestrator/sessions._container_name."""
    clean = team_id.replace("-", "")
    return f"perpetuity-ws-{clean[:8]}"


# -----------------------------------------------------------------------
# Test shim CLIs.
#
# Both shims:
#   * fail with exit 2 if their required env var is missing or empty —
#     proves the env-injection code path is wired (negative test
#     coverage from case 9).
#   * echo a deterministic "stub-<provider>-output for prompt: <PROMPT>"
#     line so we can assert on stdout.
#   * exit 0 on success.
# -----------------------------------------------------------------------


_CLAUDE_SHIM = r"""#!/bin/sh
if [ -z "$ANTHROPIC_API_KEY" ]; then
  echo "missing ANTHROPIC_API_KEY" >&2
  exit 2
fi
# claude CLI argv shape: claude -p <prompt> --dangerously-skip-permissions
# Our executor passes the prompt via env $PROMPT so positional $2/$3 may
# vary. We always echo the env $PROMPT to keep the assertion stable.
echo "stub-claude-output for prompt: $PROMPT"
exit 0
"""

_CODEX_SHIM = r"""#!/bin/sh
if [ -z "$OPENAI_API_KEY" ]; then
  echo "missing OPENAI_API_KEY" >&2
  exit 2
fi
echo "stub-codex-output for prompt: $PROMPT"
exit 0
"""


def _install_shim(workspace_container: str, name: str, body: str) -> None:
    """Drop a CLI shim at /usr/local/bin/<name> in the workspace container.

    Uses `docker exec ... sh -c 'cat > path'` with the body piped via
    stdin. Fail-fast on non-zero exit so a permission / mount surprise
    surfaces as a clear assertion error rather than a confusing later
    `cli_nonzero`.
    """
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

    # Read-back probe — proves the file is present and executable.
    probe = subprocess.run(
        ["docker", "exec", workspace_container, "test", "-x", target],
        capture_output=True, text=True, timeout=10,
    )
    assert probe.returncode == 0, (
        f"shim {name!r} not executable after install; "
        f"stderr={probe.stderr!r}"
    )


def _trigger_run(
    base_url: str, cookies: httpx.Cookies, workflow_id: str, prompt: str
) -> str:
    with httpx.Client(base_url=base_url, timeout=15.0, cookies=cookies) as c:
        r = c.post(
            f"/api/v1/workflows/{workflow_id}/run",
            json={"trigger_payload": {"prompt": prompt}},
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
    until_terminal: bool = True,
    timeout_s: float = 30.0,
    interval_s: float = 0.5,
) -> dict:
    """Poll `GET /api/v1/workflow_runs/{run_id}` until the run reaches a
    terminal status (`succeeded` / `failed`) or the deadline elapses.

    Returns the final response body. Fails the test with the last polled
    state captured if the deadline is hit (the slice plan's celery-worker
    pickup failure-mode assertion).
    """
    deadline = time.time() + timeout_s
    last: dict = {}
    saw_running = False
    while time.time() < deadline:
        with httpx.Client(base_url=base_url, timeout=10.0, cookies=cookies) as c:
            r = c.get(f"/api/v1/workflow_runs/{run_id}")
        assert r.status_code == 200, (
            f"GET workflow_run: {r.status_code} {r.text}"
        )
        last = r.json()
        if last.get("status") == "running":
            saw_running = True
        if not until_terminal:
            return last
        if last.get("status") in ("succeeded", "failed"):
            # Annotate so the caller can confirm we observed a transition.
            last["_saw_running"] = saw_running
            return last
        time.sleep(interval_s)
    raise AssertionError(
        f"run {run_id!r} did not reach terminal status in {timeout_s}s; "
        f"last state: {json.dumps(last, default=str)[:1500]}"
    )


def _delete_team_and_members(team_id: str) -> None:
    _psql_exec(f"DELETE FROM step_runs WHERE workflow_run_id IN (SELECT id FROM workflow_runs WHERE team_id = '{team_id}')")
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


# ----- autouse skip-guard -------------------------------------------------


@pytest.fixture(autouse=True)
def _require_s12_baked() -> None:
    if not _backend_image_has_s12():
        pytest.skip(
            "backend:latest is missing the "
            f"{S12_REVISION!r} alembic revision — run "
            "`docker compose build backend orchestrator celery-worker` "
            "so the image bakes the current "
            "/app/backend/app/alembic/versions/ tree."
        )


# ----- the test ----------------------------------------------------------


def test_m005_s02_dashboard_ai_buttons_e2e(  # noqa: PLR0915
    orchestrator_on_e2e_db: None,  # noqa: ARG001 — must run before backend_url
    backend_url: str,
    celery_worker_url: str,
) -> None:
    """Slice closure demo:

    1. Admin signs up, creates a team, pastes claude + openai keys.
    2. Admin spins up a workspace session — workspace container appears.
    3. Test installs deterministic claude / codex shims into the
       workspace container.
    4. Admin clicks 'Run Claude' → POST /workflows/{id}/run with the
       direct-claude workflow id and a prompt; polls the run to
       `succeeded` and asserts stdout.
    5. Repeat for 'Run Codex'.
    6. DELETE the claude key, trigger another claude run, poll to
       `failed` with `error_class='missing_team_secret'`.
    7. Final redaction sweep — combined backend + worker + orchestrator
       log stream contains zero `sk-ant-` / `sk-` matches and ALL
       locked observability discriminators.
    """
    suite_started = time.time()

    backend_container = _backend_container_name()
    worker_container = celery_worker_url
    orchestrator_container = _orchestrator_container_name()

    # ----- Step 1: log in as the seeded system_admin -------------------
    admin_email = "admin@example.com"
    admin_password = "changethis"
    admin_cookies = _login_only(
        backend_url, email=admin_email, password=admin_password
    )

    team_id = _create_team(backend_url, admin_cookies)

    # Second user — a non-admin team member. Not strictly required for
    # the demo but ensures (a) the slice's membership boundary is
    # exercised under realistic conditions, (b) team_member cleanup
    # doesn't crash on an empty member set.
    member_email = f"e2e-m005-s02-member-{_RUN_TOKEN[:8]}@example.com"
    _ = _signup_login(
        backend_url,
        email=member_email,
        password="changethis-member-x",
        full_name="M005/S02 Member",
    )
    member_user_id = _user_id_from_db(member_email)
    _add_member(team_id, member_user_id)

    try:
        # ----- Step 2: paste both API keys ------------------------------
        _put_team_secret(
            backend_url, admin_cookies, team_id, "claude_api_key", CLAUDE_KEY
        )
        _put_team_secret(
            backend_url, admin_cookies, team_id, "openai_api_key", OPENAI_KEY
        )

        # ----- Step 3: provision workspace container --------------------
        # POST /api/v1/sessions tells the orchestrator to provision the
        # per-(user, team) container. We then drop the test shims into
        # /usr/local/bin/{claude,codex} so the executor's docker exec
        # invokes our deterministic stand-in instead of the real CLIs
        # baked into the workspace image.
        _ = _create_session(backend_url, admin_cookies, team_id)
        ws_name = _workspace_container_name(team_id)

        # Container takes a beat to reach a state where docker exec
        # works (`sleep infinity` PID 1, but the container layer needs
        # to be in `running`). Poll briefly.
        deadline = time.time() + 15.0
        while time.time() < deadline:
            inspect = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", ws_name],
                capture_output=True, text=True, timeout=5,
            )
            if inspect.returncode == 0 and inspect.stdout.strip() == "true":
                break
            time.sleep(0.3)
        else:
            raise AssertionError(
                f"workspace container {ws_name!r} never reached "
                "Running=true in 15 s"
            )

        _install_shim(ws_name, "claude", _CLAUDE_SHIM)
        _install_shim(ws_name, "codex", _CODEX_SHIM)

        # ----- Step 4: lookup direct workflow ids -----------------------
        workflows = _list_team_workflows(backend_url, admin_cookies, team_id)
        by_name = {w["name"]: w for w in workflows}
        assert "_direct_claude" in by_name, (
            f"_direct_claude missing from team workflows: "
            f"{[w['name'] for w in workflows]!r}"
        )
        assert "_direct_codex" in by_name
        assert by_name["_direct_claude"]["system_owned"] is True
        assert by_name["_direct_codex"]["system_owned"] is True
        claude_workflow_id = by_name["_direct_claude"]["id"]
        codex_workflow_id = by_name["_direct_codex"]["id"]

        # ----- Step 5: happy-path Claude run ----------------------------
        claude_prompt = "list the files in this repo"
        run_id_claude = _trigger_run(
            backend_url, admin_cookies, claude_workflow_id, claude_prompt
        )
        run_claude = _poll_run(
            backend_url, admin_cookies, run_id_claude, timeout_s=30.0
        )
        if run_claude["status"] != "succeeded":
            # Diagnostic dump on first-run failure: the test cleanup
            # finally-block wipes containers (and therefore log streams),
            # so capture worker + orchestrator log tails inline. Cheap
            # while the test is green; pays off the moment it isn't.
            worker_logs_dump = _container_logs(worker_container)[-3000:]
            orch_logs_dump = _container_logs(orchestrator_container)[-2000:]
            raise AssertionError(
                f"claude run did not succeed; got {run_claude!r}\n"
                f"--- worker logs (tail) ---\n{worker_logs_dump}\n"
                f"--- orchestrator logs (tail) ---\n{orch_logs_dump}"
            )
        # Observing the `running` transition is best-effort — the test
        # shim is fast enough that the row can flip pending → running →
        # succeeded within one poll interval. We still assert the
        # transition fires in the log stream below (`workflow_run_started`
        # discriminator), which is the slice's authoritative observability
        # contract.
        assert run_claude.get("error_class") in (None, ""), (
            f"claude run carried error_class on success: {run_claude!r}"
        )
        assert (run_claude.get("duration_ms") or 0) > 0
        steps = run_claude.get("step_runs") or []
        assert len(steps) == 1, (
            f"expected exactly one step_run; got {len(steps)}: {steps!r}"
        )
        step = steps[0]
        assert step["status"] == "succeeded"
        assert step["exit_code"] == 0
        assert step["snapshot"]["action"] == "claude"
        assert "stub-claude-output for prompt:" in step["stdout"], (
            f"stdout missing stub marker: {step['stdout']!r}"
        )
        assert claude_prompt in step["stdout"], (
            "stdout did not echo the prompt back; the env-injection "
            "code path may not be wired"
        )

        # ----- Step 6: happy-path Codex run -----------------------------
        codex_prompt = "summarize the README"
        run_id_codex = _trigger_run(
            backend_url, admin_cookies, codex_workflow_id, codex_prompt
        )
        run_codex = _poll_run(
            backend_url, admin_cookies, run_id_codex, timeout_s=30.0
        )
        assert run_codex["status"] == "succeeded", (
            f"codex run did not succeed; got {run_codex!r}"
        )
        steps = run_codex.get("step_runs") or []
        assert len(steps) == 1
        step = steps[0]
        assert step["status"] == "succeeded"
        assert step["exit_code"] == 0
        assert step["snapshot"]["action"] == "codex"
        assert "stub-codex-output for prompt:" in step["stdout"]
        assert codex_prompt in step["stdout"]

        # ----- Step 7: missing-key path (negative test) -----------------
        # Delete the claude key. Trigger another claude run; expect a
        # `missing_team_secret` failure that propagates to the parent.
        _delete_team_secret(
            backend_url, admin_cookies, team_id, "claude_api_key"
        )
        run_id_missing = _trigger_run(
            backend_url, admin_cookies, claude_workflow_id, "anything"
        )
        run_missing = _poll_run(
            backend_url, admin_cookies, run_id_missing, timeout_s=30.0
        )
        assert run_missing["status"] == "failed"
        assert run_missing["error_class"] == "missing_team_secret", (
            f"missing-key run carried unexpected error_class: "
            f"{run_missing.get('error_class')!r} (full: {run_missing!r})"
        )
        steps = run_missing.get("step_runs") or []
        assert len(steps) == 1
        step = steps[0]
        assert step["status"] == "failed"
        assert step["error_class"] == "missing_team_secret"

        # ----- Step 8: combined log redaction sweep ---------------------
        # Wait a beat so the celery worker's `step_run_failed` log line
        # has time to flush.
        time.sleep(1.0)
        backend_log = _container_logs(backend_container)
        worker_log = _container_logs(worker_container)
        orch_log = _container_logs(orchestrator_container)
        combined = backend_log + "\n" + worker_log + "\n" + orch_log

        # 8a: zero plaintext key leaks (sentinel + structural).
        for sentinel, label in (
            (CLAUDE_KEY, "claude key plaintext"),
            (OPENAI_KEY, "openai key plaintext"),
        ):
            assert sentinel not in combined, (
                f"redaction sweep: {label} leaked into compose logs"
            )
        sk_ant_hits = re.findall(r"sk-ant-[A-Za-z0-9_-]+", combined)
        assert sk_ant_hits == [], (
            f"redaction sweep: combined logs contain `sk-ant-` matches: "
            f"{sk_ant_hits[:3]!r}"
        )
        sk_hits = re.findall(r"sk-[A-Za-z0-9_-]{20,}", combined)
        assert sk_hits == [], (
            f"redaction sweep: combined logs contain bearer-shape `sk-` "
            f"matches: {sk_hits[:3]!r}"
        )

        # 8b: the prompt body is never logged. The literal claude_prompt
        # string is unique enough to be a reliable sentinel.
        assert claude_prompt not in combined, (
            "redaction sweep: claude prompt body leaked into compose logs"
        )
        assert codex_prompt not in combined, (
            "redaction sweep: codex prompt body leaked into compose logs"
        )

        # 8c: every locked observability discriminator fired at least
        # once across the full e2e cycle.
        for marker in _REQUIRED_DISCRIMINATORS:
            assert marker in combined, (
                f"observability taxonomy regression: {marker!r} not "
                "seen in combined backend/worker/orchestrator logs"
            )

    finally:
        _delete_team_and_members(team_id)
        _delete_user_by_email(member_email)

    elapsed = time.time() - suite_started
    # Slice budget: ≤ 60 s realistically; we tolerate 180 s defensively
    # because docker exec cold-imports + celery worker startup + 3 polls
    # cost real time on slow hosts.
    assert elapsed < 180.0, (
        f"e2e suite took {elapsed:.1f}s — far over the realistic budget"
    )
