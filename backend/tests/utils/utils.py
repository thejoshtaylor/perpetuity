import random
import string

import httpx
from fastapi.testclient import TestClient

from app.core.config import settings


def random_lower_string() -> str:
    return "".join(random.choices(string.ascii_lowercase, k=32))


def random_email() -> str:
    return f"{random_lower_string()}@{random_lower_string()}.com"


def get_superuser_cookies(client: TestClient) -> httpx.Cookies:
    """Log in as the first superuser via /auth/login and return the session cookies.

    Clears the TestClient's cookie jar before logging in so a stale cookie
    from a prior test doesn't collide with the fresh login response and
    raise httpx.CookieConflict("Multiple cookies exist with name=...").
    """
    client.cookies.clear()
    login_data = {
        "email": settings.FIRST_SUPERUSER,
        "password": settings.FIRST_SUPERUSER_PASSWORD,
    }
    r = client.post(f"{settings.API_V1_STR}/auth/login", json=login_data)
    r.raise_for_status()
    cookies = httpx.Cookies()
    for cookie in client.cookies.jar:
        cookies.set(cookie.name, cookie.value)
    return cookies
