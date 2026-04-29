"""Unit tests for ``app.services.dispatch.dispatch_github_event``.

These tests mock the SQLAlchemy session and httpx to avoid any live DB or
network calls. The dispatch function is async; tests drive it via
``asyncio.run``.

Logging is captured via a custom _LogCollector handler attached directly to
the ``app.services.dispatch`` logger. This is necessary because alembic's
``fileConfig`` (called by migration tests) resets logging config with
``disable_existing_loggers=True``, which removes pytest's caplog handler from
the root logger and breaks ``caplog`` when migration tests run first.

Coverage:
  1. mode='rule' with matching branch_pattern → orchestrator callback called
  2. mode='rule' with non-matching branch → auto_push_skipped logged, no call
  3. mode='manual_workflow' → WorkflowRun inserted + Celery enqueued
  4. Duplicate delivery_id (IntegrityError) → swallowed, run not enqueued
  5. Missing installation block in payload → no_match warning logged
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Log collection infrastructure
# ---------------------------------------------------------------------------


class _LogCollector(logging.Handler):
    """In-memory log collector that survives alembic logging resets."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def messages(self) -> list[str]:
        return [r.getMessage() for r in self.records]


@pytest.fixture()
def dispatch_logs() -> Generator[_LogCollector, None, None]:
    """Attach a fresh log collector to app.services.dispatch.

    Also clears the disabled flag that alembic's fileConfig sets on
    pre-existing loggers (disable_existing_loggers=True default).
    """
    collector = _LogCollector()
    collector.setLevel(logging.DEBUG)
    target = logging.getLogger("app.services.dispatch")
    target.setLevel(logging.DEBUG)
    # alembic's fileConfig sets disabled=True on pre-existing loggers;
    # reset it so our handler can receive records.
    target.disabled = False
    target.addHandler(collector)
    yield collector
    target.removeHandler(collector)


# ---------------------------------------------------------------------------
# Mock builders
# ---------------------------------------------------------------------------


def _make_installation(installation_id: int) -> MagicMock:
    inst = MagicMock()
    inst.installation_id = installation_id
    return inst


def _make_project(project_id: uuid.UUID, installation_id: int) -> MagicMock:
    prj = MagicMock()
    prj.id = project_id
    prj.installation_id = installation_id
    return prj


def _make_push_rule(
    project_id: uuid.UUID,
    *,
    mode: str,
    branch_pattern: str | None = None,
    workflow_id: str | None = None,
) -> MagicMock:
    rule = MagicMock()
    rule.project_id = project_id
    rule.mode = mode
    rule.branch_pattern = branch_pattern
    rule.workflow_id = workflow_id
    return rule


def _make_workflow(team_id: uuid.UUID) -> MagicMock:
    wf = MagicMock()
    wf.id = uuid.uuid4()
    wf.team_id = team_id
    wf.scope = "user"
    wf.target_user_id = None
    wf.round_robin_cursor = 0
    return wf


