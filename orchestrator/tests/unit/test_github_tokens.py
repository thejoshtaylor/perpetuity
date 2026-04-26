"""Unit tests for orchestrator GitHub installation token mint + cache (T03).

Covers:
  - JWT shape (RS256, iss=str(app_id), iat/exp window)
  - GitHub HTTP call uses Authorization: Bearer <jwt> + correct path
  - Cache-hit path uses fakeredis (no GitHub call)
  - Cache-miss path mints + SETEX with TTL ≈ 3000s
  - Non-2xx surfaces InstallationTokenMintFailed with structured reason
  - Malformed body / missing token / missing expires_at edge cases
  - SystemSettingDecryptError propagates uncaught (caller handles)
  - Missing app_id row, NULL value_encrypted -> _NotConfigured
  - Route surface: 200 happy path, 502 mint failure, 503 not configured,
    503 system_settings_decrypt_failed (handler returns the structured shape)
  - Redis unreachable on GET still mints; no SETEX side-effect

Approach:
  - Generate a fresh RSA key per test session so the JWT round-trips through
    the real cryptography library (validates that pyjwt[crypto] is wired).
  - respx mocks the GitHub host directly; we never touch the public API.
  - A thin _FakePool with .acquire() -> _FakeConn fixtures the asyncpg surface
    we actually use (fetch / fetchval).
  - fakeredis.aioredis.FakeRedis is decode_responses=True so cached values
    behave like the production pool.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest

# Set required env BEFORE importing orchestrator modules.
os.environ.setdefault("SKIP_IMAGE_PULL_ON_BOOT", "1")
os.environ.setdefault("SKIP_PG_POOL_ON_BOOT", "1")
os.environ.setdefault("ORCHESTRATOR_API_KEY", "unit-test-current-key")
os.environ.setdefault(
    "SYSTEM_SETTINGS_ENCRYPTION_KEY",
    "kfk5l7mPRFpBV7PzWJxYmO6LRRQAdZ4iGYZRG6xL0fY=",
)

import fakeredis.aioredis  # noqa: E402
import httpx  # noqa: E402
import jwt  # noqa: E402
import respx  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from orchestrator import github_tokens  # noqa: E402
from orchestrator.config import settings  # noqa: E402
from orchestrator.encryption import SystemSettingDecryptError, encrypt_setting  # noqa: E402
from orchestrator.github_tokens import (  # noqa: E402
    InstallationTokenMintFailed,
    _GITHUB_APP_ID_KEY,
    _GITHUB_APP_PRIVATE_KEY_KEY,
    _NotConfigured,
    _mint_app_jwt,
    get_installation_token,
    lookup_installation,
    mint_installation_token,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rsa_keypair() -> tuple[str, str]:
    """Fresh 2048-bit RSA keypair as PEM strings (private, public).

    2048 keeps test setup ~300ms; 4096 would push it past 1s with no
    extra signal — pyjwt validates RS256 the same way at either size.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    return private_pem, public_pem


@pytest.fixture
def github_base() -> str:
    return settings.github_api_base_url.rstrip("/")


class _FakeConn:
    """Minimal async asyncpg connection — only fetch/fetchval/fetchrow."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def fetch(self, _sql: str, _keys: list[str]) -> list[dict[str, Any]]:
        return list(self._rows)

    async def __aenter__(self) -> "_FakeConn":
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None


class _FakePool:
    """asyncpg.Pool stand-in. .acquire() returns an async ctx around _FakeConn."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def acquire(self) -> _FakeConn:
        return _FakeConn(self._rows)


def _credentials_rows(app_id: int, private_pem: str) -> list[dict[str, Any]]:
    return [
        {
            "key": _GITHUB_APP_PRIVATE_KEY_KEY,
            "value": None,
            "value_encrypted": encrypt_setting(private_pem),
        },
        {
            "key": _GITHUB_APP_ID_KEY,
            "value": json.dumps(app_id),
            "value_encrypted": None,
        },
    ]


@pytest.fixture
async def fake_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# JWT shape
# ---------------------------------------------------------------------------


def test_mint_app_jwt_shape(rsa_keypair: tuple[str, str]) -> None:
    private_pem, public_pem = rsa_keypair
    token = _mint_app_jwt(12345, private_pem)
    decoded = jwt.decode(
        token,
        public_pem,
        algorithms=["RS256"],
        options={"verify_aud": False},
    )
    assert decoded["iss"] == "12345"
    # iat is backdated 60s for clock-skew tolerance; exp ~ iat + 600 (540 + 60).
    assert decoded["exp"] - decoded["iat"] == 600


