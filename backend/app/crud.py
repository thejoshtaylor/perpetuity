import re
import secrets
import uuid
from datetime import timedelta
from typing import Any

from fastapi import HTTPException
from sqlmodel import Session, select

from app.core.security import get_password_hash, verify_password
from app.models import (
    Item,
    ItemCreate,
    Team,
    TeamInvite,
    TeamMember,
    TeamRole,
    User,
    UserCreate,
    UserUpdate,
    get_datetime_utc,
)


INVITE_TTL_SECONDS = 7 * 24 * 60 * 60


class InviteRejectReason(str):
    """Sentinel reasons raised by accept_team_invite via ValueError.

    Using bare str subclass values keeps the exception payload a plain string
    (matches FastAPI HTTP detail shape) while giving callers a closed set of
    comparable reason tokens.
    """

    UNKNOWN = "unknown"
    EXPIRED = "expired"
    USED = "used"
    DUPLICATE_MEMBER = "duplicate_member"


def create_user(*, session: Session, user_create: UserCreate) -> User:
    db_obj = User.model_validate(
        user_create, update={"hashed_password": get_password_hash(user_create.password)}
    )
    session.add(db_obj)
    session.commit()
    session.refresh(db_obj)
    return db_obj


_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    """Normalize a team name into a URL-safe stem (<=48 chars, a-z0-9-).

    Fallback to 'user' if normalization yields an empty string. Callers must
    append a uniqueness suffix — this helper does NOT guarantee uniqueness.
    """
    lowered = name.lower()
    collapsed = _SLUG_NON_ALNUM.sub("-", lowered).strip("-")
    truncated = collapsed[:48]
    return truncated or "user"


def create_user_with_personal_team(
    *,
    session: Session,
    user_create: UserCreate,
    raise_http_on_duplicate: bool = True,
) -> tuple[User, Team]:
    """Atomically create a User, their personal Team, and the admin TeamMember.

    All three inserts share one transaction — if any step fails the whole
    thing rolls back so we never leave an orphan user or orphan team.

    Set raise_http_on_duplicate=False when called from non-HTTP contexts
    (e.g. init_db seed) — duplicates raise ValueError instead of HTTPException.
    """
    existing = get_user_by_email(session=session, email=user_create.email)
    if existing is not None:
        if raise_http_on_duplicate:
            raise HTTPException(
                status_code=400,
                detail="The user with this email already exists in the system",
            )
        raise ValueError("user already exists")

    try:
        user = User.model_validate(
            user_create,
            update={"hashed_password": get_password_hash(user_create.password)},
        )
        session.add(user)
        session.flush()

        stem = user.full_name or user.email.split("@", 1)[0]
        slug = f"{_slugify(stem)}-{user.id.hex[:8]}"
        team_name = (user.full_name or user.email.split("@", 1)[0])[:255]
        team = Team(name=team_name, slug=slug, is_personal=True)
        session.add(team)
        session.flush()

        membership = TeamMember(user_id=user.id, team_id=team.id, role=TeamRole.admin)
        session.add(membership)

        session.commit()
    except Exception:
        session.rollback()
        raise

    session.refresh(user)
    session.refresh(team)
    return user, team


def create_team_with_admin(
    *,
    session: Session,
    name: str,
    creator_id: uuid.UUID,
) -> Team:
    """Create a non-personal Team with the given user as the admin member.

    Parallel shape to `create_user_with_personal_team` but for the POST /teams
    flow: slug is derived from name + 8-char suffix of a random uuid (not the
    creator's id, so the same user can create multiple teams with the same
    name). Slug IntegrityError bubbles to the caller to map to HTTP 409.
    """
    slug_stem = _slugify(name)
    slug = f"{slug_stem}-{uuid.uuid4().hex[:8]}"
    try:
        team = Team(name=name, slug=slug, is_personal=False)
        session.add(team)
        session.flush()

        membership = TeamMember(
            user_id=creator_id, team_id=team.id, role=TeamRole.admin
        )
        session.add(membership)
        session.commit()
    except Exception:
        session.rollback()
        raise

    session.refresh(team)
    return team


