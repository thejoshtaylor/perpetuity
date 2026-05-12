# M006-ydo2ce: Personal-account repo creation via GitHub user-to-server OAuth

**Vision:** When the Perpetuity GitHub App is installed on a personal (User) GitHub account, the existing "Create new repo on GitHub" flow actually creates a repo — using the installing user's stored OAuth access token for the POST /user/repos call that the installation token cannot make. Org-install behavior is unchanged.

## Success Criteria

- A user with a personal GitHub install can create a repo end-to-end from the existing UI in ~2s without seeing a 502.
- A user with a pre-M006 personal install sees a clear "Reinstall to grant repo creation access" CTA on first attempt, completes the reinstall, and succeeds on retry.
- Org-install repo creation works byte-identically to M005-sqm8et (no regression; orchestrator log still shows install token used, no user token involved).
- User OAuth access and refresh tokens at rest in the DB are Fernet-encrypted with the existing SYSTEM_SETTINGS_ENCRYPTION_KEY; no plaintext token columns.
- A 6-month refresh-token expiry surfaces as a clean 409 with the same reinstall CTA, not a 500 or silent failure.

## Slices

- [x] **S01: S01** `risk:medium` `depends:[]`
  > After this: `alembic upgrade head` against a fresh DB creates the table at revision `s17_github_user_oauth_tokens`. A unit test round-trips a plaintext access token through `GitHubUserOAuthToken.set_access_token()` / `get_access_token()` and asserts (a) the row's `access_token_encrypted` BYTEA does not contain the plaintext, and (b) decryption returns the exact input. The migration test exercises upgrade-from-s16 + downgrade round-trip without the autouse-`db`-session DDL hang.

- [ ] **S02: Persist user token at install time + `GET /user` for github_user_id** `risk:medium` `depends:[S01]`
  > After this: A respx-mocked GitHub returns a token-exchange payload and `{id: 42, login: "alice"}` from `GET api.github.com/user`. The OAuth callback completes; a SELECT against `github_user_oauth_tokens` shows one row with user_id = current_user.id, github_user_id = 42, scopes = "repo,read:user", both ciphertext columns non-NULL and not containing the raw tokens, and access_token_expires_at ≈ now() + 28800s / refresh_token_expires_at ≈ now() + 15897600s. Re-running the callback overwrites the same row.

- [ ] **S03: `get_user_access_token` refresh-on-read helper** `risk:medium` `depends:[S01]`
  > After this: Three unit tests against a respx-mocked token endpoint: (1) row exists, access token unexpired → returns the stored plaintext directly (no GitHub call). (2) row exists, access token expired but refresh token valid → POSTs to github.com/login/oauth/access_token with grant_type=refresh_token; helper updates the row and returns the new plaintext. (3) row exists, refresh token expired → GitHub returns 400 bad_refresh_token; the helper deletes the row and raises UserTokenUnavailable. (4) row does not exist → raises UserTokenUnavailable without making any HTTP call.

- [ ] **S04: Backend forwards `X-GitHub-User-Token` to orchestrator for personal installs** `risk:low` `depends:[S02,S03]`
  > After this: Three backend-route integration tests against the test client: (1) personal install + token row present for current_user.id → orchestrator receives a httpx call with X-GitHub-User-Token: <plaintext> header; backend returns 201. (2) personal install + no token row → backend returns 409 {"detail": "github_user_token_required", "installation_id": <int>} without calling the orchestrator. (3) org install → backend calls the orchestrator without the new header (M005-sqm8et regression-clean).

- [ ] **S05: Orchestrator prefers user token for personal installs** `risk:high` `depends:[S04]`
  > After this: Three orchestrator integration tests against a respx-mocked GitHub: (1) personal install + X-GitHub-User-Token: ghu_test header → orchestrator calls POST api.github.com/user/repos with Authorization: token ghu_test; GitHub mock returns 201. (2) personal install + no header → orchestrator returns 422 with detail == "user_token_required_for_personal_install". (3) org install (with or without header) → orchestrator calls POST api.github.com/orgs/<login>/repos with Authorization: token <install_token>; no header forwarding regardless of X-GitHub-User-Token presence.

