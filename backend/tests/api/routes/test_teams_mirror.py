"""Integration tests for PATCH /api/v1/teams/{id}/mirror always_on toggle.

Backend-only — the toggle just biases the next reaper tick which reads the
`team_mirror_volumes` row directly. Verifies the team-admin gate, the
auto-create-on-first-PATCH path with placeholder `volume_path`, idempotency,
and the negative cases (non-admin → 403, non-member → 403, missing team →
404, malformed body → 422). Multi-user flows follow MEM029 (detached cookie
jar per user) — every admin-gate assertion uses two distinct users.
"""
import uuid

import httpx
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import settings
from app.models import TeamMirrorVolume
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


def _issue_invite(
    client: TestClient, cookies: httpx.Cookies, team_id: str
) -> str:
    r = client.post(
        f"{settings.API_V1_STR}/teams/{team_id}/invite", cookies=cookies
    )
    assert r.status_code == 200, r.text
    return r.json()["code"]


def _admin_with_member(client: TestClient, name: str) -> tuple[
    str, httpx.Cookies, str, httpx.Cookies, str
]:
    """Return (admin_id, admin_cookies, member_id, member_cookies, team_id)."""
    admin_id, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, name)
    code = _issue_invite(client, cookies_a, team_id)

    member_id, cookies_b = _signup(client)
    r = client.post(
        f"{settings.API_V1_STR}/teams/join/{code}", cookies=cookies_b
    )
    assert r.status_code == 200, r.text
    return admin_id, cookies_a, member_id, cookies_b, team_id


def _mirror_url(team_id: str) -> str:
    return f"{settings.API_V1_STR}/teams/{team_id}/mirror"


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_patch_mirror_first_call_auto_creates_row(
    client: TestClient, db: Session
) -> None:
    """First PATCH on a team with no mirror row inserts one with placeholder volume_path."""
    _a_id, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "AutoCreateMirror")

    r = client.patch(
        _mirror_url(team_id),
        json={"always_on": True},
        cookies=cookies_a,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["team_id"] == team_id
    assert body["always_on"] is True
    assert body["volume_path"] == f"pending:{team_id}"
    assert body["container_id"] is None

    # Verify the row landed in the DB with the placeholder volume_path so
    # the orchestrator's ensure path knows to replace it on cold-start.
    db.expire_all()
    row = db.exec(
        select(TeamMirrorVolume).where(
            TeamMirrorVolume.team_id == uuid.UUID(team_id)
        )
    ).first()
    assert row is not None
    assert row.always_on is True
    assert row.volume_path == f"pending:{team_id}"


def test_patch_mirror_toggle_off_updates_existing_row(
    client: TestClient, db: Session
) -> None:
    """PATCH on a team that already has a mirror row updates in place — no second insert."""
    _a_id, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "ToggleOffMirror")

    # First PATCH creates the row with always_on=true.
    r_on = client.patch(
        _mirror_url(team_id), json={"always_on": True}, cookies=cookies_a
    )
    assert r_on.status_code == 200, r_on.text
    row_id_first = r_on.json()["id"]

    # Second PATCH flips it off.
    r_off = client.patch(
        _mirror_url(team_id), json={"always_on": False}, cookies=cookies_a
    )
    assert r_off.status_code == 200, r_off.text
    body = r_off.json()
    assert body["always_on"] is False
    # Same row, same volume_path — proving UPDATE, not INSERT.
    assert body["id"] == row_id_first
    assert body["volume_path"] == f"pending:{team_id}"

    # DB confirms exactly one row for this team (UNIQUE on team_id would
    # have rejected a second insert, but assert it explicitly).
    db.expire_all()
    rows = db.exec(
        select(TeamMirrorVolume).where(
            TeamMirrorVolume.team_id == uuid.UUID(team_id)
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].always_on is False


def test_patch_mirror_idempotent_same_value_twice(
    client: TestClient, db: Session
) -> None:
    """PATCH twice with the same value returns 200 both times — no warning, no extra row."""
    _a_id, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "IdempotentMirror")

    r1 = client.patch(
        _mirror_url(team_id), json={"always_on": True}, cookies=cookies_a
    )
    assert r1.status_code == 200, r1.text
    r2 = client.patch(
        _mirror_url(team_id), json={"always_on": True}, cookies=cookies_a
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["always_on"] is True
    assert r2.json()["id"] == r1.json()["id"]

    db.expire_all()
    rows = db.exec(
        select(TeamMirrorVolume).where(
            TeamMirrorVolume.team_id == uuid.UUID(team_id)
        )
    ).all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Auth / membership negatives
# ---------------------------------------------------------------------------


def test_patch_mirror_as_non_admin_returns_403(client: TestClient) -> None:
    """Member B (not admin) cannot toggle the mirror flag."""
    _a_id, _cookies_a, _b_id, cookies_b, team_id = _admin_with_member(
        client, "NonAdminMirror"
    )
    r = client.patch(
        _mirror_url(team_id), json={"always_on": True}, cookies=cookies_b
    )
    assert r.status_code == 403


def test_patch_mirror_as_non_member_returns_403(client: TestClient) -> None:
    """Outsider — never a member — cannot toggle the mirror flag."""
    _a_id, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "OutsiderMirror")

    _outsider_id, cookies_out = _signup(client)
    r = client.patch(
        _mirror_url(team_id), json={"always_on": True}, cookies=cookies_out
    )
    assert r.status_code == 403


def test_patch_mirror_unknown_team_returns_404(client: TestClient) -> None:
    """PATCH against a UUID that is not a team → 404 (does NOT auto-create a row)."""
    _a_id, cookies_a = _signup(client)
    bogus = uuid.uuid4()
    r = client.patch(
        _mirror_url(str(bogus)),
        json={"always_on": True},
        cookies=cookies_a,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Body validation (pydantic)
# ---------------------------------------------------------------------------


def test_patch_mirror_missing_always_on_returns_422(
    client: TestClient,
) -> None:
    """PATCH with empty body → 422 (always_on is required)."""
    _a_id, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "MissingFieldMirror")

    r = client.patch(_mirror_url(team_id), json={}, cookies=cookies_a)
    assert r.status_code == 422


def test_patch_mirror_non_bool_always_on_returns_422(
    client: TestClient,
) -> None:
    """PATCH with non-bool-coercible always_on (e.g. 'maybe') → 422.

    Note: pydantic v2's lax bool parsing accepts 'yes'/'no'/'true'/'false'
    as bools — the plan's 'yes' example does NOT actually 422. Use a value
    pydantic genuinely rejects to prove the validator is wired in.
    """
    _a_id, cookies_a = _signup(client)
    team_id = _create_team(client, cookies_a, "NonBoolMirror")

    r = client.patch(
        _mirror_url(team_id),
        json={"always_on": "maybe"},
        cookies=cookies_a,
    )
    assert r.status_code == 422


def test_patch_mirror_invalid_uuid_path_returns_422(
    client: TestClient,
) -> None:
    """PATCH with non-uuid in path → 422 (FastAPI path parsing)."""
    _a_id, cookies_a = _signup(client)
    r = client.patch(
        f"{settings.API_V1_STR}/teams/not-a-uuid/mirror",
        json={"always_on": True},
        cookies=cookies_a,
    )
    assert r.status_code == 422
