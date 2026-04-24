"""Integration tests for the cookie-authenticated /teams router.

Real FastAPI app + real Postgres via the session-scoped `db` fixture and
module-scoped `client` fixture in tests/conftest.py. No mocks.
"""
import re

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from tests.utils.utils import random_email, random_lower_string

SIGNUP_URL = f"{settings.API_V1_STR}/auth/signup"
TEAMS_URL = f"{settings.API_V1_STR}/teams/"


def _signup(client: TestClient) -> tuple[str, httpx.Cookies]:
    """Create a fresh user and return (user_id, session cookie jar).

    Clears the TestClient's shared cookie jar before the request so stale
    cookies don't collide with the new Set-Cookie. Returns a *detached* jar
    so the caller can pass it via cookies= without sharing state with other
    tests.
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


# ---------------------------------------------------------------------------
# 1. Auth: GET /teams without cookie → 401
# ---------------------------------------------------------------------------


def test_get_teams_without_cookie_returns_401(client: TestClient) -> None:
    client.cookies.clear()
    r = client.get(TEAMS_URL)
    assert r.status_code == 401
    assert r.json()["detail"] == "Not authenticated"


# ---------------------------------------------------------------------------
# 2. Signup auto-creates exactly one personal team
# ---------------------------------------------------------------------------


def test_get_teams_after_signup_returns_only_personal_team(
    client: TestClient,
) -> None:
    _, cookies = _signup(client)

    r = client.get(TEAMS_URL, cookies=cookies)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    assert len(body["data"]) == 1

    team = body["data"][0]
    assert team["is_personal"] is True
    assert team["role"] == "admin"
    assert team["name"]  # non-empty (email-local-part derived)
    assert re.match(r"^[a-z0-9-]+$", team["slug"]), team["slug"]


# ---------------------------------------------------------------------------
# 3. POST /teams creates non-personal team with creator as admin
# ---------------------------------------------------------------------------


def test_post_teams_creates_non_personal_team_with_creator_as_admin(
    client: TestClient,
) -> None:
    _, cookies = _signup(client)

    r = client.post(TEAMS_URL, json={"name": "Widgets Inc"}, cookies=cookies)
    assert r.status_code == 200, r.text
    team = r.json()
    assert team["is_personal"] is False
    assert team["role"] == "admin"
    assert team["name"] == "Widgets Inc"
    assert team["slug"].startswith("widgets-inc-"), team["slug"]

    # Caller now has 2 teams: personal + Widgets Inc.
    r2 = client.get(TEAMS_URL, cookies=cookies)
    assert r2.status_code == 200
    assert r2.json()["count"] == 2


# ---------------------------------------------------------------------------
# 4. POST /teams with missing name → 422
# ---------------------------------------------------------------------------


def test_post_teams_missing_name_returns_422(client: TestClient) -> None:
    _, cookies = _signup(client)
    r = client.post(TEAMS_URL, json={}, cookies=cookies)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 5. POST /teams with 256-char name → 422
# ---------------------------------------------------------------------------


def test_post_teams_name_too_long_returns_422(client: TestClient) -> None:
    _, cookies = _signup(client)
    r = client.post(TEAMS_URL, json={"name": "x" * 256}, cookies=cookies)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 6. POST /teams/{personal_id}/invite → 403
# ---------------------------------------------------------------------------


def test_invite_on_personal_team_returns_403(client: TestClient) -> None:
    _, cookies = _signup(client)

    r = client.get(TEAMS_URL, cookies=cookies)
    assert r.status_code == 200
    teams = r.json()["data"]
    personal = next(t for t in teams if t["is_personal"])
    personal_id = personal["id"]

    invite_url = f"{settings.API_V1_STR}/teams/{personal_id}/invite"
    r2 = client.post(invite_url, cookies=cookies)
    assert r2.status_code == 403
    assert r2.json()["detail"] == "Cannot invite to personal teams"


# ---------------------------------------------------------------------------
# 7. POST /teams/{non_personal_id}/invite → 501 stub (S03 will flip this red)
# ---------------------------------------------------------------------------


def test_invite_on_non_personal_team_returns_501_stub(client: TestClient) -> None:
    _, cookies = _signup(client)

    # Create a non-personal team.
    r = client.post(TEAMS_URL, json={"name": "Invitees"}, cookies=cookies)
    assert r.status_code == 200
    non_personal_id = r.json()["id"]

    invite_url = f"{settings.API_V1_STR}/teams/{non_personal_id}/invite"
    r2 = client.post(invite_url, cookies=cookies)
    # S03 contract: when real invites land, this should flip to 200. The test
    # is intentional red-flag bait for the next slice's executor.
    assert r2.status_code == 501, (
        "If this test is failing with 200, S03 has wired real invites — "
        "update this assertion to match the new contract."
    )


# ---------------------------------------------------------------------------
# 8. Cross-user isolation: user B cannot see user A's team
# ---------------------------------------------------------------------------


def test_get_teams_does_not_leak_other_users_teams(client: TestClient) -> None:
    _, cookies_a = _signup(client)
    create_a = client.post(
        TEAMS_URL, json={"name": "Alpha Secret"}, cookies=cookies_a
    )
    assert create_a.status_code == 200
    alpha_id = create_a.json()["id"]

    _, cookies_b = _signup(client)
    r = client.get(TEAMS_URL, cookies=cookies_b)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1, "user B should only see their own personal team"
    assert body["data"][0]["is_personal"] is True
    assert alpha_id not in {t["id"] for t in body["data"]}


# ---------------------------------------------------------------------------
# 9. Slug collision: two users each POST /teams {name: 'Research'} both succeed
# ---------------------------------------------------------------------------


def test_slug_collision_on_identical_names_still_succeeds(
    client: TestClient,
) -> None:
    _, cookies_a = _signup(client)
    _, cookies_b = _signup(client)

    r_a = client.post(TEAMS_URL, json={"name": "Research"}, cookies=cookies_a)
    assert r_a.status_code == 200, r_a.text
    team_a = r_a.json()
    assert team_a["role"] == "admin"
    assert team_a["is_personal"] is False
    assert team_a["slug"].startswith("research-"), team_a["slug"]

    r_b = client.post(TEAMS_URL, json={"name": "Research"}, cookies=cookies_b)
    assert r_b.status_code == 200, r_b.text
    team_b = r_b.json()
    assert team_b["role"] == "admin"
    assert team_b["is_personal"] is False
    assert team_b["slug"].startswith("research-"), team_b["slug"]

    assert team_a["slug"] != team_b["slug"], "suffixes must disambiguate"
