"""Integration tests for the system admin router (S05 / T01).

Covers all three endpoints:
  - GET  /admin/teams                          (paginated list)
  - GET  /admin/teams/{team_id}/members        (cross-team roster)
  - POST /admin/users/{user_id}/promote-system-admin

For every endpoint we assert: superuser 200 happy path, non-admin 403,
missing-resource 404. Promote also covers idempotency. Pagination has its
own dedicated test creating multiple teams.

Multi-user flows follow MEM029 (detached cookie jar per user; clear the
shared TestClient jar between signups).
"""
import logging
import uuid

import httpx
from fastapi.testclient import TestClient

from app.core.config import settings
from tests.utils.utils import random_email, random_lower_string

ADMIN_TEAMS_URL = f"{settings.API_V1_STR}/admin/teams"
ADMIN_USERS_URL = f"{settings.API_V1_STR}/admin/users"
SIGNUP_URL = f"{settings.API_V1_STR}/auth/signup"
TEAMS_URL = f"{settings.API_V1_STR}/teams/"


def _signup(client: TestClient) -> tuple[str, httpx.Cookies]:
    """Sign up a fresh user and return (user_id, detached cookie jar).

    Mirrors the helper in test_members.py / test_teams.py — keeps each user's
    session cookie isolated per MEM029 so multi-user assertions do not collide
    on the shared TestClient jar.
    """
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


def _create_team(
    client: TestClient, cookies: httpx.Cookies, name: str
) -> str:
    r = client.post(TEAMS_URL, json={"name": name}, cookies=cookies)
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ---------------------------------------------------------------------------
# GET /admin/teams
# ---------------------------------------------------------------------------


