"""Shared-secret auth for backend ↔ orchestrator (D016).

Two-key acceptance: orchestrator accepts the request if it presents either
`ORCHESTRATOR_API_KEY` (current) or `ORCHESTRATOR_API_KEY_PREVIOUS` (optional).
This lets ops rotate the secret without simultaneously restarting both
services — set previous=old + current=new on the orchestrator first, deploy
the backend with the new key, then drop previous.

HTTP requests carry the key in the `X-Orchestrator-Key` header. WS upgrades
carry it as a `?key=` query string. The query-string strategy was chosen
over a `Sec-WebSocket-Protocol` subprotocol because the backend → orchestrator
hop is a server-to-server call wired with `httpx_ws`/`websockets`, where
query strings are trivially attachable. A future security audit may prefer
moving to a subprotocol-based scheme; the change would be local to this
module and the WS routes that consume it.

Failure modes:
  - HTTP missing/wrong key → 401 with no body content (does not leak which
    key would have been valid).
  - WS missing/wrong key → close 1008 reason='unauthorized' (per D016 + slice
    observability taxonomy: ERROR `orchestrator_ws_unauthorized` log line
    emits only the first 4 chars of the offered key, never the full value).
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Awaitable, Callable

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.websockets import WebSocket

from orchestrator.config import settings

logger = logging.getLogger("orchestrator")

# Paths that bypass shared-secret auth. /v1/health is the compose healthcheck
# target (Docker pings it every few seconds; gating it on the secret would
# leak the key into compose config), and /openapi/docs are disabled in main.py
# anyway. Everything else MUST present the key.
_PUBLIC_PATHS = frozenset(
    {
        "/v1/health",
    }
)


def _candidate_keys() -> tuple[str, ...]:
    """Return the keys that should be accepted right now.

    Excludes empty strings — an unset ORCHESTRATOR_API_KEY_PREVIOUS must NOT
    be treated as "any unset key works"; that would be a security regression.
    """
    keys: list[str] = []
    if settings.orchestrator_api_key:
        keys.append(settings.orchestrator_api_key)
    if settings.orchestrator_api_key_previous:
        keys.append(settings.orchestrator_api_key_previous)
    return tuple(keys)


def _key_matches(presented: str | None) -> bool:
    """Constant-time comparison against current + previous keys.

    Returns False if either side is empty — there is no "no auth required"
    fallback. Bare `==` would leak timing; `secrets.compare_digest` does not.
    """
    if not presented:
        return False
    candidates = _candidate_keys()
    if not candidates:
        return False
    # Iterate all candidates; do not short-circuit on the first match. Both
    # comparisons are constant-time within a candidate, but iterating fully
    # keeps the timing identical regardless of which key matched.
    matched = False
    for cand in candidates:
        if secrets.compare_digest(presented, cand):
            matched = True
    return matched


def _key_prefix(presented: str | None) -> str:
    """First 4 chars + ellipsis for log lines. Never log the full key."""
    if not presented:
        return "<missing>"
    return presented[:4] + "..."


class SharedSecretMiddleware(BaseHTTPMiddleware):
    """Reject HTTP requests that don't present a valid `X-Orchestrator-Key`.

    WS upgrades are handled by `authenticate_websocket` directly because
    Starlette middleware runs on the HTTP request that initiates the WS
    handshake — at that point the connection has not been accepted yet, and
    raising HTTPException here would cause the upgrade to 401 with a JSON
    body instead of the more correct WS-style close. Bypassing the middleware
    for WS scopes lets the WS endpoint emit `close(1008)` with the right
    reason string after `accept`-ing or before — depending on the framework
    semantics — and avoids double-handling.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)
        # Skip WS upgrades — the WS endpoint authenticates itself.
        if request.scope.get("type") == "websocket":
            return await call_next(request)
        presented = request.headers.get("x-orchestrator-key")
        if not _key_matches(presented):
            logger.warning(
                "orchestrator_http_unauthorized path=%s key_prefix=%s",
                request.url.path,
                _key_prefix(presented),
            )
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "unauthorized"},
            )
        return await call_next(request)


async def authenticate_websocket(websocket: WebSocket) -> bool:
    """Authenticate a WS handshake using the `?key=` query string.

    Returns True if the key matches and the caller should proceed with
    `await websocket.accept()`. Returns False after closing the WS with
    code 1008 reason='unauthorized'. Per the WS protocol, close-before-accept
    is the correct shape here — the upgrade has not been completed yet.
    """
    presented = websocket.query_params.get("key")
    if not _key_matches(presented):
        logger.error(
            "orchestrator_ws_unauthorized path=%s key_prefix=%s",
            websocket.url.path,
            _key_prefix(presented),
        )
        # close-before-accept: starlette permits close() prior to accept(),
        # which sends a 403 on the HTTP upgrade response. That's the WS
        # spec-compliant way to reject before the handshake completes.
        await websocket.close(code=1008, reason="unauthorized")
        return False
    return True


def require_boot_key() -> None:
    """Verify ORCHESTRATOR_API_KEY is set; exit 1 if not.

    Called from the lifespan startup. A misconfigured deployment that boots
    without a key would silently accept ANY presented key (because
    `_candidate_keys()` would return empty and `_key_matches` would return
    False for every request — but the operator deserves a loud failure at
    boot, not a silent service that 401s every backend call).
    """
    if not settings.orchestrator_api_key:
        raise HTTPException(
            status_code=500,
            detail="ORCHESTRATOR_API_KEY must be set at boot",
        )
