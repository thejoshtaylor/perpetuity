"""Integration tests for the system admin settings router (S03 / T02).

Covers:
  - GET  /admin/settings              (envelope; ordered by key)
  - GET  /admin/settings/{key}        (200 / 404)
  - PUT  /admin/settings/{key}        (validators, shrink warnings, idempotency)
  - 401/403 gating on PUT

Multi-user flows follow MEM029 (detached cookie jar per user; clear the
shared TestClient jar between signups).
"""
import logging
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select

from app.core.config import settings
from app.models import SystemSetting, Team, User, WorkspaceVolume
from tests.utils.utils import random_email, random_lower_string

ADMIN_SETTINGS_URL = f"{settings.API_V1_STR}/admin/settings"
SIGNUP_URL = f"{settings.API_V1_STR}/auth/signup"

WORKSPACE_VOLUME_SIZE_GB = "workspace_volume_size_gb"
IDLE_TIMEOUT_SECONDS = "idle_timeout_seconds"


@pytest.fixture(autouse=True)
def _clean_system_settings_and_volumes(db: Session):
    """Each test starts with empty system_settings + workspace_volume tables.

    Tests in this module mutate global state (single-row settings + volume
    rows) so isolation matters. workspace_volume rows are cleaned to avoid
    leaking between tests; users/teams from other modules are untouched.
    """
    db.execute(delete(WorkspaceVolume))
    db.execute(delete(SystemSetting))
    db.commit()
    yield
    db.execute(delete(WorkspaceVolume))
    db.execute(delete(SystemSetting))
    db.commit()


def _signup(client: TestClient) -> tuple[str, httpx.Cookies]:
    """Sign up a fresh user and return (user_id, detached cookie jar)."""
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


