---
estimated_steps: 29
estimated_files: 6
skills_used: []
---

# T03: Orchestrator installation token mint + Redis cache + HTTP endpoints

Add pyjwt[crypto]>=2.8.0,<3.0.0 to orchestrator/pyproject.toml and rebuild orchestrator image. Create orchestrator/orchestrator/github_tokens.py exposing:

- _GITHUB_APP_PRIVATE_KEY_KEY = 'github_app_private_key', _GITHUB_APP_ID_KEY = 'github_app_id'.
- _load_github_app_credentials(pg_pool) -> tuple[int, str] — async; reads both rows from system_settings (private key via decrypt_setting, app id from JSONB value); raises SystemSettingDecryptError on Fernet failure (NEVER catches — global handler maps to 503); raises HTTPException 503 'github_app_not_configured' if either row missing or value/value_encrypted IS NULL.
- _mint_app_jwt(app_id: int, private_key_pem: str) -> str — RS256, payload {iat:now-60, exp:now+540, iss:str(app_id)} (60s clock-skew tolerance, GitHub's documented limit is 10m max). Use jwt.encode(headers={'alg':'RS256'}).
- mint_installation_token(installation_id: int, *, http_client: httpx.AsyncClient | None = None) -> dict — mints app JWT, POSTs <settings.github_api_base_url>/app/installations/{id}/access_tokens with Authorization: Bearer <app_jwt>, Accept: application/vnd.github+json. On 2xx: parse {token, expires_at}, return {'token': token, 'expires_at': expires_at, 'source': 'mint'}; on non-2xx: log installation_token_mint_failed installation_id=<id> status=<code> reason=<short> and raise InstallationTokenMintFailed(status, reason). Token plaintext NEVER appears in logs — only token_prefix=<first4>....
- get_installation_token(installation_id: int, *, redis_client: redis.asyncio.Redis | None = None) -> dict — cache-first: GET gh:installtok:{id} from redis; on hit, log installation_token_cache_hit installation_id=<id> token_prefix=<first4>... and return {'token': cached, 'source': 'cache', 'expires_at': <ttl-derived ISO>}; on miss: call mint_installation_token, SETEX with 50*60=3000 second TTL, log installation_token_minted ..., return {'source': 'mint', ...}. On RedisUnavailable during GET → log warning redis_unreachable op=installation_token_get and proceed straight to mint without caching the result.
- lookup_installation(installation_id: int, *, http_client=None) -> dict — same auth shape; GET <base>/app/installations/{id}; returns {'account_login': resp['account']['login'], 'account_type': resp['account']['type']}.

Add orchestrator/orchestrator/routes_github.py (FastAPI APIRouter prefix='/v1/installations'):

- GET /v1/installations/{installation_id}/token → calls get_installation_token; returns {'token', 'source', 'expires_at'} as JSON. Errors: 503 on github_app_not_configured / decrypt_failed (decrypt failure flows through the global handler — handler must be ADDED in orchestrator/main.py to mirror backend); 502 on InstallationTokenMintFailed.
- GET /v1/installations/{installation_id}/lookup → calls lookup_installation; returns {'account_login', 'account_type'}.

Mount in orchestrator/main.py via app.include_router. Add github_api_base_url: str = 'https://api.github.com' to orchestrator/orchestrator/config.py (env var GITHUB_API_BASE_URL). Register SystemSettingDecryptError exception handler in orchestrator/main.py mirroring backend/app/main.py shape: emit ERROR system_settings_decrypt_failed key=<exc.key> and return JSONResponse 503 {'detail':'system_settings_decrypt_failed','key':exc.key}.

Unit tests in orchestrator/tests/unit/test_github_tokens.py: respx mounts <base>/app/installations/{id}/access_tokens and <base>/app/installations/{id}; verifies the outgoing request has Authorization: Bearer <jwt> and the JWT is RS256 with iss=<app_id>; cache-hit path uses fakeredis (or a thin _FakeRedis fixture) to seed gh:installtok:42; cache-miss path verifies SETEX TTL ≈ 3000s; non-2xx surfaces InstallationTokenMintFailed; SystemSettingDecryptError propagates uncaught (caller handles); negative tests: missing app_id row, NULL value_encrypted, malformed JSON in value. Add a TestClient-based test that boots routes_github under a fakeredis registry + respx-mocked GitHub and asserts the route returns the expected JSON shape and logs the right keys (and that the DecryptError handler returns 503 + structured log).

## Failure Modes

| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| GitHub /app/installations/{id}/access_tokens | InstallationTokenMintFailed(status, reason) → 502 to caller | 10s httpx timeout; reason='timeout' | InstallationTokenMintFailed reason='malformed_token_response' |
| GitHub /app/installations/{id} | InstallationTokenMintFailed-style propagation → 502 to caller | 10s | reason='malformed_lookup_response' |
| Redis (cache GET) | Log redis_unreachable, mint anyway, do not cache result | Same | N/A |
| Redis (cache SETEX) | Log redis_unreachable, return mint result without caching | Same | N/A |
| Postgres (system_settings read) | Existing WorkspaceVolumeStoreUnavailable handler → 503 | Same | N/A |
| Fernet decrypt (private key) | SystemSettingDecryptError raised, handler emits 503 | N/A | N/A |

## Load Profile

- **Shared resources**: Redis (1 GET + occasional SETEX per token request), Postgres (1 SELECT per cache-miss for credentials read), GitHub API (1 POST per cache-miss).
- **Per-operation cost**: cache-hit ≈ 1 Redis GET (sub-ms); cache-miss ≈ 1 Postgres + 1 RS256 sign + 1 GitHub POST (~150ms).
- **10x breakpoint**: GitHub API rate limit (5000/h per app); cache-miss rate dominates — at 10x cold rate we hit GitHub at ~25/sec which is fine. Concurrent-mint race accepted per D021.

## Negative Tests

- **Malformed inputs**: GitHub returns {'token':null} → InstallationTokenMintFailed reason='malformed_token_response'; non-JSON body → same; missing 'expires_at' → use server-side default expiry of now+3600s and log warning.
- **Error paths**: GitHub 401 (bad app JWT) → 502 reason='401:Bad credentials'; GitHub 404 (installation gone) → 502 reason='404:Not Found'; Redis unreachable → mint succeeds, response has source='mint' but no caching side-effect (verifiable by absence of the SETEX log line).
- **Boundary conditions**: cache value bytes ≠ str (decode_responses=True is set) — rejected via type check; Redis TTL < 1s → cache-hit path still returns valid token; concurrent get_installation_token from two coros both miss cache and both mint (last-write-wins per D021).

## Inputs

- ``orchestrator/orchestrator/encryption.py` — decrypt_setting, SystemSettingDecryptError imports`
- ``orchestrator/orchestrator/main.py` — global SystemSettingDecryptError handler IS NOT YET REGISTERED here; T03 must add the handler matching backend/app/main.py shape (503 + structured log)`
- ``orchestrator/orchestrator/redis_client.py` — RedisSessionRegistry shape; reuse the same redis.asyncio client construction pattern with a separate key namespace gh:installtok:`
- ``orchestrator/orchestrator/auth.py` — shared-secret middleware that gates all /v1/* routes (the new /v1/installations/* routes inherit it automatically)`
- ``orchestrator/orchestrator/config.py` — Settings class to extend with github_api_base_url`
- ``backend/app/api/routes/admin.py` — system_settings table shape (reads need raw SQL or a thin SQLAlchemy core query against the asyncpg pool — orchestrator does NOT use SQLModel)`
- ``orchestrator/pyproject.toml` — add pyjwt[crypto] dep`

## Expected Output

- ``orchestrator/pyproject.toml` — adds pyjwt[crypto]>=2.8.0,<3.0.0 under dependencies`
- ``orchestrator/orchestrator/github_tokens.py` — module with mint_installation_token, get_installation_token, lookup_installation, _load_github_app_credentials, _mint_app_jwt, InstallationTokenMintFailed exception`
- ``orchestrator/orchestrator/routes_github.py` — APIRouter with the two GET endpoints, structured logging, exception → status mapping`
- ``orchestrator/orchestrator/config.py` — adds github_api_base_url: str = 'https://api.github.com'`
- ``orchestrator/orchestrator/main.py` — includes the new router AND registers a SystemSettingDecryptError exception handler matching backend/app/main.py shape (503 + ERROR system_settings_decrypt_failed key=<name> log)`
- ``orchestrator/tests/unit/test_github_tokens.py` — respx + fakeredis suite proving JWT shape, cache-hit/miss, TTL, mint-failure surface, decrypt-failure propagation`

## Verification

cd orchestrator && uv sync && uv run pytest tests/unit/test_github_tokens.py -v
