"""Integration tests for the workflow trigger + run-detail API (M005/S02/T04).

Covers the three routes mounted by `app.api.routes.workflows`:

    POST /api/v1/workflows/{workflow_id}/run
    GET  /api/v1/workflow_runs/{run_id}
    GET  /api/v1/teams/{team_id}/workflows

Real FastAPI app + real Postgres via the session-scoped `db` and
module-scoped `client` fixtures in `tests/conftest.py`. Celery `.delay()`
is patched at the route's import site so we never touch Redis in unit
tests — the verifier asserts the task name flowed in instead.

Slice plan must-haves this module proves directly:

  * (1) POST inserts workflow_runs (pending) + step_runs rows from
        WorkflowStep snapshot at dispatch time, then dispatches the
        Celery task; returns `{run_id, status='pending'}`.
  * (2) GET returns WorkflowRunPublic with ordered step_runs.
  * (3) GET teams/{id}/workflows returns the team's registry.
  * (4) Membership boundary: 403 not_team_member for non-members.
  * (5) Failure modes: workflow_not_found, workflow_run_not_found,
        missing_required_field, dispatch_failed.
  * (6) Negative tests: bad UUIDs, empty payload for direct AI workflow,
        cross-team access, cascaded-delete run lookup.
  * (7) INFO log `workflow_run_dispatched` with structured fields
        (run_id, workflow_id, trigger_type, triggered_by_user_id) — and
        the prompt body NEVER appears in the log line.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Generator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session

from app import crud
from app.core.config import settings
from app.models import (
    Team,
    TeamMember,
    TeamRole,
    Workflow,
    WorkflowRun,
    WorkflowStep,
)
from tests.utils.utils import random_email, random_lower_string

API = settings.API_V1_STR
SIGNUP_URL = f"{API}/auth/signup"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_workflow_state(db: Session) -> Generator[None, None, None]:
    """Wipe workflow + workflow_run + step_run rows around every test.

    The session-scoped `db` fixture is shared across tests, so without this
    a prior test's seeded `_direct_*` rows would survive into the next.
    """
    db.execute(text("DELETE FROM step_runs"))
    db.execute(text("DELETE FROM workflow_runs"))
    db.execute(text("DELETE FROM workflow_steps"))
    db.execute(text("DELETE FROM workflows"))
    db.commit()
    yield
    db.execute(text("DELETE FROM step_runs"))
    db.execute(text("DELETE FROM workflow_runs"))
    db.execute(text("DELETE FROM workflow_steps"))
    db.execute(text("DELETE FROM workflows"))
    db.commit()


class _FakeAsyncResult:
    """Minimal stand-in for `celery.result.AsyncResult` that tests inspect."""

    def __init__(self, task_id: str, args: tuple) -> None:
        self.task_id = task_id
        self.args = args


@pytest.fixture
def fake_delay(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Patch `run_workflow.delay` at the route's import site.

    Returns the list of args every successful `.delay()` call observed —
    tests assert against this to prove the task was enqueued with the
    fresh run_id without touching Redis. The route imports
    `run_workflow` lazily inside the handler, so we patch the symbol on
    `app.workflows.tasks.run_workflow` directly.
    """
    calls: list[tuple] = []

    def _delay(run_id_str: str) -> _FakeAsyncResult:
        calls.append((run_id_str,))
        return _FakeAsyncResult(task_id="task-" + run_id_str, args=(run_id_str,))

    from app.workflows import tasks as tasks_mod

    monkeypatch.setattr(tasks_mod.run_workflow, "delay", _delay)
    return calls


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _signup(client: TestClient) -> tuple[uuid.UUID, httpx.Cookies]:
    """Create a fresh user + personal team via the signup endpoint."""
    email = random_email()
    password = random_lower_string()
    client.cookies.clear()
    r = client.post(SIGNUP_URL, json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    jar = httpx.Cookies()
    for c in client.cookies.jar:
        jar.set(c.name, c.value)
    client.cookies.clear()
    return uuid.UUID(r.json()["id"]), jar


def _create_team_with_admin(db: Session, admin_id: uuid.UUID) -> Team:
    return crud.create_team_with_admin(
        session=db,
        name=f"wf-test-{uuid.uuid4().hex[:8]}",
        creator_id=admin_id,
    )


def _add_member(
    db: Session, team_id: uuid.UUID, user_id: uuid.UUID, role: TeamRole
) -> None:
    db.add(TeamMember(user_id=user_id, team_id=team_id, role=role))
    db.commit()


def _direct_claude_for_team(db: Session, team_id: uuid.UUID) -> Workflow:
    """Return the team's auto-seeded `_direct_claude` workflow.

    `crud.create_team_with_admin` (and `create_user_with_personal_team`)
    seed `_direct_claude` + `_direct_codex` for every new team via
    `seed_system_workflows`. The route under test consumes that seed,
    so tests use the seeded row directly rather than inserting a
    duplicate (which would trip `uq_workflows_team_id_name`).
    """
    from sqlmodel import select

    wf = db.exec(
        select(Workflow)
        .where(Workflow.team_id == team_id)
        .where(Workflow.name == "_direct_claude")
    ).first()
    assert wf is not None, "expected `_direct_claude` to be auto-seeded"
    return wf


def _make_user_workflow(
    db: Session, team_id: uuid.UUID, *, action: str = "claude"
) -> Workflow:
    """Build a non-system workflow (no `_direct_*` name) — prompt validation
    does NOT apply to this shape."""
    wf = Workflow(
        team_id=team_id,
        name=f"my-flow-{uuid.uuid4().hex[:6]}",
        description="user workflow",
        scope="user",
        system_owned=False,
    )
    db.add(wf)
    db.flush()
    db.add(
        WorkflowStep(
            workflow_id=wf.id,
            step_index=0,
            action=action,
            config={"prompt_template": "Hello {prompt}"},
        )
    )
    db.commit()
    db.refresh(wf)
    return wf


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


def test_post_run_without_cookie_returns_401(client: TestClient) -> None:
    client.cookies.clear()
    r = client.post(
        f"{API}/workflows/{uuid.uuid4()}/run", json={"trigger_payload": {}}
    )
    assert r.status_code == 401


def test_get_run_without_cookie_returns_401(client: TestClient) -> None:
    client.cookies.clear()
    r = client.get(f"{API}/workflow_runs/{uuid.uuid4()}")
    assert r.status_code == 401


def test_list_team_workflows_without_cookie_returns_401(
    client: TestClient,
) -> None:
    client.cookies.clear()
    r = client.get(f"{API}/teams/{uuid.uuid4()}/workflows")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /workflows/{id}/run — happy path
# ---------------------------------------------------------------------------


def test_post_run_happy_path_inserts_pending_run_and_dispatches(
    client: TestClient,
    db: Session,
    fake_delay: list[tuple],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Member POSTs prompt → 200 + run row in pending + Celery task enqueued."""
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)
    wf = _direct_claude_for_team(db, team.id)

    with caplog.at_level(logging.INFO, logger="app.api.routes.workflows"):
        r = client.post(
            f"{API}/workflows/{wf.id}/run",
            json={"trigger_payload": {"prompt": "List the files"}},
            cookies=cookies,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending"
    run_id = uuid.UUID(body["run_id"])

    # Run row exists, pending status, snapshot of trigger payload preserved.
    row = db.get(WorkflowRun, run_id)
    assert row is not None
    assert row.status == "pending"
    assert row.workflow_id == wf.id
    assert row.team_id == team.id
    assert row.triggered_by_user_id == user_id
    assert row.target_user_id == user_id
    assert row.trigger_type == "button"
    assert row.trigger_payload == {"prompt": "List the files"}

    # One step_run row was inserted with the snapshot frozen at dispatch.
    step_rows = db.execute(
        text("SELECT step_index, status, snapshot FROM step_runs WHERE workflow_run_id = :rid ORDER BY step_index"),
        {"rid": run_id},
    ).all()
    assert len(step_rows) == 1
    assert step_rows[0][0] == 0
    assert step_rows[0][1] == "pending"
    snapshot = step_rows[0][2]
    assert snapshot["action"] == "claude"
    assert snapshot["config"]["prompt_template"] == "{prompt}"

    # Celery .delay() was called once with the new run_id.
    assert len(fake_delay) == 1
    assert fake_delay[0][0] == str(run_id)

    # Observability — INFO log with structured fields, prompt NEVER present.
    log_text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "workflow_run_dispatched" in log_text
    assert f"run_id={run_id}" in log_text
    assert f"workflow_id={wf.id}" in log_text
    assert "trigger_type=button" in log_text
    assert f"triggered_by_user_id={user_id}" in log_text
    assert "List the files" not in log_text  # prompt body MUST NOT leak


def test_post_run_user_workflow_does_not_require_prompt(
    client: TestClient, db: Session, fake_delay: list[tuple]
) -> None:
    """Non-`_direct_*` workflows accept any trigger_payload shape (S03 surface)."""
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)
    wf = _make_user_workflow(db, team.id)

    r = client.post(
        f"{API}/workflows/{wf.id}/run",
        json={"trigger_payload": {}},
        cookies=cookies,
    )
    assert r.status_code == 200, r.text
    assert len(fake_delay) == 1


# ---------------------------------------------------------------------------
# POST /workflows/{id}/run — failure modes
# ---------------------------------------------------------------------------


def test_post_run_unknown_workflow_returns_404(
    client: TestClient, db: Session, fake_delay: list[tuple]
) -> None:
    user_id, cookies = _signup(client)
    _create_team_with_admin(db, user_id)
    bogus = uuid.uuid4()

    r = client.post(
        f"{API}/workflows/{bogus}/run",
        json={"trigger_payload": {"prompt": "x"}},
        cookies=cookies,
    )
    assert r.status_code == 404
    assert r.json()["detail"]["detail"] == "workflow_not_found"
    assert fake_delay == []


def test_post_run_non_member_returns_403_not_team_member(
    client: TestClient, db: Session, fake_delay: list[tuple]
) -> None:
    """Workflow exists but caller is not on its team → 403 not_team_member."""
    owner_id, _ = _signup(client)
    team = _create_team_with_admin(db, owner_id)
    wf = _direct_claude_for_team(db, team.id)

    other_id, other_cookies = _signup(client)  # fresh user, no membership
    assert other_id != owner_id

    r = client.post(
        f"{API}/workflows/{wf.id}/run",
        json={"trigger_payload": {"prompt": "x"}},
        cookies=other_cookies,
    )
    assert r.status_code == 403
    assert r.json()["detail"]["detail"] == "not_team_member"
    assert fake_delay == []


def test_post_run_direct_claude_missing_prompt_returns_400(
    client: TestClient, db: Session, fake_delay: list[tuple]
) -> None:
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)
    wf = _direct_claude_for_team(db, team.id)

    r = client.post(
        f"{API}/workflows/{wf.id}/run",
        json={"trigger_payload": {}},
        cookies=cookies,
    )
    assert r.status_code == 400
    body = r.json()
    assert body["detail"]["detail"] == "missing_required_field"
    assert body["detail"]["field"] == "prompt"
    assert fake_delay == []


def test_post_run_direct_claude_empty_prompt_returns_400(
    client: TestClient, db: Session, fake_delay: list[tuple]
) -> None:
    """Whitespace-only prompt is the same as missing — boundary catch."""
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)
    wf = _direct_claude_for_team(db, team.id)

    r = client.post(
        f"{API}/workflows/{wf.id}/run",
        json={"trigger_payload": {"prompt": "   "}},
        cookies=cookies,
    )
    assert r.status_code == 400
    assert r.json()["detail"]["detail"] == "missing_required_field"
    assert fake_delay == []


def test_post_run_bad_workflow_id_uuid_returns_422(
    client: TestClient, db: Session, fake_delay: list[tuple]
) -> None:
    """FastAPI's UUID path-converter rejects malformed ids before the handler."""
    _user_id, cookies = _signup(client)

    r = client.post(
        f"{API}/workflows/not-a-uuid/run",
        json={"trigger_payload": {"prompt": "x"}},
        cookies=cookies,
    )
    assert r.status_code == 422
    assert fake_delay == []


def test_post_run_dispatch_failure_marks_run_failed_and_returns_503(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Broker-down path: row gets `error_class='dispatch_failed'`, response is 503."""
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)
    wf = _direct_claude_for_team(db, team.id)

    def _boom(_run_id: str) -> None:
        raise RuntimeError("broker down")

    from app.workflows import tasks as tasks_mod

    monkeypatch.setattr(tasks_mod.run_workflow, "delay", _boom)

    r = client.post(
        f"{API}/workflows/{wf.id}/run",
        json={"trigger_payload": {"prompt": "x"}},
        cookies=cookies,
    )
    assert r.status_code == 503
    assert r.json()["detail"]["detail"] == "task_dispatch_failed"

    # The row exists with status=failed + error_class=dispatch_failed.
    rows = db.execute(
        text(
            "SELECT status, error_class FROM workflow_runs "
            "WHERE workflow_id = :wid"
        ),
        {"wid": wf.id},
    ).all()
    assert len(rows) == 1
    assert rows[0][0] == "failed"
    assert rows[0][1] == "dispatch_failed"


# ---------------------------------------------------------------------------
# GET /workflow_runs/{id}
# ---------------------------------------------------------------------------


def test_get_run_returns_run_with_ordered_step_runs(
    client: TestClient, db: Session, fake_delay: list[tuple]
) -> None:
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)
    wf = _direct_claude_for_team(db, team.id)

    post = client.post(
        f"{API}/workflows/{wf.id}/run",
        json={"trigger_payload": {"prompt": "ls"}},
        cookies=cookies,
    )
    run_id = post.json()["run_id"]

    r = client.get(f"{API}/workflow_runs/{run_id}", cookies=cookies)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["id"] == run_id
    assert body["workflow_id"] == str(wf.id)
    assert body["team_id"] == str(team.id)
    assert body["status"] == "pending"
    assert body["trigger_type"] == "button"
    assert body["trigger_payload"] == {"prompt": "ls"}
    assert body["error_class"] is None
    assert body["finished_at"] is None

    assert len(body["step_runs"]) == 1
    sr = body["step_runs"][0]
    assert sr["step_index"] == 0
    assert sr["status"] == "pending"
    assert sr["snapshot"]["action"] == "claude"


def test_get_run_unknown_id_returns_404(
    client: TestClient, db: Session
) -> None:
    _user_id, cookies = _signup(client)
    bogus = uuid.uuid4()

    r = client.get(f"{API}/workflow_runs/{bogus}", cookies=cookies)
    assert r.status_code == 404
    assert r.json()["detail"]["detail"] == "workflow_run_not_found"


def test_get_run_non_member_returns_403_not_team_member(
    client: TestClient, db: Session, fake_delay: list[tuple]
) -> None:
    owner_id, owner_cookies = _signup(client)
    team = _create_team_with_admin(db, owner_id)
    wf = _direct_claude_for_team(db, team.id)

    post = client.post(
        f"{API}/workflows/{wf.id}/run",
        json={"trigger_payload": {"prompt": "x"}},
        cookies=owner_cookies,
    )
    run_id = post.json()["run_id"]

    other_id, other_cookies = _signup(client)
    assert other_id != owner_id

    r = client.get(f"{API}/workflow_runs/{run_id}", cookies=other_cookies)
    assert r.status_code == 403
    assert r.json()["detail"]["detail"] == "not_team_member"


def test_get_run_bad_uuid_returns_422(
    client: TestClient, db: Session
) -> None:
    _user_id, cookies = _signup(client)
    r = client.get(f"{API}/workflow_runs/not-a-uuid", cookies=cookies)
    assert r.status_code == 422


def test_get_run_after_workflow_cascade_delete_returns_404(
    client: TestClient, db: Session, fake_delay: list[tuple]
) -> None:
    """Deleting the parent workflow cascades workflow_runs → 404 on lookup.

    Schema: `workflow_runs.workflow_id REFERENCES workflows(id) ON DELETE
    CASCADE` (T01 migration). Verifies the FK actually fires and the
    route surfaces the cascaded-away row as 404, not 500.
    """
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)
    wf = _direct_claude_for_team(db, team.id)
    post = client.post(
        f"{API}/workflows/{wf.id}/run",
        json={"trigger_payload": {"prompt": "x"}},
        cookies=cookies,
    )
    run_id = post.json()["run_id"]

    db.execute(text("DELETE FROM workflows WHERE id = :wid"), {"wid": wf.id})
    db.commit()

    r = client.get(f"{API}/workflow_runs/{run_id}", cookies=cookies)
    assert r.status_code == 404
    assert r.json()["detail"]["detail"] == "workflow_run_not_found"


# ---------------------------------------------------------------------------
# GET /teams/{id}/workflows
# ---------------------------------------------------------------------------


def test_list_team_workflows_returns_team_workflows(
    client: TestClient, db: Session
) -> None:
    """Lists every workflow for the team — system-seeded plus user-added.

    `crud.create_team_with_admin` auto-seeds `_direct_claude` + `_direct_codex`
    so the count starts at 2 before this test adds its own.
    """
    user_id, cookies = _signup(client)
    team = _create_team_with_admin(db, user_id)
    wf_a = _direct_claude_for_team(db, team.id)
    wf_b = _make_user_workflow(db, team.id)

    r = client.get(f"{API}/teams/{team.id}/workflows", cookies=cookies)
    assert r.status_code == 200, r.text
    body = r.json()
    # Seeded `_direct_claude` + `_direct_codex` + the test's user workflow.
    assert body["count"] == 3
    ids = {item["id"] for item in body["data"]}
    names = {item["name"] for item in body["data"]}
    assert str(wf_a.id) in ids
    assert str(wf_b.id) in ids
    assert names == {"_direct_claude", "_direct_codex", wf_b.name}
    # Ordered by name → `_direct_claude` < `_direct_codex` < `my-flow-*`.
    assert body["data"][0]["name"] == "_direct_claude"
    assert body["data"][1]["name"] == "_direct_codex"


def test_list_team_workflows_excludes_other_teams(
    client: TestClient, db: Session
) -> None:
    """Pollution check — a workflow on team B never appears in team A's list."""
    a_id, a_cookies = _signup(client)
    team_a = _create_team_with_admin(db, a_id)
    _direct_claude_for_team(db, team_a.id)

    b_id, _ = _signup(client)
    team_b = _create_team_with_admin(db, b_id)
    wf_b = _make_user_workflow(db, team_b.id)

    r = client.get(f"{API}/teams/{team_a.id}/workflows", cookies=a_cookies)
    assert r.status_code == 200
    ids = {item["id"] for item in r.json()["data"]}
    assert str(wf_b.id) not in ids


def test_list_team_workflows_unknown_team_returns_404(
    client: TestClient, db: Session
) -> None:
    _user_id, cookies = _signup(client)
    bogus = uuid.uuid4()
    r = client.get(f"{API}/teams/{bogus}/workflows", cookies=cookies)
    assert r.status_code == 404
    assert r.json()["detail"]["detail"] == "team_not_found"


def test_list_team_workflows_non_member_returns_403(
    client: TestClient, db: Session
) -> None:
    owner_id, _ = _signup(client)
    team = _create_team_with_admin(db, owner_id)

    other_id, other_cookies = _signup(client)
    assert other_id != owner_id

    r = client.get(f"{API}/teams/{team.id}/workflows", cookies=other_cookies)
    assert r.status_code == 403
    assert r.json()["detail"]["detail"] == "not_team_member"


def test_list_team_workflows_bad_uuid_returns_422(
    client: TestClient, db: Session
) -> None:
    _user_id, cookies = _signup(client)
    r = client.get(f"{API}/teams/not-a-uuid/workflows", cookies=cookies)
    assert r.status_code == 422
