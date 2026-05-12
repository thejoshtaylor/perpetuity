# S02: Persist user token at install time + `GET /user` for github_user_id

**Goal:** After a fresh GitHub App install via the OAuth Callback URL flow, a row lands in `github_user_oauth_tokens` for the installing Perpetuity user with Fernet-encrypted access + refresh tokens, the GitHub user id, both expiry timestamps, and the granted scopes. Org installs are unaffected.
**Demo:** A respx-mocked GitHub returns a token-exchange payload and `{id: 42, login: "alice"}` from `GET api.github.com/user`. The OAuth callback completes; a SELECT against `github_user_oauth_tokens` shows one row with user_id = current_user.id, github_user_id = 42, scopes = "repo,read:user", both ciphertext columns non-NULL and not containing the raw tokens, and access_token_expires_at ≈ now() + 28800s / refresh_token_expires_at ≈ now() + 15897600s. Re-running the callback overwrites the same row.

## Must-Haves

- Install-state JWT carries user_id claim; OAuth code-exchange returns full token tuple; _process_install_callback upserts the token row in the SAME database transaction as the github_app_installations upsert (no partial-install possible); GET /user resolves github_user_id; backwards-compat: legacy JWTs without user_id are rejected with install_state_user_unknown; redaction sweep finds no plaintext tokens in logs.

## Proof Level

- This slice proves: Integration — the install callback, when given a real-shape OAuth response, ends with both the install row and the token row populated atomically and decryptable. Full backend test environment with Postgres; respx mocks GitHub. No UAT.

## Integration Closure

Upstream surfaces consumed: S01's GitHubUserOAuthToken SQLModel + encrypt_user_token; existing _decode_install_state / _mint_install_state + OAuth code-exchange helper; existing _process_install_callback upsert; existing _orch_lookup_installation. New wiring: install-state JWT carries user_id; OAuth code-exchange returns the full token tuple; _process_install_callback upserts the token row in the same transaction; _fetch_github_user_id helper added.

## Verification

- New INFO log `github_user_token_persisted`, new WARN log `github_user_token_persist_failed` on the upsert exception path, new WARN log `github_oauth_no_refresh_expiry` on missing-refresh-TTL fallback. Access tokens and refresh tokens are NEVER logged in full — only the 4-char prefix (ghu_, ghr_).

## Tasks

- [x] **T01: Extend install-state JWT to carry `user_id` + update mint/decode + install-url route** `est:1h`
  S01 keys the token table on user_id; the install callback must know which Perpetuity user is doing the install. The only durable carrier across the GitHub redirect is the signed state JWT. Change _mint_install_state(team_id) to _mint_install_state(team_id, user_id); add user_id claim to payload. Update install-url route at :502 to pass current_user.id. In _decode_install_state, after the signature-verified jwt.decode block, validate user_id claim is present and parseable as a UUID; on missing or unparseable, raise HTTPException(400, detail=install_state_user_unknown). Add unit tests for round-trip, missing-user_id rejection, and malformed-user_id rejection.
  - Files: `backend/app/api/routes/github.py`, `backend/tests/api/routes/test_github_state_jwt.py`
  - Verify: cd backend && uv run pytest tests/api/routes/test_github_state_jwt.py -v

- [x] **T02: Refactor `_resolve_installation_id_from_oauth_code` to return `ResolvedOAuthInstall`** `est:1h`
  The function already POSTs to the GitHub token endpoint and receives the full payload but throws away every field except the access token. S02 needs all four token-payload fields downstream. Define @dataclass ResolvedOAuthInstall (installation_id, access_token, refresh_token, expires_in, refresh_token_expires_in, scope). Read all five token-payload fields from token_body; if any is missing or wrong-type, raise HTTPException(502, detail=github_oauth_exchange_failed) with new log reason token_payload_incomplete field=<name>. Return the dataclass.
  - Files: `backend/app/api/routes/github.py`, `backend/tests/api/routes/test_github_oauth_resolve.py`
  - Verify: cd backend && uv run pytest tests/api/routes/test_github_oauth_resolve.py -v

- [ ] **T03: `_fetch_github_user_id` helper + token persistence in `_process_install_callback`** `est:2h`
  This is the slice's main effect — installs cause token rows. Both the GitHub GET /user call and the DB upsert live here so the transactional guarantee in must-have (6) holds. Add async _fetch_github_user_id(access_token: str) -> int colocated with _resolve_installation_id_from_oauth_code. Change _process_install_callback signature to (session, installation_id, state, oauth_tuple: ResolvedOAuthInstall | None = None). After existing github_app_installations upsert, if oauth_tuple is not None: call _fetch_github_user_id, build upsert payload for github_user_oauth_tokens, encrypt tokens via encrypt_user_token, compute *_expires_at, execute INSERT ... ON CONFLICT (user_id) DO UPDATE on same session. Single session.commit() at end commits BOTH writes.
  - Files: `backend/app/api/routes/github.py`
  - Verify: cd backend && uv run pytest tests/api/routes/test_github_install_callback.py -v && uv run pytest tests/api/routes/ -v -k oauth

- [ ] **T04: Integration test `test_github_oauth_token_persistence.py` + redaction-sweep extension** `est:1.5h`
  Proves the cross-cutting invariant: GET install callback through respx-mocked GitHub ends with a decryptable token row, no plaintext anywhere in logs. Model on backend/tests/integration/test_m005_s01_team_secrets_e2e.py for stack-bringup discipline and on existing M005-sqm8et OAuth tests for respx mock shape. Test covers must-have (8) cases (a)-(g). Include MEM162 alembic skip-guard probing for s17_github_user_oauth_tokens revision in backend:latest. Extend scripts/redaction-sweep.sh to grep for ghu_ and ghr_ token prefixes IN COMBINATION with literal mocked test-token suffix.
  - Files: `backend/tests/integration/test_github_oauth_token_persistence.py`, `scripts/redaction-sweep.sh`
  - Verify: cd backend && uv run pytest tests/integration/test_github_oauth_token_persistence.py -v && bash scripts/redaction-sweep.sh

- [ ] **T05: Backwards-compat: legacy state JWT rejection test + M005-sqm8et regression check** `est:30m`
  T01 deliberately rejects legacy state JWTs without user_id; the rejection path must be tested explicitly so a future regression that quietly accepts the legacy shape is caught. Add test that mints a legacy-shape state JWT (manually construct jwt.encode without user_id claim, using settings.SECRET_KEY), calls the GET install-callback endpoint with that state + a mock OAuth code, asserts the redirect URL contains github_install_error=install_state_user_unknown. Add a second test asserting the existing org-install path (POST /github/install-callback with installation_id, state) STILL works without modification.
  - Files: `backend/tests/api/routes/test_github_install_callback.py`
  - Verify: cd backend && uv run pytest tests/api/routes/test_github_install_callback.py -v -k "legacy_state or org_install"

## Files Likely Touched

- backend/app/api/routes/github.py
- backend/tests/api/routes/test_github_state_jwt.py
- backend/tests/api/routes/test_github_oauth_resolve.py
- backend/tests/integration/test_github_oauth_token_persistence.py
- scripts/redaction-sweep.sh
- backend/tests/api/routes/test_github_install_callback.py
