"""Minimal GitHub OAuth mock used by M006/S02/T04 e2e.

Mounted into a python:3.12-slim sibling container on `perpetuity_default`
so the ephemeral backend under test can reach `http://mock-github-oauth-<uuid>:8090`
instead of the real `github.com` and `api.github.com`.

Routes mimic the GitHub endpoints the backend calls during the OAuth
install-callback flow:

    POST /login/oauth/access_token
        Validates the request body contains client_id, client_secret, code.
        Returns the canned token payload (access_token, refresh_token,
        expires_in, refresh_token_expires_in, scope, token_type).

    GET  /user/installations
        Validates Authorization header is Bearer <access_token>.
        Returns a canned installations list with INSTALLATION_ID.

    GET  /user
        Validates Authorization header is Bearer <access_token>.
        Returns a canned user payload with GITHUB_USER_ID and login.

    GET  /healthz
        No-auth liveness probe.

Env contract:

    MOCK_CLIENT_ID       — the GitHub App OAuth client_id the backend sends
    MOCK_CLIENT_SECRET   — the GitHub App OAuth client_secret the backend sends
    MOCK_CODE            — the one-time code GitHub would send to the callback
    MOCK_ACCESS_TOKEN    — ghu_...-shaped token to return from token exchange
    MOCK_REFRESH_TOKEN   — ghr_...-shaped refresh token to return
    MOCK_EXPIRES_IN      — int seconds until access token expires (e.g. 28800)
    MOCK_REFRESH_EXPIRES_IN — int seconds until refresh token expires
    MOCK_SCOPE           — OAuth scopes string (e.g. "repo,read:user")
    MOCK_INSTALLATION_ID — int installation id to return in /user/installations
    MOCK_GITHUB_USER_ID  — int GitHub user id to return in GET /user
    MOCK_GITHUB_LOGIN    — GitHub login string to return in GET /user
"""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Request


def _env_required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"mock_github_oauth: env {name!r} is required")
    return val


MOCK_CLIENT_ID = _env_required("MOCK_CLIENT_ID")
MOCK_CLIENT_SECRET = _env_required("MOCK_CLIENT_SECRET")
MOCK_CODE = _env_required("MOCK_CODE")
MOCK_ACCESS_TOKEN = _env_required("MOCK_ACCESS_TOKEN")
MOCK_REFRESH_TOKEN = _env_required("MOCK_REFRESH_TOKEN")
MOCK_EXPIRES_IN = int(_env_required("MOCK_EXPIRES_IN"))
MOCK_REFRESH_EXPIRES_IN = int(_env_required("MOCK_REFRESH_EXPIRES_IN"))
MOCK_SCOPE = os.environ.get("MOCK_SCOPE", "repo,read:user")
MOCK_INSTALLATION_ID = int(_env_required("MOCK_INSTALLATION_ID"))
MOCK_GITHUB_USER_ID = int(_env_required("MOCK_GITHUB_USER_ID"))
MOCK_GITHUB_LOGIN = _env_required("MOCK_GITHUB_LOGIN")

app = FastAPI(title="mock-github-oauth")


def _bearer_token(request: Request) -> str:
    """Extract and return the Bearer token from Authorization header."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing_bearer")
    return auth[len("Bearer "):]


@app.post("/login/oauth/access_token")
async def token_exchange(request: Request) -> dict:
    """Simulate GitHub's OAuth token endpoint.

    Validates client_id, client_secret, and code. Returns the canned
    token payload. GitHub returns JSON when Accept: application/json.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="bad_request_body")

    if body.get("client_id") != MOCK_CLIENT_ID:
        raise HTTPException(status_code=422, detail="bad_client_id")
    if body.get("client_secret") != MOCK_CLIENT_SECRET:
        raise HTTPException(status_code=422, detail="bad_client_secret")
    if body.get("code") != MOCK_CODE:
        raise HTTPException(status_code=422, detail="bad_code")

    return {
        "access_token": MOCK_ACCESS_TOKEN,
        "refresh_token": MOCK_REFRESH_TOKEN,
        "expires_in": MOCK_EXPIRES_IN,
        "refresh_token_expires_in": MOCK_REFRESH_EXPIRES_IN,
        "scope": MOCK_SCOPE,
        "token_type": "bearer",
    }


@app.get("/user/installations")
async def user_installations(request: Request) -> dict:
    """Simulate GitHub GET /user/installations."""
    token = _bearer_token(request)
    if token != MOCK_ACCESS_TOKEN:
        raise HTTPException(status_code=401, detail="bad_token")
    return {
        "total_count": 1,
        "installations": [
            {
                "id": MOCK_INSTALLATION_ID,
                "app_slug": "test-app",
                "account": {
                    "login": MOCK_GITHUB_LOGIN,
                    "type": "User",
                },
            }
        ],
    }


@app.get("/user")
async def get_user(request: Request) -> dict:
    """Simulate GitHub GET /user — returns the authenticated user's profile."""
    token = _bearer_token(request)
    if token != MOCK_ACCESS_TOKEN:
        raise HTTPException(status_code=401, detail="bad_token")
    return {
        "id": MOCK_GITHUB_USER_ID,
        "login": MOCK_GITHUB_LOGIN,
        "type": "User",
    }


@app.get("/app/installations/{installation_id}")
async def app_installation_lookup(installation_id: int) -> dict:
    """Simulate GitHub GET /app/installations/{id} for orchestrator lookup.

    The orchestrator calls this with an RS256 App JWT. The mock intentionally
    skips JWT verification — the e2e is testing token persistence, not the
    orchestrator's JWT minting logic.
    """
    return {
        "id": installation_id,
        "account": {
            "login": MOCK_GITHUB_LOGIN,
            "type": "Organization",
        },
    }


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