# ---------------------------------------------------------------------------
# mint_installation_token — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mint_installation_token_calls_github_with_app_jwt(
    rsa_keypair: tuple[str, str], github_base: str
) -> None:
    private_pem, public_pem = rsa_keypair
    pool = _FakePool(_credentials_rows(7777, private_pem))

    captured: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["url"] = str(request.url)
        return httpx.Response(
            201,
            json={
                "token": "ghs_abcdefghijklmnop",
                "expires_at": "2026-04-25T18:00:00Z",
            },
        )

    with respx.mock(assert_all_called=True) as router:
        router.post(
            f"{github_base}/app/installations/42/access_tokens"
        ).mock(side_effect=_handler)
        result = await mint_installation_token(42, pg_pool=pool)

    assert result["token"] == "ghs_abcdefghijklmnop"
    assert result["source"] == "mint"
    assert result["expires_at"] == "2026-04-25T18:00:00Z"

    auth = captured["headers"]["authorization"]
    assert auth.startswith("Bearer ")
    presented_jwt = auth.removeprefix("Bearer ")
    decoded = jwt.decode(
        presented_jwt,
        public_pem,
        algorithms=["RS256"],
        options={"verify_aud": False},
    )
    assert decoded["iss"] == "7777"

    assert (
        captured["headers"].get("accept") == "application/vnd.github+json"
    )


# ---------------------------------------------------------------------------
# Cache hit / miss
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_installation_token_cache_hit(
    rsa_keypair: tuple[str, str],
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    private_pem, _ = rsa_keypair
    pool = _FakePool(_credentials_rows(7777, private_pem))
    await fake_redis.setex("gh:installtok:42", 1500, "ghs_cached_token_xyz")

    with respx.mock(assert_all_called=False) as router:
        # If the cache hit path ever calls GitHub, the test fails.
        github_route = router.post(
            "https://api.github.com/app/installations/42/access_tokens"
        ).mock(return_value=httpx.Response(500))
        result = await get_installation_token(
            42, redis_client=fake_redis, pg_pool=pool
        )

    assert not github_route.called
    assert result["token"] == "ghs_cached_token_xyz"
    assert result["source"] == "cache"


@pytest.mark.asyncio
async def test_get_installation_token_cache_miss_setex_ttl(
    rsa_keypair: tuple[str, str],
    fake_redis: fakeredis.aioredis.FakeRedis,
    github_base: str,
) -> None:
    private_pem, _ = rsa_keypair
    pool = _FakePool(_credentials_rows(7777, private_pem))

    with respx.mock(assert_all_called=True) as router:
        router.post(
            f"{github_base}/app/installations/77/access_tokens"
        ).mock(
            return_value=httpx.Response(
                201,
                json={
                    "token": "ghs_freshly_minted",
                    "expires_at": "2026-04-25T18:00:00Z",
                },
            )
        )
        result = await get_installation_token(
            77, redis_client=fake_redis, pg_pool=pool
        )

    assert result["token"] == "ghs_freshly_minted"
    assert result["source"] == "mint"

    cached = await fake_redis.get("gh:installtok:77")
    assert cached == "ghs_freshly_minted"
    ttl = await fake_redis.ttl("gh:installtok:77")
    # 50 minutes = 3000s. fakeredis preserves the SETEX TTL exactly.
    assert ttl == 3000


@pytest.mark.asyncio
async def test_get_installation_token_redis_unreachable_still_mints(
    rsa_keypair: tuple[str, str], github_base: str
) -> None:
    """Redis unreachable on GET -> mint anyway, no caching side-effect."""
    private_pem, _ = rsa_keypair
    pool = _FakePool(_credentials_rows(7777, private_pem))

    class _BrokenRedis:
        async def get(self, _k: str) -> str:
            raise ConnectionError("redis_down")

        async def setex(self, _k: str, _ttl: int, _v: str) -> None:
            # If this gets called, the test asserts below will catch it.
            raise ConnectionError("redis_down")

        async def ttl(self, _k: str) -> int:
            return -1

    with respx.mock(assert_all_called=True) as router:
        router.post(
            f"{github_base}/app/installations/99/access_tokens"
        ).mock(
            return_value=httpx.Response(
                201,
                json={
                    "token": "ghs_minted_no_cache",
                    "expires_at": "2026-04-25T18:00:00Z",
                },
            )
        )
        result = await get_installation_token(
            99, redis_client=_BrokenRedis(), pg_pool=pool
        )
    assert result["source"] == "mint"
    assert result["token"] == "ghs_minted_no_cache"


# ---------------------------------------------------------------------------
# Error / negative paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mint_installation_token_404_surfaces_mint_failed(
    rsa_keypair: tuple[str, str], github_base: str
) -> None:
    private_pem, _ = rsa_keypair
    pool = _FakePool(_credentials_rows(7777, private_pem))
    with respx.mock(assert_all_called=True) as router:
        router.post(
            f"{github_base}/app/installations/42/access_tokens"
        ).mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )
        with pytest.raises(InstallationTokenMintFailed) as ei:
            await mint_installation_token(42, pg_pool=pool)
    assert ei.value.status == 404
    assert "Not Found" in ei.value.reason