- [ ] **S06: Frontend reinstall CTA on 409 + admin runbook** `risk:low` `depends:[S04]`
  > After this: Playwright test mocks the backend to return 409 {"detail":"github_user_token_required","installation_id":12345,"reason":"row_missing"} on the create-repository POST. The dialog renders an inline error with the documented copy AND a button labeled "Reinstall on GitHub". Clicking the button calls GET /api/v1/teams/{teamId}/github/install-url (mocked to return install_url), then opens that URL via window.open(url, "_blank", "noopener,noreferrer"). The component test asserts the inline error is visible, the button is visible, and clicking the button fetches the install URL and calls window.open with the right url + noopener,noreferrer flags.

- [ ] **S07: Final integrated acceptance against real GitHub.com** `risk:medium` `depends:[S05,S06]`
  > After this: A human operator performs each of the three CONTEXT scenarios end-to-end against a real GitHub App installation pointed at real GitHub accounts, captures evidence (screenshot of UI success state, docker compose logs orchestrator backend excerpt showing the correct branch was taken), and pastes those artifacts into M006-ydo2ce-SUMMARY.md under a 'Final Integrated Acceptance Evidence' section.

## Boundary Map

### S01 → S02

Produces:
- New table `github_user_oauth_tokens` with columns: `user_id UUID PK FK→user(id) ON DELETE CASCADE`, `installation_id BIGINT`, `github_user_id BIGINT`, `access_token_encrypted BYTEA NOT NULL`, `refresh_token_encrypted BYTEA NOT NULL`, `access_token_expires_at TIMESTAMPTZ NOT NULL`, `refresh_token_expires_at TIMESTAMPTZ NOT NULL`, `scopes TEXT NOT NULL`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()`.
- SQLModel `GitHubUserOAuthToken` exposing the same shape; helper methods `set_access_token(plain: str)` / `get_access_token() -> str` using `encrypt_setting` / `decrypt_setting`.
- Migration test fixture `_release_autouse_db_session` copy lands in the new test module.

Consumes:
- M004/S01 encryption helpers (`encrypt_setting`, `decrypt_setting`, `SystemSettingDecryptError`)
- Existing `user` table for FK target

### S01 → S03

Produces (same as S01 → S02):
- The table and model are required for the refresh helper to read/write rows.

Consumes:
- M004/S01 encryption helpers

### S02 → S04

Produces:
- A row in `github_user_oauth_tokens` for `user_id = current_user.id` after every successful install callback.
- `_resolve_installation_id_from_oauth_code` returns a tuple `(installation_id, github_user_id, access_token, refresh_token, expires_in, refresh_token_expires_in, scopes)` instead of `int`. *(Internal signature change; only one caller.)*

Consumes:
- S01's table and model.

### S03 → S04

Produces:
- Function `get_user_access_token(session, user_id: UUID) -> str` that returns a valid token or raises `UserTokenUnavailable`.
- Sentinel exception `UserTokenUnavailable` exported from `backend/app/core/github_user_tokens.py`.

Consumes:
- S01's table and model.

### S04 → S05

Produces:
- Outgoing httpx request to `orchestrator:8001` includes header `X-GitHub-User-Token: <plain access token>` whenever the resolved `account_type` is not `Organization`.
- Backend route returns 409 with body `{"detail": "github_user_token_required", "installation_id": <int>}` when `get_user_access_token` raises `UserTokenUnavailable`.

Consumes:
- S03's `get_user_access_token`.

### S04 → S06

Produces (same as S04 → S05):
- The 409 response shape that the frontend will render against.

Consumes:
- Nothing new from S03; the frontend cares only about the HTTP response shape.

### S05 → S07

Produces:
- Orchestrator route at `orchestrator/orchestrator/routes_github.py:330-358` now reads `X-GitHub-User-Token` header. When present AND `account_type != "Organization"`, uses the header value as the bearer token for `POST /user/repos`. When absent AND `account_type != "Organization"`, returns 422 `user_token_required_for_personal_install`. Org branch unchanged.

Consumes:
- S04's header forwarding.

### S06 → S07

Produces:
- `CreateGitHubRepoDialog.tsx` recognizes 409 with `detail: "github_user_token_required"` and renders the reinstall CTA.
- `docs/runbooks/m006-github-oauth-setup.md` exists with the one-time GitHub App config steps.

Consumes:
- S04's 409 response shape.
