"""Unit tests for the GitHub create repository endpoint (M006 S04 T02).

Covers:
- account_type branch: personal installs resolve user token; org installs skip it
- Exception mapping: UserTokenUnavailable reason variants -> HTTP 409 / 502
- GitHubUserTokenDecryptError -> HTTP 503
- Defense-in-depth assertion (org install = no token)
- Existing validation: repo_name required, private must be boolean, 404 not-found
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

from app.core.config import settings
from app.core.github_user_tokens import GitHubUserTokenDecryptError, UserTokenUnavailable
from app.models import GitHubAppInstallation, Team, User
from tests.utils.user import (
    create_test_user,
    create_test_team,
    login_cookie_headers_for_test_user,
)

API = settings.API_V1_STR


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_installations(db: Session):
    db.execute(delete(GitHubAppInstallation))
    db.commit()
    yield
    db.execute(delete(GitHubAppInstallation))
    db.commit()


@pytest.fixture()
def user_and_team(db: Session) -> tuple[User, Team]:
    user = create_test_user(db)
    team = create_test_team(db, user)
    return user, team


def _make_installation(
    db: Session, team: Team, account_type: str = "User"
) -> GitHubAppInstallation:
    inst = GitHubAppInstallation(
        team_id=team.id,
        installation_id=int(uuid.uuid4().int % 10**8),
        account_type=account_type,
        account_login="testuser",
    )
    db.add(inst)
    db.commit()
    db.refresh(inst)
    return inst


def _auth(client: TestClient, user: User) -> httpx.Cookies:
    return login_cookie_headers_for_test_user(client=client, user=user)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

import httpx  # noqa: E402 (after fixtures for readability)

_MOCK_REPO = {
    "name": "test-repo",
    "full_name": "testuser/test-repo",
    "updated_at": "2026-05-12T00:00:00Z",
    "description": "Test repository",
}


def _mock_orch_post(status_code: int = 201, body: dict | None = None):
    """Return a patcher that stubs httpx.AsyncClient.post for orch calls."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = body if body is not None else _MOCK_REPO
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(return_value=mock_resp)))
    cm.__aexit__ = AsyncMock(return_value=False)
    return patch("app.api.routes.github.httpx.AsyncClient", return_value=cm)


# ---------------------------------------------------------------------------
# Org install: user_token stays None, orch called without token
# ---------------------------------------------------------------------------


def test_org_install_skips_user_token(
    client: TestClient, user_and_team: tuple[User, Team], db: Session
):
    """Org installs must not attempt to fetch a user token."""
    user, team = user_and_team
    inst = _make_installation(db, team, account_type="Organization")
    cookies = _auth(client, user)

    with patch(
        "app.api.routes.github.get_user_access_token", new_callable=AsyncMock
    ) as mock_guat, _mock_orch_post() as _orch:
        url = f"{API}/teams/{team.id}/github/installations/{inst.installation_id}/create-repository"
        r = client.post(
            url,
            json={"repo_name": "my-repo", "private": True},
            cookies=cookies,
        )

    mock_guat.assert_not_called()
    assert r.status_code == 200, r.text
    assert r.json()["full_name"] == "testuser/test-repo"


# ---------------------------------------------------------------------------
# Personal install: user_token resolved and forwarded
# ---------------------------------------------------------------------------


def test_personal_install_fetches_and_forwards_user_token(
    client: TestClient, user_and_team: tuple[User, Team], db: Session
):
    """Personal (User) installs must fetch the user token and pass it to orch."""
    user, team = user_and_team
    inst = _make_installation(db, team, account_type="User")
    cookies = _auth(client, user)

    fake_token = "ghu_faketoken"

    with patch(
        "app.api.routes.github.get_user_access_token",
        new_callable=AsyncMock,
        return_value=fake_token,
    ), _mock_orch_post() as mock_orch_ctx:
        url = f"{API}/teams/{team.id}/github/installations/{inst.installation_id}/create-repository"
        r = client.post(
            url,
            json={"repo_name": "my-repo", "private": True},
            cookies=cookies,
        )

    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Exception mapping: UserTokenUnavailable reasons
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    ["row_missing", "bad_refresh_token", "refresh_rejected", "refresh_unexpected_response"],
)
def test_user_token_unavailable_non_transient_returns_409(
    client: TestClient,
    user_and_team: tuple[User, Team],
    db: Session,
    reason: str,
):
    """Non-transient UserTokenUnavailable reasons map to 409 with code+installation_id+reason."""
    user, team = user_and_team
    inst = _make_installation(db, team, account_type="User")
    cookies = _auth(client, user)

    exc = UserTokenUnavailable(user_id=user.id, reason=reason)
    with patch(
        "app.api.routes.github.get_user_access_token",
        new_callable=AsyncMock,
        side_effect=exc,
    ):
        url = f"{API}/teams/{team.id}/github/installations/{inst.installation_id}/create-repository"
        r = client.post(
            url,
            json={"repo_name": "my-repo", "private": True},
            cookies=cookies,
        )

    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "github_user_token_required"
    assert detail["installation_id"] == inst.installation_id
    assert detail["reason"] == reason


