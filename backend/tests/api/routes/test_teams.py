"""Integration tests for the cookie-authenticated /teams router.

Real FastAPI app + real Postgres via the session-scoped `db` fixture and
module-scoped `client` fixture in tests/conftest.py. No mocks.
"""
import re
import uuid
from datetime import timedelta

import httpx
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.models import (
    Notification,
    TeamInvite,
    TeamMember,
    get_datetime_utc,
)
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
# 7. POST /teams/{non_personal_id}/invite → 200 with {code, url, expires_at}
# ---------------------------------------------------------------------------


def test_invite_on_non_personal_team_returns_code_url_and_expires_at(
    client: TestClient,
) -> None:
    _, cookies = _signup(client)

    # Create a non-personal team.
    r = client.post(TEAMS_URL, json={"name": "Invitees"}, cookies=cookies)
    assert r.status_code == 200
    non_personal_id = r.json()["id"]

    invite_url = f"{settings.API_V1_STR}/teams/{non_personal_id}/invite"
    r2 = client.post(invite_url, cookies=cookies)
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert "code" in body and isinstance(body["code"], str) and len(body["code"]) >= 20
    assert "url" in body and body["url"].endswith(f"/invite/{body['code']}")
    assert body["url"].startswith(settings.FRONTEND_HOST)
    assert "expires_at" in body and isinstance(body["expires_at"], str)


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


# ---------------------------------------------------------------------------
# Join-flow helpers (S03)
# ---------------------------------------------------------------------------


def _issue_invite(
    client: TestClient, cookies: httpx.Cookies, team_name: str = "Joiners"
) -> tuple[str, str]:
    """Create a non-personal team via the admin + issue an invite.

    Returns (team_id, code).
    """
    r = client.post(TEAMS_URL, json={"name": team_name}, cookies=cookies)
    assert r.status_code == 200, r.text
    team_id = r.json()["id"]
    invite_url = f"{settings.API_V1_STR}/teams/{team_id}/invite"
    r2 = client.post(invite_url, cookies=cookies)
    assert r2.status_code == 200, r2.text
    return team_id, r2.json()["code"]


# ---------------------------------------------------------------------------
# 10. Invite as non-admin member → 403
# ---------------------------------------------------------------------------