def _mock_session(
    *,
    installation: Any = None,
    projects: list | None = None,
    push_rule: Any = None,
    workflow: Any = None,
) -> MagicMock:
    """Build a minimal SQLModel Session mock for dispatch tests."""
    session = MagicMock()

    exec_result_install = MagicMock()
    exec_result_install.first.return_value = installation

    exec_result_projects = MagicMock()
    exec_result_projects.all.return_value = projects or []

    session.exec.side_effect = [exec_result_install, exec_result_projects]

    def _get(model, key):
        from app.models import ProjectPushRule, Workflow
        if model is ProjectPushRule:
            return push_rule
        if model is Workflow:
            return workflow
        return None

    session.get.side_effect = _get
    session.add = MagicMock()
    session.commit = MagicMock()
    session.rollback = MagicMock()
    session.execute = MagicMock()
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_dispatch_mode_rule_branch_match_calls_orchestrator(
    dispatch_logs: _LogCollector,
) -> None:
    """mode='rule' with matching branch calls orchestrator auto-push-callback."""
    from app.services.dispatch import dispatch_github_event

    installation_id = 11111
    project_id = uuid.uuid4()

    installation = _make_installation(installation_id)
    project = _make_project(project_id, installation_id)
    push_rule = _make_push_rule(project_id, mode="rule", branch_pattern="feature/*")

    session = _mock_session(
        installation=installation,
        projects=[project],
        push_rule=push_rule,
    )

    payload = {
        "installation": {"id": installation_id},
        "ref": "refs/heads/feature/foo",
    }

    with (
        patch("app.services.dispatch.httpx.Client") as mock_client_cls,
        patch("app.services.dispatch.settings") as mock_settings,
    ):
        mock_settings.ORCHESTRATOR_BASE_URL = "http://orchestrator:8001"
        mock_settings.ORCHESTRATOR_API_KEY = "test-key"
        mock_http = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_http

        asyncio.run(
            dispatch_github_event(
                "push", payload, delivery_id="delivery-001", session=session
            )
        )

    mock_http.post.assert_called_once()
    call_url = mock_http.post.call_args[0][0]
    assert str(project_id) in call_url
    assert "auto-push-callback" in call_url

    messages = dispatch_logs.messages()
    assert any(
        "webhook_dispatch_push_rule_evaluated" in m and "outcome=auto_push_triggered" in m
        for m in messages
    ), f"Expected auto_push_triggered in logs. Got: {messages}"

    assert any(
        "webhook_dispatched" in m and "dispatch_status=dispatched" in m
        for m in messages
    ), f"Expected webhook_dispatched dispatched. Got: {messages}"


def test_dispatch_mode_rule_branch_no_match_logs_skipped(
    dispatch_logs: _LogCollector,
) -> None:
    """mode='rule' with non-matching branch logs auto_push_skipped, no orchestrator call."""
    from app.services.dispatch import dispatch_github_event

    installation_id = 22222
    project_id = uuid.uuid4()

    installation = _make_installation(installation_id)
    project = _make_project(project_id, installation_id)
    push_rule = _make_push_rule(project_id, mode="rule", branch_pattern="feature/*")

    session = _mock_session(
        installation=installation,
        projects=[project],
        push_rule=push_rule,
    )

    payload = {
        "installation": {"id": installation_id},
        "ref": "refs/heads/main",
    }

    with (
        patch("app.services.dispatch.httpx.Client") as mock_client_cls,
        patch("app.services.dispatch.settings") as mock_settings,
    ):
        mock_settings.ORCHESTRATOR_BASE_URL = "http://orchestrator:8001"
        mock_settings.ORCHESTRATOR_API_KEY = "test-key"
        mock_http = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_http

        asyncio.run(
            dispatch_github_event(
                "push", payload, delivery_id="delivery-002", session=session
            )
        )

    mock_http.post.assert_not_called()

    messages = dispatch_logs.messages()
    assert any(
        "auto_push_skipped" in m and "branch_pattern_no_match" in m
        for m in messages
    ), f"Expected auto_push_skipped. Got: {messages}"

    assert any(
        "webhook_dispatch_push_rule_evaluated" in m and "outcome=branch_pattern_no_match" in m
        for m in messages
    ), f"Expected branch_pattern_no_match discriminator. Got: {messages}"


