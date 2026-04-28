# S02: Per-team GitHub connections (install flow + installation tokens)

**Goal:** Per-team GitHub App connections are installable end-to-end: a team admin retrieves a signed install URL from the backend, GitHub round-trips back to the public install-callback with the signed state token, the callback validates the state and persists a github_app_installations row scoped to the team, the team-settings UI lists installations, and the orchestrator can mint installation tokens on demand (JWT signed with the App private key → POST /app/installations/{id}/access_tokens) with a 50-minute Redis cache keyed by installation_id. The slice ships every backend HTTP surface plus the orchestrator HTTP token endpoint that S04's clone path will consume — but does not yet wire any clone or push flow.
**Demo:** Team admin clicks Install GitHub App → redirected to https://github.com/apps/<our-app>/installations/new with a signed state token → GitHub install round-trips back to /api/v1/github/install-callback → state validates → row in github_app_installations with installation_id + account_login + account_type → team settings UI shows the installation. Orchestrator mint_installation_token on first call hits GitHub; second call within 50 min hits Redis cache; cache miss after 50 min re-mints.

## Must-Haves

- ## Must-Haves
- New github_app_installations table (migration s06b) and SQLModel GitHubAppInstallation (id, team_id FK, installation_id BIGINT UNIQUE, account_login, account_type, created_at) plus GitHubAppInstallationPublic projection — covers R012 storage shape.
- GET /api/v1/teams/{team_id}/github/install-url returns {install_url, state} with state = HS256 JWT signed by SECRET_KEY, payload {team_id, nonce, exp(now+10m), iat, jti}. Install URL points to https://github.com/apps/<slug>/installations/new?state=<jwt> where <slug> is derived from github_app_client_id. Gated by team-admin (assert_caller_is_team_admin).
- POST /api/v1/github/install-callback is **public** (no team-membership check — GitHub redirects users' browsers here). Body: {installation_id, setup_action, state}. Validates JWT state token (signature, expiry, decodes team_id), confirms team exists, fetches account_login/account_type by hitting GitHub's GET /app/installations/{id} via the orchestrator (T03's lookup endpoint). Persists the row, returns 200 {installation_id, team_id, account_login, account_type}. State expired → 400 install_state_expired. Team mismatch → 400. Duplicate installation_id → 200 idempotent.
- GET /api/v1/teams/{team_id}/github/installations (team-admin) lists installations as GitHubAppInstallationPublic. DELETE /api/v1/teams/{team_id}/github/installations/{id} (team-admin) removes the row (uninstall record only — does not call GitHub).
- Orchestrator orchestrator/orchestrator/github_tokens.py exposes mint_installation_token(installation_id) -> dict (decrypts github_app_private_key via decrypt_setting, signs RS256 JWT with iss=github_app_id/iat-60s/exp+9m, POSTs <GITHUB_API_BASE_URL>/app/installations/{id}/access_tokens, returns {token, expires_at, source:'mint'}) and get_installation_token(installation_id) -> dict (Redis-cache-first under key gh:installtok:{id} with 50-min TTL, mints on miss). Also lookup_installation(installation_id) -> {account_login, account_type} for the install-callback.
- Orchestrator HTTP routes GET /v1/installations/{id}/token and GET /v1/installations/{id}/lookup (shared-secret-gated) return JSON shapes above. Tokens NEVER appear in any log line — only token_prefix=<first4> and source flag.
- GITHUB_API_BASE_URL knob lands in orchestrator/orchestrator/config.py (default https://api.github.com); backend gets GITHUB_APP_INSTALL_URL_BASE (default https://github.com).
- Fail-loud on Fernet decrypt failure: orchestrator's existing SystemSettingDecryptError global handler maps to 503 with key=github_app_private_key (S01 contract — verified end-to-end here for the first time over HTTP, closing S01's known-limitation note).
- Orchestrator pyproject MUST add pyjwt[crypto]>=2.8.0,<3.0.0 (RS256 needs the cryptography backend).
- Zero token leakage: e2e log sweep across backend + orchestrator finds no gho_/ghs_/ghu_/ghr_/github_pat_ prefix and no -----BEGIN PEM header.
- ## Threat Surface
- **Abuse**: install-callback is public — without state-token validation an attacker could attach an arbitrary installation_id to a victim team. Mitigated by HS256-signed state with 10-min expiry and a team_id claim. CSRF on GET install-url is N/A (no side effects).
- **Data exposure**: GitHub App private key (PEM) and minted installation tokens are highly sensitive — both transit the orchestrator only at decrypt-and-sign / mint-and-cache call sites. Tokens MUST NOT appear in any log line; only the 4-char token_prefix may be logged. Redis stores raw tokens (acceptable per D021 — short TTL, internal network, password-authed).
- **Input trust**: install-callback body installation_id is GitHub-supplied but always accompanied by the signed state — verify state first, then trust the id. State JWT decoder MUST use algorithms=['HS256'] (never 'none'). The setup_action field is informational only.
- ## Requirement Impact
- **Requirements touched**: R012 (per-team GitHub connections — primary), R037 (installation tokens cached in Redis, 50-min TTL — primary), R053 (Fernet decrypt fail-loud — re-verified in T03's first-real-HTTP-decrypt path), R054 (structured logs at git-op boundaries).
- **Re-verify**: S01's SystemSettingDecryptError global handler is exercised over HTTP for the first time (S01 only proved log shape, not 503-status). S01's decrypt_setting import target stability for orchestrator-side callers is exercised — confirms the parallel-copy module shape (MEM230) was the right call.
- **Decisions revisited**: D019, D020, D021 all locked — no reconsideration. D025's fail-loud-on-decrypt extends from webhook-secret to private-key.
- ## Proof Level
- This slice proves: contract + integration (real Postgres, real Redis, mocked GitHub API via in-network sidecar).
- Real runtime required: yes (compose db + redis + sibling backend + ephemeral orchestrator with GITHUB_API_BASE_URL overridden to a tiny in-network mock-github container).
- Human/UAT required: no — final UAT lives in S07 against a real GitHub test org.
- ## Verification
- backend/tests/migrations/test_s06b_github_app_installations_migration.py — alembic up/down round-trip; new table exists with correct columns + UNIQUE on installation_id.
- backend/tests/api/routes/test_github_install.py — unit tests for install-url state JWT shape + signature + expiry; install-callback happy-path (with _FakeAsyncClient stubbing the orchestrator lookup), expired-state 400, mismatched-team 400, duplicate-installation idempotent 200, public-callback bypasses auth, list/delete team-admin auth gates.
- orchestrator/tests/unit/test_github_tokens.py — respx-mocked mint_installation_token happy-path (JWT iss/iat/exp shape, RS256 signature verifies against test public key, 401 from GitHub surfaces as a structured error), get_installation_token cache-hit / cache-miss / TTL using a fakeredis fixture, decrypt-failure → SystemSettingDecryptError (caller does not catch — global handler will translate to 503).
- backend/tests/integration/test_m004_s02_github_install_e2e.py — full round-trip against the live compose stack: seed all four credentials, signup as team admin, GET install-url, decode state JWT, POST install-callback. Ephemeral orchestrator boots with GITHUB_API_BASE_URL pointed at an in-network mock-github sidecar that returns canned /app/installations/{id} and /app/installations/{id}/access_tokens responses (and verifies the inbound RS256 JWT against the test public key). Test calls orchestrator's /v1/installations/{id}/token twice — first call logs installation_token_minted source='mint', second call logs installation_token_cache_hit source='cache'. DELETE installation, GET list → empty. docker logs of both backend and orchestrator are swept for token prefixes and PEM headers — zero matches required.
- ## Observability / Diagnostics
- Runtime signals: INFO github_install_url_issued team_id=<uuid> actor_id=<uuid> state_jti=<8char>; INFO github_install_callback_accepted team_id=<uuid> installation_id=<id> account_login=<login> account_type=<type> state_jti=<8char>; WARNING github_install_callback_state_invalid reason=<expired|bad_signature|team_unknown> presented_jti=<8char>; INFO installation_token_minted installation_id=<id> token_prefix=<first4>...; INFO installation_token_cache_hit installation_id=<id> token_prefix=<first4>...; ERROR installation_token_mint_failed installation_id=<id> reason=<github_status>:<message>. ERROR system_settings_decrypt_failed key=github_app_private_key flows through S01's global handler (orchestrator side too — handler must be added in T03).
- Inspection surfaces: GET /api/v1/teams/{team_id}/github/installations; psql SELECT id, team_id, installation_id, account_login, account_type, created_at FROM github_app_installations; docker exec perpetuity-redis-1 redis-cli --pass <pw> KEYS 'gh:installtok:*'; docker exec perpetuity-redis-1 redis-cli --pass <pw> TTL 'gh:installtok:<id>'.
- Failure visibility: install-callback failures return structured 4xx with detail field naming the failure mode; orchestrator token-mint failures surface as 502 with {detail:'github_token_mint_failed', reason:'<status>:<short>'}. Decrypt failures surface S01's 503 shape unchanged.
- Redaction constraints: token plaintext NEVER in logs (only 4-char prefix); PEM plaintext NEVER in logs or error bodies; HMAC of state JWT is fine to log via the 8-char jti field; admin actor uuid is fine.
- ## Integration Closure
- Upstream surfaces consumed: S01's decrypt_setting (orchestrator side) + the four registered keys (github_app_id, github_app_client_id, github_app_private_key); S01's SystemSettingDecryptError handler shape; M002's RedisSessionRegistry shape (parallel namespace gh:installtok: — not a sibling module, just a separate key prefix on the same client); M002's shared-secret middleware on the orchestrator.
- New wiring introduced in this slice: backend app/api/routes/github.py mounted in app/api/main.py; orchestrator routes_github.py mounted in orchestrator/main.py; new orchestrator config knob GITHUB_API_BASE_URL; new backend config knob GITHUB_APP_INSTALL_URL_BASE; orchestrator SystemSettingDecryptError exception handler (added in T03 to mirror backend's S01 contract).
- What remains before the milestone is truly usable end-to-end: S03 (team-mirror container — uses get_installation_token for env-on-exec credentials), S04 (clone + push paths), S05 (webhook receiver — independent of S02), S06 (frontend), S07 (real-GitHub UAT).

## Proof Level

- This slice proves: contract + integration. Real runtime required (compose db + redis + sibling backend + ephemeral orchestrator with GITHUB_API_BASE_URL pointed at an in-network mock-github sidecar). Human UAT deferred to S07 against a real GitHub test org.

## Integration Closure

Upstream surfaces consumed: S01's decrypt_setting (orchestrator-side) + all four registered GitHub App keys; S01's SystemSettingDecryptError contract (first real HTTP exercise — closes S01's known-limitation note); M002's RedisSessionRegistry client (parallel namespace gh:installtok:); M002's shared-secret middleware. New wiring: backend app/api/routes/github.py mounted in api/main.py; orchestrator routes_github.py mounted in main.py; new GITHUB_API_BASE_URL config knob; orchestrator SystemSettingDecryptError handler. Remaining for milestone: S03 (mirror container — consumes get_installation_token), S04 (clone/push — consumes both), S05 (webhook), S06 (frontend), S07 (real-GitHub UAT).

## Verification

- Required INFO log keys: github_install_url_issued, github_install_callback_accepted, installation_token_minted, installation_token_cache_hit, github_installation_deleted. Required WARNING: github_install_callback_state_invalid. Required ERROR: installation_token_mint_failed, plus the inherited S01 system_settings_decrypt_failed key=github_app_private_key (orchestrator side handler added in T03). Token prefixes are the only token-derived value permitted in logs; PEM never appears.

## Tasks

- [x] **T01: Add github_app_installations migration + SQLModel** `est:2h`
  Create alembic revision s06b_github_app_installations (down_revision=s06_system_settings_sensitive) that creates the github_app_installations table: id UUID PK, team_id UUID FK→team(id) ON DELETE CASCADE, installation_id BIGINT UNIQUE NOT NULL, account_login VARCHAR(255) NOT NULL, account_type VARCHAR(64) NOT NULL (CHECK in {'Organization','User'}), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(). Add SQLModel GitHubAppInstallation and the public projection GitHubAppInstallationPublic to backend/app/models.py. Add a migration test mirroring the M002 pattern (release the autouse db Session + dispose engine before alembic, restore head after — see test_s01_migration.py). The model layer stays purely declarative — no API logic here. Keep installation_id BIGINT (GitHub installation ids are int64); pydantic-validate as int.

## Negative Tests

- **Boundary conditions**: a second insert with the same installation_id MUST raise IntegrityError (UNIQUE); inserting with account_type='Bot' MUST raise CheckViolation; deleting the parent team MUST cascade-delete the installation row.
- **Migration reversibility**: downgrade then re-upgrade must leave schema byte-identical (snapshot via information_schema query before/after).
  - Files: `backend/app/alembic/versions/s06b_github_app_installations.py`, `backend/app/models.py`, `backend/tests/migrations/test_s06b_github_app_installations_migration.py`
  - Verify: cd backend && POSTGRES_PORT=5432 uv run alembic heads | grep -q 's06b_github_app_installations' && POSTGRES_PORT=5432 uv run pytest tests/migrations/test_s06b_github_app_installations_migration.py -v

- [x] **T02: Backend GitHub install flow: signed-state install-url + public install-callback + list/delete** `est:5h`
  Add backend/app/api/routes/github.py mounted in backend/app/api/main.py. Endpoints:

- GET /api/v1/teams/{team_id}/github/install-url (team-admin via assert_caller_is_team_admin) — reads github_app_client_id from system_settings (404 'github_app_not_configured' if missing), mints HS256 JWT with secrets.token_urlsafe(16) jti, payload {team_id, jti, iat, exp=iat+600, iss='perpetuity-install', aud='github-install'}, returns {install_url: f"{settings.GITHUB_APP_INSTALL_URL_BASE}/apps/{client_id}/installations/new?state=<jwt>", state: <jwt>, expires_at: <iso8601>}. Uses settings.SECRET_KEY for signing.

- POST /api/v1/github/install-callback (PUBLIC — no auth dep, GitHub redirects browsers here). Body: {installation_id: int, setup_action: str, state: str}. Decode state with algorithms=['HS256'], audience='github-install'. On jwt.ExpiredSignatureError → 400 {detail:'install_state_expired'}; on jwt.InvalidTokenError → 400 {detail:'install_state_invalid'}; on team mismatch → 400 {detail:'install_state_team_unknown'}. Then call orchestrator GET /v1/installations/{installation_id}/lookup with X-Orchestrator-Key (uses _FakeAsyncClient pattern in tests, real httpx in prod) to get account_login/account_type. UPSERT row (ON CONFLICT (installation_id) DO UPDATE SET team_id=EXCLUDED.team_id, account_login=EXCLUDED.account_login, account_type=EXCLUDED.account_type) so duplicate callback is idempotent; if existing row's team_id differs, log WARNING github_install_callback_team_reassigned and accept. Returns 200 GitHubAppInstallationPublic.

- GET /api/v1/teams/{team_id}/github/installations (team-admin) — list installations for team ordered by created_at DESC.

- DELETE /api/v1/teams/{team_id}/github/installations/{id} (team-admin) — delete row by primary key, 404 if missing or wrong team_id. Does NOT call GitHub.

Logging: INFO github_install_url_issued team_id=<uuid> actor_id=<uuid> state_jti=<first8>; INFO github_install_callback_accepted team_id=<uuid> installation_id=<id> account_login=<login> account_type=<type> state_jti=<first8>; WARNING github_install_callback_state_invalid reason=<expired|bad_signature|team_unknown> presented_jti=<first8 or NA>; INFO github_installation_deleted actor_id=<uuid> team_id=<uuid> installation_id=<id>. Never log full state JWT. Add settings.GITHUB_APP_INSTALL_URL_BASE (default 'https://github.com') in backend/app/core/config.py.

Unit tests in backend/tests/api/routes/test_github_install.py: install-url state shape + signature verifies + 10m expiry; install-url 404 when client_id unset; install-callback happy path with _FakeAsyncClient stub for orchestrator lookup; expired-state 400; bad-signature 400; team-not-found 400; duplicate-installation-id idempotent 200; list returns rows ordered by created_at; delete 404 on wrong team_id; team-admin auth gate (member-only returns 403, public callback bypasses auth).

## Failure Modes

| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| Orchestrator /v1/installations/{id}/lookup | Surface 502 {detail:'github_lookup_failed', reason:<status>} — install-callback row NOT created | Use 10s httpx timeout; on timeout 502 same shape with reason='timeout' | 502 reason='malformed_lookup_response'; do NOT persist row |
| Postgres on UPSERT | Let global handler 500 — installation rollback is automatic | 5s default | N/A |
| system_settings GET (github_app_client_id) | 404 'github_app_not_configured' | N/A | N/A |

## Load Profile

- **Shared resources**: orchestrator HTTP pool (one call per install-callback), Postgres connection pool (one tx per install-callback / list / delete).
- **Per-operation cost**: install-url is pure CPU + 1 system_settings SELECT; install-callback is 1 orchestrator GET + 1 UPSERT; list is 1 SELECT; delete is 1 DELETE.
- **10x breakpoint**: install-callback throughput is bounded by orchestrator's GitHub API rate limit (5000/h per app), well above any plausible install rate.

## Negative Tests

- **Malformed inputs**: state='' → 400 install_state_invalid; state='not.a.jwt' → 400; installation_id negative → 422 (pydantic int constraint); body missing fields → 422.
- **Error paths**: orchestrator returns 503 → install-callback returns 502 github_lookup_failed; team_id in state doesn't exist in DB → 400 install_state_team_unknown; concurrent duplicate callback (same installation_id) → second is idempotent 200 with same row.
- **Boundary conditions**: state expired by 1 second → 400 install_state_expired; state issued 9m59s ago → 200; state with aud field set to wrong audience → 400; state signed with different key → 400.
  - Files: `backend/app/api/routes/github.py`, `backend/app/api/main.py`, `backend/app/core/config.py`, `backend/app/models.py`, `backend/tests/api/routes/test_github_install.py`
  - Verify: cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_github_install.py -v

- [x] **T03: Orchestrator installation token mint + Redis cache + HTTP endpoints** `est:5h`
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
  - Files: `orchestrator/pyproject.toml`, `orchestrator/orchestrator/github_tokens.py`, `orchestrator/orchestrator/routes_github.py`, `orchestrator/orchestrator/config.py`, `orchestrator/orchestrator/main.py`, `orchestrator/tests/unit/test_github_tokens.py`
  - Verify: cd orchestrator && uv sync && uv run pytest tests/unit/test_github_tokens.py -v

- [x] **T04: End-to-end install-flow + token-cache proof against compose stack with mock-github sidecar** `est:6h`
  Add backend/tests/integration/test_m004_s02_github_install_e2e.py that exercises the full install + token-mint + cache contract end-to-end. Pieces:

1. Skip-guard probing baked images for both s06b alembic revision (in backend:latest) and the new orchestrator/orchestrator/github_tokens.py module (in orchestrator:latest). Hint: docker compose build backend orchestrator.
2. Autouse cleanup fixture DELETEs github_app_installations rows AND the four github_app_* system_settings rows before AND after each test (mirrors MEM246 pattern from S01).
3. Module-local helper _seed_github_app_credentials(backend_url, admin_token, public_key_pem, private_key_pem, app_id) PUTs github_app_id (int), github_app_client_id ('perpetuity-test'), github_app_private_key (the synthetic RSA key the test generates).
4. Module-local helper _boot_mock_github(public_key_pem, fixed_token, app_id) that starts a tiny FastAPI app inside a python:3.12-slim sibling container on perpetuity_default (named mock-github-<uuid>) by mounting backend/tests/integration/fixtures/mock_github_app.py and running uvicorn. The mock app reads PUBLIC_KEY_PEM, FIXED_TOKEN, GITHUB_APP_ID from env, exposes POST /app/installations/{id}/access_tokens (verifies inbound RS256 JWT against PUBLIC_KEY_PEM with iss=<app_id>; on success returns {'token': fixed_token, 'expires_at': '<iso8601 +1h>'}) and GET /app/installations/{id} (returns {'account': {'login': 'test-org', 'type': 'Organization'}, 'id': id}). Yields the container's compose-DNS URL http://mock-github-<uuid>:8080.
5. Module-local helper _boot_orch_with_mock(mock_github_url) parameterizes the existing ephemeral-orchestrator pattern (MEM197) to set GITHUB_API_BASE_URL=<mock_github_url>, ORCHESTRATOR_API_KEY=<test-only random>, SYSTEM_SETTINGS_ENCRYPTION_KEY=SYSTEM_SETTINGS_ENCRYPTION_KEY_TEST. Proves readiness via the MEM198 docker-exec urllib probe.
6. Module-local helper _boot_sibling_backend(orch_url, orch_key) boots a sibling backend pointed at the ephemeral orchestrator (NOT the compose orchestrator) so the install-callback's lookup call hits the mock-github through the right orchestrator. Reuses the conftest backend_url shape but parameterized.
7. Test scenario A — install URL + state JWT shape: signup superuser, seed credentials, signup team-admin user with a team, GET /api/v1/teams/{tid}/github/install-url, decode the state JWT in-test against SECRET_KEY (audience='github-install'), assert team_id matches, assert exp is ~10m in the future.
8. Test scenario B — install-callback round-trip: POST install-callback {installation_id:42, setup_action:'install', state:<jwt from A>}; assert 200 with account_login='test-org' account_type='Organization'; GET /api/v1/teams/{tid}/github/installations returns 1 row.
9. Test scenario C — duplicate install-callback (same installation_id) → 200 idempotent, list still 1 row.
10. Test scenario D — installation token mint + cache: hit ephemeral orchestrator GET /v1/installations/42/token (with X-Orchestrator-Key) → expect 200 source='mint' token=fixed_token; second call within the same test → 200 source='cache' same token. Use docker exec into compose redis to verify KEYS gh:installtok:* has one match and TTL is in (1, 3001).
11. Test scenario E — expired state token: re-sign a state JWT with exp=now-60 using SECRET_KEY; POST install-callback → expect 400 detail='install_state_expired'.
12. Test scenario F — decrypt-failure surfaces 503 over HTTP (closes S01 known-limitation): UPDATE system_settings via psql to set value_encrypted = E'\\x00bad' for github_app_private_key; flush the orchestrator's redis cache key for installation 42; then call ephemeral orchestrator GET /v1/installations/42/token → expect 503 detail='system_settings_decrypt_failed' key='github_app_private_key'. ERROR log line system_settings_decrypt_failed key=github_app_private_key MUST appear in docker logs <ephemeral_orchestrator>.
13. Final redaction sweep: docker logs <sibling_backend> and docker logs <ephemeral_orchestrator> MUST NOT contain gho_, ghs_, ghu_, ghr_, github_pat_ (note: the mock-github container's logs DO contain the issued token by design — only backend/orchestrator logs are swept); MUST NOT contain -----BEGIN. Required positive markers: github_install_url_issued, github_install_callback_accepted, installation_token_minted, installation_token_cache_hit, system_settings_decrypt_failed key=github_app_private_key. The mock-github cleanup fixture stops the mock-github container regardless of test outcome.

## Failure Modes

| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| compose db/redis/orchestrator | _e2e_env_check skip | Same | Same |
| Baked images missing s06b/github_tokens.py | Skip with `docker compose build backend orchestrator` hint | N/A | N/A |
| mock-github container fails to start | pytest.fail with mock-github docker logs tail | 30s readiness deadline | N/A |
| ephemeral orchestrator boot | pytest.fail with orchestrator docker logs tail | 60s | N/A |

## Load Profile

- This is a single-process e2e — no concurrency concerns inside the test.
- Fixed installation_id=42 keeps the cache key deterministic for KEYS/TTL probes.

## Negative Tests

Covered by the scenario list above (E expired state, F decrypt failure, redaction sweep). Optional scenario G — orchestrator returns 502 on GitHub 401 (mock-github toggled to reject the JWT); install-callback's lookup propagates as 502 github_lookup_failed.
  - Files: `backend/tests/integration/test_m004_s02_github_install_e2e.py`, `backend/tests/integration/fixtures/mock_github_app.py`, `backend/tests/integration/fixtures/__init__.py`
  - Verify: docker compose build backend orchestrator && cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m004_s02_github_install_e2e.py -v

## Files Likely Touched

- backend/app/alembic/versions/s06b_github_app_installations.py
- backend/app/models.py
- backend/tests/migrations/test_s06b_github_app_installations_migration.py
- backend/app/api/routes/github.py
- backend/app/api/main.py
- backend/app/core/config.py
- backend/tests/api/routes/test_github_install.py
- orchestrator/pyproject.toml
- orchestrator/orchestrator/github_tokens.py
- orchestrator/orchestrator/routes_github.py
- orchestrator/orchestrator/config.py
- orchestrator/orchestrator/main.py
- orchestrator/tests/unit/test_github_tokens.py
- backend/tests/integration/test_m004_s02_github_install_e2e.py
- backend/tests/integration/fixtures/mock_github_app.py
- backend/tests/integration/fixtures/__init__.py
