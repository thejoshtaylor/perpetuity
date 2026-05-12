"""Unit tests for _orch_create_repository header forwarding.

Verifies:
  (a) calling with user_token=None produces a request without X-GitHub-User-Token
  (b) calling with user_token="ghu_test" produces a request with
      X-GitHub-User-Token: ghu_test
  (c) X-Orchestrator-Key is always present regardless of user_token
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routes.github import _orch_create_repository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_response(status_code: int = 201, body: dict | None = None) -> MagicMock:
    """Return a mock httpx.Response."""
    if body is None:
        body = {"full_name": "testuser/test-repo", "name": "test-repo"}
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_orch_create_repository_no_user_token_omits_header():
    """(a) X-GitHub-User-Token must NOT be present when user_token=None."""
    captured_headers: dict[str, str] = {}

    async def _fake_post(url, *, headers, json, **kwargs):  # noqa: ANN001
        captured_headers.update(headers)
        return _make_mock_response()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=_fake_post):
        await _orch_create_repository(
            installation_id=123,
            repo_name="test-repo",
            description=None,
            private=False,
            user_token=None,
        )

    assert "X-GitHub-User-Token" not in captured_headers
    assert "X-Orchestrator-Key" in captured_headers


@pytest.mark.anyio
async def test_orch_create_repository_with_user_token_sets_header():
    """(b) X-GitHub-User-Token must equal user_token when provided."""
    captured_headers: dict[str, str] = {}

    async def _fake_post(url, *, headers, json, **kwargs):  # noqa: ANN001
        captured_headers.update(headers)
        return _make_mock_response()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=_fake_post):
        await _orch_create_repository(
            installation_id=123,
            repo_name="test-repo",
            description=None,
            private=False,
            user_token="ghu_test",
        )

    assert captured_headers.get("X-GitHub-User-Token") == "ghu_test"
    assert "X-Orchestrator-Key" in captured_headers


@pytest.mark.anyio
async def test_orch_create_repository_orchestrator_key_always_present():
    """(c) X-Orchestrator-Key must be present regardless of user_token."""
    for token in (None, "ghu_test"):
        captured_headers: dict[str, str] = {}

        async def _fake_post(url, *, headers, json, **kwargs):  # noqa: ANN001
            captured_headers.update(headers)
            return _make_mock_response()

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=_fake_post):
            await _orch_create_repository(
                installation_id=456,
                repo_name="another-repo",
                description="desc",
                private=True,
                user_token=token,
            )

        assert "X-Orchestrator-Key" in captured_headers, (
            f"X-Orchestrator-Key missing when user_token={token!r}"
        )
