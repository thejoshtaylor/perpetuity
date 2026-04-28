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
from app.models import PushSubscription, SystemSetting
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
    db.execute(delete(PushSubscription))
    db.commit()
    yield
    db.execute(delete(SystemSetting))
    db.execute(delete(PushSubscription))
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


# ---------------------------------------------------------------------------
# T03: POST/DELETE /push/subscribe + GET /push/subscriptions
# ---------------------------------------------------------------------------

PUSH_SUBSCRIBE_URL = f"{API_V1}/push/subscribe"
PUSH_SUBSCRIPTIONS_URL = f"{API_V1}/push/subscriptions"

# Realistic Mozilla Push Service endpoint shape — opaque to the server, but
# we pin it here so the tests assert the same string the dispatcher would
# see in production.
_MOZ_ENDPOINT_A = (
    "https://updates.push.services.mozilla.com/wpush/v2/"
    "gAAAAABlExampleEndpointAAAAAAAAAAAAA-device-a"
)
_MOZ_ENDPOINT_B = (
    "https://updates.push.services.mozilla.com/wpush/v2/"
    "gAAAAABlExampleEndpointBBBBBBBBBBBBB-device-b"
)
# pywebpush expects p256dh + auth as url-safe-base64; payload contents don't
# matter for the route layer — pywebpush is exercised in T02's tests.
_VALID_KEYS = {
    "p256dh": (
        "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM"
    ),
    "auth": "tBHItJI5svbpez7KI4CCXg",
}


def _endpoint_hash_8(endpoint: str) -> str:
    import hashlib

    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:8]


def _subscribe_body(endpoint: str = _MOZ_ENDPOINT_A) -> dict:
    return {"endpoint": endpoint, "keys": dict(_VALID_KEYS)}


def test_subscribe_creates_row_first_time(
    client: TestClient, db: Session
) -> None:
    """POST → 201, single row in DB, endpoint_hash projected, raw endpoint
    never returned."""
    _user_id, cookies = _signup(client)

    r = client.post(
        PUSH_SUBSCRIBE_URL,
        cookies=cookies,
        json=_subscribe_body(),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["endpoint_hash"] == _endpoint_hash_8(_MOZ_ENDPOINT_A)
    assert "endpoint" not in body  # raw URL never leaks via API surface

    # Single row in DB, linked to the caller, keys persisted as JSONB.
    db.expire_all()
    rows = db.exec(select(PushSubscription)).all()
    assert len(rows) == 1
    row = rows[0]
    assert str(row.user_id) == _user_id
    assert row.endpoint == _MOZ_ENDPOINT_A
    assert row.keys == _VALID_KEYS


def test_subscribe_idempotent_upsert(
    client: TestClient, db: Session, caplog
) -> None:
    """Second POST with the same endpoint → 200, single row, last_seen_at
    advanced, ``existing=true`` log captured. Mirrors the dispatcher's
    upsert posture in T02."""
    _user_id, cookies = _signup(client)

    first = client.post(
        PUSH_SUBSCRIBE_URL, cookies=cookies, json=_subscribe_body()
    )
    assert first.status_code == 201, first.text

    db.expire_all()
    first_rows = db.exec(select(PushSubscription)).all()
    assert len(first_rows) == 1
    first_seen = first_rows[0].last_seen_at

    # Re-subscribe from the same browser. Refreshed keys must overwrite.
    refreshed_keys = dict(_VALID_KEYS)
    refreshed_keys["auth"] = "RotatedAuthValue1234"
    body2 = {"endpoint": _MOZ_ENDPOINT_A, "keys": refreshed_keys}

    with caplog.at_level(logging.INFO, logger="app.api.routes.push"):
        second = client.post(PUSH_SUBSCRIBE_URL, cookies=cookies, json=body2)
    assert second.status_code == 200, second.text

    db.expire_all()
    rows = db.exec(select(PushSubscription)).all()
    assert len(rows) == 1, "upsert must not create a second row"
    assert rows[0].keys["auth"] == "RotatedAuthValue1234"
    # last_seen_at advanced.
    assert rows[0].last_seen_at is not None
    if first_seen is not None:
        assert rows[0].last_seen_at >= first_seen

    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        "push.subscribe.upsert" in m and "existing=true" in m for m in msgs
    ), msgs