def test_list_all_teams_as_superuser_returns_200_with_envelope(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """Happy path: envelope shape `{data, count}` and TeamPublic fields."""
    r = client.get(ADMIN_TEAMS_URL, cookies=superuser_cookies)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "data" in body and "count" in body
    assert isinstance(body["data"], list)
    assert isinstance(body["count"], int)
    if body["data"]:
        sample = body["data"][0]
        # TeamPublic public fields — never role (admin doesn't have one here).
        for key in ("id", "name", "slug", "is_personal", "created_at"):
            assert key in sample


def test_list_all_teams_includes_personal_and_non_personal(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """Admin sees a brand-new personal team AND a brand-new non-personal team."""
    _u_id, cookies_u = _signup(client)  # creates personal team
    non_personal_id = _create_team(client, cookies_u, "AdminMixVisible")

    r = client.get(ADMIN_TEAMS_URL, cookies=superuser_cookies)
    assert r.status_code == 200, r.text
    by_id = {t["id"]: t for t in r.json()["data"]}
    # The non-personal team we just created is visible.
    assert non_personal_id in by_id
    assert by_id[non_personal_id]["is_personal"] is False
    # At least one personal team is in the result set globally.
    assert any(t["is_personal"] for t in r.json()["data"])


def test_list_all_teams_as_normal_user_returns_403(
    client: TestClient,
) -> None:
    """A freshly-signed-up user has role=user → guard rejects with 403."""
    _u_id, cookies_u = _signup(client)
    r = client.get(ADMIN_TEAMS_URL, cookies=cookies_u)
    assert r.status_code == 403
    assert r.json()["detail"] == "The user doesn't have enough privileges"


def test_list_all_teams_unauthenticated_returns_401(
    client: TestClient,
) -> None:
    """No session cookie → 401 (auth runs before the superuser check)."""
    client.cookies.clear()
    r = client.get(ADMIN_TEAMS_URL)
    assert r.status_code == 401


def test_list_all_teams_pagination_skip_and_limit(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """Create 3 fresh non-personal teams; assert skip/limit + count behave."""
    _u_id, cookies_u = _signup(client)
    created_ids = [
        _create_team(client, cookies_u, f"PageTest-{uuid.uuid4().hex[:6]}")
        for _ in range(3)
    ]

    # Page 1 (limit=1) returns 1 row; total count covers all teams system-wide.
    r1 = client.get(
        ADMIN_TEAMS_URL,
        params={"skip": 0, "limit": 1},
        cookies=superuser_cookies,
    )
    assert r1.status_code == 200, r1.text
    page1 = r1.json()
    assert len(page1["data"]) == 1
    assert page1["count"] >= 3  # personal teams + the 3 we created

    # Page 2 (skip=1, limit=2) returns the next 2 rows; same count.
    r2 = client.get(
        ADMIN_TEAMS_URL,
        params={"skip": 1, "limit": 2},
        cookies=superuser_cookies,
    )
    assert r2.status_code == 200, r2.text
    page2 = r2.json()
    assert len(page2["data"]) == 2
    assert page2["count"] == page1["count"]

    # No overlap between page 1 and page 2.
    p1_ids = {t["id"] for t in page1["data"]}
    p2_ids = {t["id"] for t in page2["data"]}
    assert p1_ids.isdisjoint(p2_ids)

    # Of the 3 we just created (ordered by created_at DESC), the most recent
    # should be on page 1 — newest first.
    most_recent_created = created_ids[-1]
    assert most_recent_created in p1_ids


def test_list_all_teams_emits_structured_log(
    client: TestClient,
    superuser_cookies: httpx.Cookies,
    caplog,
) -> None:
    """Slice contract: `admin_teams_listed actor_id=<uuid> skip=<n> limit=<n> count=<n>`."""
    with caplog.at_level(logging.INFO, logger="app.api.routes.admin"):
        r = client.get(
            ADMIN_TEAMS_URL,
            params={"skip": 0, "limit": 5},
            cookies=superuser_cookies,
        )
    assert r.status_code == 200, r.text
    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        "admin_teams_listed" in m and "skip=0" in m and "limit=5" in m
        for m in msgs
    ), msgs


# ---------------------------------------------------------------------------
# GET /admin/teams/{team_id}/members
# ---------------------------------------------------------------------------


def test_admin_team_members_lists_members_when_admin_is_not_a_member(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """Bypass: the seeded superuser is NOT a member of this team but still sees it."""
    user_id, cookies_u = _signup(client)
    team_id = _create_team(client, cookies_u, "BypassMembershipCheck")

    r = client.get(
        f"{ADMIN_TEAMS_URL}/{team_id}/members", cookies=superuser_cookies
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    assert body["data"][0]["user_id"] == user_id
    assert body["data"][0]["role"] == "admin"


def test_admin_team_members_as_non_admin_returns_403(
    client: TestClient,
) -> None:
    """Even the team admin (regular user) gets 403 on the /admin/* version.

    The team's own admin can read their team via /teams/{id}/members — the
    /admin variant is reserved for system_admin role regardless of team
    membership.
    """
    _u_id, cookies_u = _signup(client)
    team_id = _create_team(client, cookies_u, "AdminEndpointGated")
    r = client.get(
        f"{ADMIN_TEAMS_URL}/{team_id}/members", cookies=cookies_u
    )
    assert r.status_code == 403


def test_admin_team_members_unknown_team_returns_404(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """Unknown team_id → 404 'Team not found'."""
    bogus = uuid.uuid4()
    r = client.get(
        f"{ADMIN_TEAMS_URL}/{bogus}/members", cookies=superuser_cookies
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "Team not found"


def test_admin_team_members_emits_structured_log(
    client: TestClient,
    superuser_cookies: httpx.Cookies,
    caplog,
) -> None:
    """Slice contract: `admin_team_members_listed actor_id=<uuid> team_id=<uuid> count=<n>`."""
    _u_id, cookies_u = _signup(client)
    team_id = _create_team(client, cookies_u, "MembersLogShape")

    with caplog.at_level(logging.INFO, logger="app.api.routes.admin"):
        r = client.get(
            f"{ADMIN_TEAMS_URL}/{team_id}/members",
            cookies=superuser_cookies,
        )
    assert r.status_code == 200, r.text
    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        "admin_team_members_listed" in m and team_id in m for m in msgs
    ), msgs


# ---------------------------------------------------------------------------
# POST /admin/users/{user_id}/promote-system-admin
# ---------------------------------------------------------------------------


def test_promote_system_admin_flips_role(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """user-role target → system_admin; mutation persists across re-login."""
    user_id, cookies_u = _signup(client)

    # Pre-condition: the new user is role=user.
    r_pre = client.get(
        f"{settings.API_V1_STR}/users/me", cookies=cookies_u
    )
    assert r_pre.status_code == 200
    assert r_pre.json()["role"] == "user"

    # Promote.
    r = client.post(
        f"{ADMIN_USERS_URL}/{user_id}/promote-system-admin",
        cookies=superuser_cookies,
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "system_admin"
    assert r.json()["id"] == user_id

    # Verify persistence: the same cookie still works (no re-login needed —
    # session JWT carries user_id, role is read from the row each request).
    r_post = client.get(
        f"{settings.API_V1_STR}/users/me", cookies=cookies_u
    )
    assert r_post.status_code == 200
    assert r_post.json()["role"] == "system_admin"


def test_promote_system_admin_idempotent(
    client: TestClient,
    superuser_cookies: httpx.Cookies,
    caplog,
) -> None:
    """Calling promote twice is safe; second call logs already_admin=true."""
    user_id, _cookies_u = _signup(client)

    # First call: flips role.
    r1 = client.post(
        f"{ADMIN_USERS_URL}/{user_id}/promote-system-admin",
        cookies=superuser_cookies,
    )
    assert r1.status_code == 200
    assert r1.json()["role"] == "system_admin"

    # Second call: still 200, role unchanged, log notes already_admin=true.
    with caplog.at_level(logging.INFO, logger="app.api.routes.admin"):
        r2 = client.post(
            f"{ADMIN_USERS_URL}/{user_id}/promote-system-admin",
            cookies=superuser_cookies,
        )
    assert r2.status_code == 200
    assert r2.json()["role"] == "system_admin"
    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        "system_admin_promoted" in m
        and user_id in m
        and "already_admin=true" in m
        for m in msgs
    ), msgs


def test_promote_system_admin_first_call_logs_already_admin_false(
    client: TestClient,
    superuser_cookies: httpx.Cookies,
    caplog,
) -> None:
    """First-time promotion log must say `already_admin=false`."""
    user_id, _cookies_u = _signup(client)

    with caplog.at_level(logging.INFO, logger="app.api.routes.admin"):
        r = client.post(
            f"{ADMIN_USERS_URL}/{user_id}/promote-system-admin",
            cookies=superuser_cookies,
        )
    assert r.status_code == 200
    msgs = [rec.getMessage() for rec in caplog.records]
    assert any(
        "system_admin_promoted" in m
        and user_id in m
        and "already_admin=false" in m
        for m in msgs
    ), msgs


def test_promote_system_admin_unknown_user_returns_404(
    client: TestClient, superuser_cookies: httpx.Cookies
) -> None:
    """Unknown user_id → 404 'User not found'."""
    bogus = uuid.uuid4()
    r = client.post(
        f"{ADMIN_USERS_URL}/{bogus}/promote-system-admin",
        cookies=superuser_cookies,
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "User not found"


def test_promote_system_admin_as_normal_user_returns_403(
    client: TestClient,
) -> None:
    """Non-admin attempting to call promote → 403 (the gate fires before lookup)."""
    target_id, _cookies_target = _signup(client)
    _caller_id, cookies_caller = _signup(client)

    r = client.post(
        f"{ADMIN_USERS_URL}/{target_id}/promote-system-admin",
        cookies=cookies_caller,
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "The user doesn't have enough privileges"
