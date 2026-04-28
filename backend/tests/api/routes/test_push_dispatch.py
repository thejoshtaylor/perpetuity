"""Web Push dispatcher unit + integration tests (M005 / S03 / T02).

These tests exercise `app.core.push_dispatch.dispatch_push` against seeded
PushSubscription rows + a monkeypatched `pywebpush.webpush`. We deliberately
do NOT hit a real Mozilla Push Service — the unit test boundary is the
`webpush()` call, and the test contract is "what dispatch_push does with the
upstream's response" (200/201, 410, 5xx, exception).

The redaction test is the slice's gate: NO log line may contain the raw
endpoint URL — only the 8-hex-char sha256 prefix.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

import pytest
from pywebpush import WebPushException
from sqlalchemy import delete
from sqlmodel import Session, select

from app.core import push_dispatch
from app.models import (
    NotificationKind,
    PushSubscription,
    SystemSetting,
    User,
)


# ---------------------------------------------------------------------------
# Logger re-enable — alembic.command.upgrade calls logging.config.fileConfig
# with disable_existing_loggers=True (the default), which silently flips
# logger.disabled=True on every logger created before that moment. After a
# migration test runs, caplog stops capturing INFO/WARNING/ERROR from
# app.core.push_dispatch — re-enable per test so the order of pytest
# collection doesn't change the assertions. Mirrors the fixture in
# test_push.py (MEM359).
# ---------------------------------------------------------------------------


_LOGGERS_TO_REENABLE = (
    "app.core.push_dispatch",
    "app.core.notify",
)


@pytest.fixture(autouse=True)
def _reenable_loggers():
    for name in _LOGGERS_TO_REENABLE:
        logging.getLogger(name).disabled = False
    yield


# ---------------------------------------------------------------------------
# Fernet encryption-key fixture so VAPID-private decrypt path works.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _set_encryption_key(monkeypatch):
    monkeypatch.setenv(
        "SYSTEM_SETTINGS_ENCRYPTION_KEY",
        "q14YMz9s4jrbfD29GvcRfe_4krg82w6_mPWUu_y3LTo=",
    )
    from app.core import encryption as _enc

    _enc._load_key.cache_clear()
    yield
    _enc._load_key.cache_clear()


# ---------------------------------------------------------------------------
# Per-test cleanup: wipe push_subscriptions + system_settings.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_state(db: Session):
    db.execute(delete(PushSubscription))
    db.execute(delete(SystemSetting))
    db.commit()
    yield
    db.execute(delete(PushSubscription))
    db.execute(delete(SystemSetting))
    db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_user(db: Session) -> uuid.UUID:
    """Insert a fresh user row + return its id.

    We avoid /auth/signup because most of these tests don't need cookies and
    a direct INSERT is faster + isolates the dispatcher unit.
    """
    from app.core.security import get_password_hash

    user = User(
        email=f"push-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=get_password_hash("placeholder-password"),
        is_active=True,
        full_name="Push Test User",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user.id


def _seed_vapid_keys(db: Session) -> str:
    """Write a VAPID keypair into system_settings and return the private b64.

    Re-uses the admin route's _generate_vapid_keypair so the wire shape is
    identical to a real operator-generated keypair.
    """
    from app.api.routes.admin import (
        _generate_vapid_keypair,
        _upsert_encrypted,
        _upsert_jsonb,
    )

    public_b64, private_b64 = _generate_vapid_keypair()
    _upsert_jsonb(db, "vapid_public_key", public_b64)
    _upsert_encrypted(db, "vapid_private_key", private_b64)
    db.commit()
    return private_b64


def _seed_subscription(
    db: Session,
    *,
    user_id: uuid.UUID,
    endpoint: str = "https://mock-push.invalid/abc",
    consecutive_failures: int = 0,
    last_status_code: int | None = None,
) -> PushSubscription:
    sub = PushSubscription(
        user_id=user_id,
        endpoint=endpoint,
        keys={"p256dh": "test-p256dh", "auth": "test-auth"},
        user_agent="Mozilla/5.0 (Test)",
        consecutive_failures=consecutive_failures,
        last_status_code=last_status_code,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


class _FakeResponse:
    """Stand-in for a requests.Response that pywebpush returns on success."""

    def __init__(self, status_code: int = 201) -> None:
        self.status_code = status_code


def _patch_webpush(
    monkeypatch: pytest.MonkeyPatch,
    *,
    behavior,
) -> list[dict[str, Any]]:
    """Monkeypatch pywebpush.webpush in the dispatcher namespace.

    `behavior` is a callable invoked with the kwargs the dispatcher passes;
    it returns either a `_FakeResponse` instance OR raises an exception. It
    can also be a non-callable: list to consume sequentially, single value
    to return for every call.

    Returns a list that the test can inspect post-call to assert the
    arguments passed to webpush().
    """
    calls: list[dict[str, Any]] = []

    if callable(behavior):
        behavior_fn = behavior
    elif isinstance(behavior, list):
        iterator = iter(behavior)

        def behavior_fn(**_kw):
            try:
                outcome = next(iterator)
            except StopIteration as exc:
                raise AssertionError(
                    "webpush() called more times than mock outcomes available"
                ) from exc
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
    else:
        outcome = behavior

        def behavior_fn(**_kw):
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

    def fake_webpush(**kwargs):
        calls.append(kwargs)
        return behavior_fn(**kwargs)

    monkeypatch.setattr(push_dispatch, "webpush", fake_webpush)
    return calls


def _make_webpush_exception(status_code: int) -> WebPushException:
    """Build a WebPushException whose response carries `status_code`."""
    exc = WebPushException("mocked upstream failure")
    exc.response = _FakeResponse(status_code=status_code)
    return exc


# ---------------------------------------------------------------------------
# Dispatcher tests (8)
# ---------------------------------------------------------------------------


def test_dispatch_signs_with_vapid_and_posts_to_endpoint(
    db: Session, monkeypatch: pytest.MonkeyPatch
):
    """One POST per subscription; subscription_info + vapid_private_key flow through."""
    user_id = _seed_user(db)
    private_b64 = _seed_vapid_keys(db)
    sub = _seed_subscription(db, user_id=user_id)

    calls = _patch_webpush(monkeypatch, behavior=_FakeResponse(201))

    delivered = push_dispatch.dispatch_push(
        db,
        user_id=user_id,
        kind=NotificationKind.workflow_run_failed,
        title="Workflow failed",
        body="Run X blew up",
        url=f"/runs/{uuid.uuid4()}",
    )
    assert delivered == 1
    assert len(calls) == 1
    call = calls[0]
    assert call["subscription_info"]["endpoint"] == sub.endpoint
    assert call["subscription_info"]["keys"] == {
        "p256dh": "test-p256dh",
        "auth": "test-auth",
    }
    assert call["vapid_private_key"] == private_b64
    assert call["vapid_claims"]["sub"].startswith("mailto:")
    # Payload is JSON-encoded bytes carrying the SW-render fields.
    import json

    body = json.loads(call["data"].decode("utf-8"))
    assert body["title"] == "Workflow failed"
    assert body["body"] == "Run X blew up"
    assert body["kind"] == "workflow_run_failed"


def test_dispatch_201_marks_last_seen_and_resets_failures(
    db: Session, monkeypatch: pytest.MonkeyPatch
):
    """A 2xx response resets consecutive_failures and bumps last_seen_at."""
    user_id = _seed_user(db)
    _seed_vapid_keys(db)
    sub = _seed_subscription(
        db,
        user_id=user_id,
        consecutive_failures=3,
        last_status_code=503,
    )
    before = sub.last_seen_at

    _patch_webpush(monkeypatch, behavior=_FakeResponse(201))

    delivered = push_dispatch.dispatch_push(
        db,
        user_id=user_id,
        kind=NotificationKind.system,
        title="t",
        body="b",
        url="/",
    )
    assert delivered == 1

    db.expire_all()
    refreshed = db.get(PushSubscription, sub.id)
    assert refreshed is not None
    assert refreshed.consecutive_failures == 0
    assert refreshed.last_status_code == 201
    assert refreshed.last_seen_at is not None
    assert refreshed.last_seen_at >= before  # type: ignore[operator]


def test_dispatch_410_prunes_subscription(
    db: Session, monkeypatch: pytest.MonkeyPatch, caplog
):
    """410 from upstream → row is deleted, INFO log fires."""
    user_id = _seed_user(db)
    _seed_vapid_keys(db)
    sub = _seed_subscription(db, user_id=user_id)
    sub_id = sub.id

    _patch_webpush(monkeypatch, behavior=_make_webpush_exception(410))

    with caplog.at_level(logging.INFO, logger="app.core.push_dispatch"):
        delivered = push_dispatch.dispatch_push(
            db,
            user_id=user_id,
            kind=NotificationKind.system,
            title="t",
            body="b",
            url="/",
        )
    assert delivered == 0

    db.expire_all()
    assert db.get(PushSubscription, sub_id) is None

    msgs = [rec.getMessage() for rec in caplog.records]
    assert any("push.dispatch.pruned_410" in m for m in msgs), msgs


def test_dispatch_5xx_increments_then_prunes_at_five(
    db: Session, monkeypatch: pytest.MonkeyPatch, caplog
):
    """Five sequential 500s: warns at 1..4, prunes at 5."""
    user_id = _seed_user(db)
    _seed_vapid_keys(db)
    sub = _seed_subscription(db, user_id=user_id)
    sub_id = sub.id

    _patch_webpush(monkeypatch, behavior=_make_webpush_exception(500))

    expected_remaining_after = [True, True, True, True, False]
    expected_failure_counts = [1, 2, 3, 4, 5]

    with caplog.at_level(logging.WARNING, logger="app.core.push_dispatch"):
        for i, (still_present, fail_count) in enumerate(
            zip(expected_remaining_after, expected_failure_counts), start=1
        ):
            delivered = push_dispatch.dispatch_push(
                db,
                user_id=user_id,
                kind=NotificationKind.system,
                title="t",
                body="b",
                url="/",
            )
            assert delivered == 0, f"iteration {i}"

            db.expire_all()
            row = db.get(PushSubscription, sub_id)
            if still_present:
                assert row is not None, f"row missing at iteration {i}"
                assert row.consecutive_failures == fail_count
                assert row.last_status_code == 500
            else:
                assert row is None, f"row not pruned at iteration {i}"

    msgs = [rec.getMessage() for rec in caplog.records]
    # First four iterations emit consecutive_failure warnings.
    consecutive_msgs = [
        m for m in msgs if "push.dispatch.consecutive_failure" in m
    ]
    assert len(consecutive_msgs) == 4, msgs
    assert any("count=1" in m for m in consecutive_msgs)
    assert any("count=4" in m for m in consecutive_msgs)
    # The fifth iteration prunes.
    assert any("push.dispatch.pruned_max_failures" in m for m in msgs), msgs


def test_dispatch_multi_device_fanout(
    db: Session, monkeypatch: pytest.MonkeyPatch
):
    """Two subscriptions for one user → both receive a delivery, both updated."""
    user_id = _seed_user(db)
    _seed_vapid_keys(db)
    sub_phone = _seed_subscription(
        db,
        user_id=user_id,
        endpoint="https://mock-push.invalid/phone-token",
    )
    sub_laptop = _seed_subscription(
        db,
        user_id=user_id,
        endpoint="https://mock-push.invalid/laptop-token",
    )

    calls = _patch_webpush(monkeypatch, behavior=_FakeResponse(201))

    delivered = push_dispatch.dispatch_push(
        db,
        user_id=user_id,
        kind=NotificationKind.system,
        title="t",
        body="b",
        url="/",
    )
    assert delivered == 2
    posted_endpoints = {
        c["subscription_info"]["endpoint"] for c in calls
    }
    assert posted_endpoints == {sub_phone.endpoint, sub_laptop.endpoint}

    db.expire_all()
    for sid in (sub_phone.id, sub_laptop.id):
        row = db.get(PushSubscription, sid)
        assert row is not None
        assert row.last_status_code == 201
        assert row.consecutive_failures == 0


def test_dispatch_endpoint_logged_as_hash_only_redaction(
    db: Session, monkeypatch: pytest.MonkeyPatch, caplog
):
    """The slice's redaction gate: caplog must NEVER carry the raw endpoint."""
    user_id = _seed_user(db)
    _seed_vapid_keys(db)
    raw_endpoint = (
        "https://mock-push.invalid/very-distinctive-token-do-not-leak"
    )
    _seed_subscription(db, user_id=user_id, endpoint=raw_endpoint)

    _patch_webpush(monkeypatch, behavior=_FakeResponse(201))

    with caplog.at_level(logging.INFO, logger="app.core.push_dispatch"):
        push_dispatch.dispatch_push(
            db,
            user_id=user_id,
            kind=NotificationKind.system,
            title="t",
            body="b",
            url="/",
        )

    expected_hash = hashlib.sha256(
        raw_endpoint.encode("utf-8")
    ).hexdigest()[:8]
    msgs = [rec.getMessage() for rec in caplog.records]
    # Some delivery log line must include the 8-hex-char hash.
    assert any(f"endpoint_hash={expected_hash}" in m for m in msgs), msgs
    # No log line may contain the raw endpoint URL substring.
    for m in msgs:
        assert raw_endpoint not in m, (
            f"raw endpoint leaked into log line: {m}"
        )
        assert "very-distinctive-token-do-not-leak" not in m, (
            f"raw endpoint substring leaked into log line: {m}"
        )


