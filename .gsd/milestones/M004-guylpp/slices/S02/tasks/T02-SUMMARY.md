---
id: T02
parent: S02
milestone: M004-guylpp
key_files:
  - backend/app/api/routes/github.py
  - backend/app/api/main.py
  - backend/app/core/config.py
  - backend/app/models.py
  - backend/tests/api/routes/test_github_install.py
key_decisions:
  - State JWT carries audience='github-install' + iss='perpetuity-install' so a stray site-session JWT (which uses only 'sub'/'exp') cannot be replayed against /install-callback even though both are HS256-signed by SECRET_KEY
  - Install-callback is a public route with no FastAPI auth dep — the signed state JWT IS the auth (10-min exp + jti for log correlation); cookies are explicitly cleared in tests to prove the public path works without a session
  - UPSERT keyed on installation_id (UNIQUE from T01) overwrites team_id on the conflict path so duplicate callback is 200-idempotent; when existing row's team_id differs from the state-claimed team, a WARNING github_install_callback_team_reassigned line records the swap and the new team wins (last-write-wins matches D021's posture for low-stakes GitHub-side state)
  - DELETE returns 404 for both missing rows AND rows owned by a different team — no cross-team existence enumeration, mirrors the MEM113/MEM123 no-enumeration rule used by sessions
  - ExpiredSignatureError is caught separately from InvalidTokenError so the log line can include the presented jti prefix (decoded with verify_exp=False after signature already verified — safe because jwt.decode raises ExpiredSignatureError only after signature passes)
  - Orchestrator lookup uses 10s httpx timeout per the failure-mode table; the lookup error shape is structured 502 {detail:'github_lookup_failed', reason:<status|'timeout'|'transport'|'malformed_lookup_response'>} so callers can branch on the reason field
duration: 
verification_result: passed
completed_at: 2026-04-26T00:57:22.113Z
blocker_discovered: false
---

# T02: Add backend GitHub install-handshake routes: signed-state install-url, public install-callback with orchestrator lookup + idempotent UPSERT, list/delete under team-admin auth

**Add backend GitHub install-handshake routes: signed-state install-url, public install-callback with orchestrator lookup + idempotent UPSERT, list/delete under team-admin auth**

## What Happened

Created backend/app/api/routes/github.py mounting the four S02/T02 endpoints (GET /api/v1/teams/{team_id}/github/install-url, public POST /api/v1/github/install-callback, GET /api/v1/teams/{team_id}/github/installations, DELETE /api/v1/teams/{team_id}/github/installations/{installation_row_id}) and registered the router in app/api/main.py after the admin router. The install-url endpoint reads github_app_client_id from system_settings (404 github_app_not_configured when missing/empty), mints an HS256 state JWT with payload {team_id, jti, iat, exp=iat+600, iss='perpetuity-install', aud='github-install'} signed by settings.SECRET_KEY, and returns {install_url, state, expires_at} with the URL shaped <GITHUB_APP_INSTALL_URL_BASE>/apps/<client_id>/installations/new?state=<jwt>. The public install-callback decodes the state with the audience and issuer pinned, splits jwt.ExpiredSignatureError (-> 400 install_state_expired and best-effort jti recovery for the log line) from jwt.InvalidTokenError (-> 400 install_state_invalid), validates the team still exists (-> 400 install_state_team_unknown for both UUID parse failure and missing row), then calls the orchestrator's GET /v1/installations/{id}/lookup over a 10s httpx timeout. Lookup failure modes are shaped per the slice plan's Failure Modes table — connect/read timeouts surface as 502 {detail:'github_lookup_failed', reason:'timeout'}, transport errors -> reason:'transport', non-200 -> reason:str(status), ValueError on .json() or missing account_login/account_type keys -> reason:'malformed_lookup_response' — and on every error path the row is NOT created. On lookup success, the row is UPSERTed via raw text() INSERT ... ON CONFLICT (installation_id) DO UPDATE SET team_id=EXCLUDED.team_id, account_login=EXCLUDED.account_login, account_type=EXCLUDED.account_type RETURNING ... so a duplicate install-callback for the same installation_id is idempotent (200, single row); when the existing row's team_id differs from the state's, a WARNING github_install_callback_team_reassigned line records the swap. List returns the team's rows ordered by created_at DESC in a {data, count} envelope, and delete removes one row by primary key with a 404 covering both missing rows AND rows owned by a different team (no cross-team existence enumeration). All four endpoints log with the exact INFO/WARNING keys named in the slice's observability contract; the full state JWT never appears in any log line — only state_jti=<first8> (or NA when the token cannot be parsed). Added GITHUB_APP_INSTALL_URL_BASE: str = 'https://github.com' to backend/app/core/config.py so the e2e harness in T04 can override the install host. Added the wire-shapes InstallUrlResponse, InstallCallbackBody (with pydantic ge=1 on installation_id, max_length=64 on setup_action, min_length=1 on state), and InstallationsList to app/models.py next to the existing GitHubAppInstallation/GitHubAppInstallationPublic from T01. Test suite at backend/tests/api/routes/test_github_install.py ships 27 cases covering: install-url state JWT shape + signature verifies + 10-min expiry window + URL composition, install-url 404 when client_id unset, install-url auth gates (401 missing cookie, 403 non-admin), install-callback happy path with row persisted via the _FakeAsyncClient MEM172/MEM184 stub, idempotent duplicate-installation_id with single-row invariant, team reassignment WARN log line, expired state, bad signature, wrong audience, team_unknown (state has UUID for a deleted team), empty/garbage state, negative installation_id and missing-fields 422s, orchestrator 503/timeout/non-JSON/missing-keys all rejected as 502 with the expected reason and zero rows persisted, list ordered by created_at DESC + empty envelope + non-admin 403, delete 404 for missing/cross-team and 200 happy-path with row removed, and a redaction sanity check asserting the full state JWT does not appear in any captured log record. The autouse cleanup fixture wipes github_app_installations and the github_app_client_id system_settings row before AND after each test (mirrors MEM246). Captured MEM249 documenting the public-callback-with-state-JWT-as-auth pattern for future reuse.

