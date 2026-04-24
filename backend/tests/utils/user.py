import httpx
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import crud
from app.core.config import settings
from app.models import User, UserCreate, UserUpdate
from tests.utils.utils import random_email, random_lower_string


def login_cookie_headers(
    *, client: TestClient, email: str, password: str
) -> httpx.Cookies:
    """Log in via the cookie-based /auth/login endpoint and return the session cookies.

    Clears the TestClient's existing jar first so a leftover session cookie
    from a previous test doesn't collide with the new login response.

    Returns an httpx.Cookies object containing the session cookie. Pass it as
    `cookies=` on subsequent requests, or install onto a TestClient.
    """
    client.cookies.clear()
    data = {"email": email, "password": password}
    r = client.post(f"{settings.API_V1_STR}/auth/login", json=data)
    r.raise_for_status()
    # httpx.Client keeps cookies on the jar; copy to a standalone object so callers
    # can pass it freely without mutating the shared TestClient state.
    cookies = httpx.Cookies()
    for cookie in client.cookies.jar:
        cookies.set(cookie.name, cookie.value)
    return cookies


def create_random_user(db: Session) -> User:
    email = random_email()
    password = random_lower_string()
    user_in = UserCreate(email=email, password=password)
    user = crud.create_user(session=db, user_create=user_in)
    return user


def authentication_cookies_from_email(
    *, client: TestClient, email: str, db: Session
) -> httpx.Cookies:
    """
    Return a valid cookie jar (with session cookie set) for the user with the
    given email. If the user doesn't exist it is created first.
    """
    password = random_lower_string()
    user = crud.get_user_by_email(session=db, email=email)
    if not user:
        user_in_create = UserCreate(email=email, password=password)
        user = crud.create_user(session=db, user_create=user_in_create)
    else:
        user_in_update = UserUpdate(password=password)
        if not user.id:
            raise Exception("User id not set")
        user = crud.update_user(session=db, db_user=user, user_in=user_in_update)

    return login_cookie_headers(client=client, email=email, password=password)


def superuser_cookies(client: TestClient) -> httpx.Cookies:
    """Return a cookie jar authenticated as the FIRST_SUPERUSER."""
    return login_cookie_headers(
        client=client,
        email=settings.FIRST_SUPERUSER,
        password=settings.FIRST_SUPERUSER_PASSWORD,
    )
