"""Integration tests for POST /teams/{id}/invite + POST /teams/join/{code}.

Real FastAPI app + real Postgres via the module-scoped `client` + session-scoped
`db` fixtures. Multi-user flows follow MEM029 (detached `httpx.Cookies` jar per
user, `cookies=` kwarg on each request).
"""
import uuid
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import crud
from app.core.config import settings
from app.models import TeamInvite, TeamMember, get_datetime_utc
from tests.utils.utils import random_email, random_lower_string

SIGNUP_URL = f"{settings.API_V1_STR}/auth/signup"
TEAMS_URL = f"{settings.API_V1_STR}/teams/"


def _signup(client: TestClient) -> tuple[str, httpx.Cookies]:
    """Create a fresh user, return (user_id, detached cookie jar). See MEM015/MEM029."""
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


# ---------------------------------------------------------------------------
# Invite issuance
# ---------------------------------------------------------------------------


def test_invite_returns_code_url_expires_at(client: TestClient) -> None:
    """Admin issues invite — body shape + expiry window ≥ 6 days."""
    _, cookies = _signup(client)
    team_id = _create_team(client, cookies, "Inviters")

    before = get_datetime_utc()
    r = client.post(
        f"{settings.API_V1_STR}/teams/{team_id}/invite", cookies=cookies
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert isinstance(body["code"], str) and len(body["code"]) >= 32
    assert isinstance(body["url"], str) and "/invite/" in body["url"]
    assert body["url"].endswith(f"/invite/{body['code']}")

    from datetime import datetime

    expires_at = datetime.fromisoformat(body["expires_at"])
    assert expires_at >= before + timedelta(days=6), (
        f"expires_at {expires_at} must be ≥ 6 days in the future (before={before})"
    )


def test_invite_personal_team_returns_403(client: TestClient) -> None:
    """Defense-in-depth — personal-team rejection still works after S03 refactor."""
    _, cookies = _signup(client)
    r = client.get(TEAMS_URL, cookies=cookies)
    assert r.status_code == 200
    personal = next(t for t in r.json()["data"] if t["is_personal"])

    r2 = client.post(
        f"{settings.API_V1_STR}/teams/{personal['id']}/invite", cookies=cookies
    )
    assert r2.status_code == 403
    assert r2.json()["detail"] == "Cannot invite to personal teams"


def test_invite_as_non_admin_returns_403(client: TestClient) -> None:
    """User B (not a member at all) tries to invite on A's team → 403."""
    _, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "AOnly")

    _, cookies_b = _signup(client)
    r = client.post(
        f"{settings.API_V1_STR}/teams/{team_id}/invite", cookies=cookies_b
    )
    assert r.status_code == 403


def test_invite_as_member_not_admin_returns_403(client: TestClient) -> None:
    """User B joined as member — cannot issue invites."""
    _, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "MemberOnly")
    code = _issue_invite(client, cookies_a, team_id)

    _, cookies_b = _signup(client)
    r_join = client.post(
        f"{settings.API_V1_STR}/teams/join/{code}", cookies=cookies_b
    )
    assert r_join.status_code == 200, r_join.text

    r = client.post(
        f"{settings.API_V1_STR}/teams/{team_id}/invite", cookies=cookies_b
    )
    assert r.status_code == 403
    assert r.json()["detail"] == "Only team admins can invite"


# ---------------------------------------------------------------------------
# Join flow
# ---------------------------------------------------------------------------