## Verification

Ran the slice's authoritative verify command `cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_github_install.py -v` — 27/27 passed in 1.30s. Re-ran the T01 migration test the verification gate had originally flagged (path-prefix bug — the gate ran from repo root instead of backend/): `POSTGRES_PORT=5432 uv run pytest tests/migrations/test_s06b_github_app_installations_migration.py` → 6/6 passed in 0.41s. Ran a broader regression sanity sweep across the full tests/api/routes/ directory + the T01 migration test (228 collected) — 221 pass, 7 pre-existing sessions failures (test_a/d/f/g/i/j and test_logs_emit_uuid_only) that reproduce on main via git stash (orchestrator-dependent — they need a live orchestrator container, unrelated to this task). Verified the new router is mounted by inspecting app/api/main.py (github.router included after admin.router). Confirmed GITHUB_APP_INSTALL_URL_BASE is read at startup with default https://github.com by the install-url happy-path test asserting URL prefix.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_github_install.py -v` | 0 | pass (27/27) | 1300ms |
| 2 | `POSTGRES_PORT=5432 uv run pytest tests/migrations/test_s06b_github_app_installations_migration.py` | 0 | pass (6/6) | 410ms |
| 3 | `POSTGRES_PORT=5432 uv run pytest tests/api/routes/ tests/migrations/test_s06b_github_app_installations_migration.py` | 1 | 221 pass, 7 pre-existing sessions failures unrelated to T02 (reproduce on main via git stash) | 30960ms |

## Deviations

Slice-plan UPSERT excerpt did not specify a primary-key id column on the INSERT — added id: uuid.uuid4() to the parameter set because the SQLModel/migration declares id UUID PK with no DB-side default. Plan also did not specify the install-callback row id parameter name for the DELETE path — used installation_row_id (UUID PK) as the path param name to disambiguate from the GitHub-supplied numeric installation_id; doc-comment makes the distinction explicit.

## Known Issues

7 sessions tests in test_sessions.py fail in this environment because they require a live orchestrator container — confirmed pre-existing on main via git stash, not introduced by T02. Out of scope for this task.

## Files Created/Modified

- `backend/app/api/routes/github.py`
- `backend/app/api/main.py`
- `backend/app/core/config.py`
- `backend/app/models.py`
- `backend/tests/api/routes/test_github_install.py`