def test_subscribe_two_devices_for_one_user(
    client: TestClient, db: Session
) -> None:
    """Two distinct endpoints for the same user → two rows, same user_id."""
    user_id, cookies = _signup(client)

    a = client.post(
        PUSH_SUBSCRIBE_URL,
        cookies=cookies,
        json=_subscribe_body(_MOZ_ENDPOINT_A),
    )
    b = client.post(
        PUSH_SUBSCRIBE_URL,
        cookies=cookies,
        json=_subscribe_body(_MOZ_ENDPOINT_B),
    )
    assert a.status_code == 201, a.text
    assert b.status_code == 201, b.text

    db.expire_all()
    rows = db.exec(select(PushSubscription)).all()
    assert len(rows) == 2
    assert {str(r.user_id) for r in rows} == {user_id}
    assert {r.endpoint for r in rows} == {_MOZ_ENDPOINT_A, _MOZ_ENDPOINT_B}


def test_unsubscribe_by_endpoint(
    client: TestClient, db: Session, caplog
) -> None:
    """DELETE → row gone, ``deleted=true`` log."""
    _user_id, cookies = _signup(client)

    r = client.post(
        PUSH_SUBSCRIBE_URL, cookies=cookies, json=_subscribe_body()
    )
    assert r.status_code == 201

    with caplog.at_level(logging.INFO, logger="app.api.routes.push"):
        d = client.request(
            "DELETE",
            PUSH_SUBSCRIBE_URL,
            cookies=cookies,
            json={"endpoint": _MOZ_ENDPOINT_A},
        )
    assert d.status_code == 204, d.text

    db.expire_all()
    rows = db.exec(select(PushSubscription)).all()
    assert rows == []

    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        "push.unsubscribe" in m and "deleted=true" in m for m in msgs
    ), msgs


def test_unsubscribe_unknown_endpoint_is_noop(
    client: TestClient, db: Session, caplog
) -> None:
    """DELETE for endpoint that does not belong to the user → 204 with
    ``deleted=false`` log; row count unchanged."""
    _user_id, cookies = _signup(client)

    # Seed an unrelated endpoint so we can confirm the row count is stable.
    r = client.post(
        PUSH_SUBSCRIBE_URL,
        cookies=cookies,
        json=_subscribe_body(_MOZ_ENDPOINT_A),
    )
    assert r.status_code == 201

    with caplog.at_level(logging.INFO, logger="app.api.routes.push"):
        d = client.request(
            "DELETE",
            PUSH_SUBSCRIBE_URL,
            cookies=cookies,
            json={"endpoint": _MOZ_ENDPOINT_B},
        )
    assert d.status_code == 204, d.text

    db.expire_all()
    rows = db.exec(select(PushSubscription)).all()
    assert len(rows) == 1
    assert rows[0].endpoint == _MOZ_ENDPOINT_A

    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        "push.unsubscribe" in m and "deleted=false" in m for m in msgs
    ), msgs


def test_subscribe_requires_auth(client: TestClient) -> None:
    """No cookie → 401."""
    client.cookies.clear()
    r = client.post(PUSH_SUBSCRIBE_URL, json=_subscribe_body())
    assert r.status_code == 401


def test_unsubscribe_requires_auth(client: TestClient) -> None:
    client.cookies.clear()
    r = client.request(
        "DELETE", PUSH_SUBSCRIBE_URL, json={"endpoint": _MOZ_ENDPOINT_A}
    )
    assert r.status_code == 401


def test_get_subscriptions_requires_auth(client: TestClient) -> None:
    client.cookies.clear()
    r = client.get(PUSH_SUBSCRIPTIONS_URL)
    assert r.status_code == 401