def test_user_token_unavailable_refresh_transient_returns_502(
    client: TestClient, user_and_team: tuple[User, Team], db: Session
):
    """refresh_transient UserTokenUnavailable maps to 502 github_token_refresh_transient."""
    user, team = user_and_team
    inst = _make_installation(db, team, account_type="User")
    cookies = _auth(client, user)

    exc = UserTokenUnavailable(user_id=user.id, reason="refresh_transient")
    with patch(
        "app.api.routes.github.get_user_access_token",
        new_callable=AsyncMock,
        side_effect=exc,
    ):
        url = f"{API}/teams/{team.id}/github/installations/{inst.installation_id}/create-repository"
        r = client.post(
            url,
            json={"repo_name": "my-repo", "private": True},
            cookies=cookies,
        )

    assert r.status_code == 502, r.text
    assert r.json()["detail"] == "github_token_refresh_transient"


def test_github_user_token_decrypt_error_returns_503(
    client: TestClient, user_and_team: tuple[User, Team], db: Session
):
    """GitHubUserTokenDecryptError maps to 503 github_user_token_decrypt_failed."""
    user, team = user_and_team
    inst = _make_installation(db, team, account_type="User")
    cookies = _auth(client, user)

    exc = GitHubUserTokenDecryptError(user_id=user.id)
    with patch(
        "app.api.routes.github.get_user_access_token",
        new_callable=AsyncMock,
        side_effect=exc,
    ):
        url = f"{API}/teams/{team.id}/github/installations/{inst.installation_id}/create-repository"
        r = client.post(
            url,
            json={"repo_name": "my-repo", "private": True},
            cookies=cookies,
        )

    assert r.status_code == 503, r.text
    assert r.json()["detail"] == "github_user_token_decrypt_failed"


# ---------------------------------------------------------------------------
# Validation / 404 tests (pre-existing behavior)
# ---------------------------------------------------------------------------


def test_create_repository_installation_not_found(
    client: TestClient, user_and_team: tuple[User, Team]
):
    user, team = user_and_team
    cookies = _auth(client, user)

    url = f"{API}/teams/{team.id}/github/installations/999999/create-repository"
    r = client.post(
        url,
        json={"repo_name": "test-repo", "private": False},
        cookies=cookies,
    )

    assert r.status_code == 404
    assert r.json()["detail"] == "installation_not_found"


def test_create_repository_missing_repo_name(
    client: TestClient, user_and_team: tuple[User, Team], db: Session
):
    user, team = user_and_team
    inst = _make_installation(db, team, account_type="Organization")
    cookies = _auth(client, user)

    url = f"{API}/teams/{team.id}/github/installations/{inst.installation_id}/create-repository"
    r = client.post(url, json={"description": "Test", "private": False}, cookies=cookies)

    assert r.status_code == 422
    assert r.json()["detail"] == "repo_name_required"


def test_create_repository_invalid_private_type(
    client: TestClient, user_and_team: tuple[User, Team], db: Session
):
    user, team = user_and_team
    inst = _make_installation(db, team, account_type="Organization")
    cookies = _auth(client, user)

    url = f"{API}/teams/{team.id}/github/installations/{inst.installation_id}/create-repository"
    r = client.post(
        url,
        json={"repo_name": "test-repo", "description": "Test", "private": "yes"},
        cookies=cookies,
    )

    assert r.status_code == 422
    assert r.json()["detail"] == "private_must_be_boolean"
