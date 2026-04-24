from fastapi import Response

from app.core.config import settings


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.ENVIRONMENT != "local",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.ENVIRONMENT != "local",
    )