def test_subscribe_log_uses_endpoint_hash_not_url(
    client: TestClient, caplog
) -> None:
    """Log redaction: subscribe + upsert + unsubscribe lines must contain
    ``endpoint_hash=<sha256:8>`` and MUST NOT contain the raw endpoint URL.
    Mirrors the T02 redaction test for the dispatcher."""
    _user_id, cookies = _signup(client)
    expected_hash = _endpoint_hash_8(_MOZ_ENDPOINT_A)

    with caplog.at_level(logging.INFO, logger="app.api.routes.push"):
        # First POST: insert
        r1 = client.post(
            PUSH_SUBSCRIBE_URL, cookies=cookies, json=_subscribe_body()
        )
        # Second POST: upsert
        r2 = client.post(
            PUSH_SUBSCRIBE_URL, cookies=cookies, json=_subscribe_body()
        )
        # DELETE: drop
        r3 = client.request(
            "DELETE",
            PUSH_SUBSCRIBE_URL,
            cookies=cookies,
            json={"endpoint": _MOZ_ENDPOINT_A},
        )
    assert r1.status_code == 201
    assert r2.status_code == 200
    assert r3.status_code == 204

    msgs = [rec.getMessage() for rec in caplog.records]
    # Hash must appear at least once on each log family.
    assert any(
        "push.subscribe" in m
        and "upsert" not in m
        and f"endpoint_hash={expected_hash}" in m
        for m in msgs
    ), msgs
    assert any(
        "push.subscribe.upsert" in m
        and f"endpoint_hash={expected_hash}" in m
        for m in msgs
    ), msgs
    assert any(
        "push.unsubscribe" in m
        and f"endpoint_hash={expected_hash}" in m
        for m in msgs
    ), msgs
    # Raw endpoint URL must NEVER appear in any log line.
    for m in msgs:
        assert _MOZ_ENDPOINT_A not in m, (
            f"raw endpoint leaked into log line: {m}"
        )


def test_subscribe_does_not_log_full_user_agent(
    client: TestClient, caplog, db: Session
) -> None:
    """The full UA is captured into the row but never logged in full —
    only the leading non-whitespace token is emitted."""
    _user_id, cookies = _signup(client)
    full_ua = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X)"
        " AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148"
    )

    with caplog.at_level(logging.INFO, logger="app.api.routes.push"):
        r = client.post(
            PUSH_SUBSCRIBE_URL,
            cookies=cookies,
            json=_subscribe_body(),
            headers={"user-agent": full_ua},
        )
    assert r.status_code == 201, r.text

    # Row holds the full UA (truncated to 500 chars by the column).
    db.expire_all()
    rows = db.exec(select(PushSubscription)).all()
    assert len(rows) == 1
    assert rows[0].user_agent == full_ua

    # No log line carries the entire UA — only the leading "Mozilla/5.0" token.
    msgs = [rec.getMessage() for rec in caplog.records]
    assert any("ua=Mozilla/5.0" in m for m in msgs), msgs
    for m in msgs:
        assert "iPhone" not in m, f"UA leaked into log: {m}"


def test_get_subscriptions_lists_only_callers_rows(
    client: TestClient,
) -> None:
    """Two users, each with a subscription — each only sees their own."""
    user_a, cookies_a = _signup(client)
    _user_b, cookies_b = _signup(client)

    ra = client.post(
        PUSH_SUBSCRIBE_URL,
        cookies=cookies_a,
        json=_subscribe_body(_MOZ_ENDPOINT_A),
    )
    rb = client.post(
        PUSH_SUBSCRIBE_URL,
        cookies=cookies_b,
        json=_subscribe_body(_MOZ_ENDPOINT_B),
    )
    assert ra.status_code == 201
    assert rb.status_code == 201

    # User A sees only the A endpoint.
    la = client.get(PUSH_SUBSCRIPTIONS_URL, cookies=cookies_a)
    assert la.status_code == 200
    body_a = la.json()
    assert body_a["count"] == 1
    assert body_a["data"][0]["endpoint_hash"] == _endpoint_hash_8(
        _MOZ_ENDPOINT_A
    )
    assert "endpoint" not in body_a["data"][0]

    # User B sees only the B endpoint.
    lb = client.get(PUSH_SUBSCRIPTIONS_URL, cookies=cookies_b)
    assert lb.status_code == 200
    body_b = lb.json()
    assert body_b["count"] == 1
    assert body_b["data"][0]["endpoint_hash"] == _endpoint_hash_8(
        _MOZ_ENDPOINT_B
    )

    # Cross-check: caller A is structurally separated from caller B.
    assert (
        body_a["data"][0]["endpoint_hash"]
        != body_b["data"][0]["endpoint_hash"]
    )
