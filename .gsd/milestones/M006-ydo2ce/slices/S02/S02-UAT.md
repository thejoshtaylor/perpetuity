# S02: Persist user token at install time + GET /user for github_user_id — UAT

**Milestone:** M006-ydo2ce
**Written:** 2026-05-12T21:43:39.147Z

# S02 User Acceptance Tests

## Preconditions
- Fresh PostgreSQL with alembic revision s17_github_user_oauth_tokens applied
- Test user "alice" created with id = current_user.id
- respx mocking GitHub API: POST /login/oauth/access_token returns {"access_token": "ghu_test...", "refresh_token": "ghr_test...", "expires_in": 28800, "refresh_token_expires_in": 15897600, "scope": "repo,read:user"}
- respx mocking GET /user returns {"id": 42, "login": "alice"}
- Perpetuity backend running with SYSTEM_SETTINGS_ENCRYPTION_KEY set

## UAT: Happy Path — Token Persisted on OAuth Callback

**Steps:**
1. User initiates GitHub App install on their personal account, gets redirected to GitHub OAuth consent screen
2. User approves; GitHub redirects to `/api/v1/github/callback?code=auth_code&state=<JWT with user_id>`
3. Backend exchanges code for token_body (mocked: access + refresh + expires)
4. Backend calls GET /user with access token (mocked: returns id=42)
5. Backend upserts github_app_installations + github_user_oauth_tokens in single transaction

**Expected Outcomes:**
- github_user_oauth_tokens row exists: user_id = alice.id, github_user_id = 42, scopes = "repo,read:user"
- access_token_encrypted column is non-NULL BYTEA, decrypts to plaintext "ghu_test..."
- refresh_token_encrypted column is non-NULL BYTEA, decrypts to plaintext "ghr_test..."
- access_token_expires_at ≈ now() + 28800s (8h), refresh_token_expires_at ≈ now() + 15897600s (6mo)
- No plaintext tokens in application logs (only 4-char prefix "ghu_" / "ghr_")
- HTTP response redirects to frontend with success signal

**UAT Type:** Integration — Postgres + respx mocks + encryption round-trip

## UAT: Idempotent Upsert

**Steps:**
1. Run happy-path flow once; verify token row created
2. Simulate user reinstalling App (same state JWT + new code)
3. Backend processes callback again

**Expected Outcomes:**
- github_user_oauth_tokens row for (user_id, installation_id) is UPDATED, not duplicated
- Count of rows for user_id = alice.id remains 1
- New tokens (if mocked to differ) overwrite old ones
- updated_at timestamp changes

**UAT Type:** Integration — Postgres upsert semantics

## UAT: Backwards-Compat Rejection of Legacy State JWT

**Steps:**
1. Manually mint a legacy state JWT (no user_id claim, only team_id)
2. Call OAuth callback with legacy state + mock auth code
3. Backend processes callback

**Expected Outcomes:**
- HTTP 400 response
- Redirect location includes github_install_error=install_state_user_unknown
- No token row created
- Logs show "install_state_user_unknown" reason

**UAT Type:** Unit — JWT decoding validation

## UAT: Org Install Regression Check

**Steps:**
1. Call POST /api/v1/github/install-callback with installation_id + state (org-install path)
2. Backend processes callback

**Expected Outcomes:**
- github_app_installations row is created/updated
- NO github_user_oauth_tokens row is created (org installs don't need user tokens yet)
- M005-sqm8et behavior unchanged (install token path works as before)

**UAT Type:** Integration — Regression proof

## Edge Cases Not Proven By This UAT

- Refresh token expired at install time (T03 refresh-on-read, S03)
- Missing X-GitHub-User-Token header on repo-create (S04)
- Orchestrator receiving invalid token (S05)
- Frontend 409 rendering (S06)
- End-to-end against real GitHub.com (S07)
