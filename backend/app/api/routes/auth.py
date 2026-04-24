import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, EmailStr, Field

from app import crud
from app.api.deps import SessionDep
from app.core.cookies import clear_session_cookie, set_session_cookie
from app.core.security import create_session_token
from app.models import Message, User, UserCreate, UserPublic, UserRole

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _redact_email(email: str) -> str:
    """Return a log-safe form of an email.

    Per the S01 Redaction constraint: emails must be hashed or redacted in logs.
    Shape: `abc***@domain.com` — preserves domain + first 3 chars of local part.
    """
    local, sep, domain = email.partition("@")
    if not sep:
        return f"{local[:3]}***"
    return f"{local[:3]}***@{domain}"


class SignupBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


class LoginBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


def _issue_session(response: Response, user: User) -> None:
    token = create_session_token(user.id)
    set_session_cookie(response, token)


@router.post("/signup", response_model=UserPublic)
def signup(session: SessionDep, body: SignupBody, response: Response) -> Any:
    """Create a new user with role=user, set session cookie, return UserPublic."""
    existing = crud.get_user_by_email(session=session, email=body.email)
    if existing:
        logger.info("signup rejected: email already registered %s", _redact_email(body.email))
        raise HTTPException(
            status_code=400,
            detail="The user with this email already exists in the system",
        )

    user_create = UserCreate(
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        role=UserRole.user,
    )
    user = crud.create_user(session=session, user_create=user_create)
    _issue_session(response, user)
    logger.info("signup ok %s", _redact_email(user.email))
    return user


@router.post("/login", response_model=UserPublic)
def login(session: SessionDep, body: LoginBody, response: Response) -> Any:
    """Validate credentials, set session cookie, return UserPublic."""
    user = crud.authenticate(session=session, email=body.email, password=body.password)
    if not user:
        logger.info("login failed %s", _redact_email(body.email))
        raise HTTPException(status_code=400, detail="Incorrect email or password")
    if not user.is_active:
        logger.info("login rejected inactive %s", _redact_email(user.email))
        raise HTTPException(status_code=400, detail="Inactive user")
    _issue_session(response, user)
    logger.info("login ok %s", _redact_email(user.email))
    return user


@router.post("/logout", response_model=Message)
def logout(response: Response) -> Message:
    """Clear the session cookie. Idempotent — works without an existing cookie."""
    clear_session_cookie(response)
    logger.info("logout ok")
    return Message(message="Logged out")