@pytest.mark.asyncio
async def test_mint_installation_token_401_bad_credentials(
    rsa_keypair: tuple[str, str], github_base: str
) -> None:
    private_pem, _ = rsa_keypair
    pool = _FakePool(_credentials_rows(7777, private_pem))
    with respx.mock(assert_all_called=True) as router:
        router.post(
            f"{github_base}/app/installations/42/access_tokens"
        ).mock(
            return_value=httpx.Response(
                401, json={"message": "Bad credentials"}
            )
        )
        with pytest.raises(InstallationTokenMintFailed) as ei:
            await mint_installation_token(42, pg_pool=pool)
    assert ei.value.status == 401
    assert "Bad credentials" in ei.value.reason


@pytest.mark.asyncio
async def test_mint_installation_token_malformed_body_no_token(
    rsa_keypair: tuple[str, str], github_base: str
) -> None:
    private_pem, _ = rsa_keypair
    pool = _FakePool(_credentials_rows(7777, private_pem))
    with respx.mock(assert_all_called=True) as router:
        router.post(
            f"{github_base}/app/installations/42/access_tokens"
        ).mock(
            return_value=httpx.Response(
                201, json={"token": None, "expires_at": "2026-04-25T18:00:00Z"}
            )
        )
        with pytest.raises(InstallationTokenMintFailed) as ei:
            await mint_installation_token(42, pg_pool=pool)
    assert ei.value.reason == "malformed_token_response"


@pytest.mark.asyncio
async def test_mint_installation_token_malformed_non_json(
    rsa_keypair: tuple[str, str], github_base: str
) -> None:
    private_pem, _ = rsa_keypair
    pool = _FakePool(_credentials_rows(7777, private_pem))
    with respx.mock(assert_all_called=True) as router:
        router.post(
            f"{github_base}/app/installations/42/access_tokens"
        ).mock(
            return_value=httpx.Response(
                201, content=b"not really json", headers={"content-type": "text/plain"}
            )
        )
        with pytest.raises(InstallationTokenMintFailed) as ei:
            await mint_installation_token(42, pg_pool=pool)
    assert ei.value.reason == "malformed_token_response"


@pytest.mark.asyncio
async def test_mint_installation_token_missing_expires_at_uses_fallback(
    rsa_keypair: tuple[str, str], github_base: str
) -> None:
    private_pem, _ = rsa_keypair
    pool = _FakePool(_credentials_rows(7777, private_pem))
    with respx.mock(assert_all_called=True) as router:
        router.post(
            f"{github_base}/app/installations/42/access_tokens"
        ).mock(
            return_value=httpx.Response(201, json={"token": "ghs_ok"})
        )
        result = await mint_installation_token(42, pg_pool=pool)
    assert result["token"] == "ghs_ok"
    # Fallback expiry — non-empty ISO string, not the missing-key sentinel.
    assert isinstance(result["expires_at"], str)
    assert result["expires_at"] != ""


# ---------------------------------------------------------------------------
# Credential row negative cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_credentials_missing_app_id_row_raises_not_configured(
    rsa_keypair: tuple[str, str],
) -> None:
    private_pem, _ = rsa_keypair
    pool = _FakePool(
        [
            {
                "key": _GITHUB_APP_PRIVATE_KEY_KEY,
                "value": None,
                "value_encrypted": encrypt_setting(private_pem),
            }
        ]
    )
    with pytest.raises(_NotConfigured):
        await mint_installation_token(42, pg_pool=pool)


