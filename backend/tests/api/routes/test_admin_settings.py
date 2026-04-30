"""Integration tests for the system admin settings router (S03 / T02).

Covers:
  - GET  /admin/settings              (envelope; ordered by key)
  - GET  /admin/settings/{key}        (200 / 404)
  - PUT  /admin/settings/{key}        (validators, shrink warnings, idempotency)
  - 401/403 gating on PUT

Multi-user flows follow MEM029 (detached cookie jar per user; clear the
shared TestClient jar between signups).
"""
import json
import logging
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, delete, select

from app.core.config import settings
from app.models import SystemSetting, Team, User, WorkspaceVolume
from tests.utils.utils import random_email, random_lower_string

ADMIN_SETTINGS_URL = f"{settings.API_V1_STR}/admin/settings"
SIGNUP_URL = f"{settings.API_V1_STR}/auth/signup"

WORKSPACE_VOLUME_SIZE_GB = "workspace_volume_size_gb"
IDLE_TIMEOUT_SECONDS = "idle_timeout_seconds"
MIRROR_IDLE_TIMEOUT_SECONDS = "mirror_idle_timeout_seconds"


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
    """Empty DB still returns one row per registered key, all with has_value=False.

    The list endpoint merges the _VALIDATORS registry with DB rows so the
    frontend renders the full settings panel on a fresh deployment instead of
    showing the "No system settings registered" empty state.
    """
    from app.api.routes.admin import _VALIDATORS

    r = client.get(ADMIN_SETTINGS_URL, cookies=superuser_cookies)
    assert r.status_code == 200, r.text
    body = r.json()
    expected_count = len(_VALIDATORS)
    assert body["count"] == expected_count
    assert len(body["data"]) == expected_count
    # All rows have has_value=False (nothing has been PUT yet)
    assert all(row["has_value"] is False for row in body["data"])
    # Rows are sorted by key
    keys = [row["key"] for row in body["data"]]
    assert keys == sorted(keys)
    # Every registered key is present
    assert set(keys) == set(_VALIDATORS.keys())


