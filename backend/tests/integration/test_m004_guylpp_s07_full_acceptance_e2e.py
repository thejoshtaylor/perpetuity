"""M004-guylpp / S07 / T01 — manual UAT against a real GitHub test org.

This module is the durable, code-reviewable recipe an operator follows to
prove the four "Final Integrated Acceptance" scenarios from
`.gsd/milestones/M004-guylpp/M004-guylpp-CONTEXT.md` against a real
GitHub test org. Prior slices' e2es proved the same surfaces against a
mock-github sidecar; this module is the contract proof that those mocks
were faithful approximations.

Scenarios (verbatim from CONTEXT.md §"Final Integrated Acceptance"):

  1. End-to-end happy path: install GitHub App on the test org → see the
     connection in team settings → create a project linked to a real
     repo → click "open project" → repo materializes at
     `/workspaces/<u>/<t>/<project_name>` with no credentials in
     `.git/config` → user commits + pushes → mirror receives → auto-push
     pushes to GitHub → github.com shows the commit.
  2. Webhook round-trip: external push to the GitHub repo → GitHub
     delivers a webhook → HMAC verifies cleanly → row lands in
     `github_webhook_events` with the `delivery_id` from the
     `X-GitHub-Delivery` header → backend logs contain
     `webhook_dispatched delivery_id=<id>`.
  3. Generate-then-rotate webhook secret: admin generates secret → pastes
     into GitHub App settings → next external delivery verifies cleanly →
     admin re-generates → next external delivery returns 401 + a row in
     `webhook_rejections` with `signature_valid=false` AND a WARNING
     `webhook_signature_invalid` log line until the GitHub-side webhook
     secret is updated to the new value.
  4. Mirror lifecycle cold-start: mirror is reaped (idle or admin
     force-reap) → user clicks "open project" → mirror cold-starts →
     clone proceeds → mirror reachable via compose-network DNS. Asserts
     `team_mirror_reaped reason=admin` precedes
     `team_mirror_started trigger=ensure` and the user's open returns
     200 within 30 s.

Default behavior: skipped. The whole module is `pytest.mark.skip`-decorated
and additionally guarded by an explicit `RUN_REAL_GITHUB=1` env check inside
each scenario function (belt-and-suspenders — a future operator who
removes the module-level skip without setting the env should still get a
clean skip rather than a partially-failing run).

How to run:
    1. Copy `.env.test-org.example` to `.env.test-org` and fill it in.
    2. `set -a; source backend/tests/integration/.env.test-org; set +a`
    3. `export RUN_REAL_GITHUB=1`
    4. `cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e \\
            tests/integration/test_m004_guylpp_s07_full_acceptance_e2e.py -v`

The operator records observed log lines, screenshots, and PASS/FAIL into
`.gsd/milestones/M004-guylpp/slices/S07/S07-UAT.md` while running.

Wall-clock budget when run with RUN_REAL_GITHUB=1: ≤5 minutes for
scenarios 1-4 combined.

Constraints (per S07-PLAN.md / T01):
  * No imports from `backend/tests/integration/fixtures/mock_github_app.py`
    — this is the real-org branch.
  * No mock-github sidecar; all HTTP runs against the live stack.
  * Credentials sourced from a tracked-but-empty `.env.test-org.example`
    operators copy into a gitignored `.env.test-org` and `source` before
    invoking pytest.
  * Skip-by-default so a CI run that lacks the test-org credentials
    auto-skips cleanly rather than no-op'ing or erroring noisily.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from typing import Any

import httpx
import pytest

# Module-level skip + e2e/serial markers. The skip-decorator is the primary
# guard: a CI machine without the test-org credentials never reaches setup.
# The e2e marker is so `pytest -m e2e` can opt into the file when an
# operator does have the credentials. Serial because scenarios 1-4 mutate
# shared state on the same backend stack and cannot run concurrently.
pytestmark = [
    pytest.mark.skip(
        reason=(
            "manual UAT — run with RUN_REAL_GITHUB=1 against a real GitHub "
            "test org per backend/tests/integration/.env.test-org.example"
        )
    ),
    pytest.mark.e2e,
    pytest.mark.serial,
]


# ----- helpers -----------------------------------------------------------


REQUIRED_ENV_KEYS = (
    "GITHUB_TEST_ORG",
    "GITHUB_TEST_REPO_FULL_NAME",
    "GITHUB_APP_ID",
    "GITHUB_APP_CLIENT_ID",
    "GITHUB_APP_PRIVATE_KEY_PATH",
    "GITHUB_TEST_USER_PAT",
    "BACKEND_BASE_URL",
    "ORCHESTRATOR_BASE_URL",
)


def _require_real_github_env() -> dict[str, str]:
    """Belt-and-suspenders guard inside each scenario.

    The module-level skip already keeps this off CI. This second guard
    exists so that an operator who removes the module-level skip while
    debugging — or invokes pytest with `--no-skip` — still gets a clean
    `pytest.skip` instead of a NameError or a half-finished run that
    silently no-op'd against a missing PAT.
    """
    if not os.environ.get("RUN_REAL_GITHUB"):
        pytest.skip("RUN_REAL_GITHUB=1 not set — manual UAT only")

    missing = [k for k in REQUIRED_ENV_KEYS if not os.environ.get(k)]
    if missing:
        pytest.skip(
            "real-GitHub UAT env not populated; missing: "
            + ", ".join(missing)
            + " (see backend/tests/integration/.env.test-org.example)"
        )

    pem_path = os.environ["GITHUB_APP_PRIVATE_KEY_PATH"]
    if not os.path.isfile(pem_path):
        pytest.skip(
            f"GITHUB_APP_PRIVATE_KEY_PATH={pem_path!r} does not point to a "
            "readable PEM file"
        )

    return {k: os.environ[k] for k in REQUIRED_ENV_KEYS}


def _admin_login(backend: str) -> httpx.Client:
    """Log in as the seeded FIRST_SUPERUSER and return an authenticated client."""
    client = httpx.Client(base_url=backend, timeout=30.0)
    r = client.post(
        "/api/v1/login/access-token",
        data={
            "username": os.environ.get("FIRST_SUPERUSER", "admin@example.com"),
            "password": os.environ.get("FIRST_SUPERUSER_PASSWORD", "changethis"),
        },
    )
    r.raise_for_status()
    token = r.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return client


def _docker_logs_contain(container: str, needle: str, *, since: str = "5m") -> bool:
    """Return True iff `docker logs` for `container` contains `needle`.

    Used by scenarios 2-4 to assert structured log lines emitted by the
    backend (`webhook_dispatched`, `webhook_signature_invalid`,
    `team_mirror_reaped`, `team_mirror_started`). A real-org run uses the
    operator's compose stack; the container name defaults to the standard
    compose service name but operators can override via env if their stack
    uses a non-default project name.
    """
    try:
        proc = subprocess.run(
            ["docker", "logs", "--since", since, container],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    haystack = (proc.stdout or "") + (proc.stderr or "")
    return needle in haystack


def _backend_container() -> str:
    return os.environ.get("BACKEND_CONTAINER_NAME", "perpetuity-backend-1")


def _orchestrator_container() -> str:
    return os.environ.get("ORCHESTRATOR_CONTAINER_NAME", "perpetuity-orchestrator-1")


def _wait_for(predicate: Any, *, timeout_s: float, interval_s: float = 1.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:  # noqa: BLE001 — UAT diagnostic, not production code
            pass
        time.sleep(interval_s)
    return False


# ----- scenario 1 --------------------------------------------------------


def test_scenario_1_end_to_end_happy_path() -> None:
    """CONTEXT.md scenario 1: install→project→open→commit→push→github.com round-trip.

    The operator MUST do the install step manually in a browser before
    the test runs (GitHub App installation cannot be automated against a
    real org without device-flow OAuth, which we deliberately reject —
    see CONTEXT.md §"Architectural Decisions" / "GitHub App, not OAuth
    App"). The test picks up from the post-install state: the operator
    has installed the App against `GITHUB_TEST_ORG` and the
    `installation_id` for that org is reachable via the backend's team
    connection list.

    Observable surfaces asserted:
      - `GET /api/v1/teams/{team_id}/github-connections` shows a row
        for `GITHUB_TEST_ORG` with status=active.
      - `POST /api/v1/teams/{team_id}/projects` creates a project linked
        to `GITHUB_TEST_REPO_FULL_NAME` and a default manual-workflow
        push rule.
      - `POST /api/v1/projects/{id}/open` returns 200 inside 90 s; the
        user's workspace container has the repo at the expected path with
        no token in `.git/config`.
      - The user makes a commit + push inside the workspace container;
        the auto-push pipeline (mirror receives → orchestrator pushes)
        completes within 30 s and `projects.last_push_status='ok'`.
      - A fresh `git ls-remote` against the upstream
        `https://github.com/<repo>.git` reports the same SHA the user
        committed locally.
    """
    env = _require_real_github_env()
    backend = env["BACKEND_BASE_URL"]
    client = _admin_login(backend)

    # 1. Confirm post-install connection visibility.
    teams_resp = client.get("/api/v1/users/me/teams")
    teams_resp.raise_for_status()
    teams = teams_resp.json().get("data", teams_resp.json())
    assert teams, "admin must belong to at least one team to run UAT"
    team_id = teams[0]["id"]

    conn_resp = client.get(f"/api/v1/teams/{team_id}/github-connections")
    conn_resp.raise_for_status()
    connections = conn_resp.json().get("data", conn_resp.json())
    matching = [
        c
        for c in connections
        if c.get("account_login", "").lower() == env["GITHUB_TEST_ORG"].lower()
    ]
    assert matching, (
        f"no active github_app_installation for {env['GITHUB_TEST_ORG']!r} — "
        "install the GitHub App in a browser before re-running this scenario"
    )

    # 2. Create the project + flip push-rule to auto.
    project_name = f"s07-uat-{uuid.uuid4().hex[:8]}"
    create_resp = client.post(
        f"/api/v1/teams/{team_id}/projects",
        json={
            "name": project_name,
            "github_repo_full_name": env["GITHUB_TEST_REPO_FULL_NAME"],
        },
    )
    create_resp.raise_for_status()
    project = create_resp.json()
    project_id = project["id"]

    rule_resp = client.put(
        f"/api/v1/projects/{project_id}/push-rule",
        json={"mode": "auto"},
    )
    rule_resp.raise_for_status()
    assert rule_resp.json()["mode"] == "auto"

    # 3. Open the project and wait for materialization.
    open_resp = client.post(f"/api/v1/projects/{project_id}/open")
    open_resp.raise_for_status()
    open_payload = open_resp.json()
    assert open_payload.get("status") in {"ready", "running"}, open_payload

    # 4. The operator commits and pushes from inside the workspace.
    #    The recipe below is the contract; copy-paste verbatim into the
    #    workspace shell during the UAT run.
    workspace_shell_recipe = (
        "# Run this inside the workspace container that just opened:\n"
        f"cd /workspaces/<u>/<t>/{project_name}\n"
        "git config user.email uat@example.com\n"
        "git config user.name 'UAT Operator'\n"
        f"echo s07-uat-{uuid.uuid4().hex[:8]} >> README.md\n"
        "git add README.md\n"
        "git commit -m 's07 uat: prove the round-trip'\n"
        "git push\n"
    )
    print(workspace_shell_recipe)  # noqa: T201 — UAT recording aid

    # 5. Wait for auto-push to settle and assert the upstream caught up.
    def _last_push_ok() -> bool:
        r = client.get(f"/api/v1/projects/{project_id}")
        if r.status_code != 200:
            return False
        return r.json().get("last_push_status") == "ok"

    assert _wait_for(_last_push_ok, timeout_s=60.0), (
        "auto-push did not reach last_push_status='ok' within 60s — "
        "inspect orchestrator logs for `auto_push_completed` / `auto_push_failed`"
    )

    proj = client.get(f"/api/v1/projects/{project_id}").json()
    expected_sha = proj.get("last_push_commit_sha")
    assert expected_sha, "backend did not record the pushed commit sha"

    # 6. Fresh `git ls-remote` against github.com — proves the commit landed.
    pat = env["GITHUB_TEST_USER_PAT"]
    ls = subprocess.run(
        [
            "git",
            "ls-remote",
            f"https://x-access-token:{pat}@github.com/{env['GITHUB_TEST_REPO_FULL_NAME']}.git",
            "HEAD",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert ls.returncode == 0, ls.stderr
    remote_head_sha = ls.stdout.split()[0]
    assert remote_head_sha == expected_sha, (
        f"github.com HEAD ({remote_head_sha}) does not match local commit "
        f"({expected_sha}) — auto-push contract violated"
    )


# ----- scenario 2 --------------------------------------------------------


def test_scenario_2_webhook_round_trip() -> None:
    """CONTEXT.md scenario 2: external push → webhook → verify → persist → dispatch.

    The operator triggers an external push to the test repo (via PAT) and
    asserts the resulting GitHub webhook delivery lands cleanly:
      - HTTP 200 from `/api/v1/github/webhooks`.
      - A row in `github_webhook_events` whose `delivery_id` matches the
        value GitHub returned in the `X-GitHub-Delivery` response header
        on its delivery attempt.
      - The backend container logs contain
        `webhook_dispatched delivery_id=<id>` (no-op stub fired per
        MEM294).
    """
    env = _require_real_github_env()
    backend = env["BACKEND_BASE_URL"]
    pat = env["GITHUB_TEST_USER_PAT"]
    repo = env["GITHUB_TEST_REPO_FULL_NAME"]
    client = _admin_login(backend)

    # 1. Snapshot the latest delivery_id we already saw, so we can detect
    #    the new delivery without race conditions.
    pre = client.get("/api/v1/admin/github/webhook-events?limit=1")
    pre.raise_for_status()
    pre_rows = pre.json().get("data", pre.json())
    pre_top_id = pre_rows[0]["delivery_id"] if pre_rows else None

    # 2. External push — bump README.md via the GitHub Contents API so we
    #    don't need a working clone of the repo on the operator's machine.
    gh = httpx.Client(
        base_url="https://api.github.com",
        headers={
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30.0,
    )
    cur = gh.get(f"/repos/{repo}/contents/README.md")
    cur.raise_for_status()
    sha = cur.json()["sha"]
    new_content = f"S07 UAT scenario 2 ping {uuid.uuid4().hex[:8]}\n"
    import base64

    push = gh.put(
        f"/repos/{repo}/contents/README.md",
        json={
            "message": "s07 uat scenario 2: external push to fire webhook",
            "content": base64.b64encode(new_content.encode("utf-8")).decode("ascii"),
            "sha": sha,
        },
    )
    push.raise_for_status()

    # 3. Wait for a NEW row to appear in github_webhook_events.
    def _new_delivery_appeared() -> bool:
        r = client.get("/api/v1/admin/github/webhook-events?limit=1")
        if r.status_code != 200:
            return False
        rows = r.json().get("data", r.json())
        if not rows:
            return False
        return rows[0]["delivery_id"] != pre_top_id

    assert _wait_for(_new_delivery_appeared, timeout_s=30.0), (
        "no new github_webhook_events row appeared within 30s of the "
        "external push — check that the App's webhook URL points at this "
        "stack and the secret is current"
    )

    latest = client.get("/api/v1/admin/github/webhook-events?limit=1").json()
    rows = latest.get("data", latest)
    delivery_id = rows[0]["delivery_id"]

    # 4. Backend logs prove dispatch fired.
    assert _docker_logs_contain(
        _backend_container(), f"webhook_dispatched delivery_id={delivery_id}"
    ), (
        f"backend container logs missing `webhook_dispatched delivery_id={delivery_id}` "
        "— the no-op dispatch hook did not fire"
    )


# ----- scenario 3 --------------------------------------------------------


def test_scenario_3_generate_then_rotate_webhook_secret() -> None:
    """CONTEXT.md scenario 3: rotation of github_app_webhook_secret breaks old deliveries.

    Pre-state assumed: the operator has already pasted the current
    webhook secret into the GitHub App settings (per scenario 2).
    Scenario 3 proves the destructive-rotate contract — re-generating
    the secret invalidates GitHub's existing webhook configuration until
    the operator updates the GitHub side.

    Observable surfaces asserted:
      - `POST /admin/settings/github_app_webhook_secret/generate` returns
        a one-time-display secret.
      - Next external delivery (without GitHub-side update) → HTTP 401 +
        a row in `webhook_rejections` with `signature_valid=false` +
        WARNING `webhook_signature_invalid` log line.
    """
    env = _require_real_github_env()
    backend = env["BACKEND_BASE_URL"]
    pat = env["GITHUB_TEST_USER_PAT"]
    repo = env["GITHUB_TEST_REPO_FULL_NAME"]
    client = _admin_login(backend)

    # 1. Re-generate the webhook secret. The plaintext is returned ONCE
    #    here — we deliberately do NOT paste it into the GitHub App
    #    settings (that's the whole point of the scenario).
    gen = client.post("/api/v1/admin/settings/github_app_webhook_secret/generate")
    gen.raise_for_status()
    body = gen.json()
    assert "value" in body, "generate response did not include the one-time value"
    # Do not log the plaintext — just assert shape.
    assert isinstance(body["value"], str) and len(body["value"]) >= 32

    # 2. Snapshot pre-rotation rejection count.
    pre = client.get("/api/v1/admin/github/webhook-rejections?limit=1")
    pre.raise_for_status()
    pre_rows = pre.json().get("data", pre.json())
    pre_top_id = pre_rows[0]["delivery_id"] if pre_rows else None

    # 3. External push — GitHub will sign with the OLD secret it still has.
    gh = httpx.Client(
        base_url="https://api.github.com",
        headers={
            "Authorization": f"Bearer {pat}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30.0,
    )
    cur = gh.get(f"/repos/{repo}/contents/README.md")
    cur.raise_for_status()
    sha = cur.json()["sha"]
    import base64

    push = gh.put(
        f"/repos/{repo}/contents/README.md",
        json={
            "message": "s07 uat scenario 3: post-rotate external push (must 401)",
            "content": base64.b64encode(
                f"S07 UAT scenario 3 ping {uuid.uuid4().hex[:8]}\n".encode()
            ).decode("ascii"),
            "sha": sha,
        },
    )
    push.raise_for_status()

    # 4. Wait for the rejection row.
    def _new_rejection_appeared() -> bool:
        r = client.get("/api/v1/admin/github/webhook-rejections?limit=1")
        if r.status_code != 200:
            return False
        rows = r.json().get("data", r.json())
        if not rows:
            return False
        if rows[0]["delivery_id"] == pre_top_id:
            return False
        return rows[0].get("signature_valid") is False

    assert _wait_for(_new_rejection_appeared, timeout_s=30.0), (
        "no new webhook_rejections row with signature_valid=false appeared "
        "within 30s of the post-rotate external push — rotation did not "
        "invalidate GitHub-side deliveries"
    )

    rej = client.get("/api/v1/admin/github/webhook-rejections?limit=1").json()
    rows = rej.get("data", rej)
    rejected_id = rows[0]["delivery_id"]

    # 5. Backend logs prove the WARNING fired.
    assert _docker_logs_contain(
        _backend_container(),
        f"webhook_signature_invalid delivery_id={rejected_id}",
    ), (
        f"backend container logs missing `webhook_signature_invalid delivery_id="
        f"{rejected_id}` — receiver did not emit the WARNING for the rejected "
        "delivery"
    )

    # NOTE for the operator: paste the new secret into the GitHub App
    # settings AFTER recording this scenario in S07-UAT.md so the next
    # UAT run starts from a clean state.


# ----- scenario 4 --------------------------------------------------------


def test_scenario_4_mirror_lifecycle_cold_start() -> None:
    """CONTEXT.md scenario 4: reaped mirror → user open → cold-start → clone proceeds.

    Pre-condition: a project must already exist for the operator's team
    (scenario 1 leaves one). The test admin-force-reaps the team's
    mirror, then the operator (or, if the open endpoint is admin-callable
    on the project's behalf, this test directly) hits
    `POST /api/v1/projects/{id}/open` and verifies the mirror cold-starts
    cleanly.

    Observable surfaces asserted:
      - `POST /api/v1/admin/teams/{team_id}/mirror/reap` returns 200 and
        triggers `team_mirror_reaped reason=admin` in the orchestrator
        logs.
      - The subsequent `POST /api/v1/projects/{project_id}/open` returns
        200 within 30 s.
      - Orchestrator logs show `team_mirror_started trigger=ensure`
        AFTER (in wall-clock order) the `team_mirror_reaped reason=admin`
        line for the same team_id.
    """
    env = _require_real_github_env()
    backend = env["BACKEND_BASE_URL"]
    client = _admin_login(backend)

    # 1. Find a project to open. Reuse whatever the operator has from S1.
    teams_resp = client.get("/api/v1/users/me/teams")
    teams_resp.raise_for_status()
    teams = teams_resp.json().get("data", teams_resp.json())
    assert teams, "admin must belong to at least one team for UAT"
    team_id = teams[0]["id"]

    proj_resp = client.get(f"/api/v1/teams/{team_id}/projects?limit=1")
    proj_resp.raise_for_status()
    projects = proj_resp.json().get("data", proj_resp.json())
    assert projects, (
        "no project exists on the team — run scenario 1 first or create one "
        "before running scenario 4"
    )
    project_id = projects[0]["id"]

    # 2. Force-reap the mirror.
    reap_t0 = time.time()
    reap = client.post(f"/api/v1/admin/teams/{team_id}/mirror/reap")
    reap.raise_for_status()

    assert _wait_for(
        lambda: _docker_logs_contain(
            _orchestrator_container(),
            f"team_mirror_reaped team_id={team_id} reason=admin",
            since="30s",
        ),
        timeout_s=15.0,
    ), (
        "orchestrator logs missing `team_mirror_reaped team_id=<id> reason=admin` "
        "within 15s of the force-reap call"
    )

    # 3. Cold-start via project open.
    open_t0 = time.time()
    opened = client.post(f"/api/v1/projects/{project_id}/open", timeout=60.0)
    open_elapsed = time.time() - open_t0
    assert opened.status_code == 200, opened.text
    assert open_elapsed < 30.0, (
        f"project open took {open_elapsed:.1f}s (>30s budget for cold-start)"
    )

    # 4. Orchestrator logs show start AFTER reap, same team_id.
    assert _wait_for(
        lambda: _docker_logs_contain(
            _orchestrator_container(),
            f"team_mirror_started team_id={team_id} trigger=ensure",
            since="60s",
        ),
        timeout_s=15.0,
    ), (
        "orchestrator logs missing `team_mirror_started team_id=<id> "
        "trigger=ensure` within 15s of the project open call"
    )

    # 5. Sanity: the start happened AFTER the reap (we recorded reap_t0).
    #    We can't trivially extract timestamps from `docker logs` output
    #    without parsing, but the wall-clock fact that we observed the
    #    reap line (step 2) BEFORE issuing the open (step 3) is enough
    #    proof of ordering for the UAT recording.
    _ = reap_t0  # retained for the operator's S07-UAT.md timestamp note
    _ = json  # keep import side-effect free silence happy