def _seed_volume(
    db: Session, user_id: uuid.UUID, team_id: uuid.UUID, size_gb: int
) -> WorkspaceVolume:
    row = WorkspaceVolume(
        user_id=user_id,
        team_id=team_id,
        size_gb=size_gb,
        img_path=f"/var/lib/perpetuity/vols/{uuid.uuid4()}.img",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# GET /admin/settings
# ---------------------------------------------------------------------------


def test_list_settings_empty_returns_envelope(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """Happy path on empty table: `{data: [], count: 0}`."""
    r = client.get(ADMIN_SETTINGS_URL, cookies=superuser_cookies)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"data": [], "count": 0}


def test_list_settings_populated_returns_rows_ordered_by_key(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """Two PUTs land two rows; GET returns them ordered alphabetically by key."""
    # Only workspace_volume_size_gb is registered today; for ordering we need
    # at least one. Insert one via the API and assert envelope shape.
    r_put = client.put(
        f"{ADMIN_SETTINGS_URL}/{WORKSPACE_VOLUME_SIZE_GB}",
        json={"value": 4},
        cookies=superuser_cookies,
    )
    assert r_put.status_code == 200, r_put.text

    r = client.get(ADMIN_SETTINGS_URL, cookies=superuser_cookies)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    assert body["data"][0]["key"] == WORKSPACE_VOLUME_SIZE_GB
    assert body["data"][0]["value"] == 4
    assert "updated_at" in body["data"][0]


# ---------------------------------------------------------------------------
# GET /admin/settings/{key}
# ---------------------------------------------------------------------------


def test_get_setting_unknown_key_returns_404(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    r = client.get(
        f"{ADMIN_SETTINGS_URL}/{WORKSPACE_VOLUME_SIZE_GB}",
        cookies=superuser_cookies,
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "setting_not_found"


def test_get_setting_after_put_returns_value(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    client.put(
        f"{ADMIN_SETTINGS_URL}/{WORKSPACE_VOLUME_SIZE_GB}",
        json={"value": 4},
        cookies=superuser_cookies,
    )
    r = client.get(
        f"{ADMIN_SETTINGS_URL}/{WORKSPACE_VOLUME_SIZE_GB}",
        cookies=superuser_cookies,
    )
    assert r.status_code == 200, r.text
    assert r.json()["key"] == WORKSPACE_VOLUME_SIZE_GB
    assert r.json()["value"] == 4


# ---------------------------------------------------------------------------
# PUT /admin/settings/{key} — happy path
# ---------------------------------------------------------------------------


def test_put_workspace_volume_size_gb_returns_200_with_empty_warnings(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """First PUT with no existing volumes: empty warnings."""
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{WORKSPACE_VOLUME_SIZE_GB}",
        json={"value": 4},
        cookies=superuser_cookies,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["key"] == WORKSPACE_VOLUME_SIZE_GB
    assert body["value"] == 4
    assert body["warnings"] == []


def test_put_workspace_volume_size_gb_idempotent_logs_previous_value_present(
    client: TestClient,
    superuser_cookies: httpx.Cookies,
    caplog,
) -> None:
    """Two PUTs with the same value both return 200; second logs previous_value_present=true."""
    r1 = client.put(
        f"{ADMIN_SETTINGS_URL}/{WORKSPACE_VOLUME_SIZE_GB}",
        json={"value": 4},
        cookies=superuser_cookies,
    )
    assert r1.status_code == 200

    with caplog.at_level(logging.INFO, logger="app.api.routes.admin"):
        r2 = client.put(
            f"{ADMIN_SETTINGS_URL}/{WORKSPACE_VOLUME_SIZE_GB}",
            json={"value": 4},
            cookies=superuser_cookies,
        )
    assert r2.status_code == 200

    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        "system_setting_updated" in m
        and f"key={WORKSPACE_VOLUME_SIZE_GB}" in m
        and "previous_value_present=true" in m
        for m in msgs
    ), msgs


def test_put_first_time_logs_previous_value_present_false(
    client: TestClient,
    superuser_cookies: httpx.Cookies,
    caplog,
) -> None:
    """First-time PUT logs previous_value_present=false."""
    with caplog.at_level(logging.INFO, logger="app.api.routes.admin"):
        r = client.put(
            f"{ADMIN_SETTINGS_URL}/{WORKSPACE_VOLUME_SIZE_GB}",
            json={"value": 4},
            cookies=superuser_cookies,
        )
    assert r.status_code == 200, r.text
    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        "system_setting_updated" in m
        and "previous_value_present=false" in m
        for m in msgs
    ), msgs


# ---------------------------------------------------------------------------
# PUT /admin/settings/{key} — partial-apply shrink (D015)
# ---------------------------------------------------------------------------


def test_put_shrink_emits_warnings_for_existing_volumes(
    client: TestClient,
    superuser_cookies: httpx.Cookies,
    caplog,
    db: Session,
) -> None:
    """Two volumes (size 4 and 2); PUT to 1 → warnings list both, DB unchanged."""
    # Sign up two users, each with a personal team — the signup helper creates
    # the team automatically. We seed workspace_volume rows pointing at those.
    user_a_id, _cookies_a = _signup(client)
    user_b_id, _cookies_b = _signup(client)

    # Resolve their personal team_ids from the team_member join.
    user_a = db.exec(
        select(User).where(User.id == uuid.UUID(user_a_id))
    ).one()
    user_b = db.exec(
        select(User).where(User.id == uuid.UUID(user_b_id))
    ).one()
    team_a_id = db.exec(
        select(Team.id).where(Team.slug == user_a.email)
    ).first()
    team_b_id = db.exec(
        select(Team.id).where(Team.slug == user_b.email)
    ).first()
    # If slug naming differs, just pick any team they belong to via the join.
    if team_a_id is None:
        from app.models import TeamMember

        team_a_id = db.exec(
            select(TeamMember.team_id).where(
                TeamMember.user_id == uuid.UUID(user_a_id)
            )
        ).first()
        team_b_id = db.exec(
            select(TeamMember.team_id).where(
                TeamMember.user_id == uuid.UUID(user_b_id)
            )
        ).first()
    assert team_a_id is not None and team_b_id is not None

    vol_a = _seed_volume(
        db, uuid.UUID(user_a_id), team_a_id, size_gb=4
    )
    vol_b = _seed_volume(
        db, uuid.UUID(user_b_id), team_b_id, size_gb=2
    )

    with caplog.at_level(logging.INFO, logger="app.api.routes.admin"):
        r = client.put(
            f"{ADMIN_SETTINGS_URL}/{WORKSPACE_VOLUME_SIZE_GB}",
            json={"value": 1},
            cookies=superuser_cookies,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["value"] == 1
    warnings = body["warnings"]
    assert len(warnings) == 2

    # Both warnings present, ordered by created_at; vol_a was created first.
    by_user = {w["user_id"]: w for w in warnings}
    assert user_a_id in by_user
    assert user_b_id in by_user
    assert by_user[user_a_id]["size_gb"] == 4
    assert by_user[user_b_id]["size_gb"] == 2
    assert by_user[user_a_id]["usage_bytes"] is None
    assert by_user[user_b_id]["usage_bytes"] is None

    # DB-side rows unchanged.
    db.refresh(vol_a)
    db.refresh(vol_b)
    assert vol_a.size_gb == 4
    assert vol_b.size_gb == 2

    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        "system_setting_shrink_warnings_emitted" in m
        and f"key={WORKSPACE_VOLUME_SIZE_GB}" in m
        and "affected=2" in m
        for m in msgs
    ), msgs


def test_put_no_shrink_does_not_log_shrink_warnings(
    client: TestClient,
    superuser_cookies: httpx.Cookies,
    caplog,
) -> None:
    """PUT without affected volumes does NOT emit the shrink-warnings log line."""
    with caplog.at_level(logging.INFO, logger="app.api.routes.admin"):
        r = client.put(
            f"{ADMIN_SETTINGS_URL}/{WORKSPACE_VOLUME_SIZE_GB}",
            json={"value": 8},
            cookies=superuser_cookies,
        )
    assert r.status_code == 200
    msgs = [rec.getMessage() for rec in caplog.records]
    assert not any(
        "system_setting_shrink_warnings_emitted" in m for m in msgs
    ), msgs


# ---------------------------------------------------------------------------
# PUT /admin/settings/{key} — validation
# ---------------------------------------------------------------------------


def test_put_workspace_volume_size_gb_non_int_returns_422(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{WORKSPACE_VOLUME_SIZE_GB}",
        json={"value": "four"},
        cookies=superuser_cookies,
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["detail"] == "invalid_value_for_key"
    assert detail["key"] == WORKSPACE_VOLUME_SIZE_GB
    assert "must be int in 1..256" in detail["reason"]


def test_put_workspace_volume_size_gb_out_of_range_returns_422(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{WORKSPACE_VOLUME_SIZE_GB}",
        json={"value": 300},
        cookies=superuser_cookies,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["detail"] == "invalid_value_for_key"


def test_put_workspace_volume_size_gb_zero_returns_422(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """Boundary: 0 is below the 1..256 range."""
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{WORKSPACE_VOLUME_SIZE_GB}",
        json={"value": 0},
        cookies=superuser_cookies,
    )
    assert r.status_code == 422


def test_put_unknown_key_returns_422_unknown_setting_key(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/never_registered_key",
        json={"value": 1},
        cookies=superuser_cookies,
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["detail"] == "unknown_setting_key"
    assert detail["key"] == "never_registered_key"


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


def test_put_as_normal_user_returns_403(client: TestClient) -> None:
    _u_id, cookies_u = _signup(client)
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{WORKSPACE_VOLUME_SIZE_GB}",
        json={"value": 4},
        cookies=cookies_u,
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "The user doesn't have enough privileges"


def test_put_unauthenticated_returns_401(client: TestClient) -> None:
    client.cookies.clear()
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{WORKSPACE_VOLUME_SIZE_GB}",
        json={"value": 4},
    )
    assert r.status_code == 401


def test_get_list_unauthenticated_returns_401(client: TestClient) -> None:
    client.cookies.clear()
    r = client.get(ADMIN_SETTINGS_URL)
    assert r.status_code == 401


def test_get_list_as_normal_user_returns_403(client: TestClient) -> None:
    _u_id, cookies_u = _signup(client)
    r = client.get(ADMIN_SETTINGS_URL, cookies=cookies_u)
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# PUT /admin/settings/{key} — idle_timeout_seconds (S04 / T02)
# ---------------------------------------------------------------------------


def test_put_idle_timeout_seconds_returns_200_no_warnings(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """Happy path: PUT idle_timeout_seconds=120 returns 200 with empty warnings.

    Unlike workspace_volume_size_gb, this key has NO partial-apply
    warnings — the new value just biases the next reaper tick. Empty
    `warnings` is the contract.
    """
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{IDLE_TIMEOUT_SECONDS}",
        json={"value": 120},
        cookies=superuser_cookies,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["key"] == IDLE_TIMEOUT_SECONDS
    assert body["value"] == 120
    assert body["warnings"] == []


def test_put_idle_timeout_seconds_idempotent_logs_previous_value_present(
    client: TestClient,
    superuser_cookies: httpx.Cookies,
    caplog,
) -> None:
    """Two PUTs with the same value: second logs previous_value_present=true."""
    r1 = client.put(
        f"{ADMIN_SETTINGS_URL}/{IDLE_TIMEOUT_SECONDS}",
        json={"value": 120},
        cookies=superuser_cookies,
    )
    assert r1.status_code == 200

    with caplog.at_level(logging.INFO, logger="app.api.routes.admin"):
        r2 = client.put(
            f"{ADMIN_SETTINGS_URL}/{IDLE_TIMEOUT_SECONDS}",
            json={"value": 120},
            cookies=superuser_cookies,
        )
    assert r2.status_code == 200

    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        "system_setting_updated" in m
        and f"key={IDLE_TIMEOUT_SECONDS}" in m
        and "previous_value_present=true" in m
        for m in msgs
    ), msgs


def test_put_idle_timeout_seconds_first_time_logs_previous_value_present_false(
    client: TestClient,
    superuser_cookies: httpx.Cookies,
    caplog,
) -> None:
    """First-time PUT for idle_timeout_seconds logs previous_value_present=false."""
    with caplog.at_level(logging.INFO, logger="app.api.routes.admin"):
        r = client.put(
            f"{ADMIN_SETTINGS_URL}/{IDLE_TIMEOUT_SECONDS}",
            json={"value": 600},
            cookies=superuser_cookies,
        )
    assert r.status_code == 200, r.text
    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        "system_setting_updated" in m
        and f"key={IDLE_TIMEOUT_SECONDS}" in m
        and "previous_value_present=false" in m
        for m in msgs
    ), msgs


def test_put_idle_timeout_seconds_does_not_log_shrink_warnings(
    client: TestClient,
    superuser_cookies: httpx.Cookies,
    caplog,
) -> None:
    """idle_timeout_seconds NEVER emits the shrink-warnings log line.

    Only workspace_volume_size_gb has per-row state to reconcile; the
    reaper key is stateless (the next tick reads the new value and
    moves on).
    """
    with caplog.at_level(logging.INFO, logger="app.api.routes.admin"):
        r = client.put(
            f"{ADMIN_SETTINGS_URL}/{IDLE_TIMEOUT_SECONDS}",
            json={"value": 30},
            cookies=superuser_cookies,
        )
    assert r.status_code == 200
    msgs = [rec.getMessage() for rec in caplog.records]
    assert not any(
        "system_setting_shrink_warnings_emitted" in m for m in msgs
    ), msgs


def test_put_idle_timeout_seconds_non_int_returns_422(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{IDLE_TIMEOUT_SECONDS}",
        json={"value": "five-minutes"},
        cookies=superuser_cookies,
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["detail"] == "invalid_value_for_key"
    assert detail["key"] == IDLE_TIMEOUT_SECONDS
    assert "must be int in 1..86400" in detail["reason"]


def test_put_idle_timeout_seconds_bool_returns_422(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """JSON `true` MUST be rejected — bool is a subclass of int in Python.

    Same pattern as the workspace_volume_size_gb validator.
    """
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{IDLE_TIMEOUT_SECONDS}",
        json={"value": True},
        cookies=superuser_cookies,
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["detail"] == "invalid_value_for_key"
    assert detail["key"] == IDLE_TIMEOUT_SECONDS


def test_put_idle_timeout_seconds_zero_returns_422(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """Boundary: 0 is below the 1..86400 range — would disable the reaper."""
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{IDLE_TIMEOUT_SECONDS}",
        json={"value": 0},
        cookies=superuser_cookies,
    )
    assert r.status_code == 422


def test_put_idle_timeout_seconds_too_large_returns_422(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """Boundary: 86401 exceeds the 24h cap."""
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{IDLE_TIMEOUT_SECONDS}",
        json={"value": 86401},
        cookies=superuser_cookies,
    )
    assert r.status_code == 422


def test_put_idle_timeout_seconds_max_allowed_returns_200(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """Boundary: 86400 (exact max, 24h) is accepted."""
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{IDLE_TIMEOUT_SECONDS}",
        json={"value": 86400},
        cookies=superuser_cookies,
    )
    assert r.status_code == 200, r.text
    assert r.json()["value"] == 86400
