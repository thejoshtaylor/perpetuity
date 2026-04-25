"""System admin endpoints — bypass per-team membership.

Every route in this module is gated by `get_current_active_superuser`, which
already enforces `current_user.role == UserRole.system_admin` in deps.py.
Per-team membership checks (`_assert_caller_is_team_member` /
`_assert_caller_is_team_admin` from teams.py) are deliberately NOT reused —
system admins can inspect any team's roster and promote any user.

Out of scope for this slice (S05): demote-from-system-admin. The promote
endpoint is one-directional; demotion is future work.

Logs are UUID-only (matches S03 redaction posture) — no email or team name.
"""
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import col, func, select

from app.api.deps import (
    CurrentUser,
    SessionDep,
    get_current_active_superuser,
)
from app.models import (
    Team,
    TeamMember,
    TeamMemberPublic,
    TeamMembersPublic,
    TeamPublic,
    User,
    UserPublic,
    UserRole,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(get_current_active_superuser)],
)


@router.get("/teams")
def read_all_teams(
    session: SessionDep,
    current_user: CurrentUser,
    skip: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    """List every team in the system, paginated, ordered by created_at DESC.

    Returns `{data: [TeamPublic, ...], count: int}` where `count` is the
    unfiltered total (so the FE can render Prev/Next correctly even with
    skip/limit applied). Mirrors the count+skip/limit pattern in
    `users.py::read_users`.
    """
    count_statement = select(func.count()).select_from(Team)
    count = session.exec(count_statement).one()

    statement = (
        select(Team)
        .order_by(col(Team.created_at).desc())
        .offset(skip)
        .limit(limit)
    )
    teams = session.exec(statement).all()
    data = [TeamPublic.model_validate(team, from_attributes=True) for team in teams]

    logger.info(
        "admin_teams_listed actor_id=%s skip=%s limit=%s count=%s",
        current_user.id,
        skip,
        limit,
        len(data),
    )
    return {"data": data, "count": count}


@router.get(
    "/teams/{team_id}/members", response_model=TeamMembersPublic
)
def read_admin_team_members(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    team_id: uuid.UUID,
) -> Any:
    """Return the roster of any team — does NOT require caller membership.

    - 404 if team missing.
    - 200 `{data: [{user_id, email, full_name, role}, ...], count: int}`.

    Note: deliberately does not call `_assert_caller_is_team_member` from
    teams.py — system admin must be able to inspect teams they aren't on.
    """
    team = session.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")

    statement = (
        select(User, TeamMember.role)
        .join(TeamMember, TeamMember.user_id == User.id)
        .where(TeamMember.team_id == team_id)
        .order_by(col(User.email))
    )
    rows = session.exec(statement).all()
    data = [
        TeamMemberPublic(
            user_id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=role,
        )
        for user, role in rows
    ]
    logger.info(
        "admin_team_members_listed actor_id=%s team_id=%s count=%s",
        current_user.id,
        team_id,
        len(data),
    )
    return TeamMembersPublic(data=data, count=len(data))


@router.post(
    "/users/{user_id}/promote-system-admin", response_model=UserPublic
)
def promote_system_admin(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    user_id: uuid.UUID,
) -> Any:
    """Promote a user to UserRole.system_admin. Idempotent.

    - 404 if target user does not exist.
    - 200 with the (possibly unchanged) UserPublic on success.
    - If the target is already system_admin, no DB write is performed and
      the log line records `already_admin=true`.

    Demotion (system_admin → user) is intentionally not exposed — out of
    scope for S05. A future slice can add it with last-admin guards.
    """
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    already_admin = target.role == UserRole.system_admin
    if not already_admin:
        target.role = UserRole.system_admin
        try:
            session.add(target)
            session.commit()
            session.refresh(target)
        except Exception:
            session.rollback()
            logger.warning(
                "system_admin_promote_tx_rollback actor_id=%s target_user_id=%s",
                current_user.id,
                user_id,
            )
            raise

    logger.info(
        "system_admin_promoted actor_id=%s target_user_id=%s already_admin=%s",
        current_user.id,
        user_id,
        str(already_admin).lower(),
    )
    return target
