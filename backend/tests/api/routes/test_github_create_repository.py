"""Unit tests for the GitHub create repository endpoint.

Tests the backend router that delegates to the orchestrator's
POST /v1/installations/{installation_id}/create-repository endpoint.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

from app.core.config import settings
from app.models import GitHubAppInstallation, Team, User
from tests.utils.user import create_test_user, create_test_team

API = settings.API_V1_STR


@pytest.fixture(autouse=True)
def _clean_installations(db: Session):
    """Clean GitHub app installations before and after each test."""
    db.execute(delete(GitHubAppInstallation))
    db.commit()
    yield
    db.execute(delete(GitHubAppInstallation))
    db.commit()


@pytest.fixture
def user_and_team(db: Session) -> tuple[User, Team]:
    """Create a test user and team."""
    user = create_test_user(db)
    team = create_test_team(db, user)
    return user, team


@pytest.fixture
def installation(db: Session, user_and_team: tuple[User, Team]) -> GitHubAppInstallation:
    """Create a test GitHub app installation."""
    user, team = user_and_team
    inst = GitHubAppInstallation(
        team_id=team.id,
        installation_id=123456,
        account_type="User",
        account_login="testuser",
    )
    db.add(inst)
    db.commit()
    db.refresh(inst)
    return inst


def test_create_repository_success(
    client: TestClient, user_and_team: tuple[User, Team], installation: GitHubAppInstallation
):
    """Test successful repository creation."""
    user, team = user_and_team
    
    # Sign in as user
    client.cookies.clear()
    r = client.post(
        f"{API}/login/access-token",
        data={"username": user.email, "password": "password"},
    )
    assert r.status_code == 200
    
    # Mock the orchestrator response
    mock_repo = {
        "name": "test-repo",
        "full_name": "testuser/test-repo",
        "updated_at": "2026-05-12T00:00:00Z",
        "description": "Test repository",
    }
    
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_response = AsyncMock()
        mock_response.status_code = 201
        mock_response.json.return_value = mock_repo
        mock_post.return_value = mock_response
        
        # Create repository
        url = f"{API}/teams/{team.id}/github/installations/{installation.installation_id}/create-repository"
        r = client.post(
            url,
            json={
                "repo_name": "test-repo",
                "description": "Test repository",
                "private": False,
            },
        )
    
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["name"] == "test-repo"
    assert data["full_name"] == "testuser/test-repo"


def test_create_repository_missing_repo_name(
    client: TestClient, user_and_team: tuple[User, Team], installation: GitHubAppInstallation
):
    """Test validation: repo_name is required."""
    user, team = user_and_team
    
    # Sign in
    client.cookies.clear()
    r = client.post(
        f"{API}/login/access-token",
        data={"username": user.email, "password": "password"},
    )
    assert r.status_code == 200
    
    # Try without repo_name
    url = f"{API}/teams/{team.id}/github/installations/{installation.installation_id}/create-repository"
    r = client.post(url, json={"description": "Test", "private": False})
    
    assert r.status_code == 422
    assert r.json()["detail"] == "repo_name_required"


def test_create_repository_invalid_private_type(
    client: TestClient, user_and_team: tuple[User, Team], installation: GitHubAppInstallation
):
    """Test validation: private must be boolean."""
    user, team = user_and_team
    
    # Sign in
    client.cookies.clear()
    r = client.post(
        f"{API}/login/access-token",
        data={"username": user.email, "password": "password"},
    )
    assert r.status_code == 200
    
    # Try with invalid private
    url = f"{API}/teams/{team.id}/github/installations/{installation.installation_id}/create-repository"
    r = client.post(
        url,
        json={
            "repo_name": "test-repo",
            "description": "Test",
            "private": "yes",
        },
    )
    
    assert r.status_code == 422
    assert r.json()["detail"] == "private_must_be_boolean"


def test_create_repository_installation_not_found(
    client: TestClient, user_and_team: tuple[User, Team]
):
    """Test: installation_not_found when installation doesn't exist."""
    user, team = user_and_team
    
    # Sign in
    client.cookies.clear()
    r = client.post(
        f"{API}/login/access-token",
        data={"username": user.email, "password": "password"},
    )
    assert r.status_code == 200
    
    # Try with non-existent installation
    url = f"{API}/teams/{team.id}/github/installations/999999/create-repository"
    r = client.post(
        url,
        json={
            "repo_name": "test-repo",
            "description": "Test",
            "private": False,
        },
    )
    
    assert r.status_code == 404
    assert r.json()["detail"] == "installation_not_found"
