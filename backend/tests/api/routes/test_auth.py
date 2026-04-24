"""Integration tests for the cookie-based /auth router (signup/login/logout) and
the cookie-authenticated /users/me endpoint. No mocks — these talk to the real
FastAPI app and the real Postgres test DB."""
import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import jwt
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import crud
from app.core.config import settings
from app.core.security import ALGORITHM
from app.models import User, UserCreate
from tests.utils.utils import random_email, random_lower_string

SIGNUP_URL = f"{settings.API_V1_STR}/auth/signup"
LOGIN_URL = f"{settings.API_V1_STR}/auth/login"
LOGOUT_URL = f"{settings.API_V1_STR}/auth/logout"
ME_URL = f"{settings.API_V1_STR}/users/me"


def _cookies_only(client: TestClient) -> httpx.Cookies:
    """Snapshot the TestClient's current cookies as a detached httpx.Cookies.

    TestClient persists cookies across requests; we snapshot so tests can pass
    them explicitly and not rely on shared jar state between tests.
    """
    jar = httpx.Cookies()
    for name, value in client.cookies.items():
        jar.set(name, value)
    return jar


# ---------------------------------------------------------------------------
# /auth/signup
# ---------------------------------------------------------------------------


def test_signup_sets_session_cookie(client: TestClient, db: Session) -> None:
    email = random_email()
    password = random_lower_string()
    # Isolate this test from leftover cookies.
    client.cookies.clear()

    r = client.post(SIGNUP_URL, json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email"] == email
    assert body["role"] == "user"

    # Session cookie is set on the response.
    set_cookie_header = r.headers.get("set-cookie", "")
    assert settings.SESSION_COOKIE_NAME in set_cookie_header
    assert "HttpOnly" in set_cookie_header

    # Follow-up request with the same jar hits /users/me successfully.
    me = client.get(ME_URL)
    assert me.status_code == 200, me.text
    assert me.json()["email"] == email


def test_signup_duplicate_email_returns_400(client: TestClient, db: Session) -> None:
    email = random_email()
    password = random_lower_string()
    crud.create_user(
        session=db, user_create=UserCreate(email=email, password=password)
    )

    client.cookies.clear()
    r = client.post(SIGNUP_URL, json={"email": email, "password": password})
    assert r.status_code == 400
    assert "already exists" in r.json()["detail"]


# ---------------------------------------------------------------------------
# /auth/login
# ---------------------------------------------------------------------------


def test_login_sets_session_cookie(client: TestClient, db: Session) -> None:
    email = random_email()
    password = random_lower_string()
    crud.create_user(
        session=db, user_create=UserCreate(email=email, password=password)
    )

    client.cookies.clear()
    r = client.post(LOGIN_URL, json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    assert r.json()["email"] == email
    assert settings.SESSION_COOKIE_NAME in r.cookies

    me = client.get(ME_URL)
    assert me.status_code == 200
    assert me.json()["email"] == email


def test_login_wrong_password_returns_400(client: TestClient, db: Session) -> None:
    email = random_email()
    password = random_lower_string()
    crud.create_user(
        session=db, user_create=UserCreate(email=email, password=password)
    )

    client.cookies.clear()
    r = client.post(LOGIN_URL, json={"email": email, "password": "wrongpassword"})
    assert r.status_code == 400
    assert r.json()["detail"] == "Incorrect email or password"
    # Failed login MUST NOT set a session cookie.
    assert settings.SESSION_COOKIE_NAME not in r.cookies


def test_login_unknown_email_returns_400(client: TestClient) -> None:
    client.cookies.clear()
    r = client.post(
        LOGIN_URL,
        json={"email": random_email(), "password": random_lower_string()},
    )
    assert r.status_code == 400
    # Generic message — no user-enumeration leak.
    assert r.json()["detail"] == "Incorrect email or password"


# ---------------------------------------------------------------------------
# /users/me cookie enforcement
# ---------------------------------------------------------------------------


def test_users_me_without_cookie_returns_401(client: TestClient) -> None:
    client.cookies.clear()
    r = client.get(ME_URL)
    assert r.status_code == 401
    assert r.json()["detail"] == "Not authenticated"


def test_users_me_with_cookie_returns_role(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    client.cookies.clear()
    r = client.get(ME_URL, cookies=superuser_cookies)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["email"] == settings.FIRST_SUPERUSER
    assert body["role"] == "system_admin"
    # is_superuser has been removed — role is the only authority now.
    assert "is_superuser" not in body


def test_users_me_with_tampered_cookie_returns_401(client: TestClient) -> None:
    client.cookies.clear()
    client.cookies.set(settings.SESSION_COOKIE_NAME, "garbage.not.a.jwt")
    r = client.get(ME_URL)
    assert r.status_code == 401
    assert r.json()["detail"] == "Not authenticated"


def test_users_me_with_expired_cookie_returns_401(
    client: TestClient, db: Session
) -> None:
    """Forge a JWT that's already expired and verify the server rejects it."""
    email = random_email()
    password = random_lower_string()
    user = crud.create_user(
        session=db, user_create=UserCreate(email=email, password=password)
    )
    # Expiry in the past — jwt.decode will raise ExpiredSignatureError.
    assert isinstance(user.id, uuid.UUID)
    expired = jwt.encode(
        {
            "sub": str(user.id),
            "exp": datetime.now(timezone.utc) - timedelta(minutes=5),
        },
        settings.SECRET_KEY,
        algorithm=ALGORITHM,
    )
    client.cookies.clear()
    client.cookies.set(settings.SESSION_COOKIE_NAME, expired)
    r = client.get(ME_URL)
    assert r.status_code == 401
    assert r.json()["detail"] == "Not authenticated"


def test_users_me_with_cookie_for_deleted_user_returns_401(
    client: TestClient, db: Session
) -> None:
    """A valid JWT whose sub user_id no longer exists in the DB → 401 (not 404)."""
    ghost_id = uuid.uuid4()
    token = jwt.encode(
        {
            "sub": str(ghost_id),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        settings.SECRET_KEY,
        algorithm=ALGORITHM,
    )
    # Also confirm we haven't somehow just created this user.
    assert db.get(User, ghost_id) is None

    client.cookies.clear()
    client.cookies.set(settings.SESSION_COOKIE_NAME, token)
    r = client.get(ME_URL)
    assert r.status_code == 401
    assert r.json()["detail"] == "Not authenticated"


# ---------------------------------------------------------------------------
# /auth/logout
# ---------------------------------------------------------------------------


def test_logout_clears_cookie(client: TestClient, db: Session) -> None:
    email = random_email()
    password = random_lower_string()
    crud.create_user(
        session=db, user_create=UserCreate(email=email, password=password)
    )

    client.cookies.clear()
    login = client.post(LOGIN_URL, json={"email": email, "password": password})
    assert login.status_code == 200
    assert settings.SESSION_COOKIE_NAME in client.cookies

    r = client.post(LOGOUT_URL)
    assert r.status_code == 200
    assert r.json() == {"message": "Logged out"}
    # delete_cookie sets Max-Age=0 / an expired date on the response.
    set_cookie = r.headers.get("set-cookie", "")
    assert settings.SESSION_COOKIE_NAME in set_cookie

    # TestClient honours Max-Age=0 and drops the cookie from its jar.
    assert settings.SESSION_COOKIE_NAME not in client.cookies

    # Subsequent /users/me → 401.
    me = client.get(ME_URL)
    assert me.status_code == 401


def test_logout_idempotent_without_cookie(client: TestClient) -> None:
    client.cookies.clear()
    r = client.post(LOGOUT_URL)
    assert r.status_code == 200
    assert r.json() == {"message": "Logged out"}


# ---------------------------------------------------------------------------
# Login redaction (structural — ensure email is never logged raw)
# ---------------------------------------------------------------------------


def test_failed_login_does_not_leak_raw_email_in_logs(
    client: TestClient, caplog
) -> None:
    """Structural check: the log line for a failed login must redact the email."""
    email = "leaky_user@example.com"
    client.cookies.clear()
    with caplog.at_level(logging.INFO, logger="app.api.routes.auth"):
        r = client.post(
            LOGIN_URL, json={"email": email, "password": "definitely-wrong"}
        )
    assert r.status_code == 400
    combined = "\n".join(rec.message for rec in caplog.records)
    # The raw local-part must not appear verbatim. Redaction uses first 3 chars + "***".
    assert "leaky_user@example.com" not in combined
