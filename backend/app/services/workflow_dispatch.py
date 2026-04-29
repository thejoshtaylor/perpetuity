"""Target-user resolution for workflow dispatch.

Invoked at the API boundary (T04) BEFORE a WorkflowRun row is inserted, so
the resolved target_user_id is known at row-create time.

Scope semantics:
  user          → triggering user always
  team_specific → workflow.target_user_id, or TargetUserNoMembershipError
  round_robin   → cursor-based pick among live team members with workspace
                  fallback to triggering user when no member has a live
                  workspace provisioned within the last 7 days.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select, text

from app.models import TeamMember, WorkflowScope

logger = logging.getLogger(__name__)

_LIVE_WORKSPACE_WINDOW_DAYS = 7


class TargetUserNoMembershipError(Exception):
    """Raised when team_specific target is NULL or no longer a team member."""

    def __init__(self, workflow_id: uuid.UUID, target_user_id: uuid.UUID | None) -> None:
        self.workflow_id = workflow_id
        self.target_user_id = target_user_id
        super().__init__(
            f"target_user_no_membership workflow_id={workflow_id} "
            f"target_user_id={target_user_id}"
        )


def _is_member(session: Session, user_id: uuid.UUID, team_id: uuid.UUID) -> bool:
    row = session.exec(
        select(TeamMember)
        .where(TeamMember.user_id == user_id)
        .where(TeamMember.team_id == team_id)
    ).first()
    return row is not None


def _has_live_workspace(
    session: Session, user_id: uuid.UUID, team_id: uuid.UUID
) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(days=_LIVE_WORKSPACE_WINDOW_DAYS)
    row = session.exec(
        text(
            "SELECT id FROM workspace_volume "
            "WHERE user_id = :uid AND team_id = :tid AND created_at >= :cutoff "
            "LIMIT 1"
        ).bindparams(uid=user_id, tid=team_id, cutoff=cutoff)
    ).first()
    return row is not None


def _list_team_members_ordered(
    session: Session, team_id: uuid.UUID
) -> list[uuid.UUID]:
    rows = session.exec(
        select(TeamMember.user_id)
        .where(TeamMember.team_id == team_id)
        .order_by(TeamMember.created_at)
    ).all()
    return list(rows)


def _atomic_cursor_increment(session: Session, workflow_id: uuid.UUID) -> int:
    """Increment round_robin_cursor atomically and return the NEW value."""
    result = session.exec(
        text(
            "UPDATE workflows "
            "SET round_robin_cursor = round_robin_cursor + 1 "
            "WHERE id = :wid "
            "RETURNING round_robin_cursor"
        ).bindparams(wid=workflow_id)
    ).first()
    return result[0] if result else 1


def resolve_target_user(
    session: Session,
    workflow,  # app.models.Workflow
    triggering_user_id: uuid.UUID,
    *,
    run_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, str | None]:
    """Resolve which user workspace should receive this dispatch.

    Returns:
        (target_user_id, fallback_reason | None)

    Raises:
        TargetUserNoMembershipError: for team_specific scope when the target
            is NULL or is no longer a team member.
    """
    scope = workflow.scope

    # ── scope='user' ──────────────────────────────────────────────────────────
    if scope == WorkflowScope.user or scope == "user":
        return triggering_user_id, None

    # ── scope='team_specific' ─────────────────────────────────────────────────
    if scope == WorkflowScope.team or scope == "team" or scope == "team_specific":
        target = workflow.target_user_id
        if target is None:
            logger.error(
                "workflow_dispatch_target_user_no_membership workflow_id=%s target_user_id=None",
                workflow.id,
            )
            raise TargetUserNoMembershipError(workflow.id, None)
        if not _is_member(session, target, workflow.team_id):
            logger.error(
                "workflow_dispatch_target_user_no_membership workflow_id=%s target_user_id=%s",
                workflow.id,
                target,
            )
            raise TargetUserNoMembershipError(workflow.id, target)
        return target, None

    # ── scope='round_robin' ───────────────────────────────────────────────────
    if scope == WorkflowScope.round_robin or scope == "round_robin":
        members = _list_team_members_ordered(session, workflow.team_id)
        n = len(members)
        if n == 0:
            # No members at all — fall back
            logger.info(
                "workflow_dispatch_fallback run_id=%s workflow_id=%s "
                "reason=no_live_workspace fallback_target=triggering_user",
                run_id,
                workflow.id,
            )
            return triggering_user_id, "no_live_workspace"

        cursor_before = workflow.round_robin_cursor
        # Try up to n members starting from cursor
        for probe in range(n):
            idx = (cursor_before + probe) % n
            candidate = members[idx]
            if _has_live_workspace(session, candidate, workflow.team_id):
                cursor_after = _atomic_cursor_increment(session, workflow.id)
                logger.info(
                    "workflow_dispatch_round_robin_pick run_id=%s workflow_id=%s "
                    "target_user_id=%s cursor_before=%d cursor_after=%d",
                    run_id,
                    workflow.id,
                    candidate,
                    cursor_before,
                    cursor_after,
                )
                return candidate, None

        # No live workspace found — fall back to triggering user
        logger.info(
            "workflow_dispatch_fallback run_id=%s workflow_id=%s "
            "reason=no_live_workspace fallback_target=triggering_user",
            run_id,
            workflow.id,
        )
        return triggering_user_id, "no_live_workspace"

    # Unknown scope — safe default
    return triggering_user_id, None