def update_user(*, session: Session, db_user: User, user_in: UserUpdate) -> Any:
    user_data = user_in.model_dump(exclude_unset=True)
    extra_data = {}
    if "password" in user_data:
        password = user_data["password"]
        hashed_password = get_password_hash(password)
        extra_data["hashed_password"] = hashed_password
    db_user.sqlmodel_update(user_data, update=extra_data)
    session.add(db_user)
    session.commit()
    session.refresh(db_user)
    return db_user


def get_user_by_email(*, session: Session, email: str) -> User | None:
    statement = select(User).where(User.email == email)
    session_user = session.exec(statement).first()
    return session_user


# Dummy hash to use for timing attack prevention when user is not found
# This is an Argon2 hash of a random password, used to ensure constant-time comparison
DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=4$MjQyZWE1MzBjYjJlZTI0Yw$YTU4NGM5ZTZmYjE2NzZlZjY0ZWY3ZGRkY2U2OWFjNjk"


def authenticate(*, session: Session, email: str, password: str) -> User | None:
    db_user = get_user_by_email(session=session, email=email)
    if not db_user:
        # Prevent timing attacks by running password verification even when user doesn't exist
        # This ensures the response time is similar whether or not the email exists
        verify_password(password, DUMMY_HASH)
        return None
    verified, updated_password_hash = verify_password(password, db_user.hashed_password)
    if not verified:
        return None
    if updated_password_hash:
        db_user.hashed_password = updated_password_hash
        session.add(db_user)
        session.commit()
        session.refresh(db_user)
    return db_user


def create_item(*, session: Session, item_in: ItemCreate, owner_id: uuid.UUID) -> Item:
    db_item = Item.model_validate(item_in, update={"owner_id": owner_id})
    session.add(db_item)
    session.commit()
    session.refresh(db_item)
    return db_item


def create_team_invite(
    *,
    session: Session,
    team_id: uuid.UUID,
    created_by: uuid.UUID,
    ttl_seconds: int = INVITE_TTL_SECONDS,
) -> TeamInvite:
    """Issue a new TeamInvite bearer code.

    Caller is responsible for authorizing — this helper does no membership or
    is_personal checks. Commits once and returns the refreshed row.
    """
    code = secrets.token_urlsafe(24)
    expires_at = get_datetime_utc() + timedelta(seconds=ttl_seconds)
    invite = TeamInvite(
        code=code,
        team_id=team_id,
        created_by=created_by,
        expires_at=expires_at,
    )
    try:
        session.add(invite)
        session.commit()
    except Exception:
        session.rollback()
        raise
    session.refresh(invite)
    return invite


def accept_team_invite(
    *,
    session: Session,
    code: str,
    caller_id: uuid.UUID,
) -> tuple[Team, TeamMember]:
    """Atomically consume an invite code and insert the new TeamMember.

    Raises ValueError(reason) where reason is an InviteRejectReason constant
    (unknown/expired/used/duplicate_member) — route layer maps to 404/410/409.
    On any other failure (e.g. FK violation from a race), rolls back and
    re-raises the original exception so we never leave the invite marked used
    without a matching team_member row (mirrors create_user_with_personal_team).
    """
    invite = session.exec(
        select(TeamInvite).where(TeamInvite.code == code)
    ).first()
    if invite is None:
        raise ValueError(InviteRejectReason.UNKNOWN)

    now = get_datetime_utc()
    if invite.expires_at < now:
        raise ValueError(InviteRejectReason.EXPIRED)
    if invite.used_at is not None:
        raise ValueError(InviteRejectReason.USED)

    existing_member = session.exec(
        select(TeamMember)
        .where(TeamMember.team_id == invite.team_id)
        .where(TeamMember.user_id == caller_id)
    ).first()
    if existing_member is not None:
        raise ValueError(InviteRejectReason.DUPLICATE_MEMBER)

    try:
        membership = TeamMember(
            user_id=caller_id, team_id=invite.team_id, role=TeamRole.member
        )
        session.add(membership)
        invite.used_at = now
        invite.used_by = caller_id
        session.add(invite)
        session.commit()
    except Exception:
        session.rollback()
        raise

    session.refresh(membership)
    team = session.get(Team, invite.team_id)
    if team is None:
        # Theoretical: team was hard-deleted between fetch and commit. Let the
        # caller surface a 500 — we won't pretend we joined a non-existent team.
        raise RuntimeError("team vanished during invite accept")
    return team, membership
