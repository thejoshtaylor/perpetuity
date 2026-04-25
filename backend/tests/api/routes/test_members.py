"""Integration tests for PATCH /teams/{id}/members/{uid}/role + DELETE member.

Multi-user flows follow MEM029 (detached cookie jar per user). Every test that
exercises an admin check involves at least two distinct users to catch any
accidental drop of the admin gate.
"""
import uuid

import httpx
from fastapi.testclient import TestClient

from app.core.config import settings
from tests.utils.utils import random_email, random_lower_string

SIGNUP_URL = f"{settings.API_V1_STR}/auth/signup"
TEAMS_URL = f"{settings.API_V1_STR}/teams/"


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


def _create_team(client: TestClient, cookies: httpx.Cookies, name: str) -> str:
    r = client.post(TEAMS_URL, json={"name": name}, cookies=cookies)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _issue_invite(client: TestClient, cookies: httpx.Cookies, team_id: str) -> str:
    r = client.post(
        f"{settings.API_V1_STR}/teams/{team_id}/invite", cookies=cookies
    )
    assert r.status_code == 200, r.text
    return r.json()["code"]


def _admin_with_member(client: TestClient, name: str) -> tuple[
    str, httpx.Cookies, str, httpx.Cookies, str
]:
    """Return (admin_id, admin_cookies, member_id, member_cookies, team_id).

    A signs up, creates a team, invites B. B signs up + accepts the invite
    (landing as a regular member).
    """
    admin_id, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, name)
    code = _issue_invite(client, cookies_a, team_id)

    member_id, cookies_b = _signup(client)
    r = client.post(
        f"{settings.API_V1_STR}/teams/join/{code}", cookies=cookies_b
    )
    assert r.status_code == 200, r.text
    return admin_id, cookies_a, member_id, cookies_b, team_id


# ---------------------------------------------------------------------------
# PATCH role
# ---------------------------------------------------------------------------


