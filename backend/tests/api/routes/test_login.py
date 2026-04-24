"""Tests for the legacy /login router.

After S01, /login no longer exposes /access-token — authentication moved to the
cookie-based /auth router (see test_auth.py). This file keeps only the
password-recovery + reset-password coverage, plus the password-hasher upgrade
tests because they only need a real login (now cookie-based) to exercise the
upgrade path.
"""
import uuid
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from pwdlib.hashers.bcrypt import BcryptHasher
from sqlmodel import Session

from app.core.config import settings
from app.core.security import get_password_hash, verify_password
from app.crud import create_user
from app.models import User, UserCreate
from app.utils import generate_password_reset_token
from tests.utils.user import login_cookie_headers
from tests.utils.utils import random_email, random_lower_string


def test_recovery_password(
    client: TestClient, normal_user_cookies: httpx.Cookies
) -> None:
    with (
        patch("app.core.config.settings.SMTP_HOST", "smtp.example.com"),
        patch("app.core.config.settings.SMTP_USER", "admin@example.com"),
    ):
        email = "test@example.com"
        r = client.post(
            f"{settings.API_V1_STR}/password-recovery/{email}",
            cookies=normal_user_cookies,
        )
        assert r.status_code == 200
        assert r.json() == {
            "message": "If that email is registered, we sent a password recovery link"
        }


def test_recovery_password_user_not_exists(
    client: TestClient, normal_user_cookies: httpx.Cookies
) -> None:
    email = f"missing_{uuid.uuid4().hex}@example.com"
    r = client.post(
        f"{settings.API_V1_STR}/password-recovery/{email}",
        cookies=normal_user_cookies,
    )
    # Should return 200 with generic message to prevent email enumeration attacks
    assert r.status_code == 200
    assert r.json() == {
        "message": "If that email is registered, we sent a password recovery link"
    }


def test_reset_password(client: TestClient, db: Session) -> None:
    email = random_email()
    password = random_lower_string()
    new_password = random_lower_string()

    user_create = UserCreate(
        email=email,
        full_name="Test User",
        password=password,
        is_active=True,
    )
    user = create_user(session=db, user_create=user_create)
    token = generate_password_reset_token(email=email)
    cookies = login_cookie_headers(client=client, email=email, password=password)
    data = {"new_password": new_password, "token": token}

    r = client.post(
        f"{settings.API_V1_STR}/reset-password/",
        cookies=cookies,
        json=data,
    )

    assert r.status_code == 200
    assert r.json() == {"message": "Password updated successfully"}

    db.refresh(user)
    verified, _ = verify_password(new_password, user.hashed_password)
    assert verified


def test_reset_password_invalid_token(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    data = {"new_password": "changethis", "token": "invalid"}
    r = client.post(
        f"{settings.API_V1_STR}/reset-password/",
        cookies=superuser_cookies,
        json=data,
    )
    response = r.json()

    assert "detail" in response
    assert r.status_code == 400
    assert response["detail"] == "Invalid token"


def test_login_with_bcrypt_password_upgrades_to_argon2(
    client: TestClient, db: Session
) -> None:
    """Logging in via /auth/login with a bcrypt hash upgrades it to argon2."""
    email = random_email()
    password = random_lower_string()

    # Create a bcrypt hash directly (simulating legacy password).
    bcrypt_hasher = BcryptHasher()
    bcrypt_hash = bcrypt_hasher.hash(password)
    assert bcrypt_hash.startswith("$2")

    user = User(email=email, hashed_password=bcrypt_hash, is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    assert user.hashed_password.startswith("$2")

    client.cookies.clear()
    r = client.post(
        f"{settings.API_V1_STR}/auth/login",
        json={"email": email, "password": password},
    )
    assert r.status_code == 200
    assert settings.SESSION_COOKIE_NAME in r.cookies

    db.refresh(user)
    assert user.hashed_password.startswith("$argon2")
    verified, updated_hash = verify_password(password, user.hashed_password)
    assert verified
    assert updated_hash is None


def test_login_with_argon2_password_keeps_hash(
    client: TestClient, db: Session
) -> None:
    """Logging in with an argon2 password hash does not re-hash it."""
    email = random_email()
    password = random_lower_string()

    argon2_hash = get_password_hash(password)
    assert argon2_hash.startswith("$argon2")

    user = User(email=email, hashed_password=argon2_hash, is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    original_hash = user.hashed_password

    client.cookies.clear()
    r = client.post(
        f"{settings.API_V1_STR}/auth/login",
        json={"email": email, "password": password},
    )
    assert r.status_code == 200

    db.refresh(user)
    assert user.hashed_password == original_hash
    assert user.hashed_password.startswith("$argon2")