def test_dispatch_vapid_decrypt_failure_logs_503_path(
    db: Session, monkeypatch: pytest.MonkeyPatch, caplog
):
    """Corrupt the encrypted private-key row → return 0, log vapid_decrypt_failed."""
    user_id = _seed_user(db)
    _seed_vapid_keys(db)
    _seed_subscription(db, user_id=user_id)

    # Corrupt the encrypted bytes — Fernet.decrypt() will raise InvalidToken
    # which the encryption layer translates into SystemSettingDecryptError.
    priv = db.exec(
        select(SystemSetting).where(SystemSetting.key == "vapid_private_key")
    ).one()
    priv.value_encrypted = b"this is not a valid Fernet token at all"
    db.add(priv)
    db.commit()

    # Ensure webpush() is never invoked — patch with a sentinel that raises.
    def _should_not_be_called(**_kw):
        raise AssertionError("webpush() must not run when VAPID decrypt fails")

    monkeypatch.setattr(push_dispatch, "webpush", _should_not_be_called)

    with caplog.at_level(logging.ERROR, logger="app.core.push_dispatch"):
        delivered = push_dispatch.dispatch_push(
            db,
            user_id=user_id,
            kind=NotificationKind.system,
            title="t",
            body="b",
            url="/",
        )
    assert delivered == 0

    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        "push.vapid_decrypt_failed" in m and "key=vapid_private_key" in m
        for m in msgs
    ), msgs


def test_dispatch_unknown_exception_logs_send_failed_no_prune(
    db: Session, monkeypatch: pytest.MonkeyPatch, caplog
):
    """Non-WebPushException → ERROR log, NO prune, returns 0 deliveries."""
    user_id = _seed_user(db)
    _seed_vapid_keys(db)
    sub = _seed_subscription(db, user_id=user_id)
    sub_id = sub.id

    _patch_webpush(monkeypatch, behavior=RuntimeError("transport blew up"))

    with caplog.at_level(logging.ERROR, logger="app.core.push_dispatch"):
        delivered = push_dispatch.dispatch_push(
            db,
            user_id=user_id,
            kind=NotificationKind.system,
            title="t",
            body="b",
            url="/",
        )
    assert delivered == 0

    db.expire_all()
    # Row is NOT pruned — we can't tell whether it was the upstream's fault.
    assert db.get(PushSubscription, sub_id) is not None

    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        "push.dispatch.send_failed" in m and "cause=RuntimeError" in m
        for m in msgs
    ), msgs
