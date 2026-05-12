"""Orchestrator GitHub install routes (M004 / S02 / T03).

Surfaces:

    GET /v1/installations/{installation_id}/token
        -> {"token": <str>, "source": "cache"|"mint", "expires_at": <iso>}
        Cache-first via Redis (50-min TTL). Mints from GitHub on miss.

    GET /v1/installations/{installation_id}/lookup
        -> {"account_login": <str>, "account_type": <str>}
        Used by the backend install-callback to attribute the install row
        to a GitHub account before persisting.

Auth: inherited from the orchestrator-wide SharedSecretMiddleware. The
backend presents `X-Orchestrator-Key`; nothing else can reach these routes.

Error mapping:
    503 github_app_not_configured  — credential row missing or NULL
    503 system_settings_decrypt_failed — Fernet decrypt failed (handled
        by the global SystemSettingDecryptError handler in main.py)
    502 github_token_mint_failed   — GitHub returned 4xx/5xx, transport
        error, or a malformed body. `reason` carries the short label.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

from orchestrator.github_tokens import (
    InstallationTokenMintFailed,
    _NotConfigured,
    get_installation_token,
    lookup_installation,
)

logger = logging.getLogger("orchestrator")

router = APIRouter(prefix="/v1/installations", tags=["github"])


def _redis_client_from(request: Request) -> Any:
    """Pull the underlying redis.asyncio client off the registry.

    We re-use the existing RedisSessionRegistry singleton wired by the
    lifespan rather than constructing a second client — same pool, same
    auth, just a different keyspace prefix.
    """
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        return None
    # The registry exposes its underlying client as a private attribute
    # `_client` (see redis_client.py). We deliberately reach for it here
    # instead of widening the registry's public surface — the install
    # token cache is the only consumer outside the session map and giving
    # it its own getter would invite drift.
    return getattr(registry, "_client", None)


@router.get("/{installation_id}/token")
async def get_installation_token_route(
    installation_id: int,
    request: Request,
) -> dict[str, Any]:
    redis_client = _redis_client_from(request)
    pg_pool = getattr(request.app.state, "pg", None)
    try:
        return await get_installation_token(
            installation_id,
            redis_client=redis_client,
            pg_pool=pg_pool,
        )
    except _NotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=exc.detail,
        )
    except InstallationTokenMintFailed as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "detail": "github_token_mint_failed",
                "status": exc.status,
                "reason": exc.reason,
            },
        )


@router.get("/{installation_id}/lookup")
async def lookup_installation_route(
    installation_id: int,
    request: Request,
) -> dict[str, str]:
    pg_pool = getattr(request.app.state, "pg", None)
    try:
        return await lookup_installation(installation_id, pg_pool=pg_pool)
    except _NotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=exc.detail,
        )
    except InstallationTokenMintFailed as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "detail": "github_lookup_failed",
                "status": exc.status,
                "reason": exc.reason,
            },
        )


@router.get("/{installation_id}/repositories")
async def list_installation_repositories_route(
    installation_id: int,
    request: Request,
) -> list[dict[str, Any]]:
    """List repositories accessible via a GitHub App installation.

    Returns a list of repositories sorted by most recently updated at the top,
    each with: {name, full_name, updated_at, description, ...}

    Fetches from GitHub API using an installation token.
    """
    pg_pool = getattr(request.app.state, "pg", None)
    try:
        token_response = await get_installation_token(
            installation_id,
            redis_client=_redis_client_from(request),
            pg_pool=pg_pool,
        )
    except _NotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=exc.detail,
        )
    except InstallationTokenMintFailed as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "detail": "github_list_repositories_failed",
                "status": exc.status,
                "reason": exc.reason,
            },
        )

    token = token_response.get("token")
    if not token:
        logger.warning(
            "github_list_repositories_failed installation_id=%s reason=no_token",
            installation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="github_list_repositories_failed",
        )

    # Fetch repositories from GitHub API, paginated
    import httpx
    
    all_repos = []
    page = 1
    per_page = 100
    
    try:
        async with httpx.AsyncClient() as client:
            while True:
                r = await client.get(
                    "https://api.github.com/installation/repositories",
                    headers={
                        "Authorization": f"token {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                    params={
                        "page": page,
                        "per_page": per_page,
                        "sort": "updated",
                        "direction": "desc",
                    },
                    timeout=30.0,
                )
                
                if r.status_code != 200:
                    logger.warning(
                        "github_list_repositories_failed installation_id=%s reason=api_status status=%s",
                        installation_id,
                        r.status_code,
                    )
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail="github_list_repositories_failed",
                    )
                
                body = r.json()
                repos = body.get("repositories", [])
                
                if not repos:
                    break
                
                all_repos.extend(repos)
                page += 1
    except httpx.HTTPError as exc:
        logger.warning(
            "github_list_repositories_failed installation_id=%s reason=transport err=%s",
            installation_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="github_list_repositories_failed",
        )

    # Transform to minimal schema: {name, full_name, updated_at, description}
    result = [
        {
            "name": repo.get("name"),
            "full_name": repo.get("full_name"),
            "updated_at": repo.get("updated_at"),
            "description": repo.get("description"),
        }
        for repo in all_repos
    ]
    
    return result
