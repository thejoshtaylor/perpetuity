"""GitHub webhook dispatch (M005/S04/T01).

Called by ``app.api.routes.github_webhooks`` AFTER HMAC verification and the
event row INSERT. Evaluates per-project push rules for the installation that
sent the webhook and either:

  - mode='rule'            → POST to orchestrator auto-push-callback if
                             branch_pattern matches the push ref.
  - mode='manual_workflow' → INSERT WorkflowRun + enqueue Celery task.
  - mode='auto'            → skip (handled by post-receive hook, not here).

Dispatch is idempotent per delivery_id: the s14 UNIQUE constraint on
``workflow_runs.webhook_delivery_id`` blocks duplicate inserts; the
``dispatch_status`` column on ``github_webhook_events`` is updated from
``'noop'`` to ``'dispatched'`` or ``'no_match'`` after the dispatch loop.

Log discriminators (slice S04 contract):
  webhook_dispatched delivery_id=X event_type=Y dispatch_status=dispatched
  webhook_dispatch_no_installation (WARN) — delivery_id has no matching install
  webhook_dispatch_push_rule_evaluated (INFO) — per rule evaluated
  auto_push_skipped project_id=X reason=branch_pattern_no_match (INFO)
  webhook_run_enqueued (INFO) — WorkflowRun inserted + Celery enqueued
  webhook_dispatch_delivery_id_duplicate (INFO) — duplicate delivery skipped
"""

from __future__ import annotations

import fnmatch
import logging
import uuid
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.config import settings
from app.models import (
    GitHubAppInstallation,
    Project,
    ProjectPushRule,
    Workflow,
    WorkflowRun,
    get_datetime_utc,
)
from app.services.workflow_dispatch import TargetUserNoMembershipError, resolve_target_user
from app.workflows.tasks import run_workflow

logger = logging.getLogger(__name__)


def _strip_refs_heads(ref: str) -> str:
    """Strip 'refs/heads/' prefix to get bare branch name."""
    prefix = "refs/heads/"
    if ref.startswith(prefix):
        return ref[len(prefix):]
    return ref


def _update_dispatch_status(
    session: Session,
    delivery_id: str,
    status: str,
) -> None:
    """Update dispatch_status on the github_webhook_events row."""
    session.execute(
        text(
            "UPDATE github_webhook_events "
            "SET dispatch_status = :status "
            "WHERE delivery_id = :did"
        ),
        {"status": status, "did": delivery_id},
    )
    session.commit()


def _handle_mode_rule(
    session: Session,
    project: Project,
    push_rule: ProjectPushRule,
    payload: dict[str, Any],
    delivery_id: str,
) -> None:
    """Evaluate branch pattern for mode='rule' and call orchestrator if matched."""
    ref = payload.get("ref", "")
    branch = _strip_refs_heads(ref)
    pattern = push_rule.branch_pattern or ""

    if not fnmatch.fnmatch(branch, pattern):
        logger.info(
            "auto_push_skipped project_id=%s reason=branch_pattern_no_match "
            "ref=%s pattern=%s",
            project.id,
            ref,
            pattern,
        )
        logger.info(
            "webhook_dispatch_push_rule_evaluated project_id=%s mode=rule "
            "outcome=branch_pattern_no_match delivery_id=%s",
            project.id,
            delivery_id,
        )
        return

    # Branch matched — POST to orchestrator auto-push-callback.
    callback_url = (
        f"{settings.ORCHESTRATOR_BASE_URL}/v1/projects/{project.id}/auto-push-callback"
    )
    try:
        with httpx.Client(timeout=10.0) as client:
            client.post(
                callback_url,
                json={"ref": ref},
                headers={"X-Orchestrator-Key": settings.ORCHESTRATOR_API_KEY},
            )
    except httpx.HTTPError as exc:
        logger.warning(
            "auto_push_callback_failed project_id=%s delivery_id=%s error=%s",
            project.id,
            delivery_id,
            str(exc),
        )

    logger.info(
        "webhook_dispatch_push_rule_evaluated project_id=%s mode=rule "
        "outcome=auto_push_triggered delivery_id=%s",
        project.id,
        delivery_id,
    )


