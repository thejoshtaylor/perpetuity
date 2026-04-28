"""Endpoint + helper tests for the notifications router (M005 / S02 / T02).

Real TestClient + real Postgres. We exercise the route surface end-to-end
(cookie auth, list / read / read_all / preferences) and call notify()
directly to verify preference suppression and payload redaction.
"""
from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select

from app.core.config import settings
from app.core.notify import DEFAULTS, notify
from app.models import (
    Notification,
    NotificationKind,
    NotificationPreference,
)
from tests.utils.utils import random_email, random_lower_string

API = settings.API_V1_STR
SIGNUP_URL = f"{API}/auth/signup"
NOTIF_URL = f"{API}/notifications"


# ---------------------------------------------------------------------------
# Test isolation — wipe notifications + preferences before AND after each test.
# We do not delete users; the session-scoped `db` fixture intentionally leaks
# them across tests (matching the test_projects.py pattern).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_notifications_state(db: Session):
    db.execute(delete(Notification))
    db.execute(delete(NotificationPreference))
    db.commit()
    yield
    db.execute(delete(Notification))
    db.execute(delete(NotificationPreference))
    db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _signup(client: TestClient) -> tuple[uuid.UUID, httpx.Cookies]:
    """Create a fresh user and return (user_id, cookie jar)."""
    email = random_email()
    password = random_lower_string()
    client.cookies.clear()
    r = client.post(SIGNUP_URL, json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    user_id = uuid.UUID(r.json()["id"])
    jar = httpx.Cookies()
    for cookie in client.cookies.jar:
        jar.set(cookie.name, cookie.value)
    client.cookies.clear()
    return user_id, jar


def _seed(db: Session, *, user_id: uuid.UUID, kind: str = "system",
          payload: dict | None = None, read: bool = False) -> Notification:
    row = Notification(
        user_id=user_id,
        kind=kind,
        payload=payload or {},
    )
    if read:
        from datetime import datetime, timezone
        row.read_at = datetime.now(timezone.utc)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------


def test_list_returns_empty_initially(client: TestClient, db: Session):
    _, jar = _signup(client)
    r = client.get(NOTIF_URL, cookies=jar)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 0
    assert body["data"] == []


def test_list_returns_descending_by_created_at(
    client: TestClient, db: Session
):
    user_id, jar = _signup(client)
    first = _seed(db, user_id=user_id, kind="system", payload={"n": 1})
    # Force the second row to have a strictly later created_at.
    import time as _time
    _time.sleep(0.01)
    second = _seed(db, user_id=user_id, kind="project_created", payload={"n": 2})

    r = client.get(NOTIF_URL, cookies=jar)
    assert r.status_code == 200
    data = r.json()["data"]
    assert [row["id"] for row in data] == [str(second.id), str(first.id)]


def test_list_unread_only_filters(client: TestClient, db: Session):
    user_id, jar = _signup(client)
    _seed(db, user_id=user_id, kind="system", read=True)
    unread = _seed(db, user_id=user_id, kind="project_created", read=False)

    r = client.get(NOTIF_URL, params={"unread_only": True}, cookies=jar)
    assert r.status_code == 200
    data = r.json()["data"]
    assert [row["id"] for row in data] == [str(unread.id)]


def test_unread_count(client: TestClient, db: Session):
    user_id, jar = _signup(client)
    _seed(db, user_id=user_id, kind="system", read=True)
    _seed(db, user_id=user_id, kind="project_created", read=False)
    _seed(db, user_id=user_id, kind="team_invite_accepted", read=False)

    r = client.get(f"{NOTIF_URL}/unread_count", cookies=jar)
    assert r.status_code == 200
    assert r.json() == {"count": 2}


def test_mark_read_transitions_read_at(client: TestClient, db: Session):
    user_id, jar = _signup(client)
    row = _seed(db, user_id=user_id, kind="system")
    assert row.read_at is None

    r = client.post(f"{NOTIF_URL}/{row.id}/read", cookies=jar)
    assert r.status_code == 200, r.text
    assert r.json()["read_at"] is not None

    db.expire_all()
    refreshed = db.get(Notification, row.id)
    assert refreshed is not None
    assert refreshed.read_at is not None


def test_mark_read_is_idempotent(client: TestClient, db: Session):
    user_id, jar = _signup(client)
    row = _seed(db, user_id=user_id, kind="system")

    r1 = client.post(f"{NOTIF_URL}/{row.id}/read", cookies=jar)
    first_read_at = r1.json()["read_at"]
    r2 = client.post(f"{NOTIF_URL}/{row.id}/read", cookies=jar)
    assert r2.status_code == 200
    # Second call must NOT bump read_at again (idempotent).
    assert r2.json()["read_at"] == first_read_at


def test_mark_read_404_for_other_users_notification(
    client: TestClient, db: Session
):
    owner_id, _ = _signup(client)
    other_id, other_jar = _signup(client)
    row = _seed(db, user_id=owner_id, kind="system")

    r = client.post(f"{NOTIF_URL}/{row.id}/read", cookies=other_jar)
    assert r.status_code == 404
    assert r.json()["detail"] == "notification_not_found"


def test_mark_read_404_for_unknown_id(client: TestClient, db: Session):
    _, jar = _signup(client)
    bogus = uuid.uuid4()
    r = client.post(f"{NOTIF_URL}/{bogus}/read", cookies=jar)
    assert r.status_code == 404


def test_read_all_only_affects_caller(client: TestClient, db: Session):
    me_id, my_jar = _signup(client)
    other_id, _ = _signup(client)

    mine_a = _seed(db, user_id=me_id, kind="system")
    mine_b = _seed(db, user_id=me_id, kind="project_created")
    theirs = _seed(db, user_id=other_id, kind="system")

    r = client.post(f"{NOTIF_URL}/read_all", cookies=my_jar)
    assert r.status_code == 200
    assert r.json() == {"affected": 2}

    db.expire_all()
    assert db.get(Notification, mine_a.id).read_at is not None  # type: ignore[union-attr]
    assert db.get(Notification, mine_b.id).read_at is not None  # type: ignore[union-attr]
    # Other user's notification stays unread.
    assert db.get(Notification, theirs.id).read_at is None  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


def test_preferences_returns_seven_defaults_when_no_rows(
    client: TestClient, db: Session
):
    _, jar = _signup(client)
    r = client.get(f"{NOTIF_URL}/preferences", cookies=jar)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 7
    by_kind = {row["event_type"]: row for row in rows}
    for kind in NotificationKind:
        assert kind.value in by_kind, f"missing kind {kind.value}"
        assert by_kind[kind.value]["in_app"] is DEFAULTS[kind]
        assert by_kind[kind.value]["push"] is False


def test_preferences_returns_kinds_in_enum_order(
    client: TestClient, db: Session
):
    _, jar = _signup(client)
    r = client.get(f"{NOTIF_URL}/preferences", cookies=jar)
    assert r.status_code == 200
    expected = [k.value for k in NotificationKind]
    assert [row["event_type"] for row in r.json()] == expected


def test_preferences_put_then_get_reflects_new_value(
    client: TestClient, db: Session
):
    _, jar = _signup(client)
    r = client.put(
        f"{NOTIF_URL}/preferences/team_invite_accepted",
        json={"in_app": False, "push": True},
        cookies=jar,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["in_app"] is False
    assert body["push"] is True
    assert body["event_type"] == "team_invite_accepted"

    r2 = client.get(f"{NOTIF_URL}/preferences", cookies=jar)
    assert r2.status_code == 200
    by_kind = {row["event_type"]: row for row in r2.json()}
    assert by_kind["team_invite_accepted"]["in_app"] is False
    assert by_kind["team_invite_accepted"]["push"] is True
    # Untouched kinds still report DEFAULTS.
    assert by_kind["system"]["in_app"] is DEFAULTS[NotificationKind.system]


def test_preferences_put_upserts_existing_row(
    client: TestClient, db: Session
):
    _, jar = _signup(client)
    # First PUT inserts.
    r1 = client.put(
        f"{NOTIF_URL}/preferences/system",
        json={"in_app": False, "push": False},
        cookies=jar,
    )
    assert r1.status_code == 200
    pref_id_first = r1.json()["id"]

    # Second PUT updates the same row (same id).
    r2 = client.put(
        f"{NOTIF_URL}/preferences/system",
        json={"in_app": True, "push": True},
        cookies=jar,
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == pref_id_first
    assert r2.json()["in_app"] is True


def test_preferences_put_unknown_event_type_422(
    client: TestClient, db: Session
):
    _, jar = _signup(client)
    r = client.put(
        f"{NOTIF_URL}/preferences/not_a_real_kind",
        json={"in_app": True, "push": False},
        cookies=jar,
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "unknown_event_type"


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


def test_list_requires_auth(client: TestClient):
    client.cookies.clear()
    r = client.get(NOTIF_URL)
    assert r.status_code == 401


def test_preferences_requires_auth(client: TestClient):
    client.cookies.clear()
    r = client.get(f"{NOTIF_URL}/preferences")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# notify() helper — direct unit-style coverage
# ---------------------------------------------------------------------------


def test_notify_inserts_with_default_in_app_true(
    client: TestClient, db: Session
):
    user_id, _ = _signup(client)
    row = notify(
        db,
        user_id=user_id,
        kind=NotificationKind.team_invite_accepted,
        payload={"team_name": "Foo"},
    )
    assert row is not None
    assert row.kind == "team_invite_accepted"
    assert row.payload == {"team_name": "Foo"}

    # Verify it is visible in the list endpoint.
    fresh = db.exec(
        select(Notification).where(Notification.user_id == user_id)
    ).all()
    assert len(fresh) == 1


def test_notify_skipped_when_in_app_false(client: TestClient, db: Session):
    user_id, _ = _signup(client)
    db.add(
        NotificationPreference(
            user_id=user_id,
            workflow_id=None,
            event_type="team_invite_accepted",
            in_app=False,
            push=False,
        )
    )
    db.commit()

    row = notify(
        db,
        user_id=user_id,
        kind=NotificationKind.team_invite_accepted,
        payload={"team_name": "Foo"},
    )
    assert row is None

    fresh = db.exec(
        select(Notification).where(Notification.user_id == user_id)
    ).all()
    assert fresh == []


def test_notify_redacts_sensitive_payload_keys(
    client: TestClient, db: Session
):
    user_id, _ = _signup(client)
    row = notify(
        db,
        user_id=user_id,
        kind=NotificationKind.system,
        payload={
            "token": "xxx",
            "email": "a@b.com",
            "team_name": "Foo",
            "user_password": "shh",
            "auth_secret": "shh",
        },
    )
    assert row is not None
    db.refresh(row)
    assert row.payload["token"] == "<redacted>"
    assert row.payload["email"] == "<redacted>"
    assert row.payload["user_password"] == "<redacted>"
    assert row.payload["auth_secret"] == "<redacted>"
    # Non-sensitive keys are passed through unchanged.
    assert row.payload["team_name"] == "Foo"


def test_notify_does_not_mutate_caller_payload(
    client: TestClient, db: Session
):
    user_id, _ = _signup(client)
    payload: dict[str, Any] = {"token": "xxx", "team_name": "Foo"}
    notify(
        db,
        user_id=user_id,
        kind=NotificationKind.system,
        payload=payload,
    )
    # Caller's dict stays intact (we redact a copy, not the original).
    assert payload == {"token": "xxx", "team_name": "Foo"}


def test_notify_records_source_columns(client: TestClient, db: Session):
    user_id, _ = _signup(client)
    team_id = uuid.uuid4()  # FK is SET NULL on missing team — record stays
    workflow_run_id = uuid.uuid4()
    row = notify(
        db,
        user_id=user_id,
        kind=NotificationKind.system,
        payload={"k": "v"},
        source_workflow_run_id=workflow_run_id,
    )
    assert row is not None
    db.refresh(row)
    assert row.source_workflow_run_id == workflow_run_id


def test_notify_preference_with_workflow_id_is_ignored_for_team_default(
    client: TestClient, db: Session
):
    """Override rows (workflow_id IS NOT NULL) must not affect the team-default
    in_app resolution path. Only the workflow_id IS NULL row counts."""
    user_id, _ = _signup(client)
    db.add(
        NotificationPreference(
            user_id=user_id,
            workflow_id=uuid.uuid4(),  # specific workflow override
            event_type="system",
            in_app=False,
            push=False,
        )
    )
    db.commit()

    # No team-default row exists, so DEFAULTS[system] = True applies.
    row = notify(
        db,
        user_id=user_id,
        kind=NotificationKind.system,
        payload={"k": "v"},
    )
    assert row is not None  # team-default resolved to True from DEFAULTS


# ---------------------------------------------------------------------------
# POST /notifications/test — system-admin seed trigger
# ---------------------------------------------------------------------------


def test_notifications_test_endpoint_creates_system_kind(
    client: TestClient, db: Session, superuser_cookies: httpx.Cookies
):
    """Non-superuser → 403; superuser → inserts a kind=system row that the
    bell can render via the standard list endpoint."""
    # Non-superuser: signup gets a UserRole.user account, which the
    # superuser dependency must reject.
    _, jar_user = _signup(client)
    r_user = client.post(
        f"{NOTIF_URL}/test",
        json={"message": "should not work"},
        cookies=jar_user,
    )
    assert r_user.status_code == 403, r_user.text

    # Superuser: must succeed and the row must be visible in their list.
    r_admin = client.post(
        f"{NOTIF_URL}/test",
        json={"message": "hello from admin"},
        cookies=superuser_cookies,
    )
    assert r_admin.status_code == 200, r_admin.text
    body = r_admin.json()
    assert body["kind"] == "system"
    assert body["payload"]["message"] == "hello from admin"

    # Visible via GET /notifications for the same admin.
    r_list = client.get(NOTIF_URL, cookies=superuser_cookies)
    assert r_list.status_code == 200
    kinds = {row["kind"] for row in r_list.json()["data"]}
    assert "system" in kinds
