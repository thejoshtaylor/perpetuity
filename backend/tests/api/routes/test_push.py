"""Integration tests for the M005/S03/T01 push router + VAPID admin endpoint.

Covers:
  - GET  /push/vapid_public_key                      (503 when unset; 200 + value when set)
  - POST /admin/settings/vapid_keys/generate         (atomic two-key write)
  - 401/403 gating on the admin endpoint

The `_set_encryption_key` fixture is required for the encrypted private-key
write path; it mirrors the test_admin_settings.py setup so the Fernet loader
sees a deterministic key.
"""
from __future__ import annotations

import base64
import logging

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlmodel import Session, select

from app.core.config import settings
from app.models import SystemSetting
from tests.utils.utils import random_email, random_lower_string

# Migration tests in this project call alembic ``command.upgrade`` which
# in turn invokes ``logging.config.fileConfig`` on alembic.ini. fileConfig
# defaults to ``disable_existing_loggers=True``, which silently flips the
# ``disabled`` flag on every logger created before the call (incl.
# ``app.api.routes.push`` and ``app.api.routes.admin``). After that, caplog
# captures nothing from those loggers because emit() is short-circuited at
# the source. Re-enable them on every test so the order of pytest collection
# (migrations-then-routes vs routes-only) doesn't change the assertions.
_LOGGERS_TO_REENABLE = (
    "app.api.routes.push",
    "app.api.routes.admin",
)


@pytest.fixture(autouse=True)
def _reenable_route_loggers():
    for name in _LOGGERS_TO_REENABLE:
        logging.getLogger(name).disabled = False
    yield

API_V1 = settings.API_V1_STR
ADMIN_SETTINGS_URL = f"{API_V1}/admin/settings"
VAPID_GENERATE_URL = f"{ADMIN_SETTINGS_URL}/vapid_keys/generate"
VAPID_PUBLIC_KEY_URL = f"{API_V1}/push/vapid_public_key"
SIGNUP_URL = f"{API_V1}/auth/signup"

VAPID_PUBLIC_KEY = "vapid_public_key"
VAPID_PRIVATE_KEY = "vapid_private_key"


@pytest.fixture(autouse=True)
def _clean_system_settings(db: Session):
    """Each test starts with empty system_settings (single-row global state)."""
    db.execute(delete(SystemSetting))
    db.commit()
    yield
    db.execute(delete(SystemSetting))
    db.commit()


@pytest.fixture(autouse=True)
def _set_encryption_key(monkeypatch):
    """Fernet key for the encrypted private-key write path.

    Mirrors test_admin_settings._set_encryption_key — a deterministic 44-char
    base64 key plus a cache-clear so each test sees a fresh load.
    """
    monkeypatch.setenv(
        "SYSTEM_SETTINGS_ENCRYPTION_KEY",
        "q14YMz9s4jrbfD29GvcRfe_4krg82w6_mPWUu_y3LTo=",
    )
    from app.core import encryption as _enc

    _enc._load_key.cache_clear()
    yield
    _enc._load_key.cache_clear()


def _signup(client: TestClient) -> tuple[str, httpx.Cookies]:
    email = random_email()
    password = random_lower_string()
    client.cookies.clear()
    r = client.post(SIGNUP_URL, json={"email": email, "password": password})
    assert r.status_code == 200, r.text

    jar = httpx.Cookies()
    for cookie in client.cookies.jar:
        jar.set(cookie.name, cookie.value)
    client.cookies.clear()
    return r.json()["id"], jar


# ---------------------------------------------------------------------------
# GET /push/vapid_public_key
# ---------------------------------------------------------------------------


def test_get_vapid_public_key_returns_503_when_unset(
    client: TestClient,
) -> None:
    """No keypair generated yet → 503, machine-readable detail body."""
    client.cookies.clear()
    r = client.get(VAPID_PUBLIC_KEY_URL)
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert detail["detail"] == "vapid_public_key_not_configured"
    assert "vapid_keys/generate" in detail["remediation"]


def test_get_vapid_public_key_no_auth_required(
    client: TestClient,
    superuser_cookies: httpx.Cookies,
) -> None:
    """The endpoint is intentionally world-readable — browsers fetch it
    before any user is in scope."""
    # Seed a keypair as the superuser, then fetch the public key with no
    # auth.
    g = client.post(VAPID_GENERATE_URL, cookies=superuser_cookies)
    assert g.status_code == 200, g.text
    expected_public = g.json()["public_key"]

    client.cookies.clear()
    r = client.get(VAPID_PUBLIC_KEY_URL)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["public_key"] == expected_public


def test_get_vapid_public_key_emits_served_log(
    client: TestClient,
    superuser_cookies: httpx.Cookies,
    caplog,
) -> None:
    """The served-log carries the key-prefix only — never the full key."""
    g = client.post(VAPID_GENERATE_URL, cookies=superuser_cookies)
    assert g.status_code == 200
    public_key = g.json()["public_key"]

    client.cookies.clear()
    with caplog.at_level(logging.INFO, logger="app.api.routes.push"):
        r = client.get(VAPID_PUBLIC_KEY_URL)
    assert r.status_code == 200

    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        "push.vapid_public_key.served" in m
        and f"key_prefix={public_key[:4]}" in m
        for m in msgs
    ), msgs
    # Full key MUST NOT appear in any log line.
    for m in msgs:
        assert public_key not in m


# ---------------------------------------------------------------------------
# POST /admin/settings/vapid_keys/generate
# ---------------------------------------------------------------------------


