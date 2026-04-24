import hashlib
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app import crud
from app.api.deps import CurrentUser, SessionDep
from app.core.config import settings
from app.crud import InviteRejectReason
from app.models import (
    InviteIssued,
    Team,
    TeamCreate,
    TeamMember,
    TeamRole,
    TeamWithRole,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/teams", tags=["teams"])


def _code_hash(code: str) -> str:
    """Short sha256 prefix of an invite code — safe to log.

    Never log raw codes. The 8-char prefix is enough to correlate a single
    invite across logs without letting a log reader redeem it.
    """
    return hashlib.sha256(code.encode()).hexdigest()[:8]


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


@router.post("/{team_id}/invite", response_model=InviteIssued)
def invite_to_team(
    *, session: SessionDep, current_user: CurrentUser, team_id: uuid.UUID
) -> Any:
    """Issue a bearer-token invite to join a non-personal team.

    - 404 if team missing.
    - 403 if caller is not a member, or is a member but not an admin.
    - 403 "Cannot invite to personal teams" if team.is_personal.
    - 200 with {code, url, expires_at} on success.
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

    try:
        invite = crud.create_team_invite(
            session=session, team_id=team.id, created_by=current_user.id
        )
    except Exception:
        logger.warning(
            "invite_issue_tx_rollback team_id=%s inviter_id=%s",
            team.id,
            current_user.id,
        )
        raise

    code_hash = _code_hash(invite.code)
    logger.info(
        "invite_issued team_id=%s inviter_id=%s code_hash=%s expires_at=%s",
        team.id,
        current_user.id,
        code_hash,
        invite.expires_at.isoformat(),
    )
    return InviteIssued(
        code=invite.code,
        url=f"{settings.FRONTEND_HOST}/invite/{invite.code}",
        expires_at=invite.expires_at,
    )


@router.post("/join/{code}", response_model=TeamWithRole)
def join_team(
    *, session: SessionDep, current_user: CurrentUser, code: str
) -> Any:
    """Accept an invite code — atomically add caller as a member of the team.

    - 404 if code unknown.
    - 410 if invite expired or already used.
    - 409 if caller is already a member of the team.
    - 200 TeamWithRole on success.
    """
    code_hash = _code_hash(code)
    try:
        team, membership = crud.accept_team_invite(
            session=session, code=code, caller_id=current_user.id
        )
    except ValueError as exc:
        reason = str(exc)
        logger.info(
            "invite_rejected reason=%s code_hash=%s caller_id=%s",
            reason,
            code_hash,
            current_user.id,
        )
        if reason == InviteRejectReason.UNKNOWN:
            raise HTTPException(status_code=404, detail="Invite not found")
        if reason == InviteRejectReason.EXPIRED:
            raise HTTPException(status_code=410, detail="Invite expired")
        if reason == InviteRejectReason.USED:
            raise HTTPException(status_code=410, detail="Invite already used")
        if reason == InviteRejectReason.DUPLICATE_MEMBER:
            raise HTTPException(status_code=409, detail="Already a member")
        # Unknown rejection reason — treat as server bug, not user input.
        raise
    except Exception:
        logger.warning(
            "invite_accept_tx_rollback code_hash=%s caller_id=%s",
            code_hash,
            current_user.id,
        )
        raise

    logger.info(
        "invite_accepted team_id=%s user_id=%s code_hash=%s",
        team.id,
        current_user.id,
        code_hash,
    )
    return TeamWithRole(**team.model_dump(), role=membership.role)
