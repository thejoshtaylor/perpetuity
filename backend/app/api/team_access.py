"""Shared team-access guards used by HTTP routers.

Originally lived as private helpers (`_assert_caller_is_team_member` /
`_assert_caller_is_team_admin`) inside `app/api/routes/teams.py`. Lifted to a
module-scoped helper here in T05 because the new `routes/sessions.py` needs
the membership check before forwarding to the orchestrator. Keeping the
guard in one place avoids two divergent membership-shaped queries — the
membership boundary is a security primitive, so duplicating it would be a
correctness hazard.

The `teams.py` module re-exports both names so the original call sites keep
working without churn.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlmodel import Session, select

from app.models import Team, TeamMember, TeamRole


def assert_caller_is_team_member(
    session: Session, team_id: uuid.UUID, caller_id: uuid.UUID
) -> Team:
    """Return the Team when caller is a member (any role) on it, else 404/403.

    Used by read endpoints and by the M002 sessions router (T05) which needs
    to verify the caller can act on `team_id` before forwarding the create
    request to the orchestrator.
    """
    team = session.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")

    membership = session.exec(
        select(TeamMember)
        .where(TeamMember.team_id == team_id)
        .where(TeamMember.user_id == caller_id)
    ).first()
    if membership is None:
        raise HTTPException(
            status_code=403, detail="Not a member of this team"
        )
    return team


def assert_caller_is_team_admin(
    session: Session, team_id: uuid.UUID, caller_id: uuid.UUID
) -> Team:
    """Return the Team when caller is an admin on it, else raise 404/403.

    Collapses the shared "team exists + caller is admin" precondition used
    by every team mutation endpoint — invite, PATCH role, DELETE member.
    """
    team = session.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")

    membership = session.exec(
        select(TeamMember)
        .where(TeamMember.team_id == team_id)
        .where(TeamMember.user_id == caller_id)
    ).first()
    if membership is None or membership.role != TeamRole.admin:
        raise HTTPException(
            status_code=403, detail="Only team admins can invite"
        )
    return team
