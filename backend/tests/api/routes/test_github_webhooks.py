"""Unit tests for the GitHub webhook receiver (M004 / S05 / T02).

Stands up a real TestClient + real Postgres so the HMAC pipeline, the
``system_settings`` decrypt hop, the ON CONFLICT DO NOTHING insert, and
the rejection-row write all execute end-to-end. The seven scenarios from
the task plan:

  (a) valid signature → 200 + event row + dispatch invoked + 3 INFO logs
  (b) invalid signature → 401 + rejection row + WARNING log + no event
      row + no dispatch
  (c) absent signature header → 401 + rejection row signature_present=false
  (d) duplicate delivery_id → 200 + only one event row + dispatch
      invoked exactly once
  (e) malformed JSON body with valid signature → 400 + no event row
  (f) decrypt failure (mocked) → 503 via the global handler with the
      named key in the response and the system_settings_decrypt_failed
      log line
  (g) unconfigured webhook secret → 503 webhook_secret_not_configured

The receiver does NOT require auth — the HMAC IS the auth. Tests post
directly to the public endpoint without cookies.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, delete

from app.api.routes.admin import GITHUB_APP_WEBHOOK_SECRET_KEY
from app.core import encryption
from app.core.config import settings as app_settings
from app.core.encryption import (
    SystemSettingDecryptError,
    encrypt_setting,
)
from app.models import (
    GitHubWebhookEvent,
    SystemSetting,
    WebhookRejection,
)

API = app_settings.API_V1_STR
WEBHOOK_URL = f"{API}/github/webhooks"

# Fixed test plaintext secret. We seed system_settings with the Fernet
# ciphertext of this value, then sign requests with it on the test side
# so HMAC verification can run against the real decrypt path.
_TEST_SECRET = "test-webhook-secret-do-not-ship"


# ---------------------------------------------------------------------------
# Encryption-key bootstrap
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _ensure_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make sure SYSTEM_SETTINGS_ENCRYPTION_KEY is set for every test.

    Per MEM243/MEM230 the encryption module reads the key directly from
    ``os.environ`` (pydantic-settings does NOT propagate) and caches the
    Fernet instance via ``functools.cache``. If a test runner started the
    process without the key, set a deterministic test key and clear the
    cache. We also clear the cache on teardown so a later test that wants
    a different key does not see a stale Fernet.
    """
    key = os.environ.get("SYSTEM_SETTINGS_ENCRYPTION_KEY")
    if not key:
        # Fernet.generate_key()-shaped value, deterministic for tests.
        key = "p3rpetuity_test_keyAAAAAAAAAAAAAAAAAAAAAAAA="
        monkeypatch.setenv("SYSTEM_SETTINGS_ENCRYPTION_KEY", key)
        encryption._load_key.cache_clear()
    yield
    encryption._load_key.cache_clear()


# ---------------------------------------------------------------------------
# Test isolation — wipe webhook tables + secret row before/after each test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_webhook_state(db: Session) -> Generator[None, None, None]:
    """Wipe webhook-receiver state so tests do not leak into each other.

    Mirrors the cleanup posture from ``test_github_install.py``: delete
    before AND after so a flake cannot poison the next test, and the
    surrounding suite is unaffected. We do not delete users, teams, or
    installations — those are unrelated to this route.
    """
    db.execute(delete(GitHubWebhookEvent))
    db.execute(delete(WebhookRejection))
    db.execute(
        delete(SystemSetting).where(
            SystemSetting.key == GITHUB_APP_WEBHOOK_SECRET_KEY
        )
    )
    db.commit()
    yield
    db.execute(delete(GitHubWebhookEvent))
    db.execute(delete(WebhookRejection))
    db.execute(
        delete(SystemSetting).where(
            SystemSetting.key == GITHUB_APP_WEBHOOK_SECRET_KEY
        )
    )
    db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_secret(db: Session, plaintext: str = _TEST_SECRET) -> None:
    """Seed `github_app_webhook_secret` as if the operator had POSTed
    `/admin/settings/.../generate`. Bypasses the admin route to keep this
    test focused on the receiver."""
    ct = encrypt_setting(plaintext)
    db.execute(
        text(
            """
            INSERT INTO system_settings
                (key, value, value_encrypted, sensitive, has_value, updated_at)
            VALUES
                (:key, NULL, :ct, TRUE, TRUE, NOW())
            ON CONFLICT (key) DO UPDATE
            SET value = NULL,
                value_encrypted = EXCLUDED.value_encrypted,
                sensitive = TRUE,
                has_value = TRUE,
                updated_at = NOW()
            """
        ),
        {"key": GITHUB_APP_WEBHOOK_SECRET_KEY, "ct": ct},
    )
    db.commit()