def test_list_settings_populated_returns_rows_ordered_by_key(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """PUT one setting; GET returns all registered keys ordered alphabetically.

    The PUT'd key has its value and has_value=True; all other registered keys
    appear with has_value=False (not yet set).
    """
    from app.api.routes.admin import _VALIDATORS

    r_put = client.put(
        f"{ADMIN_SETTINGS_URL}/{WORKSPACE_VOLUME_SIZE_GB}",
        json={"value": 4},
        cookies=superuser_cookies,
    )
    assert r_put.status_code == 200, r_put.text

    r = client.get(ADMIN_SETTINGS_URL, cookies=superuser_cookies)
    assert r.status_code == 200, r.text
    body = r.json()
    # All registered keys are present regardless of which were PUT
    assert body["count"] == len(_VALIDATORS)
    keys = [row["key"] for row in body["data"]]
    assert keys == sorted(keys)
    # The PUT'd key has its value and has_value=True
    vol_row = next(row for row in body["data"] if row["key"] == WORKSPACE_VOLUME_SIZE_GB)
    assert vol_row["value"] == 4
    assert vol_row["has_value"] is True
    assert "updated_at" in vol_row
    # All other keys have has_value=False
    unset = [row for row in body["data"] if row["key"] != WORKSPACE_VOLUME_SIZE_GB]
    assert all(row["has_value"] is False for row in unset)


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


# ---------------------------------------------------------------------------
# PUT /admin/settings/{key} — mirror_idle_timeout_seconds (S03 / T01)
#
# Per-team mirror reaper window. Same int-in-range shape as
# idle_timeout_seconds, but with a stricter floor (60s) so a misconfigured
# value can't tear down the mirror container on every reaper tick.
# ---------------------------------------------------------------------------


def test_put_mirror_idle_timeout_seconds_default_returns_200(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """Happy path: PUT mirror_idle_timeout_seconds=1800 returns 200, no warnings."""
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{MIRROR_IDLE_TIMEOUT_SECONDS}",
        json={"value": 1800},
        cookies=superuser_cookies,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["key"] == MIRROR_IDLE_TIMEOUT_SECONDS
    assert body["value"] == 1800
    assert body["warnings"] == []


def test_put_mirror_idle_timeout_seconds_get_after_put(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """GET after PUT round-trips with sensitive=False, has_value=True."""
    client.put(
        f"{ADMIN_SETTINGS_URL}/{MIRROR_IDLE_TIMEOUT_SECONDS}",
        json={"value": 1800},
        cookies=superuser_cookies,
    )
    r = client.get(
        f"{ADMIN_SETTINGS_URL}/{MIRROR_IDLE_TIMEOUT_SECONDS}",
        cookies=superuser_cookies,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["key"] == MIRROR_IDLE_TIMEOUT_SECONDS
    assert body["value"] == 1800
    assert body["sensitive"] is False
    assert body["has_value"] is True


def test_put_mirror_idle_timeout_seconds_below_floor_returns_422(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """Boundary: 59 is below the 60s floor — would weaponize the reaper."""
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{MIRROR_IDLE_TIMEOUT_SECONDS}",
        json={"value": 59},
        cookies=superuser_cookies,
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["detail"] == "invalid_value_for_key"
    assert detail["key"] == MIRROR_IDLE_TIMEOUT_SECONDS
    assert "must be int in 60..86400" in detail["reason"]


def test_put_mirror_idle_timeout_seconds_min_allowed_returns_200(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """Boundary: 60 (exact floor) is accepted."""
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{MIRROR_IDLE_TIMEOUT_SECONDS}",
        json={"value": 60},
        cookies=superuser_cookies,
    )
    assert r.status_code == 200, r.text
    assert r.json()["value"] == 60


def test_put_mirror_idle_timeout_seconds_above_cap_returns_422(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """Boundary: 86401 exceeds the 24h cap."""
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{MIRROR_IDLE_TIMEOUT_SECONDS}",
        json={"value": 86401},
        cookies=superuser_cookies,
    )
    assert r.status_code == 422


def test_put_mirror_idle_timeout_seconds_max_allowed_returns_200(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """Boundary: 86400 (exact max, 24h) is accepted."""
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{MIRROR_IDLE_TIMEOUT_SECONDS}",
        json={"value": 86400},
        cookies=superuser_cookies,
    )
    assert r.status_code == 200, r.text
    assert r.json()["value"] == 86400


def test_put_mirror_idle_timeout_seconds_bool_returns_422(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """JSON `true` MUST be rejected — bool is a subclass of int."""
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{MIRROR_IDLE_TIMEOUT_SECONDS}",
        json={"value": True},
        cookies=superuser_cookies,
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["detail"] == "invalid_value_for_key"
    assert detail["key"] == MIRROR_IDLE_TIMEOUT_SECONDS


def test_put_mirror_idle_timeout_seconds_str_returns_422(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """Non-int string rejected with structured 422."""
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{MIRROR_IDLE_TIMEOUT_SECONDS}",
        json={"value": "thirty-minutes"},
        cookies=superuser_cookies,
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["detail"] == "invalid_value_for_key"
    assert detail["key"] == MIRROR_IDLE_TIMEOUT_SECONDS
    assert "must be int in 60..86400" in detail["reason"]


def test_put_mirror_idle_timeout_seconds_float_returns_422(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """Floats rejected — int is the only accepted shape."""
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{MIRROR_IDLE_TIMEOUT_SECONDS}",
        json={"value": 1800.5},
        cookies=superuser_cookies,
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["detail"] == "invalid_value_for_key"
    assert detail["key"] == MIRROR_IDLE_TIMEOUT_SECONDS


def test_put_mirror_idle_timeout_seconds_zero_returns_422(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """Boundary: 0 is below the 60..86400 range — would disable the reaper."""
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{MIRROR_IDLE_TIMEOUT_SECONDS}",
        json={"value": 0},
        cookies=superuser_cookies,
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# M004/S01: sensitive keys (github_app_*) — encrypted PUT, redacted GET,
# generate endpoint, decrypt-failure 503.
# ---------------------------------------------------------------------------

GITHUB_APP_ID = "github_app_id"
GITHUB_APP_CLIENT_ID = "github_app_client_id"
GITHUB_APP_SLUG = "github_app_slug"
GITHUB_APP_PRIVATE_KEY = "github_app_private_key"
GITHUB_APP_WEBHOOK_SECRET = "github_app_webhook_secret"


@pytest.fixture(autouse=True)
def _set_encryption_key(monkeypatch):
    """Ensure SYSTEM_SETTINGS_ENCRYPTION_KEY is present for sensitive paths.

    Encryption is module-cached (`@functools.cache`) — clearing the cache so
    each test sees a fresh load is what makes the env-var swap take effect.
    """
    # A real Fernet key (44-char url-safe base64 of 32 bytes). Generated
    # once and pinned here so the tests are deterministic.
    monkeypatch.setenv(
        "SYSTEM_SETTINGS_ENCRYPTION_KEY",
        "q14YMz9s4jrbfD29GvcRfe_4krg82w6_mPWUu_y3LTo=",
    )
    from app.core import encryption as _enc

    _enc._load_key.cache_clear()
    yield
    _enc._load_key.cache_clear()


def _valid_pem() -> str:
    """Return a structurally-valid PEM body. Length within validator bounds.

    The bytes do not need to be a real RSA key — the validator is structural
    (begins/ends/length); semantic validation is deferred to S02's first
    JWT-sign call.
    """
    body = "A" * 200
    return f"-----BEGIN RSA PRIVATE KEY-----\n{body}\n-----END RSA PRIVATE KEY-----"


# --- PUT sensitive: github_app_private_key ---------------------------------


def test_put_github_app_private_key_redacts_value_in_response(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """PUT a sensitive PEM: response shape carries no plaintext."""
    pem = _valid_pem()
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{GITHUB_APP_PRIVATE_KEY}",
        json={"value": pem},
        cookies=superuser_cookies,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["key"] == GITHUB_APP_PRIVATE_KEY
    # PutResponse for sensitive keys exposes value=None — plaintext does
    # NOT cross the API boundary on PUT.
    assert body["value"] is None
    assert body["warnings"] == []


def test_get_github_app_private_key_after_put_returns_redacted(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """After a sensitive PUT, GET shows has_value:true and value:null."""
    client.put(
        f"{ADMIN_SETTINGS_URL}/{GITHUB_APP_PRIVATE_KEY}",
        json={"value": _valid_pem()},
        cookies=superuser_cookies,
    )
    r = client.get(
        f"{ADMIN_SETTINGS_URL}/{GITHUB_APP_PRIVATE_KEY}",
        cookies=superuser_cookies,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["key"] == GITHUB_APP_PRIVATE_KEY
    assert body["sensitive"] is True
    assert body["has_value"] is True
    assert body["value"] is None


def test_list_settings_redacts_sensitive_values(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """The list surface NEVER returns plaintext for sensitive rows."""
    client.put(
        f"{ADMIN_SETTINGS_URL}/{GITHUB_APP_PRIVATE_KEY}",
        json={"value": _valid_pem()},
        cookies=superuser_cookies,
    )
    client.put(
        f"{ADMIN_SETTINGS_URL}/{WORKSPACE_VOLUME_SIZE_GB}",
        json={"value": 4},
        cookies=superuser_cookies,
    )
    r = client.get(ADMIN_SETTINGS_URL, cookies=superuser_cookies)
    assert r.status_code == 200, r.text
    by_key = {row["key"]: row for row in r.json()["data"]}
    assert by_key[GITHUB_APP_PRIVATE_KEY]["value"] is None
    assert by_key[GITHUB_APP_PRIVATE_KEY]["has_value"] is True
    assert by_key[GITHUB_APP_PRIVATE_KEY]["sensitive"] is True
    # Non-sensitive row is unchanged — still carries its value.
    assert by_key[WORKSPACE_VOLUME_SIZE_GB]["value"] == 4
    assert by_key[WORKSPACE_VOLUME_SIZE_GB]["sensitive"] is False


def test_put_github_app_private_key_logs_sensitive_true_no_plaintext(
    client: TestClient, superuser_cookies: httpx.Cookies, caplog
) -> None:
    """Updated log line carries sensitive=true and never the plaintext."""
    pem = _valid_pem()
    with caplog.at_level(logging.INFO, logger="app.api.routes.admin"):
        r = client.put(
            f"{ADMIN_SETTINGS_URL}/{GITHUB_APP_PRIVATE_KEY}",
            json={"value": pem},
            cookies=superuser_cookies,
        )
    assert r.status_code == 200
    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        "system_setting_updated" in m
        and f"key={GITHUB_APP_PRIVATE_KEY}" in m
        and "sensitive=true" in m
        for m in msgs
    ), msgs
    # Plaintext MUST NOT appear in any log line.
    for m in msgs:
        assert pem not in m


def test_put_github_app_private_key_invalid_pem_returns_422_no_value(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """Malformed PEM rejected; reason text never contains the value."""
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{GITHUB_APP_PRIVATE_KEY}",
        json={"value": "not a pem"},
        cookies=superuser_cookies,
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["detail"] == "invalid_value_for_key"
    assert detail["key"] == GITHUB_APP_PRIVATE_KEY
    assert "not a pem" not in detail["reason"]


def test_put_github_app_private_key_too_short_returns_422(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """Below the structural-length floor."""
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{GITHUB_APP_PRIVATE_KEY}",
        json={"value": "-----BEGIN-----END"},
        cookies=superuser_cookies,
    )
    assert r.status_code == 422


# --- PUT non-sensitive: github_app_id, github_app_client_id ---------------


def test_put_github_app_id_stores_in_jsonb(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """github_app_id is non-sensitive and round-trips through GET."""
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{GITHUB_APP_ID}",
        json={"value": 12345},
        cookies=superuser_cookies,
    )
    assert r.status_code == 200, r.text
    assert r.json()["value"] == 12345
    g = client.get(
        f"{ADMIN_SETTINGS_URL}/{GITHUB_APP_ID}",
        cookies=superuser_cookies,
    )
    assert g.json()["value"] == 12345
    assert g.json()["sensitive"] is False


def test_put_github_app_id_bool_returns_422(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """JSON true must be rejected (bool is subclass of int)."""
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{GITHUB_APP_ID}",
        json={"value": True},
        cookies=superuser_cookies,
    )
    assert r.status_code == 422


def test_put_github_app_client_id_stores_string(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{GITHUB_APP_CLIENT_ID}",
        json={"value": "Iv1.abc123"},
        cookies=superuser_cookies,
    )
    assert r.status_code == 200, r.text
    assert r.json()["value"] == "Iv1.abc123"


def test_put_github_app_client_id_empty_returns_422(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{GITHUB_APP_CLIENT_ID}",
        json={"value": ""},
        cookies=superuser_cookies,
    )
    assert r.status_code == 422


def test_put_github_app_slug_stores_string(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{GITHUB_APP_SLUG}",
        json={"value": "my-company-app"},
        cookies=superuser_cookies,
    )
    assert r.status_code == 200, r.text
    assert r.json()["value"] == "my-company-app"
    g = client.get(
        f"{ADMIN_SETTINGS_URL}/{GITHUB_APP_SLUG}",
        cookies=superuser_cookies,
    )
    assert g.json()["value"] == "my-company-app"
    assert g.json()["sensitive"] is False


def test_put_github_app_slug_empty_returns_422(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/{GITHUB_APP_SLUG}",
        json={"value": ""},
        cookies=superuser_cookies,
    )
    assert r.status_code == 422


# --- POST /generate -------------------------------------------------------


def test_generate_webhook_secret_returns_value_once(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """The plaintext webhook secret crosses the API boundary exactly once.

    First call: response carries value plaintext + has_value/generated.
    Subsequent GET: value=null, has_value=true.
    """
    r = client.post(
        f"{ADMIN_SETTINGS_URL}/{GITHUB_APP_WEBHOOK_SECRET}/generate",
        cookies=superuser_cookies,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["key"] == GITHUB_APP_WEBHOOK_SECRET
    assert isinstance(body["value"], str)
    assert len(body["value"]) >= 32  # token_urlsafe(32) ≈ 43 chars
    plaintext = body["value"]
    assert body["has_value"] is True
    assert body["generated"] is True

    # Subsequent GET returns the redacted shape.
    g = client.get(
        f"{ADMIN_SETTINGS_URL}/{GITHUB_APP_WEBHOOK_SECRET}",
        cookies=superuser_cookies,
    )
    assert g.status_code == 200
    assert g.json()["value"] is None
    assert g.json()["has_value"] is True
    assert g.json()["sensitive"] is True
    # Confirm the plaintext doesn't leak into list either.
    lst = client.get(ADMIN_SETTINGS_URL, cookies=superuser_cookies)
    by_key = {row["key"]: row for row in lst.json()["data"]}
    assert by_key[GITHUB_APP_WEBHOOK_SECRET]["value"] is None
    # And separately: regenerating yields a NEW plaintext (destructive on
    # re-call — D025).
    r2 = client.post(
        f"{ADMIN_SETTINGS_URL}/{GITHUB_APP_WEBHOOK_SECRET}/generate",
        cookies=superuser_cookies,
    )
    assert r2.status_code == 200
    assert r2.json()["value"] != plaintext


def test_generate_emits_system_setting_generated_log(
    client: TestClient, superuser_cookies: httpx.Cookies, caplog
) -> None:
    with caplog.at_level(logging.INFO, logger="app.api.routes.admin"):
        r = client.post(
            f"{ADMIN_SETTINGS_URL}/{GITHUB_APP_WEBHOOK_SECRET}/generate",
            cookies=superuser_cookies,
        )
    assert r.status_code == 200
    plaintext = r.json()["value"]
    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        "system_setting_generated" in m
        and f"key={GITHUB_APP_WEBHOOK_SECRET}" in m
        for m in msgs
    ), msgs
    # Plaintext MUST NOT appear in any log line.
    for m in msgs:
        assert plaintext not in m


def test_generate_unregistered_key_returns_422(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    r = client.post(
        f"{ADMIN_SETTINGS_URL}/never_registered_key/generate",
        cookies=superuser_cookies,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["detail"] == "unknown_setting_key"


def test_generate_key_without_generator_returns_422(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """github_app_private_key has no server-side seed — operator pastes it."""
    r = client.post(
        f"{ADMIN_SETTINGS_URL}/{GITHUB_APP_PRIVATE_KEY}/generate",
        cookies=superuser_cookies,
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["detail"] == "no_generator_for_key"
    assert detail["key"] == GITHUB_APP_PRIVATE_KEY


def test_generate_unauthenticated_returns_401(client: TestClient) -> None:
    client.cookies.clear()
    r = client.post(
        f"{ADMIN_SETTINGS_URL}/{GITHUB_APP_WEBHOOK_SECRET}/generate",
    )
    assert r.status_code == 401


def test_generate_as_normal_user_returns_403(client: TestClient) -> None:
    _u_id, cookies_u = _signup(client)
    r = client.post(
        f"{ADMIN_SETTINGS_URL}/{GITHUB_APP_WEBHOOK_SECRET}/generate",
        cookies=cookies_u,
    )
    assert r.status_code == 403


# --- Decrypt-failure 503 handler ------------------------------------------


def test_corrupted_ciphertext_decrypts_to_decrypt_error(
    client: TestClient,
    superuser_cookies: httpx.Cookies,
    db: Session,
) -> None:
    """Corrupted Fernet ciphertext raises SystemSettingDecryptError.

    The decrypt helper is the single source of truth for sensitive-key
    failures; the global handler in main.py translates the exception into
    503 + the structured log. This test confirms the helper-side contract;
    `test_decrypt_error_handler_returns_503_with_key_and_log` confirms the
    handler-side translation.
    """
    from app.core.encryption import (
        SystemSettingDecryptError,
        decrypt_setting,
    )

    # Seed a valid sensitive row and then corrupt the ciphertext at the DB.
    client.put(
        f"{ADMIN_SETTINGS_URL}/{GITHUB_APP_PRIVATE_KEY}",
        json={"value": _valid_pem()},
        cookies=superuser_cookies,
    )
    db.execute(
        text(
            "UPDATE system_settings SET value_encrypted = :ct WHERE key = :k"
        ),
        {"ct": b"not-a-valid-fernet-token", "k": GITHUB_APP_PRIVATE_KEY},
    )
    db.commit()

    row = db.exec(
        select(SystemSetting).where(
            SystemSetting.key == GITHUB_APP_PRIVATE_KEY
        )
    ).one()
    with pytest.raises(SystemSettingDecryptError):
        decrypt_setting(row.value_encrypted)


def test_decrypt_error_handler_returns_503_with_key_and_log(
    caplog,
) -> None:
    """The global handler turns SystemSettingDecryptError into 503 + log.

    Drives the handler directly with a synthetic request so the test does
    not depend on a real decrypt site (the only such site lands in S02).
    Confirms the response shape (`detail`, `key`) and the ERROR log line
    `system_settings_decrypt_failed key=<name>` — the single fan-in for
    every decrypt failure.
    """
    import asyncio

    from app.core.encryption import SystemSettingDecryptError
    from app.main import _system_settings_decrypt_failed_handler

    exc = SystemSettingDecryptError(key="github_app_private_key")
    with caplog.at_level(logging.ERROR, logger="app.main"):
        response = asyncio.run(
            _system_settings_decrypt_failed_handler(None, exc)  # type: ignore[arg-type]
        )

    assert response.status_code == 503
    body = json.loads(response.body)
    assert body["detail"] == "system_settings_decrypt_failed"
    assert body["key"] == "github_app_private_key"

    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        "system_settings_decrypt_failed" in m
        and "key=github_app_private_key" in m
        for m in msgs
    ), msgs


# --- Boot-time encryption-key validation ----------------------------------


def test_encryption_key_unset_at_decrypt_call_raises_runtime_error(
    monkeypatch,
) -> None:
    """Bad/missing SYSTEM_SETTINGS_ENCRYPTION_KEY surfaces at first use.

    The loader is `@functools.cache`-d and lazy: importing the module
    succeeds even with no key registered (preserving dev ergonomics), but
    the first encrypt/decrypt call fails loudly with RuntimeError naming
    the env var. This is the contract documented in T01.
    """
    from app.core import encryption as _enc

    monkeypatch.delenv("SYSTEM_SETTINGS_ENCRYPTION_KEY", raising=False)
    _enc._load_key.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="SYSTEM_SETTINGS_ENCRYPTION_KEY"):
            _enc.encrypt_setting("anything")
    finally:
        _enc._load_key.cache_clear()


# --- Voice settings registry ----------------------------------------------


def test_put_grok_stt_api_key_is_sensitive_and_redacted(
    client: TestClient,
    superuser_cookies: httpx.Cookies,
    db: Session,
) -> None:
    value = "xai-test-secret"
    r = client.put(
        f"{ADMIN_SETTINGS_URL}/grok_stt_api_key",
        json={"value": value},
        cookies=superuser_cookies,
    )
    assert r.status_code == 200, r.text
    assert r.json()["key"] == "grok_stt_api_key"
    assert r.json()["value"] is None

    db.expire_all()
    row = db.exec(
        select(SystemSetting).where(SystemSetting.key == "grok_stt_api_key")
    ).one()
    assert row.sensitive is True
    assert row.has_value is True
    assert row.value is None
    assert row.value_encrypted is not None
    assert value.encode("utf-8") not in row.value_encrypted

    g = client.get(
        f"{ADMIN_SETTINGS_URL}/grok_stt_api_key",
        cookies=superuser_cookies,
    )
    assert g.status_code == 200
    assert g.json()["value"] is None
    assert g.json()["sensitive"] is True
    assert g.json()["has_value"] is True


def test_put_max_voice_transcribes_per_hour_global_validates_range(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    ok = client.put(
        f"{ADMIN_SETTINGS_URL}/max_voice_transcribes_per_hour_global",
        json={"value": 3600},
        cookies=superuser_cookies,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["value"] == 3600

    bad = client.put(
        f"{ADMIN_SETTINGS_URL}/max_voice_transcribes_per_hour_global",
        json={"value": 0},
        cookies=superuser_cookies,
    )
    assert bad.status_code == 422
    assert bad.json()["detail"]["detail"] == "invalid_value_for_key"