def test_patch_role_promotes_member_to_admin(client: TestClient) -> None:
    """Admin A promotes member B → B's GET /teams shows role=admin."""
    _a_id, cookies_a, b_id, cookies_b, team_id = _admin_with_member(
        client, "PromoteMe"
    )

    r = client.patch(
        f"{settings.API_V1_STR}/teams/{team_id}/members/{b_id}/role",
        json={"role": "admin"},
        cookies=cookies_a,
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "admin"

    r_b = client.get(TEAMS_URL, cookies=cookies_b)
    assert r_b.status_code == 200
    teams_b = {t["id"]: t for t in r_b.json()["data"]}
    assert teams_b[team_id]["role"] == "admin"


def test_patch_role_demotes_admin_to_member(client: TestClient) -> None:
    """Promote B to admin, then demote back to member."""
    _a_id, cookies_a, b_id, cookies_b, team_id = _admin_with_member(
        client, "DemoteMe"
    )

    # Promote.
    r_up = client.patch(
        f"{settings.API_V1_STR}/teams/{team_id}/members/{b_id}/role",
        json={"role": "admin"},
        cookies=cookies_a,
    )
    assert r_up.status_code == 200, r_up.text

    # Demote.
    r_down = client.patch(
        f"{settings.API_V1_STR}/teams/{team_id}/members/{b_id}/role",
        json={"role": "member"},
        cookies=cookies_a,
    )
    assert r_down.status_code == 200, r_down.text
    assert r_down.json()["role"] == "member"

    r_b = client.get(TEAMS_URL, cookies=cookies_b)
    assert r_b.status_code == 200
    teams_b = {t["id"]: t for t in r_b.json()["data"]}
    assert teams_b[team_id]["role"] == "member"


def test_patch_role_as_non_admin_returns_403(client: TestClient) -> None:
    """Member B cannot PATCH A's role."""
    a_id, _cookies_a, _b_id, cookies_b, team_id = _admin_with_member(
        client, "NonAdminPatch"
    )
    r = client.patch(
        f"{settings.API_V1_STR}/teams/{team_id}/members/{a_id}/role",
        json={"role": "member"},
        cookies=cookies_b,
    )
    assert r.status_code == 403


def test_patch_role_demoting_last_admin_returns_400(
    client: TestClient,
) -> None:
    """Sole admin cannot demote self."""
    a_id, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "LastAdminPatch")

    r = client.patch(
        f"{settings.API_V1_STR}/teams/{team_id}/members/{a_id}/role",
        json={"role": "member"},
        cookies=cookies_a,
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "Cannot demote the last admin"


def test_patch_role_unknown_target_returns_404(client: TestClient) -> None:
    """PATCH a UUID that is not a member of the team → 404."""
    _a_id, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "UnknownTarget")
    bogus = uuid.uuid4()

    r = client.patch(
        f"{settings.API_V1_STR}/teams/{team_id}/members/{bogus}/role",
        json={"role": "member"},
        cookies=cookies_a,
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "Membership not found"


def test_patch_role_invalid_body_returns_422(client: TestClient) -> None:
    """Role outside the TeamRole enum (e.g. 'owner') → 422."""
    a_id, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "BadRoleBody")
    r = client.patch(
        f"{settings.API_V1_STR}/teams/{team_id}/members/{a_id}/role",
        json={"role": "owner"},
        cookies=cookies_a,
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# DELETE member
# ---------------------------------------------------------------------------


def test_delete_member_removes_row_returns_204(client: TestClient) -> None:
    """Admin removes member — DELETE returns 204, member's GET /teams drops the team."""
    _a_id, cookies_a, b_id, cookies_b, team_id = _admin_with_member(
        client, "DeleteMember"
    )

    r = client.delete(
        f"{settings.API_V1_STR}/teams/{team_id}/members/{b_id}",
        cookies=cookies_a,
    )
    assert r.status_code == 204
    assert r.text == ""

    r_b = client.get(TEAMS_URL, cookies=cookies_b)
    assert r_b.status_code == 200
    body = r_b.json()
    assert body["count"] == 1
    assert body["data"][0]["is_personal"] is True


def test_delete_last_admin_returns_400(client: TestClient) -> None:
    """Sole admin cannot DELETE self."""
    a_id, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "LastAdminDelete")

    r = client.delete(
        f"{settings.API_V1_STR}/teams/{team_id}/members/{a_id}",
        cookies=cookies_a,
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "Cannot remove the last admin"


# ---------------------------------------------------------------------------
# GET /teams/{id}/members
# ---------------------------------------------------------------------------


def test_get_team_members_lists_admin_and_member(client: TestClient) -> None:
    """Admin sees both their own row and the invited member's row."""
    a_id, cookies_a, b_id, _cookies_b, team_id = _admin_with_member(
        client, "RosterHappy"
    )

    r = client.get(
        f"{settings.API_V1_STR}/teams/{team_id}/members", cookies=cookies_a
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    by_id = {row["user_id"]: row for row in body["data"]}
    assert by_id[a_id]["role"] == "admin"
    assert by_id[b_id]["role"] == "member"
    # Each row carries the contact fields the FE renders.
    assert "email" in by_id[a_id]
    assert "full_name" in by_id[a_id]


def test_get_team_members_as_non_member_returns_403(client: TestClient) -> None:
    """An unrelated user cannot read another team's roster."""
    _a_id, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "PrivateRoster")

    _outsider_id, cookies_out = _signup(client)
    r = client.get(
        f"{settings.API_V1_STR}/teams/{team_id}/members", cookies=cookies_out
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "Not a member of this team"


def test_get_team_members_unknown_team_returns_404(client: TestClient) -> None:
    """Unknown team ID → 404 (does not leak existence to non-members)."""
    _a_id, cookies_a = _signup(client)
    bogus = uuid.uuid4()
    r = client.get(
        f"{settings.API_V1_STR}/teams/{bogus}/members", cookies=cookies_a
    )
    assert r.status_code == 404


def test_delete_on_personal_team_returns_400(client: TestClient) -> None:
    """Cannot remove anyone from a personal team — refused at the team layer."""
    a_id, cookies_a = _signup(client)

    # Find A's personal team.
    r = client.get(TEAMS_URL, cookies=cookies_a)
    assert r.status_code == 200
    personal = next(t for t in r.json()["data"] if t["is_personal"])

    r_del = client.delete(
        f"{settings.API_V1_STR}/teams/{personal['id']}/members/{a_id}",
        cookies=cookies_a,
    )
    assert r_del.status_code == 400
    assert r_del.json()["detail"] == "Cannot remove members from personal teams"