def _seed_corrupt_secret(db: Session) -> None:
    """Seed a row whose value_encrypted is not a valid Fernet token.

    decrypt_setting raises InvalidToken → SystemSettingDecryptError when
    called on this. Used to exercise the global decrypt-failure handler.
    """
    db.execute(
        text(
            """
            INSERT INTO system_settings
                (key, value, value_encrypted, sensitive, has_value, updated_at)
            VALUES
                (:key, NULL, :ct, TRUE, TRUE, NOW())
            ON CONFLICT (key) DO UPDATE
            SET value = NULL,
                value_encrypted = EXCLUDED.value_encrypted,
                sensitive = TRUE,
                has_value = TRUE,
                updated_at = NOW()
            """
        ),
        {
            "key": GITHUB_APP_WEBHOOK_SECRET_KEY,
            "ct": b"not-a-real-fernet-token",
        },
    )
    db.commit()


def _sign(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _post(
    client: TestClient,
    *,
    body: bytes,
    signature: str | None,
    delivery_id: str | None = "deadbeef-0000-0000-0000-000000000001",
    event_type: str | None = "push",
    install_target: str | None = None,
) -> Any:
    """Helper to POST a raw body so the receiver sees the exact bytes."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature
    if delivery_id is not None:
        headers["X-GitHub-Delivery"] = delivery_id
    if event_type is not None:
        headers["X-GitHub-Event"] = event_type
    if install_target is not None:
        headers["X-GitHub-Hook-Installation-Target-Id"] = install_target
    return client.post(WEBHOOK_URL, content=body, headers=headers)


def _events(db: Session) -> list[Any]:
    db.expire_all()
    return list(
        db.execute(
            text(
                "SELECT id, installation_id, event_type, delivery_id,"
                " dispatch_status, dispatch_error FROM github_webhook_events"
                " ORDER BY received_at"
            )
        ).fetchall()
    )


def _rejections(db: Session) -> list[Any]:
    db.expire_all()
    return list(
        db.execute(
            text(
                "SELECT delivery_id, signature_present, signature_valid,"
                " source_ip FROM webhook_rejections ORDER BY received_at"
            )
        ).fetchall()
    )


# ---------------------------------------------------------------------------
# (a) Valid signature → 200 + row + dispatch + 3 INFO logs
# ---------------------------------------------------------------------------


def test_valid_signature_persists_and_dispatches(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _seed_secret(db)

    captured: list[tuple[str, dict[str, Any], str | None]] = []

    async def _spy(
        event_type: str, payload: dict[str, Any], *, delivery_id: str | None = None, session: Any = None
    ) -> None:
        captured.append((event_type, payload, delivery_id))

    # Patch the binding inside the route module — that is the symbol the
    # endpoint resolves at call time.
    import app.api.routes.github_webhooks as wh

    monkeypatch.setattr(wh, "dispatch_github_event", _spy)

    body = json.dumps({"zen": "Speak like a human."}).encode()
    sig = _sign(_TEST_SECRET, body)

    caplog.set_level("INFO", logger="app.api.routes.github_webhooks")
    caplog.set_level("INFO", logger="app.services.dispatch")

    r = _post(
        client,
        body=body,
        signature=sig,
        delivery_id="dlv-valid-001",
        event_type="ping",
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ok", "duplicate": False}

    rows = _events(db)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row.delivery_id == "dlv-valid-001"
    assert row.event_type == "ping"
    # installation_id is nullable when the FK target row does not exist;
    # in production the install would be present, but this unit test
    # focuses on the receiver — see the integration test for the
    # FK-bound case.
    assert row.installation_id is None
    assert row.dispatch_status == "noop"

    # Dispatch was invoked exactly once with the parsed payload.
    assert captured == [
        ("ping", {"zen": "Speak like a human."}, "dlv-valid-001")
    ], captured

    # 3 contract log lines: webhook_received, webhook_verified, and (the
    # spy stands in for app.services.dispatch so its log line does not
    # fire — but the route's two contract lines must).
    msgs = [r.getMessage() for r in caplog.records]
    assert any("webhook_received" in m and "dlv-valid-001" in m for m in msgs), (
        msgs
    )
    assert any(
        "webhook_verified" in m and "dlv-valid-001" in m for m in msgs
    ), msgs


def test_valid_signature_dispatch_log_emitted_with_real_dispatch(
    client: TestClient,
    db: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """End-to-end: the no-op dispatch helper emits webhook_dispatched.

    Separate from the spy test above so we can verify the dispatch log
    line is emitted by the real ``app.services.dispatch`` module without
    an intervening monkeypatch.
    """
    _seed_secret(db)

    body = json.dumps({"foo": "bar"}).encode()
    sig = _sign(_TEST_SECRET, body)

    caplog.set_level("INFO", logger="app.api.routes.github_webhooks")
    caplog.set_level("INFO", logger="app.services.dispatch")

    r = _post(
        client,
        body=body,
        signature=sig,
        delivery_id="dlv-valid-real-001",
        event_type="push",
    )
    assert r.status_code == 200, r.text

    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        "webhook_dispatched" in m
        and "dlv-valid-real-001" in m
        for m in msgs
    ), msgs


# ---------------------------------------------------------------------------
# (b) Invalid signature → 401 + rejection row + WARNING log
# ---------------------------------------------------------------------------


def test_invalid_signature_rejects_and_audits(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _seed_secret(db)

    dispatched: list[tuple[str, dict[str, Any], str | None]] = []

    async def _spy(
        event_type: str, payload: dict[str, Any], *, delivery_id: str | None = None, session: Any = None
    ) -> None:
        dispatched.append((event_type, payload, delivery_id))

    import app.api.routes.github_webhooks as wh

    monkeypatch.setattr(wh, "dispatch_github_event", _spy)

    body = json.dumps({"foo": "bar"}).encode()

    caplog.set_level("WARNING", logger="app.api.routes.github_webhooks")

    r = _post(
        client,
        body=body,
        signature="sha256=" + ("0" * 64),
        delivery_id="dlv-bad-sig-001",
        event_type="push",
    )
    assert r.status_code == 401, r.text
    assert r.json() == {"detail": "invalid_signature"}

    # No event row.
    assert _events(db) == []
    # Exactly one rejection row, signature_present=true, valid=false.
    rejs = _rejections(db)
    assert len(rejs) == 1, rejs
    rej = rejs[0]
    assert rej.delivery_id == "dlv-bad-sig-001"
    assert rej.signature_present is True
    assert rej.signature_valid is False
    # Dispatch never invoked.
    assert dispatched == []

    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        "webhook_signature_invalid" in m
        and "dlv-bad-sig-001" in m
        and "signature_present=true" in m
        for m in msgs
    ), msgs


# ---------------------------------------------------------------------------
# (c) Absent signature header → 401 + rejection row signature_present=false
# ---------------------------------------------------------------------------


def test_absent_signature_header_rejects(
    client: TestClient,
    db: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _seed_secret(db)
    body = json.dumps({"foo": "bar"}).encode()

    caplog.set_level("WARNING", logger="app.api.routes.github_webhooks")

    r = _post(
        client,
        body=body,
        signature=None,  # absent
        delivery_id="dlv-no-sig-001",
        event_type="push",
    )
    assert r.status_code == 401, r.text
    assert r.json() == {"detail": "invalid_signature"}

    rejs = _rejections(db)
    assert len(rejs) == 1, rejs
    rej = rejs[0]
    assert rej.delivery_id == "dlv-no-sig-001"
    assert rej.signature_present is False
    assert rej.signature_valid is False

    assert _events(db) == []

    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        "webhook_signature_invalid" in m
        and "signature_present=false" in m
        for m in msgs
    ), msgs


# ---------------------------------------------------------------------------
# (d) Duplicate delivery_id → 200 idempotent, only one row, one dispatch
# ---------------------------------------------------------------------------


def test_duplicate_delivery_id_is_idempotent(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _seed_secret(db)

    invocations: list[str | None] = []

    async def _spy(
        event_type: str, payload: dict[str, Any], *, delivery_id: str | None = None, session: Any = None
    ) -> None:
        invocations.append(delivery_id)

    import app.api.routes.github_webhooks as wh

    monkeypatch.setattr(wh, "dispatch_github_event", _spy)

    body = json.dumps({"first": True}).encode()
    sig = _sign(_TEST_SECRET, body)

    caplog.set_level("INFO", logger="app.api.routes.github_webhooks")

    r1 = _post(
        client,
        body=body,
        signature=sig,
        delivery_id="dlv-dup-xyz",
        event_type="push",
    )
    assert r1.status_code == 200, r1.text
    assert r1.json() == {"status": "ok", "duplicate": False}

    # Second POST — same delivery_id, same body.
    r2 = _post(
        client,
        body=body,
        signature=sig,
        delivery_id="dlv-dup-xyz",
        event_type="push",
    )
    assert r2.status_code == 200, r2.text
    assert r2.json() == {"status": "ok", "duplicate": True}

    rows = _events(db)
    assert len(rows) == 1, rows
    assert rows[0].delivery_id == "dlv-dup-xyz"

    # Dispatch invoked exactly once on the first POST.
    assert invocations == ["dlv-dup-xyz"], invocations

    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        "webhook_duplicate_delivery" in m and "dlv-dup-xyz" in m for m in msgs
    ), msgs


# ---------------------------------------------------------------------------
# (e) Malformed JSON body with valid signature → 400 + no row
# ---------------------------------------------------------------------------


def test_valid_signature_but_malformed_json_returns_400(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_secret(db)

    invocations: list[Any] = []

    async def _spy(*a: Any, **kw: Any) -> None:
        invocations.append((a, kw))

    import app.api.routes.github_webhooks as wh

    monkeypatch.setattr(wh, "dispatch_github_event", _spy)

    body = b"this is not json {"
    sig = _sign(_TEST_SECRET, body)

    r = _post(
        client,
        body=body,
        signature=sig,
        delivery_id="dlv-bad-json-001",
        event_type="push",
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "invalid_json"

    # No event row, no rejection row (signature was good — this isn't a
    # rejection, it's a contract break we surface as 400).
    assert _events(db) == []
    assert invocations == []


# ---------------------------------------------------------------------------
# (f) Decrypt failure → 503 via global handler with the named key
# ---------------------------------------------------------------------------


def test_decrypt_failure_returns_503_via_global_handler(
    client: TestClient,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Mock decrypt_setting so it raises with key=None and confirm the
    receiver re-raises with the GITHUB_APP_WEBHOOK_SECRET_KEY attached,
    the global handler in app.main produces the system_settings_decrypt_failed
    log, and the response is 503 with the named key."""
    # Seed a row so the unconfigured-secret branch does NOT trip first;
    # the row's ciphertext bytes are valid-looking but decrypt_setting is
    # mocked to always raise.
    _seed_secret(db)

    import app.api.routes.github_webhooks as wh

    def _boom(_ct: bytes) -> str:
        raise SystemSettingDecryptError(key=None)

    monkeypatch.setattr(wh, "decrypt_setting", _boom)

    body = json.dumps({"foo": "bar"}).encode()
    sig = _sign(_TEST_SECRET, body)

    caplog.set_level("ERROR", logger="app.main")

    r = _post(
        client,
        body=body,
        signature=sig,
        delivery_id="dlv-decrypt-fail-001",
    )
    assert r.status_code == 503, r.text
    body_json = r.json()
    assert body_json["detail"] == "system_settings_decrypt_failed"
    assert body_json["key"] == GITHUB_APP_WEBHOOK_SECRET_KEY

    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        "system_settings_decrypt_failed" in m
        and GITHUB_APP_WEBHOOK_SECRET_KEY in m
        for m in msgs
    ), msgs