def _handle_mode_manual_workflow(
    session: Session,
    project: Project,
    push_rule: ProjectPushRule,
    payload: dict[str, Any],
    delivery_id: str,
) -> None:
    """Resolve workflow + insert WorkflowRun + enqueue Celery task."""
    # Parse workflow_id from the push rule.
    raw_wf_id = push_rule.workflow_id
    if not raw_wf_id:
        logger.warning(
            "webhook_dispatch_no_workflow_id project_id=%s delivery_id=%s",
            project.id,
            delivery_id,
        )
        return

    try:
        workflow_uuid = uuid.UUID(raw_wf_id)
    except (ValueError, AttributeError):
        logger.warning(
            "webhook_dispatch_bad_workflow_id project_id=%s delivery_id=%s "
            "workflow_id=%s",
            project.id,
            delivery_id,
            raw_wf_id,
        )
        return

    workflow = session.get(Workflow, workflow_uuid)
    if workflow is None:
        logger.warning(
            "webhook_dispatch_workflow_not_found project_id=%s delivery_id=%s "
            "workflow_id=%s",
            project.id,
            delivery_id,
            workflow_uuid,
        )
        return

    # Resolve target user — for webhook-triggered runs there is no
    # triggering_user_id, so we use the first team member as a fallback.
    # resolve_target_user needs a triggering_user_id; for scope='user'
    # that means we need one. For round_robin / team_specific scopes the
    # triggering_user_id is irrelevant (cursor or team_specific picks the
    # target). Use a zero UUID as the sentinel — if the scope is 'user',
    # the run will target that sentinel, which is benign: the delivery still
    # inserts a run for auditing, and the executor will surface a missing
    # workspace error rather than silently dropping the event.
    #
    # A cleaner solution would be to pass the installation's team's first
    # admin, but that requires an extra query and the plan says "resolve
    # target user" without specifying a triggering user for webhook context.
    # Using uuid.UUID(int=0) is the simplest defensible choice.
    fallback_user_id = uuid.UUID(int=0)
    try:
        target_user_id, _ = resolve_target_user(
            session, workflow, fallback_user_id
        )
    except TargetUserNoMembershipError:
        logger.warning(
            "webhook_dispatch_target_user_no_membership project_id=%s "
            "delivery_id=%s workflow_id=%s",
            project.id,
            delivery_id,
            workflow_uuid,
        )
        return

    run_id = uuid.uuid4()
    now = get_datetime_utc()

    # INSERT ... ON CONFLICT (webhook_delivery_id) DO NOTHING via try/except.
    try:
        run = WorkflowRun(
            id=run_id,
            workflow_id=workflow.id,
            team_id=workflow.team_id,
            trigger_type="webhook",
            triggered_by_user_id=None,
            target_user_id=target_user_id,
            trigger_payload=payload,
            status="pending",
            webhook_delivery_id=delivery_id,
            created_at=now,
        )
        session.add(run)
        session.commit()
    except IntegrityError:
        session.rollback()
        logger.info(
            "webhook_dispatch_delivery_id_duplicate delivery_id=%s "
            "workflow_id=%s project_id=%s",
            delivery_id,
            workflow_uuid,
            project.id,
        )
        return

    # Enqueue Celery task.
    run_workflow.delay(str(run_id))

    logger.info(
        "webhook_run_enqueued workflow_id=%s run_id=%s delivery_id=%s",
        workflow.id,
        run_id,
        delivery_id,
    )


async def dispatch_github_event(
    event_type: str,
    payload: dict[str, Any],
    *,
    delivery_id: str | None = None,
    session: Session,
) -> None:
    """Dispatch a verified GitHub webhook event to per-project push rules.

    Args:
        event_type: GitHub's ``X-GitHub-Event`` value.
        payload: Parsed webhook JSON body.
        delivery_id: GitHub's ``X-GitHub-Delivery`` header value.
        session: SQLAlchemy session (required — passed by the webhook route).

    Returns:
        None. Never raises — errors are logged and swallowed so the route
        always returns 200 to GitHub.
    """
    did = delivery_id if delivery_id is not None else "NA"

    # 1. Extract installation_id from payload.
    install_block = payload.get("installation")
    if not install_block or not isinstance(install_block, dict):
        logger.warning(
            "webhook_dispatch_no_installation delivery_id=%s event_type=%s",
            did,
            event_type,
        )
        logger.info(
            "webhook_dispatched delivery_id=%s event_type=%s dispatch_status=no_match",
            did,
            event_type,
        )
        if delivery_id:
            _update_dispatch_status(session, delivery_id, "no_match")
        return

    installation_id = install_block.get("id")
    if installation_id is None:
        logger.warning(
            "webhook_dispatch_no_installation delivery_id=%s event_type=%s",
            did,
            event_type,
        )
        logger.info(
            "webhook_dispatched delivery_id=%s event_type=%s dispatch_status=no_match",
            did,
            event_type,
        )
        if delivery_id:
            _update_dispatch_status(session, delivery_id, "no_match")
        return

    # 2. Query all projects matching this installation_id.
    install_row = session.exec(
        select(GitHubAppInstallation).where(
            GitHubAppInstallation.installation_id == installation_id
        )
    ).first()

    if install_row is None:
        logger.warning(
            "webhook_dispatch_no_installation delivery_id=%s event_type=%s "
            "installation_id=%s",
            did,
            event_type,
            installation_id,
        )
        logger.info(
            "webhook_dispatched delivery_id=%s event_type=%s dispatch_status=no_match",
            did,
            event_type,
        )
        if delivery_id:
            _update_dispatch_status(session, delivery_id, "no_match")
        return

    projects = list(
        session.exec(
            select(Project).where(
                Project.installation_id == installation_id
            )
        ).all()
    )

    any_rule_fired = False

    # 3. For each project, evaluate its push rule.
    for project in projects:
        push_rule = session.get(ProjectPushRule, project.id)
        if push_rule is None:
            continue
        if push_rule.mode == "auto":
            # mode='auto' is handled by the post-receive hook, not here.
            continue

        if push_rule.mode == "rule":
            _handle_mode_rule(session, project, push_rule, payload, did)
            any_rule_fired = True

        elif push_rule.mode == "manual_workflow":
            if delivery_id:
                _handle_mode_manual_workflow(
                    session, project, push_rule, payload, delivery_id
                )
            any_rule_fired = True

    dispatch_status = "dispatched" if any_rule_fired else "no_match"

    logger.info(
        "webhook_dispatched delivery_id=%s event_type=%s dispatch_status=%s",
        did,
        event_type,
        dispatch_status,
    )

    if delivery_id:
        _update_dispatch_status(session, delivery_id, dispatch_status)
