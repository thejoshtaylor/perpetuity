"""Orchestrator GitHub App credential boundary (M004 / S02 / T03).

Owns the path from the encrypted system_settings row -> GitHub App JWT ->
short-lived installation token, plus the Redis cache layer that keeps the
50-minute (10-min safety margin under GitHub's 1h TTL) reuse window from
hammering GitHub on every clone.

Why the orchestrator and not the backend: the GitHub App private key is
the credential the workspace clone path will use; co-locating credential
read with the clone path keeps the secret confined to one process and
matches MEM225 (installation token caching strategy) + MEM228 (credential
discipline in two-hop clone).

Public surface:

    InstallationTokenMintFailed                    -- exception, status+reason
    mint_installation_token(installation_id, *)    -- one-shot mint, no cache
    get_installation_token(installation_id, *)     -- cache-first, mint on miss
    lookup_installation(installation_id, *)        -- {account_login, account_type}

Logging discipline (slice observability contract, MEM134):
    INFO  installation_token_minted     installation_id=<id> token_prefix=<4>...
    INFO  installation_token_cache_hit  installation_id=<id> token_prefix=<4>...
    ERROR installation_token_mint_failed installation_id=<id> status=<code> reason=<short>

Token plaintext NEVER appears in logs — only the first-4-char prefix. The
PEM private key NEVER appears in any log line, exception message, or
response body.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import httpx
import jwt
import redis.asyncio as redis_async
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError

from orchestrator.config import settings
from orchestrator.encryption import SystemSettingDecryptError, decrypt_setting
from orchestrator.volume_store import get_pool

logger = logging.getLogger("orchestrator")


# system_settings row keys. Co-located here (rather than imported from the
# backend admin module) because the orchestrator package is its own
# deployable; pulling in app.api.routes.admin would couple us to backend
# imports we don't otherwise need.
_GITHUB_APP_PRIVATE_KEY_KEY = "github_app_private_key"
_GITHUB_APP_ID_KEY = "github_app_id"

# Redis cache contract.
_CACHE_KEY_PREFIX = "gh:installtok:"
_CACHE_TTL_SECONDS = 50 * 60  # 50 minutes — 10-min safety margin under 1h.

# httpx timeout for outgoing GitHub calls. Aligns with the slice plan's
# failure-mode table (10s total, 3s connect).
_GITHUB_TIMEOUT = httpx.Timeout(10.0, connect=3.0)

# JWT shape. App JWTs are short-lived (10 min max per GitHub docs); we
# claim 9 minutes with a 60-second backdated iat for clock-skew tolerance.
_APP_JWT_ALGO = "RS256"
_APP_JWT_LIFETIME_SECONDS = 540
_APP_JWT_CLOCK_SKEW_SECONDS = 60


class InstallationTokenMintFailed(Exception):
    """Raised when the GitHub /access_tokens call returns non-2xx or a malformed body.

    `status` is the HTTP status code (or 0 for transport / malformed body).
    `reason` is a short, log-safe string identifying the failure mode —
    never includes the response body verbatim. Plaintext token (if any
    sneaked into the body) is NEVER part of `reason`.
    """

    def __init__(self, status: int, reason: str) -> None:
        self.status = status
        self.reason = reason
        super().__init__(f"installation_token_mint_failed status={status} reason={reason}")


def _token_prefix(token: str | None) -> str:
    """Log-safe 4-char prefix of an installation token (or '<missing>')."""
    if not token or not isinstance(token, str):
        return "<missing>"
    return token[:4] + "..."


def _cache_key(installation_id: int) -> str:
    return f"{_CACHE_KEY_PREFIX}{installation_id}"


# ---------------------------------------------------------------------------
# Credential read
# ---------------------------------------------------------------------------


async def _load_github_app_credentials(
    pg_pool: asyncpg.Pool,
) -> tuple[int, str]:
    """Read (app_id, private_key_pem) from system_settings.

    Both rows must exist with non-NULL value/value_encrypted. Either side
    missing surfaces as `github_app_not_configured` (503 to the caller).
    Fernet decrypt failure surfaces as `SystemSettingDecryptError` —
    NEVER caught here so the global handler can emit the structured
    ERROR + 503.

    asyncpg returns JSONB columns as raw JSON text by default (no codec
    registered on the pool). The app_id row stores `123` so we json.loads
    the string and assert int.
    """
    sql = (
        "SELECT key, value, value_encrypted FROM system_settings "
        "WHERE key = ANY($1::text[])"
    )
    keys = [_GITHUB_APP_PRIVATE_KEY_KEY, _GITHUB_APP_ID_KEY]
    try:
        async with pg_pool.acquire() as conn:
            rows = await conn.fetch(sql, keys)
    except (OSError, asyncpg.PostgresError, asyncpg.InterfaceError) as exc:
        logger.warning(
            "github_app_credentials_lookup_failed reason=%s",
            type(exc).__name__,
        )
        # 503 — same shape the volume store uses on pg trouble; the global
        # WorkspaceVolumeStoreUnavailable handler will not fire here, so we
        # raise a transport-shaped HTTPException via the upstream caller.
        raise _NotConfigured("github_app_not_configured") from exc

    by_key: dict[str, asyncpg.Record] = {row["key"]: row for row in rows}

    pk_row = by_key.get(_GITHUB_APP_PRIVATE_KEY_KEY)
    if pk_row is None or pk_row["value_encrypted"] is None:
        raise _NotConfigured("github_app_not_configured")

    id_row = by_key.get(_GITHUB_APP_ID_KEY)
    if id_row is None or id_row["value"] is None:
        raise _NotConfigured("github_app_not_configured")

    # Decrypt. SystemSettingDecryptError(key=...) propagates uncaught.
    try:
        private_key_pem = decrypt_setting(bytes(pk_row["value_encrypted"]))
    except SystemSettingDecryptError as exc:
        # Re-raise with the row key attached so the global handler logs
        # `key=github_app_private_key`.
        exc.key = _GITHUB_APP_PRIVATE_KEY_KEY
        raise

    # asyncpg returns JSONB as JSON-encoded text. The admin PUT validator
    # already enforced int 1..2_147_483_647 at write time; we json.loads
    # and re-assert defensively.
    raw = id_row["value"]
    try:
        app_id_value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as exc:
        logger.warning(
            "github_app_credentials_lookup_failed reason=%s key=%s",
            "json_decode",
            _GITHUB_APP_ID_KEY,
        )
        raise _NotConfigured("github_app_not_configured") from exc

    if isinstance(app_id_value, bool) or not isinstance(app_id_value, int):
        logger.warning(
            "github_app_credentials_lookup_failed reason=%s key=%s",
            "InvalidValue",
            _GITHUB_APP_ID_KEY,
        )
        raise _NotConfigured("github_app_not_configured")

    return app_id_value, private_key_pem


class _NotConfigured(Exception):
    """Internal — translated to 503 github_app_not_configured at the route layer."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