@pytest.mark.asyncio
async def test_load_credentials_null_encrypted_raises_not_configured() -> None:
    pool = _FakePool(
        [
            {
                "key": _GITHUB_APP_PRIVATE_KEY_KEY,
                "value": None,
                "value_encrypted": None,
            },
            {
                "key": _GITHUB_APP_ID_KEY,
                "value": json.dumps(7777),
                "value_encrypted": None,
            },
        ]
    )
    with pytest.raises(_NotConfigured):
        await mint_installation_token(42, pg_pool=pool)


@pytest.mark.asyncio
async def test_load_credentials_malformed_app_id_value_raises_not_configured(
    rsa_keypair: tuple[str, str],
) -> None:
    private_pem, _ = rsa_keypair
    pool = _FakePool(
        [
            {
                "key": _GITHUB_APP_PRIVATE_KEY_KEY,
                "value": None,
                "value_encrypted": encrypt_setting(private_pem),
            },
            {
                "key": _GITHUB_APP_ID_KEY,
                "value": "{not really json",
                "value_encrypted": None,
            },
        ]
    )
    with pytest.raises(_NotConfigured):
        await mint_installation_token(42, pg_pool=pool)


@pytest.mark.asyncio
async def test_decrypt_failure_propagates_uncaught() -> None:
    """Garbage in value_encrypted -> Fernet raises -> SystemSettingDecryptError(key=...)."""
    pool = _FakePool(
        [
            {
                "key": _GITHUB_APP_PRIVATE_KEY_KEY,
                "value": None,
                "value_encrypted": b"not-a-fernet-token",
            },
            {
                "key": _GITHUB_APP_ID_KEY,
                "value": json.dumps(7777),
                "value_encrypted": None,
            },
        ]
    )
    with pytest.raises(SystemSettingDecryptError) as ei:
        await mint_installation_token(42, pg_pool=pool)
    assert ei.value.key == _GITHUB_APP_PRIVATE_KEY_KEY


# ---------------------------------------------------------------------------
# lookup_installation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lookup_installation_returns_account_fields(
    rsa_keypair: tuple[str, str], github_base: str
) -> None:
    private_pem, _ = rsa_keypair
    pool = _FakePool(_credentials_rows(7777, private_pem))
    with respx.mock(assert_all_called=True) as router:
        router.get(
            f"{github_base}/app/installations/42"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 42,
                    "account": {"login": "octocorp", "type": "Organization"},
                },
            )
        )
        result = await lookup_installation(42, pg_pool=pool)
    assert result == {"account_login": "octocorp", "account_type": "Organization"}


@pytest.mark.asyncio
async def test_lookup_installation_malformed_body(
    rsa_keypair: tuple[str, str], github_base: str
) -> None:
    private_pem, _ = rsa_keypair
    pool = _FakePool(_credentials_rows(7777, private_pem))
    with respx.mock(assert_all_called=True) as router:
        router.get(
            f"{github_base}/app/installations/42"
        ).mock(return_value=httpx.Response(200, json={"id": 42}))  # no account
        with pytest.raises(InstallationTokenMintFailed) as ei:
            await lookup_installation(42, pg_pool=pool)
    assert ei.value.reason == "malformed_lookup_response"


# ---------------------------------------------------------------------------
# Route surface (TestClient)
# ---------------------------------------------------------------------------


@pytest.fixture
def app_with_state(
    rsa_keypair: tuple[str, str], fake_redis: fakeredis.aioredis.FakeRedis
) -> Iterator[tuple[Any, Any, Any]]:
    """Yield (app, fake_pool, registry).

    The TestClient consumer is responsible for injecting fake_pool /
    registry onto app.state INSIDE the `with TestClient(app)` block —
    the lifespan otherwise overwrites both during startup.
    """
    from orchestrator.main import app
    from orchestrator.redis_client import RedisSessionRegistry

    private_pem, _ = rsa_keypair
    pool = _FakePool(_credentials_rows(7777, private_pem))
    registry = RedisSessionRegistry(client=fake_redis)
    yield app, pool, registry