def test_dispatch_mode_manual_workflow_enqueues_run(
    dispatch_logs: _LogCollector,
) -> None:
    """mode='manual_workflow' inserts WorkflowRun and enqueues Celery task."""
    from app.services.dispatch import dispatch_github_event

    installation_id = 33333
    project_id = uuid.uuid4()
    team_id = uuid.uuid4()
    wf_id = uuid.uuid4()

    installation = _make_installation(installation_id)
    project = _make_project(project_id, installation_id)
    push_rule = _make_push_rule(
        project_id, mode="manual_workflow", workflow_id=str(wf_id)
    )
    workflow = _make_workflow(team_id)
    workflow.id = wf_id

    session = _mock_session(
        installation=installation,
        projects=[project],
        push_rule=push_rule,
        workflow=workflow,
    )

    payload = {
        "installation": {"id": installation_id},
        "pull_request": {"number": 42, "diff_url": "https://example.com/diff"},
    }

    with (
        patch("app.services.dispatch.run_workflow") as mock_task,
        patch("app.services.dispatch.resolve_target_user") as mock_resolve,
    ):
        mock_resolve.return_value = (uuid.uuid4(), None)

        asyncio.run(
            dispatch_github_event(
                "pull_request",
                payload,
                delivery_id="delivery-003",
                session=session,
            )
        )

    session.add.assert_called()
    session.commit.assert_called()
    mock_task.delay.assert_called_once()

    messages = dispatch_logs.messages()
    assert any(
        "webhook_run_enqueued" in m for m in messages
    ), f"Expected webhook_run_enqueued. Got: {messages}"

    assert any(
        "webhook_dispatched" in m and "dispatch_status=dispatched" in m
        for m in messages
    ), f"Expected webhook_dispatched dispatched. Got: {messages}"


def test_dispatch_duplicate_delivery_id_skipped(
    dispatch_logs: _LogCollector,
) -> None:
    """Duplicate delivery_id (IntegrityError on insert) is swallowed gracefully."""
    from sqlalchemy.exc import IntegrityError as SAIntegrityError
    from app.services.dispatch import dispatch_github_event

    installation_id = 44444
    project_id = uuid.uuid4()
    team_id = uuid.uuid4()
    wf_id = uuid.uuid4()

    installation = _make_installation(installation_id)
    project = _make_project(project_id, installation_id)
    push_rule = _make_push_rule(
        project_id, mode="manual_workflow", workflow_id=str(wf_id)
    )
    workflow = _make_workflow(team_id)
    workflow.id = wf_id

    session = _mock_session(
        installation=installation,
        projects=[project],
        push_rule=push_rule,
        workflow=workflow,
    )

    # First commit (WorkflowRun insert) raises IntegrityError; subsequent
    # commits (dispatch_status update) succeed normally.
    session.commit.side_effect = [
        SAIntegrityError(statement=None, params=None, orig=Exception("duplicate key")),
        None,  # _update_dispatch_status commit
    ]

    payload = {
        "installation": {"id": installation_id},
        "pull_request": {"number": 1},
    }

    with (
        patch("app.services.dispatch.run_workflow") as mock_task,
        patch("app.services.dispatch.resolve_target_user") as mock_resolve,
    ):
        mock_resolve.return_value = (uuid.uuid4(), None)

        asyncio.run(
            dispatch_github_event(
                "pull_request",
                payload,
                delivery_id="delivery-dup",
                session=session,
            )
        )

    mock_task.delay.assert_not_called()
    session.rollback.assert_called()

    messages = dispatch_logs.messages()
    assert any(
        "webhook_dispatch_delivery_id_duplicate" in m for m in messages
    ), f"Expected webhook_dispatch_delivery_id_duplicate. Got: {messages}"


def test_dispatch_missing_installation_logs_warn(
    dispatch_logs: _LogCollector,
) -> None:
    """Payload without 'installation' key logs warning and returns no_match."""
    from app.services.dispatch import dispatch_github_event

    session = MagicMock()
    session.execute = MagicMock()
    session.commit = MagicMock()

    payload = {"pull_request": {"number": 1}}

    asyncio.run(
        dispatch_github_event(
            "pull_request",
            payload,
            delivery_id="delivery-no-install",
            session=session,
        )
    )

    messages = dispatch_logs.messages()
    assert any(
        "webhook_dispatch_no_installation" in m for m in messages
    ), f"Expected webhook_dispatch_no_installation. Got: {messages}"

    # dispatch_status update to 'no_match' should be called
    session.execute.assert_called()