# ---------------------------------------------------------------------------
# JWT mint
# ---------------------------------------------------------------------------


def _mint_app_jwt(app_id: int, private_key_pem: str) -> str:
    """Mint a short-lived RS256 GitHub App JWT.

    Claims shape (per GitHub docs):
      iat = now - 60       (clock-skew tolerance)
      exp = now + 540      (9 min — under the 10 min max)
      iss = str(app_id)
    """
    now = int(time.time())
    payload = {
        "iat": now - _APP_JWT_CLOCK_SKEW_SECONDS,
        "exp": now + _APP_JWT_LIFETIME_SECONDS,
        "iss": str(app_id),
    }
    token = jwt.encode(
        payload, private_key_pem, algorithm=_APP_JWT_ALGO, headers={"alg": _APP_JWT_ALGO}
    )
    return token


# ---------------------------------------------------------------------------
# GitHub HTTP calls
# ---------------------------------------------------------------------------


def _github_headers(app_jwt: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {app_jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _post_access_tokens(
    *,
    installation_id: int,
    app_jwt: str,
    http_client: httpx.AsyncClient | None,
) -> dict[str, Any]:
    """POST <base>/app/installations/{id}/access_tokens.

    Returns the parsed body on 2xx. Raises InstallationTokenMintFailed on
    everything else, with a log-safe `reason`.
    """
    base = settings.github_api_base_url.rstrip("/")
    url = f"{base}/app/installations/{installation_id}/access_tokens"
    headers = _github_headers(app_jwt)

    own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=_GITHUB_TIMEOUT)
    try:
        try:
            r = await client.post(url, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
            logger.error(
                "installation_token_mint_failed installation_id=%s status=%s reason=%s",
                installation_id,
                0,
                "timeout",
            )
            raise InstallationTokenMintFailed(0, "timeout")
        except httpx.HTTPError as exc:
            logger.error(
                "installation_token_mint_failed installation_id=%s status=%s reason=%s",
                installation_id,
                0,
                f"transport:{type(exc).__name__}",
            )
            raise InstallationTokenMintFailed(
                0, f"transport:{type(exc).__name__}"
            )

        if r.status_code < 200 or r.status_code >= 300:
            # Truncate any server message to a short label; never let the
            # body verbatim land in logs.
            short = _short_error_label(r)
            reason = f"{r.status_code}:{short}"
            logger.error(
                "installation_token_mint_failed installation_id=%s status=%s reason=%s",
                installation_id,
                r.status_code,
                reason,
            )
            raise InstallationTokenMintFailed(r.status_code, reason)

        try:
            body = r.json()
        except ValueError:
            logger.error(
                "installation_token_mint_failed installation_id=%s status=%s reason=%s",
                installation_id,
                r.status_code,
                "malformed_token_response",
            )
            raise InstallationTokenMintFailed(
                r.status_code, "malformed_token_response"
            )

        if not isinstance(body, dict):
            logger.error(
                "installation_token_mint_failed installation_id=%s status=%s reason=%s",
                installation_id,
                r.status_code,
                "malformed_token_response",
            )
            raise InstallationTokenMintFailed(
                r.status_code, "malformed_token_response"
            )
        return body
    finally:
        if own_client:
            await client.aclose()


def _short_error_label(r: httpx.Response) -> str:
    """Extract a 1-2 word, log-safe label from a non-2xx GitHub response.

    Body verbatim never appears. We pull `message` if it's a string and
    short, otherwise return a fixed stub.
    """
    try:
        body = r.json()
    except ValueError:
        return "non_json"
    if isinstance(body, dict):
        msg = body.get("message")
        if isinstance(msg, str) and 1 <= len(msg) <= 64:
            # Whitelist letters+digits+spaces+a few harmless punctuation.
            return "".join(
                ch if ch.isalnum() or ch in " ._-:" else "_" for ch in msg
            )
    return "error"


async def mint_installation_token(
    installation_id: int,
    *,
    http_client: httpx.AsyncClient | None = None,
    pg_pool: asyncpg.Pool | None = None,
) -> dict[str, Any]:
    """Mint a fresh installation token directly against GitHub. No cache.

    Returns:
        {"token": <str>, "expires_at": <iso8601 str>, "source": "mint"}

    Raises:
        InstallationTokenMintFailed on 4xx/5xx/transport/malformed.
        SystemSettingDecryptError if the private-key row exists but Fernet
            decrypt fails — uncaught so the global handler emits 503.
        _NotConfigured (translated to 503 by the route layer) if either
            credential row is missing or NULL.
    """
    pool = pg_pool if pg_pool is not None else get_pool()
    app_id, private_key_pem = await _load_github_app_credentials(pool)
    app_jwt = _mint_app_jwt(app_id, private_key_pem)
    body = await _post_access_tokens(
        installation_id=installation_id,
        app_jwt=app_jwt,
        http_client=http_client,
    )

    token = body.get("token")
    if not isinstance(token, str) or not token:
        # We saw 2xx but no usable token — treat as malformed.
        logger.error(
            "installation_token_mint_failed installation_id=%s status=%s reason=%s",
            installation_id,
            200,
            "malformed_token_response",
        )
        raise InstallationTokenMintFailed(200, "malformed_token_response")

    expires_at = body.get("expires_at")
    if not isinstance(expires_at, str) or not expires_at:
        # Spec says `expires_at` is always present, but if GitHub ever
        # returns a 200 without one we substitute now+1h and warn rather
        # than fail — the cache TTL is the actual usability bound.
        fallback = datetime.now(timezone.utc) + timedelta(seconds=3600)
        expires_at = fallback.isoformat().replace("+00:00", "Z")
        logger.warning(
            "installation_token_missing_expires_at installation_id=%s",
            installation_id,
        )

    logger.info(
        "installation_token_minted installation_id=%s token_prefix=%s",
        installation_id,
        _token_prefix(token),
    )
    return {"token": token, "expires_at": expires_at, "source": "mint"}


async def lookup_installation(
    installation_id: int,
    *,
    http_client: httpx.AsyncClient | None = None,
    pg_pool: asyncpg.Pool | None = None,
) -> dict[str, str]:
    """GET <base>/app/installations/{id} -> {account_login, account_type}.

    Same auth shape as mint (App JWT). Used by the backend install-callback
    to attribute the install row to a GitHub account before it persists the
    row. Failure surfaces as InstallationTokenMintFailed-shaped errors so
    the route layer can map both endpoints to 502 with the same shape.
    """
    pool = pg_pool if pg_pool is not None else get_pool()
    app_id, private_key_pem = await _load_github_app_credentials(pool)
    app_jwt = _mint_app_jwt(app_id, private_key_pem)

    base = settings.github_api_base_url.rstrip("/")
    url = f"{base}/app/installations/{installation_id}"
    headers = _github_headers(app_jwt)

    own_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=_GITHUB_TIMEOUT)
    try:
        try:
            r = await client.get(url, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
            logger.error(
                "installation_lookup_failed installation_id=%s status=%s reason=%s",
                installation_id,
                0,
                "timeout",
            )
            raise InstallationTokenMintFailed(0, "timeout")
        except httpx.HTTPError as exc:
            logger.error(
                "installation_lookup_failed installation_id=%s status=%s reason=%s",
                installation_id,
                0,
                f"transport:{type(exc).__name__}",
            )
            raise InstallationTokenMintFailed(
                0, f"transport:{type(exc).__name__}"
            )

        if r.status_code < 200 or r.status_code >= 300:
            short = _short_error_label(r)
            reason = f"{r.status_code}:{short}"
            logger.error(
                "installation_lookup_failed installation_id=%s status=%s reason=%s",
                installation_id,
                r.status_code,
                reason,
            )
            raise InstallationTokenMintFailed(r.status_code, reason)

        try:
            body = r.json()
        except ValueError:
            logger.error(
                "installation_lookup_failed installation_id=%s status=%s reason=%s",
                installation_id,
                r.status_code,
                "malformed_lookup_response",
            )
            raise InstallationTokenMintFailed(
                r.status_code, "malformed_lookup_response"
            )

        account = body.get("account") if isinstance(body, dict) else None
        if not isinstance(account, dict):
            logger.error(
                "installation_lookup_failed installation_id=%s status=%s reason=%s",
                installation_id,
                r.status_code,
                "malformed_lookup_response",
            )
            raise InstallationTokenMintFailed(
                r.status_code, "malformed_lookup_response"
            )

        login = account.get("login")
        acc_type = account.get("type")
        if not isinstance(login, str) or not isinstance(acc_type, str):
            logger.error(
                "installation_lookup_failed installation_id=%s status=%s reason=%s",
                installation_id,
                r.status_code,
                "malformed_lookup_response",
            )
            raise InstallationTokenMintFailed(
                r.status_code, "malformed_lookup_response"
            )
        return {"account_login": login, "account_type": acc_type}
    finally:
        if own_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# Cache layer
# ---------------------------------------------------------------------------


async def get_installation_token(
    installation_id: int,
    *,
    redis_client: redis_async.Redis | None = None,
    http_client: httpx.AsyncClient | None = None,
    pg_pool: asyncpg.Pool | None = None,
) -> dict[str, Any]:
    """Cache-first installation token retrieval.

    GET gh:installtok:<id> from Redis.
      - hit  -> log installation_token_cache_hit, return source='cache'
      - miss -> mint, SETEX 50min, log installation_token_minted, source='mint'
      - Redis unreachable on GET -> warn redis_unreachable, mint without caching
      - Redis unreachable on SETEX -> warn redis_unreachable, return mint result

    Concurrent-mint race accepted per D021 (last-write-wins on the SETEX).
    """
    cache_key = _cache_key(installation_id)
    cached: str | None = None
    if redis_client is not None:
        try:
            raw = await redis_client.get(cache_key)
        except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
            logger.warning(
                "redis_unreachable op=installation_token_get reason=%s",
                type(exc).__name__,
            )
            raw = None
        except RedisError as exc:
            logger.warning(
                "redis_error op=installation_token_get reason=%s",
                type(exc).__name__,
            )
            raw = None
        if raw is not None:
            # decode_responses=True is the canonical pool config; reject
            # bytes defensively in case a future caller passes a raw client.
            if isinstance(raw, bytes):
                try:
                    cached = raw.decode("utf-8")
                except UnicodeDecodeError:
                    cached = None
            elif isinstance(raw, str):
                cached = raw
            else:
                cached = None

    if cached:
        # TTL-derived expiry: best-effort. If the GET succeeded, TTL is
        # safe to ask for; we tolerate failure and fall back to "unknown".
        expires_at = "unknown"
        try:
            ttl = await redis_client.ttl(cache_key)  # type: ignore[union-attr]
            if isinstance(ttl, int) and ttl > 0:
                expires_at = (
                    datetime.now(timezone.utc) + timedelta(seconds=ttl)
                ).isoformat().replace("+00:00", "Z")
        except (RedisError, OSError, AttributeError):
            pass
        logger.info(
            "installation_token_cache_hit installation_id=%s token_prefix=%s",
            installation_id,
            _token_prefix(cached),
        )
        return {"token": cached, "expires_at": expires_at, "source": "cache"}

    # Miss path — mint, then write-through.
    minted = await mint_installation_token(
        installation_id, http_client=http_client, pg_pool=pg_pool
    )
    if redis_client is not None:
        try:
            await redis_client.setex(
                cache_key, _CACHE_TTL_SECONDS, minted["token"]
            )
        except (RedisConnectionError, RedisTimeoutError, OSError) as exc:
            logger.warning(
                "redis_unreachable op=installation_token_setex reason=%s",
                type(exc).__name__,
            )
        except RedisError as exc:
            logger.warning(
                "redis_error op=installation_token_setex reason=%s",
                type(exc).__name__,
            )
    return minted


__all__ = [
    "InstallationTokenMintFailed",
    "_NotConfigured",
    "_GITHUB_APP_PRIVATE_KEY_KEY",
    "_GITHUB_APP_ID_KEY",
    "_load_github_app_credentials",
    "_mint_app_jwt",
    "mint_installation_token",
    "get_installation_token",
    "lookup_installation",
]
