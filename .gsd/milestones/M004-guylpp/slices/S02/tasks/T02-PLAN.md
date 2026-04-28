---
estimated_steps: 21
estimated_files: 5
skills_used: []
---

# T02: Backend GitHub install flow: signed-state install-url + public install-callback + list/delete

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

## Inputs

- ``backend/app/alembic/versions/s06b_github_app_installations.py` — migration that the route depends on (T01 output)`
- ``backend/app/models.py` — GitHubAppInstallation from T01 used as the SQLModel target`
- ``backend/app/api/routes/admin.py` — system_settings reader pattern + _VALIDATORS registry (read github_app_client_id)`
- ``backend/app/api/team_access.py` — assert_caller_is_team_admin helper used as auth gate`
- ``backend/app/api/routes/sessions.py` — httpx.AsyncClient pattern + _FakeAsyncClient test stub (MEM172/MEM184) for orchestrator calls`
- ``backend/app/core/security.py` — existing JWT helpers + SECRET_KEY usage to mirror for state token`
- ``backend/app/api/main.py` — router include pattern; mount the new github router after admin`

## Expected Output

- ``backend/app/api/routes/github.py` — new module exporting router with the four endpoints, state JWT helpers, structured log lines`
- ``backend/app/api/main.py` — adds `from app.api.routes import github` and `api_router.include_router(github.router)` after admin`
- ``backend/app/core/config.py` — adds `GITHUB_APP_INSTALL_URL_BASE: str = 'https://github.com'``
- ``backend/app/models.py` — adds InstallCallbackBody, InstallUrlResponse, InstallCallbackResponse request/response shapes (or co-located in routes/github.py — keep one source)`
- ``backend/tests/api/routes/test_github_install.py` — full unit suite covering happy path, all 4xx shapes, auth gates, idempotency`

## Verification

cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_github_install.py -v
