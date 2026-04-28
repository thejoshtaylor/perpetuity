---
id: T03
parent: S02
milestone: M004-guylpp
key_files:
  - orchestrator/orchestrator/github_tokens.py
  - orchestrator/orchestrator/routes_github.py
  - orchestrator/orchestrator/main.py
  - orchestrator/orchestrator/config.py
  - orchestrator/pyproject.toml
  - orchestrator/tests/unit/test_github_tokens.py
key_decisions:
  - Cache key prefix `gh:installtok:` keeps the install-token namespace cleanly separated from the existing `session:` / `user_sessions:` keys in the same Redis — reuses the existing RedisSessionRegistry client rather than opening a second one (same pool, just a different keyspace prefix; route fetches via `request.app.state.registry._client`).
  - App JWT lifetime is 9 minutes with a 60s backdated iat — well under GitHub's 10-min hard cap and tolerant of small clock skew between orchestrator and GitHub. exp-iat==600s is asserted in tests so a future bump is loud.
  - InstallationTokenMintFailed carries (status, reason) where reason is a sanitized 1-2 word label derived from the GitHub `message` field via a strict whitelist (alnum + ` ._-:`). Body verbatim never reaches logs; transports/timeouts use status=0 with reason='timeout' or 'transport:<ExcName>'.
  - Internal `_NotConfigured` exception (rather than HTTPException raised inside `github_tokens.py`) keeps the credential-loader pure / testable without FastAPI; the route layer translates it to 503 `github_app_not_configured`. Tests assert against the exception class directly, not HTTP status, for unit-level coverage.
  - SystemSettingDecryptError handler is wired in `main.py` mirroring `backend/app/main.py` exactly (ERROR log + 503 with structured `{detail, key}`); the credential loader re-attaches `exc.key = _GITHUB_APP_PRIVATE_KEY_KEY` before re-raising so the handler's log line names the row that failed.
duration: 
verification_result: passed
completed_at: 2026-04-26T01:06:19.107Z
blocker_discovered: false
---

# T03: Add orchestrator installation-token mint with RS256 App JWT, Redis 50-min cache, /v1/installations/{id}/{token,lookup} routes, and SystemSettingDecryptError 503 handler

**Add orchestrator installation-token mint with RS256 App JWT, Redis 50-min cache, /v1/installations/{id}/{token,lookup} routes, and SystemSettingDecryptError 503 handler**

## What Happened

T03 lands the orchestrator side of the GitHub installation handshake — the credential-bearing process that S04's clone path will consume.

`orchestrator/orchestrator/github_tokens.py` owns the credential read + mint + cache pipeline. `_load_github_app_credentials(pg_pool)` reads both system_settings rows in a single `SELECT key,value,value_encrypted FROM system_settings WHERE key = ANY($1::text[])`, decrypts the private key via the existing Fernet substrate, and re-attaches the row key on `SystemSettingDecryptError` so the global handler logs `key=github_app_private_key`. Missing rows or NULL value/value_encrypted surface as an internal `_NotConfigured("github_app_not_configured")` (route layer maps to 503).

`_mint_app_jwt(app_id, private_key_pem)` uses `jwt.encode` with `algorithm='RS256'`, claims `{iat=now-60, exp=now+540, iss=str(app_id)}` — the 60s clock-skew backdate keeps a slow-clock orchestrator inside GitHub's 10-minute window. `mint_installation_token` POSTs `<github_api_base_url>/app/installations/{id}/access_tokens` with `Authorization: Bearer <jwt>` and `Accept: application/vnd.github+json`, parses `{token, expires_at}`, and logs `installation_token_minted installation_id=<id> token_prefix=<first4>...`. Non-2xx → `InstallationTokenMintFailed(status, reason)` with a `_short_error_label` that whitelists alnum + a few harmless punctuation characters so a hostile GitHub message can never sneak control characters into the log line. Body verbatim never appears in logs.

`get_installation_token` is the cache-first wrapper. Cache key `gh:installtok:<id>`, TTL 3000s (50 min). Hit → log `installation_token_cache_hit` and return `source='cache'` with a TTL-derived ISO expiry. Miss → mint → SETEX. Redis unreachable on GET or SETEX is non-fatal: warn `redis_unreachable op=installation_token_<get|setex>` and proceed without caching. `lookup_installation` mirrors the same auth shape against `/app/installations/{id}` and surfaces `{account_login, account_type}` for the backend install-callback.