def test_generate_vapid_keys_returns_both_plaintexts(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """First call: response carries plaintext public + private + overwrote=False."""
    r = client.post(VAPID_GENERATE_URL, cookies=superuser_cookies)
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["public_key"], str)
    assert isinstance(body["private_key"], str)
    assert body["overwrote_existing"] is False

    # Sanity: P-256 uncompressed point is 65 bytes → ~88 chars b64url no-pad.
    # P-256 raw private scalar is 32 bytes → ~43 chars b64url no-pad.
    assert 80 <= len(body["public_key"]) <= 100
    assert 40 <= len(body["private_key"]) <= 50

    # url-safe-base64 (no padding) decodes cleanly with re-padding.
    def _decode(s: str) -> bytes:
        pad = (-len(s)) % 4
        return base64.urlsafe_b64decode(s + "=" * pad)

    assert len(_decode(body["public_key"])) == 65
    assert _decode(body["public_key"])[0:1] == b"\x04"  # uncompressed point
    assert len(_decode(body["private_key"])) == 32


def test_generate_vapid_keys_persists_public_plain_private_encrypted(
    client: TestClient, superuser_cookies: httpx.Cookies, db: Session
) -> None:
    """Public lands in JSONB value; private lands in BYTEA value_encrypted."""
    r = client.post(VAPID_GENERATE_URL, cookies=superuser_cookies)
    assert r.status_code == 200, r.text
    expected_public = r.json()["public_key"]
    expected_private = r.json()["private_key"]

    db.expire_all()
    pub = db.exec(
        select(SystemSetting).where(SystemSetting.key == VAPID_PUBLIC_KEY)
    ).one()
    priv = db.exec(
        select(SystemSetting).where(SystemSetting.key == VAPID_PRIVATE_KEY)
    ).one()

    # Public row: non-sensitive, plain JSONB carries the value.
    assert pub.sensitive is False
    assert pub.has_value is True
    assert pub.value == expected_public
    assert pub.value_encrypted is None

    # Private row: sensitive, value column NULL, value_encrypted populated.
    assert priv.sensitive is True
    assert priv.has_value is True
    assert priv.value is None
    assert priv.value_encrypted is not None
    # The encrypted bytes MUST NOT contain the plaintext.
    assert (
        expected_private.encode("ascii") not in priv.value_encrypted
    ), "private key plaintext must not appear in ciphertext bytes"


def test_generate_vapid_keys_redacts_private_on_subsequent_get(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """After the one-shot, admin GET on the private row returns the redacted shape."""
    g = client.post(VAPID_GENERATE_URL, cookies=superuser_cookies)
    assert g.status_code == 200

    r = client.get(
        f"{ADMIN_SETTINGS_URL}/{VAPID_PRIVATE_KEY}",
        cookies=superuser_cookies,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["key"] == VAPID_PRIVATE_KEY
    assert body["sensitive"] is True
    assert body["has_value"] is True
    assert body["value"] is None

    # Public row: still visible as plain JSONB.
    pr = client.get(
        f"{ADMIN_SETTINGS_URL}/{VAPID_PUBLIC_KEY}",
        cookies=superuser_cookies,
    )
    assert pr.status_code == 200
    pbody = pr.json()
    assert pbody["sensitive"] is False
    assert pbody["has_value"] is True
    assert pbody["value"] == g.json()["public_key"]


def test_generate_vapid_keys_re_call_sets_overwrote_existing(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """Re-call is destructive: returns fresh keys + overwrote_existing=True."""
    first = client.post(VAPID_GENERATE_URL, cookies=superuser_cookies)
    assert first.status_code == 200, first.text
    first_pub = first.json()["public_key"]
    first_priv = first.json()["private_key"]
    assert first.json()["overwrote_existing"] is False

    second = client.post(VAPID_GENERATE_URL, cookies=superuser_cookies)
    assert second.status_code == 200, second.text
    second_pub = second.json()["public_key"]
    second_priv = second.json()["private_key"]
    assert second.json()["overwrote_existing"] is True

    # Fresh keypair on rotate.
    assert first_pub != second_pub
    assert first_priv != second_priv


def test_generate_vapid_keys_emits_audit_log(
    client: TestClient, superuser_cookies: httpx.Cookies, caplog
) -> None:
    """The audit log carries the public-key prefix only — never raw keys."""
    with caplog.at_level(logging.INFO, logger="app.api.routes.admin"):
        r = client.post(VAPID_GENERATE_URL, cookies=superuser_cookies)
    assert r.status_code == 200, r.text
    public_key = r.json()["public_key"]
    private_key = r.json()["private_key"]

    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        "admin.vapid_keys.generated" in m
        and f"key_prefix={public_key[:4]}" in m
        and "overwrote=false" in m
        for m in msgs
    ), msgs
    # Neither raw key may appear in any log line.
    for m in msgs:
        assert public_key not in m
        assert private_key not in m


def test_generate_vapid_keys_unauthenticated_returns_401(
    client: TestClient,
) -> None:
    client.cookies.clear()
    r = client.post(VAPID_GENERATE_URL)
    assert r.status_code == 401


def test_generate_vapid_keys_as_normal_user_returns_403(
    client: TestClient,
) -> None:
    _u_id, cookies_u = _signup(client)
    r = client.post(VAPID_GENERATE_URL, cookies=cookies_u)
    assert r.status_code == 403


def test_per_key_generate_refused_for_vapid_public(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """The per-key /generate endpoint must redirect operators to the atomic path."""
    r = client.post(
        f"{ADMIN_SETTINGS_URL}/{VAPID_PUBLIC_KEY}/generate",
        cookies=superuser_cookies,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["detail"] == "use_atomic_endpoint_for_vapid_keys"


def test_per_key_generate_refused_for_vapid_private(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    r = client.post(
        f"{ADMIN_SETTINGS_URL}/{VAPID_PRIVATE_KEY}/generate",
        cookies=superuser_cookies,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["detail"] == "use_atomic_endpoint_for_vapid_keys"