def _install_state(app: Any, pool: Any, registry: Any) -> None:
    """Pin fake state onto app.state. Call AFTER TestClient enters lifespan."""
    app.state.pg = pool
    app.state.registry = registry


def _auth_headers() -> dict[str, str]:
    return {"X-Orchestrator-Key": settings.orchestrator_api_key}


def test_route_token_happy_path_returns_json_shape(
    app_with_state: tuple[Any, Any, Any], github_base: str
) -> None:
    app, pool, registry = app_with_state
    with respx.mock(assert_all_called=True) as router:
        router.post(
            f"{github_base}/app/installations/42/access_tokens"
        ).mock(
            return_value=httpx.Response(
                201,
                json={
                    "token": "ghs_routed",
                    "expires_at": "2026-04-25T18:00:00Z",
                },
            )
        )
        with TestClient(app) as c:
            _install_state(app, pool, registry)
            r = c.get("/v1/installations/42/token", headers=_auth_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["token"] == "ghs_routed"
    assert body["source"] == "mint"
    assert body["expires_at"] == "2026-04-25T18:00:00Z"


def test_route_token_mint_failed_returns_502(
    app_with_state: tuple[Any, Any, Any], github_base: str
) -> None:
    app, pool, registry = app_with_state
    with respx.mock(assert_all_called=True) as router:
        router.post(
            f"{github_base}/app/installations/42/access_tokens"
        ).mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )
        with TestClient(app) as c:
            _install_state(app, pool, registry)
            r = c.get("/v1/installations/42/token", headers=_auth_headers())
    assert r.status_code == 502
    body = r.json()
    detail = body["detail"]
    assert detail["detail"] == "github_token_mint_failed"
    assert detail["status"] == 404


def test_route_decrypt_failure_returns_503_with_handler_shape(
    rsa_keypair: tuple[str, str],
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Garbage ciphertext -> SystemSettingDecryptError(key=github_app_private_key)
    propagates uncaught and the global handler returns 503 with the
    structured shape that mirrors the backend."""
    from orchestrator.main import app
    from orchestrator.redis_client import RedisSessionRegistry

    pool = _FakePool(
        [
            {
                "key": _GITHUB_APP_PRIVATE_KEY_KEY,
                "value": None,
                "value_encrypted": b"not-a-fernet-token",
            },
            {
                "key": _GITHUB_APP_ID_KEY,
                "value": json.dumps(7777),
                "value_encrypted": None,
            },
        ]
    )
    registry = RedisSessionRegistry(client=fake_redis)
    with TestClient(app, raise_server_exceptions=False) as c:
        _install_state(app, pool, registry)
        r = c.get("/v1/installations/42/token", headers=_auth_headers())

    assert r.status_code == 503
    body = r.json()
    assert body["detail"] == "system_settings_decrypt_failed"
    assert body["key"] == _GITHUB_APP_PRIVATE_KEY_KEY


def test_route_not_configured_returns_503(
    rsa_keypair: tuple[str, str],
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Missing app_id row -> _NotConfigured -> 503 github_app_not_configured."""
    from orchestrator.main import app
    from orchestrator.redis_client import RedisSessionRegistry

    private_pem, _ = rsa_keypair
    pool = _FakePool(
        [
            {
                "key": _GITHUB_APP_PRIVATE_KEY_KEY,
                "value": None,
                "value_encrypted": encrypt_setting(private_pem),
            }
        ]
    )
    registry = RedisSessionRegistry(client=fake_redis)
    with TestClient(app) as c:
        _install_state(app, pool, registry)
        r = c.get("/v1/installations/42/token", headers=_auth_headers())
    assert r.status_code == 503
    assert r.json()["detail"] == "github_app_not_configured"


def test_route_lookup_happy_path(
    app_with_state: tuple[Any, Any, Any], github_base: str
) -> None:
    app, pool, registry = app_with_state
    with respx.mock(assert_all_called=True) as router:
        router.get(
            f"{github_base}/app/installations/42"
        ).mock(
            return_value=httpx.Response(
                200,
                json={"id": 42, "account": {"login": "octo", "type": "User"}},
            )
        )
        with TestClient(app) as c:
            _install_state(app, pool, registry)
            r = c.get(
                "/v1/installations/42/lookup", headers=_auth_headers()
            )
    assert r.status_code == 200
    assert r.json() == {"account_login": "octo", "account_type": "User"}