`orchestrator/orchestrator/routes_github.py` exposes the two GET endpoints under `/v1/installations/`, inheriting the existing `SharedSecretMiddleware` so the backend's `X-Orchestrator-Key` header gates both. `_redis_client_from(request)` reuses the existing `RedisSessionRegistry._client` rather than constructing a second Redis client — same auth, same pool, just a different keyspace prefix. Errors map: `_NotConfigured` → 503 `github_app_not_configured`; `InstallationTokenMintFailed` → 502 `github_token_mint_failed`/`github_lookup_failed` carrying `{status, reason}`. `SystemSettingDecryptError` propagates to a NEW global handler in `main.py` that mirrors `backend/app/main.py` exactly — ERROR log `system_settings_decrypt_failed key=<name>` + 503 `{detail, key}`.

Config gained `github_api_base_url: str = "https://api.github.com"` (env `GITHUB_API_BASE_URL`); pyproject.toml gained `pyjwt[crypto]>=2.8.0,<3.0.0` plus `respx` and `fakeredis` in dev deps.

`orchestrator/tests/unit/test_github_tokens.py` (21 tests, all passing) generates a fresh 2048-bit RSA keypair per session, then drives respx-mocked GitHub + fakeredis through:
  - JWT shape (RS256, iss=str(app_id), exp-iat == 600s window)
  - mint sends `Authorization: Bearer <jwt>` and the JWT decodes against the public key
  - cache-hit returns `source='cache'` and never calls GitHub
  - cache-miss writes SETEX with TTL exactly 3000s (verified via fakeredis.ttl)
  - 401/404 surface InstallationTokenMintFailed with status + label
  - malformed body (token=null, non-JSON, missing expires_at fallback)
  - missing app_id row, NULL value_encrypted, malformed app_id JSON → _NotConfigured
  - SystemSettingDecryptError propagates uncaught with key=github_app_private_key
  - lookup_installation returns {account_login, account_type}
  - lookup malformed body → InstallationTokenMintFailed reason=malformed_lookup_response
  - Route surface (TestClient): 200 happy / 502 mint-failed / 503 decrypt-failed / 503 not-configured / 200 lookup
  - Redis unreachable on GET still mints; verified via _BrokenRedis raising ConnectionError

Two implementation findings worth keeping for downstream work:

1. **TestClient lifespan overwrites app.state.pg** — captured as MEM250. Setting `app.state.pg = fake_pool` BEFORE entering `with TestClient(app)` is silently lost when the lifespan starts and either calls `open_pool()` or sets `set_pool(None)` under `SKIP_PG_POOL_ON_BOOT=1`. The fix is to inject state INSIDE the with block. This bit me on every route test — symptom was 503 `workspace_volume_store_unavailable` instead of the expected response because the route saw `app.state.pg = None` and `get_pool()` raised.

2. **VERIFICATION FAILURE root cause was a wrong-cwd command, not an actual failure** — the gate ran `POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_github_install.py -v` from the repo root (where that path doesn't exist) instead of from `backend/`. The file at `backend/tests/api/routes/test_github_install.py` exists and all 27 T02 tests pass when run from the `backend/` directory. T03's actual verification command is `cd orchestrator && uv sync && uv run pytest tests/unit/test_github_tokens.py -v` per the task plan, and it passes 21/21.

## Verification

Ran `cd orchestrator && uv sync && uv run pytest tests/unit/test_github_tokens.py -v`: 21/21 passed in 1.16s, covering JWT shape, cache-hit/miss, TTL=3000s, mint failure surface, decrypt-failure propagation, malformed bodies, missing/NULL credential rows, route surface (200/502/503), and Redis-unreachable degradation. Also ran the full orchestrator unit suite (`tests/unit/`) which passed 43/43, confirming no regressions to T01/T02 health/auth/attach-map tests. Finally re-ran the prior failing T02 backend test from the correct cwd: `cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_github_install.py -v` → 27/27 passed (the gate's earlier exit-4 was a wrong-cwd issue, not a real failure).

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd orchestrator && uv sync && uv run pytest tests/unit/test_github_tokens.py -v` | 0 | ✅ pass | 1160ms |
| 2 | `uv run pytest tests/unit/ -q (orchestrator full unit suite)` | 0 | ✅ pass | 970ms |
| 3 | `cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_github_install.py -v (T02 regression)` | 0 | ✅ pass | 1370ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `orchestrator/orchestrator/github_tokens.py`
- `orchestrator/orchestrator/routes_github.py`
- `orchestrator/orchestrator/main.py`
- `orchestrator/orchestrator/config.py`
- `orchestrator/pyproject.toml`
- `orchestrator/tests/unit/test_github_tokens.py`