def test_join_valid_code_adds_member_and_marks_used(
    client: TestClient, db: Session
) -> None:
    """Happy path: B accepts A's invite — 200 TeamWithRole, used_at + used_by stamped."""
    _, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "JoinHappy2")
    code = _issue_invite(client, cookies_a, team_id)

    b_id, cookies_b = _signup(client)
    r = client.post(
        f"{settings.API_V1_STR}/teams/join/{code}", cookies=cookies_b
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == team_id
    assert body["role"] == "member"
    assert body["is_personal"] is False

    # B's GET /teams now returns 2 teams (personal + joined).
    r2 = client.get(TEAMS_URL, cookies=cookies_b)
    assert r2.status_code == 200
    assert r2.json()["count"] == 2

    # DB: used_at + used_by set.
    db.expire_all()
    invite = db.exec(select(TeamInvite).where(TeamInvite.code == code)).first()
    assert invite is not None
    assert invite.used_at is not None
    assert invite.used_by == uuid.UUID(b_id)


def test_join_unknown_code_returns_404(client: TestClient) -> None:
    _, cookies = _signup(client)
    r = client.post(
        f"{settings.API_V1_STR}/teams/join/garbage-code-xyz", cookies=cookies
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "Invite not found"


def test_join_expired_code_returns_410(
    client: TestClient, db: Session
) -> None:
    """Backdate expires_at via direct DB UPDATE on the `db` fixture session."""
    _, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "ExpireMe2")
    code = _issue_invite(client, cookies_a, team_id)

    invite = db.exec(select(TeamInvite).where(TeamInvite.code == code)).first()
    assert invite is not None
    invite.expires_at = get_datetime_utc() - timedelta(days=1)
    db.add(invite)
    db.commit()

    _, cookies_b = _signup(client)
    r = client.post(
        f"{settings.API_V1_STR}/teams/join/{code}", cookies=cookies_b
    )
    assert r.status_code == 410
    assert r.json()["detail"] == "Invite expired"


def test_join_used_code_returns_410(client: TestClient) -> None:
    """B accepts, then C tries the same code → 410 'Invite already used'."""
    _, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "SpentCode2")
    code = _issue_invite(client, cookies_a, team_id)

    _, cookies_b = _signup(client)
    r_b = client.post(
        f"{settings.API_V1_STR}/teams/join/{code}", cookies=cookies_b
    )
    assert r_b.status_code == 200, r_b.text

    _, cookies_c = _signup(client)
    r_c = client.post(
        f"{settings.API_V1_STR}/teams/join/{code}", cookies=cookies_c
    )
    assert r_c.status_code == 410
    assert r_c.json()["detail"] == "Invite already used"


def test_join_duplicate_member_returns_409(client: TestClient) -> None:
    """Admin A (already a member) tries to redeem A's own invite → 409."""
    _, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "SelfJoin2")
    code = _issue_invite(client, cookies_a, team_id)

    r = client.post(
        f"{settings.API_V1_STR}/teams/join/{code}", cookies=cookies_a
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "Already a member"


def test_join_atomicity_on_membership_insert_failure(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If accept_team_invite raises mid-transaction, invite.used_at stays NULL.

    Mirrors test_signup_rolls_back_on_mid_transaction_failure (MEM030). Uses a
    local TestClient(app, raise_server_exceptions=False) so the 500 surfaces as
    a response rather than propagating.
    """
    from app.api.routes import teams as teams_route
    from app.main import app

    _, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "AtomicJoin")
    code = _issue_invite(client, cookies_a, team_id)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated mid-tx failure")

    # Patch the symbol the route imported (crud.accept_team_invite).
    monkeypatch.setattr(teams_route.crud, "accept_team_invite", _boom)

    # Fresh joiner + local client that captures 500 as response.
    _, cookies_b = _signup(client)
    with TestClient(app, raise_server_exceptions=False) as local_client:
        # httpx.Cookies doesn't play nicely with jar iteration order — copy in.
        for cookie in cookies_b.jar:
            local_client.cookies.set(cookie.name, cookie.value)
        r = local_client.post(f"{settings.API_V1_STR}/teams/join/{code}")
    assert r.status_code == 500, r.text

    # Invite not marked used — rollback proven.
    db.expire_all()
    invite = db.exec(select(TeamInvite).where(TeamInvite.code == code)).first()
    assert invite is not None
    assert invite.used_at is None
    assert invite.used_by is None

    # No team_member row inserted for the joiner — only the admin remains.
    rows = db.exec(
        select(TeamMember).where(TeamMember.team_id == uuid.UUID(team_id))
    ).all()
    assert len(rows) == 1, f"expected only admin; got {len(rows)} members"
