import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app import crud
from app.api.deps import CurrentUser, SessionDep
from app.models import Team, TeamCreate, TeamMember, TeamRole, TeamWithRole

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/teams", tags=["teams"])


class TeamsPublic(dict):
    """Response envelope for GET /teams — `{data: [...], count: int}`.

    Using a plain dict is sufficient; FastAPI serializes the declared
    response_model via the actual shape below without needing a SQLModel.
    """


@router.get("/")
def read_teams(session: SessionDep, current_user: CurrentUser) -> dict[str, Any]:
    """Return all teams the caller is a member of with their role.

    Single SELECT JOIN — no N+1. Filtered by the caller's user_id so the
    endpoint can never leak teams the caller is not a member of.
    """
    statement = (
        select(Team, TeamMember.role)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == current_user.id)
        .order_by(Team.created_at.desc())  # type: ignore[attr-defined]
    )
    rows = session.exec(statement).all()
    data = [TeamWithRole(**team.model_dump(), role=role) for team, role in rows]
    return {"data": data, "count": len(data)}


@router.post("/", response_model=TeamWithRole)
def create_team(
    *, session: SessionDep, current_user: CurrentUser, team_in: TeamCreate
) -> Any:
    """Create a non-personal team with the caller as admin. Returns TeamWithRole."""
    try:
        team = crud.create_team_with_admin(
            session=session, name=team_in.name, creator_id=current_user.id
        )
    except IntegrityError:
        # Extremely rare given the 8-char random suffix, but possible.
        logger.warning(
            "team_create_slug_conflict user_id=%s", current_user.id
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Team slug conflict — retry",
        )

    logger.info(
        "team_created team_id=%s is_personal=%s creator_id=%s",
        team.id,
        team.is_personal,
        current_user.id,
    )
    return TeamWithRole(**team.model_dump(), role=TeamRole.admin)


@router.post("/{team_id}/invite")
def invite_to_team(
    *, session: SessionDep, current_user: CurrentUser, team_id: uuid.UUID
) -> Any:
    """Invite stub — delivers the S02→S03 boundary contract.

    - 404 if team missing.
    - 403 if caller is not an admin of the team.
    - 403 "Cannot invite to personal teams" if team.is_personal.
    - 501 otherwise (S03 replaces this with real invite issuance).
    """
    team = session.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")

    membership = session.exec(
        select(TeamMember)
        .where(TeamMember.team_id == team_id)
        .where(TeamMember.user_id == current_user.id)
    ).first()
    if membership is None or membership.role != TeamRole.admin:
        raise HTTPException(
            status_code=403, detail="Only team admins can invite"
        )

    if team.is_personal:
        logger.info(
            "invite_rejected_personal team_id=%s caller_id=%s",
            team.id,
            current_user.id,
        )
        raise HTTPException(
            status_code=403, detail="Cannot invite to personal teams"
        )

    raise HTTPException(
        status_code=501,
        detail="Invite endpoint not yet implemented — see S03",
    )