def test_invite_by_non_admin_member_returns_403(
    client: TestClient, db: Session
) -> None:
    _admin_id, cookies_admin = _signup(client)
    team_id, code = _issue_invite(client, cookies_admin, team_name="WithMember")

    # Member accepts → is now a TeamRole.member.
    _member_id, cookies_member = _signup(client)
    r_join = client.post(
        f"{settings.API_V1_STR}/teams/join/{code}", cookies=cookies_member
    )
    assert r_join.status_code == 200, r_join.text

    # Member tries to invite — should 403.
    r = client.post(
        f"{settings.API_V1_STR}/teams/{team_id}/invite", cookies=cookies_member
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "Only team admins can invite"


# ---------------------------------------------------------------------------
# 11. Join: happy path — second user redeems code and becomes member
# ---------------------------------------------------------------------------


def test_join_with_valid_code_adds_caller_as_member(
    client: TestClient,
) -> None:
    _, cookies_admin = _signup(client)
    team_id, code = _issue_invite(client, cookies_admin, team_name="JoinHappy")

    _, cookies_joiner = _signup(client)
    r = client.post(
        f"{settings.API_V1_STR}/teams/join/{code}", cookies=cookies_joiner
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == team_id
    assert body["role"] == "member"
    assert body["is_personal"] is False

    # Joiner's GET /teams now shows personal + joined.
    r2 = client.get(TEAMS_URL, cookies=cookies_joiner)
    assert r2.status_code == 200
    ids = {t["id"] for t in r2.json()["data"]}
    assert team_id in ids


# ---------------------------------------------------------------------------
# 12. Join: unknown code → 404
# ---------------------------------------------------------------------------


def test_join_with_unknown_code_returns_404(client: TestClient) -> None:
    _, cookies = _signup(client)
    r = client.post(
        f"{settings.API_V1_STR}/teams/join/does-not-exist-abc123",
        cookies=cookies,
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "Invite not found"


# ---------------------------------------------------------------------------
# 13. Join: expired code → 410
# ---------------------------------------------------------------------------


def test_join_with_expired_code_returns_410(
    client: TestClient, db: Session
) -> None:
    _, cookies_admin = _signup(client)
    _team_id, code = _issue_invite(client, cookies_admin, team_name="ExpireMe")

    # Force expiry by rewinding the row's expires_at to yesterday.
    invite = db.exec(select(TeamInvite).where(TeamInvite.code == code)).first()
    assert invite is not None
    invite.expires_at = get_datetime_utc() - timedelta(days=1)
    db.add(invite)
    db.commit()

    _, cookies_joiner = _signup(client)
    r = client.post(
        f"{settings.API_V1_STR}/teams/join/{code}", cookies=cookies_joiner
    )
    assert r.status_code == 410
    assert r.json()["detail"] == "Invite expired"


# ---------------------------------------------------------------------------
# 14. Join: already-used code → 410
# ---------------------------------------------------------------------------


def test_join_with_already_used_code_returns_410(
    client: TestClient,
) -> None:
    _, cookies_admin = _signup(client)
    _team_id, code = _issue_invite(client, cookies_admin, team_name="SpentCode")

    # User B accepts.
    _, cookies_b = _signup(client)
    r_b = client.post(
        f"{settings.API_V1_STR}/teams/join/{code}", cookies=cookies_b
    )
    assert r_b.status_code == 200, r_b.text

    # User C tries the same code — should 410.
    _, cookies_c = _signup(client)
    r_c = client.post(
        f"{settings.API_V1_STR}/teams/join/{code}", cookies=cookies_c
    )
    assert r_c.status_code == 410
    assert r_c.json()["detail"] == "Invite already used"


# ---------------------------------------------------------------------------
# 15. Join: caller already a member → 409 (inviter re-joining their own team)
# ---------------------------------------------------------------------------


def test_join_by_existing_member_returns_409(
    client: TestClient,
) -> None:
    _, cookies_admin = _signup(client)
    _team_id, code = _issue_invite(client, cookies_admin, team_name="SelfJoin")

    # Admin is already a member of the team — redeeming own code → 409.
    r = client.post(
        f"{settings.API_V1_STR}/teams/join/{code}", cookies=cookies_admin
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "Already a member"


# ---------------------------------------------------------------------------
# 16. Join: no cookie → 401
# ---------------------------------------------------------------------------


def test_join_without_cookie_returns_401(client: TestClient) -> None:
    client.cookies.clear()
    r = client.post(f"{settings.API_V1_STR}/teams/join/anything")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# 17. Invite on missing team → 404
# ---------------------------------------------------------------------------


def test_invite_on_missing_team_returns_404(client: TestClient) -> None:
    _, cookies = _signup(client)
    bogus = uuid.uuid4()
    r = client.post(
        f"{settings.API_V1_STR}/teams/{bogus}/invite", cookies=cookies
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "Team not found"


# ---------------------------------------------------------------------------
# 18. Invite on non-UUID team_id → 422 (FastAPI validator)
# ---------------------------------------------------------------------------


def test_invite_on_non_uuid_team_id_returns_422(client: TestClient) -> None:
    _, cookies = _signup(client)
    r = client.post(
        f"{settings.API_V1_STR}/teams/not-a-uuid/invite", cookies=cookies
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 19. Atomicity: a rejected join (expired) leaves no team_member row
# ---------------------------------------------------------------------------


def test_rejected_join_leaves_no_membership(
    client: TestClient, db: Session
) -> None:
    _, cookies_admin = _signup(client)
    _team_id, code = _issue_invite(client, cookies_admin, team_name="NoOrphan")

    # Expire the code.
    invite = db.exec(select(TeamInvite).where(TeamInvite.code == code)).first()
    assert invite is not None
    invite.expires_at = get_datetime_utc() - timedelta(days=1)
    db.add(invite)
    db.commit()

    joiner_id, cookies_joiner = _signup(client)
    r = client.post(
        f"{settings.API_V1_STR}/teams/join/{code}", cookies=cookies_joiner
    )
    assert r.status_code == 410

    # No team_member row for (joiner, invite.team_id).
    membership = db.exec(
        select(TeamMember)
        .where(TeamMember.user_id == uuid.UUID(joiner_id))
        .where(TeamMember.team_id == invite.team_id)
    ).first()
    assert membership is None


# ---------------------------------------------------------------------------
# 20. notify() side-effect: accepting an invite inserts a notifications row
# ---------------------------------------------------------------------------


def test_invite_accept_creates_notification(
    client: TestClient, db: Session
) -> None:
    """Joining a team via invite-code fans out a `team_invite_accepted`
    notification to the accepter — the bell's seed-truth content."""
    _, cookies_admin = _signup(client)
    team_id, code = _issue_invite(
        client, cookies_admin, team_name="NotifySeed"
    )

    joiner_id, cookies_joiner = _signup(client)

    # Sanity: the accepter has zero notifications before redeeming the code.
    pre = db.exec(
        select(Notification).where(
            Notification.user_id == uuid.UUID(joiner_id)
        )
    ).all()
    assert pre == []

    r = client.post(
        f"{settings.API_V1_STR}/teams/join/{code}", cookies=cookies_joiner
    )
    assert r.status_code == 200, r.text

    db.expire_all()
    rows = db.exec(
        select(Notification)
        .where(Notification.user_id == uuid.UUID(joiner_id))
        .where(Notification.kind == "team_invite_accepted")
    ).all()
    assert len(rows) == 1, "exactly one team_invite_accepted row expected"

    only = rows[0]
    assert only.payload.get("team_id") == team_id
    # The team's display name is rendered from the API and should land in
    # the payload (used by the bell's panel row text).
    assert only.payload.get("team_name") == "NotifySeed"
    assert only.source_team_id == uuid.UUID(team_id)
    assert only.read_at is None