# ---------------------------------------------------------------------------
# (g) Unconfigured secret → 503 webhook_secret_not_configured
# ---------------------------------------------------------------------------


def test_unconfigured_webhook_secret_returns_503(
    client: TestClient,
    db: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # No _seed_secret call — the row is absent.
    body = json.dumps({"foo": "bar"}).encode()

    caplog.set_level("WARNING", logger="app.api.routes.github_webhooks")

    r = _post(
        client,
        body=body,
        signature=_sign(_TEST_SECRET, body),
        delivery_id="dlv-no-secret-001",
    )
    assert r.status_code == 503, r.text
    assert r.json() == {"detail": "webhook_secret_not_configured"}

    # No rejection row — operator misconfiguration, not a probe.
    assert _rejections(db) == []
    assert _events(db) == []

    msgs = [rec.getMessage() for rec in caplog.records]
    assert any("webhook_secret_not_configured" in m for m in msgs), msgs


def test_secret_row_present_but_no_value_returns_503(
    client: TestClient,
    db: Session,
) -> None:
    """has_value=False (operator started a generate but it failed) is
    treated identically to the missing-row case."""
    db.execute(
        text(
            """
            INSERT INTO system_settings
                (key, value, value_encrypted, sensitive, has_value, updated_at)
            VALUES
                (:key, NULL, NULL, TRUE, FALSE, NOW())
            ON CONFLICT (key) DO UPDATE
            SET value_encrypted = NULL, has_value = FALSE
            """
        ),
        {"key": GITHUB_APP_WEBHOOK_SECRET_KEY},
    )
    db.commit()

    body = b"{}"
    r = _post(
        client,
        body=body,
        signature=_sign(_TEST_SECRET, body),
        delivery_id="dlv-no-value-001",
    )
    assert r.status_code == 503
    assert r.json() == {"detail": "webhook_secret_not_configured"}
