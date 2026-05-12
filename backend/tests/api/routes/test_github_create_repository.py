"""Route integration tests for POST /teams/{team_id}/github/installations/{id}/create-repository (M006 S04 T03).

Covers the full 1-of-5 HTTP response decision tree:
 1. Personal install + token present  → 200, orch receives X-GitHub-User-Token header
 2. Personal install + token missing  → 409 github_user_token_required, orch NOT called
 3. Org install                        → 200, orch called WITHOUT X-GitHub-User-Token (M005-sqm8et regression)
 4. Personal install + refresh_transient → 502 github_token_refresh_transient, orch NOT called
 5. Personal install + decrypt error  → 503 github_user_token_decrypt_failed, orch NOT called
 6. Personal install + bad_refresh_token → 409 with reason field in detail

Redaction rule (caplog sweep): no log record may contain the literal mocked token plaintext.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, call, patch

import httpx
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
# Orchestrator mock helpers
# ---------------------------------------------------------------------------

_MOCK_REPO = {
    "name": "test-repo",
    "full_name": "testuser/test-repo",
    "updated_at": "2026-05-12T00:00:00Z",
    "description": "Test repository",
}


def _build_orch_mock(status_code: int = 201, body: dict | None = None):
    """Return (mock_post_fn, context_manager_patcher).

    The mock_post_fn is the AsyncMock for `httpx.AsyncClient().post` so callers
    can introspect call_count and call_args after the request completes.
    """
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = body if body is not None else _MOCK_REPO

    mock_post = AsyncMock(return_value=mock_resp)

    mock_client_instance = MagicMock()
    mock_client_instance.post = mock_post
    # Also stub get for any other orch helpers that might be triggered
    mock_client_instance.get = AsyncMock(return_value=mock_resp)

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_client_instance)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    patcher = patch("app.api.routes.github.httpx.AsyncClient", return_value=mock_cm)
    return mock_post, patcher


def _url(team_id, installation_id) -> str:
    return f"{API}/teams/{team_id}/github/installations/{installation_id}/create-repository"


# ---------------------------------------------------------------------------
# T03 test 1: personal install + token present → orch receives X-GitHub-User-Token
# ---------------------------------------------------------------------------


def test_personal_install_forwards_user_token(
    client: TestClient, user_and_team: tuple[User, Team], db: Session, caplog
):
    """Personal installs: resolved token forwarded as X-GitHub-User-Token to the orchestrator."""
    user, team = user_and_team
    inst = _make_installation(db, team, account_type="User")
    cookies = _auth(client, user)

    fake_token = "ghu_faketoken_abc123"

    mock_post, orch_patcher = _build_orch_mock(status_code=201)

    with patch(
        "app.api.routes.github.get_user_access_token",
        new_callable=AsyncMock,
        return_value=fake_token,
    ), orch_patcher:
        r = client.post(
            _url(team.id, inst.installation_id),
            json={"repo_name": "my-repo", "private": True},
            cookies=cookies,
        )

    assert r.status_code == 200, r.text
    assert r.json()["full_name"] == "testuser/test-repo"

    # Orchestrator was called exactly once
    assert mock_post.call_count == 1, f"Expected 1 orch call, got {mock_post.call_count}"

    # The call carried X-GitHub-User-Token: <plaintext token>
    _, orch_kwargs = mock_post.call_args
    sent_headers = orch_kwargs.get("headers", {})
    assert sent_headers.get("X-GitHub-User-Token") == fake_token, (
        f"Expected X-GitHub-User-Token={fake_token!r} in {sent_headers!r}"
    )

    # Redaction sweep: token plaintext must not appear in any log record
    for record in caplog.records:
        assert fake_token not in record.getMessage(), (
            f"Token plaintext leaked in log: {record.getMessage()!r}"
        )


# ---------------------------------------------------------------------------
# T03 test 2: personal install + missing token row → 409, orch NOT called
# ---------------------------------------------------------------------------


def test_personal_install_missing_token_returns_409(
    client: TestClient, user_and_team: tuple[User, Team], db: Session
):
    """row_missing reason → 409 github_user_token_required; orchestrator must not be called."""
    user, team = user_and_team
    inst = _make_installation(db, team, account_type="User")
    cookies = _auth(client, user)

    mock_post, orch_patcher = _build_orch_mock()
    exc = UserTokenUnavailable(user_id=user.id, reason="row_missing")

    with patch(
        "app.api.routes.github.get_user_access_token",
        new_callable=AsyncMock,
        side_effect=exc,
    ), orch_patcher:
        r = client.post(
            _url(team.id, inst.installation_id),
            json={"repo_name": "my-repo", "private": True},
            cookies=cookies,
        )

    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "github_user_token_required"
    assert detail["installation_id"] == inst.installation_id
    assert detail["reason"] == "row_missing"

    # Orchestrator must NOT have been called
    assert mock_post.call_count == 0, (
        f"Orchestrator should not be called on 409 path; got {mock_post.call_count} calls"
    )


# ---------------------------------------------------------------------------
# T03 test 3: org install → orch called, X-GitHub-User-Token absent (M005-sqm8et regression)
# ---------------------------------------------------------------------------


def test_org_install_no_user_token_header(
    client: TestClient, user_and_team: tuple[User, Team], db: Session, caplog
):
    """Org installs: orchestrator is called, but X-GitHub-User-Token header must be absent."""
    user, team = user_and_team
    inst = _make_installation(db, team, account_type="Organization")
    cookies = _auth(client, user)

    mock_post, orch_patcher = _build_orch_mock(status_code=201)

    with patch(
        "app.api.routes.github.get_user_access_token", new_callable=AsyncMock
    ) as mock_guat, orch_patcher:
        r = client.post(
            _url(team.id, inst.installation_id),
            json={"repo_name": "my-repo", "private": True},
            cookies=cookies,
        )

    mock_guat.assert_not_called()
    assert r.status_code == 200, r.text

    # Orchestrator WAS called
    assert mock_post.call_count == 1, f"Expected 1 orch call, got {mock_post.call_count}"

    # X-GitHub-User-Token must not appear in the headers sent to the orchestrator
    _, orch_kwargs = mock_post.call_args
    sent_headers = orch_kwargs.get("headers", {})
    assert "X-GitHub-User-Token" not in sent_headers, (
        f"X-GitHub-User-Token must not be present for org install; headers={sent_headers!r}"
    )


# ---------------------------------------------------------------------------
# T03 test 4: personal install + refresh_transient → 502, orch NOT called
# ---------------------------------------------------------------------------


def test_personal_install_refresh_transient_returns_502(
    client: TestClient, user_and_team: tuple[User, Team], db: Session
):
    """refresh_transient → 502 github_token_refresh_transient; orchestrator must not be called."""
    user, team = user_and_team
    inst = _make_installation(db, team, account_type="User")
    cookies = _auth(client, user)

    mock_post, orch_patcher = _build_orch_mock()
    exc = UserTokenUnavailable(user_id=user.id, reason="refresh_transient")

    with patch(
        "app.api.routes.github.get_user_access_token",
        new_callable=AsyncMock,
        side_effect=exc,
    ), orch_patcher:
        r = client.post(
            _url(team.id, inst.installation_id),
            json={"repo_name": "my-repo", "private": True},
            cookies=cookies,
        )

    assert r.status_code == 502, r.text
    assert r.json()["detail"] == "github_token_refresh_transient"

    assert mock_post.call_count == 0, (
        f"Orchestrator should not be called on 502 path; got {mock_post.call_count} calls"
    )


# ---------------------------------------------------------------------------
# T03 test 5: personal install + decrypt error → 503, orch NOT called
# ---------------------------------------------------------------------------


def test_personal_install_decrypt_failure_returns_503(
    client: TestClient, user_and_team: tuple[User, Team], db: Session
):
    """GitHubUserTokenDecryptError → 503 github_user_token_decrypt_failed; orchestrator must not be called."""
    user, team = user_and_team
    inst = _make_installation(db, team, account_type="User")
    cookies = _auth(client, user)

    mock_post, orch_patcher = _build_orch_mock()
    exc = GitHubUserTokenDecryptError(user_id=user.id)

    with patch(
        "app.api.routes.github.get_user_access_token",
        new_callable=AsyncMock,
        side_effect=exc,
    ), orch_patcher:
        r = client.post(
            _url(team.id, inst.installation_id),
            json={"repo_name": "my-repo", "private": True},
            cookies=cookies,
        )

    assert r.status_code == 503, r.text
    assert r.json()["detail"] == "github_user_token_decrypt_failed"

    assert mock_post.call_count == 0, (
        f"Orchestrator should not be called on 503 path; got {mock_post.call_count} calls"
    )


# ---------------------------------------------------------------------------
# T03 test 6: personal install + bad_refresh_token → 409 with reason in detail
# ---------------------------------------------------------------------------


def test_personal_install_bad_refresh_token_includes_reason(
    client: TestClient, user_and_team: tuple[User, Team], db: Session
):
    """bad_refresh_token reason surfaces in the 409 detail body so the frontend CTA can branch."""
    user, team = user_and_team
    inst = _make_installation(db, team, account_type="User")
    cookies = _auth(client, user)

    mock_post, orch_patcher = _build_orch_mock()
    exc = UserTokenUnavailable(user_id=user.id, reason="bad_refresh_token")

    with patch(
        "app.api.routes.github.get_user_access_token",
        new_callable=AsyncMock,
        side_effect=exc,
    ), orch_patcher:
        r = client.post(
            _url(team.id, inst.installation_id),
            json={"repo_name": "my-repo", "private": True},
            cookies=cookies,
        )

    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "github_user_token_required"
    assert detail["installation_id"] == inst.installation_id
    assert detail["reason"] == "bad_refresh_token"

    assert mock_post.call_count == 0, (
        f"Orchestrator should not be called on 409 path; got {mock_post.call_count} calls"
    )


# ---------------------------------------------------------------------------
# Validation / 404 tests (pre-existing behavior, S04 regression suite)
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

    url = _url(team.id, inst.installation_id)
    r = client.post(url, json={"description": "Test", "private": False}, cookies=cookies)

    assert r.status_code == 422
    assert r.json()["detail"] == "repo_name_required"


def test_create_repository_invalid_private_type(
    client: TestClient, user_and_team: tuple[User, Team], db: Session
):
    user, team = user_and_team
    inst = _make_installation(db, team, account_type="Organization")
    cookies = _auth(client, user)

    url = _url(team.id, inst.installation_id)
    r = client.post(
        url,
        json={"repo_name": "test-repo", "description": "Test", "private": "yes"},
        cookies=cookies,
    )

    assert r.status_code == 422
    assert r.json()["detail"] == "private_must_be_boolean"
