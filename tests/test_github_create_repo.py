"""Tests for GitHub repository creation endpoint.

Tests the POST /api/v1/teams/{team_id}/github/installations/{installation_id}/create-repository
endpoint to ensure proper validation and error handling.
"""

import pytest
from fastapi import HTTPException
from sqlmodel import Session


def test_create_github_repository_missing_repo_name(
    client,
    session: Session,
    setup_team_admin_user,
    setup_github_installation,
):
    """Test that missing repo_name returns 422."""
    team_id, user_id = setup_team_admin_user
    installation_id = setup_github_installation(team_id)

    response = client.post(
        f"/api/v1/teams/{team_id}/github/installations/{installation_id}/create-repository",
        json={
            "description": "Test repo",
            "private": True,
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "repo_name_required"


def test_create_github_repository_invalid_private_type(
    client,
    session: Session,
    setup_team_admin_user,
    setup_github_installation,
):
    """Test that non-boolean private returns 422."""
    team_id, user_id = setup_team_admin_user
    installation_id = setup_github_installation(team_id)

    response = client.post(
        f"/api/v1/teams/{team_id}/github/installations/{installation_id}/create-repository",
        json={
            "repo_name": "test-repo",
            "private": "yes",  # Should be boolean
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "private_must_be_boolean"


def test_create_github_repository_invalid_description_type(
    client,
    session: Session,
    setup_team_admin_user,
    setup_github_installation,
):
    """Test that non-string description returns 422."""
    team_id, user_id = setup_team_admin_user
    installation_id = setup_github_installation(team_id)

    response = client.post(
        f"/api/v1/teams/{team_id}/github/installations/{installation_id}/create-repository",
        json={
            "repo_name": "test-repo",
            "description": 123,  # Should be string
            "private": True,
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "description_must_be_string"


def test_create_github_repository_installation_not_found(
    client,
    session: Session,
    setup_team_admin_user,
):
    """Test that non-existent installation returns 404."""
    team_id, user_id = setup_team_admin_user

    response = client.post(
        f"/api/v1/teams/{team_id}/github/installations/999999/create-repository",
        json={
            "repo_name": "test-repo",
            "description": "Test description",
            "private": True,
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "installation_not_found"


def test_create_github_repository_not_team_admin(
    client,
    session: Session,
    setup_team_with_member,
    setup_github_installation,
):
    """Test that non-admin cannot create repositories."""
    team_id, admin_id, member_id = setup_team_with_member
    installation_id = setup_github_installation(team_id)

    # Try to create repo as a non-admin member
    client.headers = {"Authorization": f"Bearer {member_id}"}
    response = client.post(
        f"/api/v1/teams/{team_id}/github/installations/{installation_id}/create-repository",
        json={
            "repo_name": "test-repo",
            "description": "Test description",
            "private": True,
        },
    )
    assert response.status_code == 403 or response.status_code == 404


def test_create_github_repository_cross_team_installation(
    client,
    session: Session,
    setup_team_admin_user,
    setup_github_installation,
):
    """Test that admin of team A cannot create repo in team B's installation."""
    team_id_a, user_id_a = setup_team_admin_user

    # Create another team and its installation
    team_id_b, user_id_b = setup_team_admin_user()
    installation_id_b = setup_github_installation(team_id_b)

    # Try to create repo in team B's installation as team A's admin
    client.headers = {"Authorization": f"Bearer {user_id_a}"}
    response = client.post(
        f"/api/v1/teams/{team_id_a}/github/installations/{installation_id_b}/create-repository",
        json={
            "repo_name": "test-repo",
            "description": "Test description",
            "private": True,
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "installation_not_found"
