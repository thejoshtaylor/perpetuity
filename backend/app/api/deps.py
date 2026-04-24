import logging
from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, WebSocket, status
from sqlmodel import Session
from starlette.websockets import WebSocketDisconnect

from app.core.config import settings
from app.core.db import engine
from app.core.security import decode_session_token
from app.models import User, UserRole

logger = logging.getLogger(__name__)


def get_db() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_db)]


def get_current_user(session: SessionDep, request: Request) -> User:
    # Read the session cookie by the configured name so deployments can
    # override SESSION_COOKIE_NAME without touching code.
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    user_id = decode_session_token(token)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    user = session.get(User, user_id)
    if not user:
        # Do not leak user existence via a 404 — generic 401.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_current_active_superuser(current_user: CurrentUser) -> User:
    if current_user.role != UserRole.system_admin:
        raise HTTPException(
            status_code=403, detail="The user doesn't have enough privileges"
        )
    return current_user


async def get_current_user_ws(websocket: WebSocket) -> User:
    """Authenticate a WebSocket connection via the session cookie.

    On any auth failure, closes the socket with code 1008 and a machine-readable
    reason string, then raises WebSocketDisconnect so the endpoint aborts. The
    reason strings are part of the inspection surface documented in the slice
    plan — do not change without updating callers and tests.
    """
    token = websocket.cookies.get(settings.SESSION_COOKIE_NAME)
    if not token:
        logger.info("ws_auth_reject reason=missing_cookie")
        await websocket.close(code=1008, reason="missing_cookie")
        raise WebSocketDisconnect(code=1008)

    user_id = decode_session_token(token)
    if user_id is None:
        logger.info("ws_auth_reject reason=invalid_token")
        await websocket.close(code=1008, reason="invalid_token")
        raise WebSocketDisconnect(code=1008)

    with Session(engine) as session:
        user = session.get(User, user_id)
        if not user:
            logger.info("ws_auth_reject reason=user_not_found")
            await websocket.close(code=1008, reason="user_not_found")
            raise WebSocketDisconnect(code=1008)
        if not user.is_active:
            logger.info("ws_auth_reject reason=user_inactive")
            await websocket.close(code=1008, reason="user_inactive")
            raise WebSocketDisconnect(code=1008)
        logger.debug("ws_auth_ok user_id=%s", user.id)
        return user
