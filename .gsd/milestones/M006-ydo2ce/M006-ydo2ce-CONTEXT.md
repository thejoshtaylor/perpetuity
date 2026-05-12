# M006-ydo2ce: Personal-account repo creation via GitHub user-to-server OAuth — Context

**Gathered:** 2026-05-12
**Status:** Ready for planning

## Project Description

M005-sqm8et's GitHub repo-creation feature works for installations on **organization** accounts but 502s for installations on **personal (User) accounts**. The orchestrator route at `orchestrator/orchestrator/routes_github.py:330-358` correctly branches on `account_type`, but for personal accounts it calls `POST /user/repos` using the installation access token — and GitHub's docs are unambiguous: `POST /user/repos` only accepts OAuth user tokens, classic PATs, and fine-grained user tokens. There is no `works_with_github_apps` marker on that endpoint. The server returns 403 "Resource not accessible by integration"; the orchestrator translates that into a 502 back to the backend.

This milestone makes personal-account repo creation work by using the **installing user's OAuth access token** for the `POST /user/repos` call. The OAuth code-exchange flow already runs during install (`backend/app/api/routes/github.py:290-462`) — we just throw the access token away at line 462 after extracting the installation_id. M006-ydo2ce keeps that exchange, persists the resulting access token (and refresh token), refreshes it on demand, and forwards it to the orchestrator so the orchestrator can use it instead of the installation token when the install is personal.

Organization installs are unchanged: they continue to use the installation token against `POST /orgs/{org}/repos`, which does support GitHub App tokens.

Three things this milestone is *not*:

- Not adding GitHub OAuth as a login method (`auth.py` is untouched). The user token is purely for API calls, not session identity.
- Not adding per-team or per-other-user token sharing. The token is bound to the user who installed the App on their personal account. Other team members who try to create a repo against that personal install get a clean 409 telling them to (re)install with their own GitHub account.
- Not changing org-install behavior. The org branch in `routes_github.py:332-333` is correct and stays.

## Why This Milestone

The bug surfaced immediately after M005-sqm8et shipped the create-repository UX: every demo against a personal GitHub account 502s with no useful diagnostic in the UI, even though the orchestrator log clearly shows the upstream 403. For solo developers (the primary M005-sqm8et persona — devs on their own laptop, single-team install, personal GitHub account), this is the single most-likely repo creation path, so a 502 here makes the feature feel broken in the most common case.

Two paths exist to fix it:

1. **Restrict to org installs** — close the personal-account branch, return a clean 422 explaining "install on an org instead." Cheap, but cuts off the solo-developer flow entirely. The product is meant to support a "log in, install on your personal GitHub, create a project repo, run a workflow" path with zero org setup.
2. **Use the user OAuth token** — make personal-account installs actually work by storing the user's OAuth access token at install time and using it for the API call.

We chose option 2 because the solo-developer path is a primary use case, not an edge.

## User-Visible Outcome

### When this milestone is complete, the user can:

- **Solo user on a personal GitHub account:** install the Perpetuity GitHub App on their personal GitHub account, then on the team's project setup screen click "Create new repo on GitHub", enter a name, optionally a description and private flag, and have the repo actually created on their GitHub account within ~2s — same success UX as the existing org-install path.
- **User who installed before M006-ydo2ce shipped:** the first time they attempt to create a repo on a personal install after this milestone deploys, they see a clear inline error "Reinstall the Perpetuity App on GitHub to grant repo creation access" with a button that opens the install URL; after reinstalling, the next create attempt succeeds.
- **Team member who is NOT the installing user:** if they attempt to create a repo against a personal install owned by a teammate, they see a 409 with a clear message ("This GitHub App was installed by <other user>; ask them to create the repo, or install the App on your own GitHub account"). They are not asked to authorize a token they cannot get.
- **System operator:** has nothing new to configure beyond enabling "Request user authorization (OAuth) during installation" in the GitHub App settings and adding `repo` to the user-to-server scopes — a one-time admin task documented in `docs/runbooks/`.

### Entry point / environment

- Entry point: existing `CreateGitHubRepoDialog.tsx` modal launched from the project setup screen
- Environment: full compose stack (Postgres + Redis + orchestrator + backend + frontend) plus real GitHub.com for end-to-end acceptance; respx-mocked GitHub for integration tests
- Live dependencies involved: GitHub OAuth token-exchange endpoint (`github.com/login/oauth/access_token`), GitHub API (`api.github.com/user/repos`, `api.github.com/user`), GitHub App installation token mint (already used by M005-sqm8et)

## Completion Class

- **Contract complete means:** unit tests for the new `github_user_oauth_tokens` upsert + Fernet round-trip; unit tests for `github_user_tokens.get_user_access_token` happy path, expired-access-token refresh, expired-refresh-token, revoked-refresh-token, and missing-row; route tests for backend forwarding of `X-GitHub-User-Token` header to the orchestrator when install is personal, and 409 when token row is missing; orchestrator route tests with a fake GitHub server confirming personal installs use the forwarded user token and org installs use the install token; alembic migration test for the new table.
- **Integration complete means:** end-to-end against a respx-mocked GitHub from install callback through repo creation succeeds for a personal install; the same flow with a forced "refresh token expired" mock returns the 409 to the frontend and the frontend renders the reinstall CTA; the same flow for an org install is byte-identical to M005-sqm8et behavior (regression).
- **Operational complete means:** with the GitHub App correctly configured (OAuth + `repo` scope), a real install on a personal GitHub account followed by a real repo creation produces a real repo on GitHub.com; access tokens stored at rest are Fernet-encrypted (decryptable with the same `SYSTEM_SETTINGS_ENCRYPTION_KEY` already used by M004/S01) — no plaintext tokens in the DB.
- **UAT complete means:** the three scenarios in "Final Integrated Acceptance" pass against real GitHub.com.

## Final Integrated Acceptance

To call this milestone complete, we must prove the following end-to-end (cannot be simulated in headless tests alone):

1. **Personal-install happy path:** a developer installs the Perpetuity GitHub App on their personal GitHub account from the team's project setup screen, opens the "Create new repo on GitHub" modal, enters a name, clicks Create, and a real repo appears on their GitHub account within ~2s. The UI navigates to the next step of project setup with the new repo URL prefilled.
2. **Pre-M006 install needs reinstall:** a developer with an existing personal install (created before the OAuth scope was enabled) clicks Create, sees an inline error "Reinstall to grant repo creation access" with a working reinstall button, clicks it, completes the GitHub install flow, returns to the modal, retries Create, and the repo is created successfully.
3. **Org-install regression check:** a developer who installs the App on an organization can still create a repo against that org — same UX, same success — without any user token involved. The orchestrator log shows the install token was used, not a user token.

## Architectural Decisions

### User OAuth token persistence, keyed on user_id (not user_id+installation_id)

**Decision:** Store user OAuth access tokens in a new table `github_user_oauth_tokens` with `user_id` as the primary key. A user has at most one stored token at a time; reinstalling on a different GitHub account overwrites the previous row.

**Rationale:** The OAuth user-to-server token represents the GitHub user's grant to Perpetuity — it is not tied to a specific installation. A user could install the App on multiple personal/org accounts using the same Perpetuity login, but the OAuth token they get back authorizes API calls against whichever GitHub user they authenticated as during the OAuth dance. Keying on `user_id` keeps the schema simple and matches reality. The cost is that if a user installs on two different personal GitHub accounts from the same Perpetuity user, the second install overwrites the first — but creating repos against two different personal accounts from one Perpetuity identity is not a real workflow, and the org-install path is unaffected (org installs do not need user tokens).

**Alternatives Considered:**
- Composite key `(user_id, installation_id)` — rejected: more complex schema, more failure modes (which row do we pick at creation time?), no real-world workflow that needs the multiplicity
- Composite key `(user_id, github_user_id)` — rejected: same complexity as above; we'd have to look up `github_user_id` before every read, and we don't already have it on the user record

---

### Reuse `SYSTEM_SETTINGS_ENCRYPTION_KEY` Fernet cipher; no new secret

**Decision:** Encrypt `access_token` and `refresh_token` at rest using the same Fernet cipher already wired up for `SystemSetting.value_encrypted` (see `backend/app/core/encryption.py:75-154` and M004/S01).

**Rationale:** Adding another encryption key is operational overhead with no security benefit — the threat model is the same (DB compromise, backup leak). The existing key is already managed, rotated as a unit, and tested. Token columns will be `BYTEA` matching `system_setting.value_encrypted`'s pattern, with the same `decrypt_setting` / `encrypt_setting` helpers.

**Alternatives Considered:**
- Separate key per-table — rejected: more operational burden, no incremental security
- Hash-only storage — rejected: we need to decrypt these tokens to send them in API calls; hashing is wrong

---

### Reinstall (not separate OAuth dance) to backfill missing tokens

**Decision:** When a user has an installation but no stored OAuth token (early installs, or any future case where the row is gone), the recovery path is to reinstall the GitHub App. The frontend surfaces a 409 with a "Reinstall to grant repo creation access" CTA that opens the install URL — same URL the team already uses for first-time installs.

**Rationale:** Reinstall reuses the existing OAuth code-exchange flow exactly. The alternative — a standalone "authorize" endpoint that does just the OAuth dance without reinstalling — would add a second OAuth code-handling route, a second state-JWT type, and a second redirect surface, for a code path the user will hit at most once per refresh-token-lifetime (6 months). Reinstall is the cheaper and more honest UX: "we need your authorization, click here to grant it" is the same gesture as the initial install.

**Alternatives Considered:**
- Standalone "authorize" link without reinstall — rejected: doubles the OAuth surface for a once-per-six-months path
- Lazy token refresh that silently fails to a 409 — what we're doing anyway; the question was whether to *also* offer a non-reinstall path. Decided no.

---

### Token forwarded as `X-GitHub-User-Token` header from backend to orchestrator

**Decision:** Backend resolves the current user's access token (via the refresh helper) and forwards it to the orchestrator as an HTTP header `X-GitHub-User-Token` on the existing `POST /v1/installations/{iid}/create-repository` request. The orchestrator uses the header when `account_type != "Organization"` and falls through to the installation token otherwise.

**Rationale:** Header keeps the token out of JSON request bodies (which sometimes get logged at debug levels). The orchestrator's existing route signature is preserved — we just look for one new header. Org installs send no `X-GitHub-User-Token` header and the orchestrator's behavior is byte-identical to today.

**Alternatives Considered:**
- Body field `user_token` — rejected: more likely to end up in request logs
- New orchestrator endpoint (`/v1/installations/{iid}/create-repository-user`) — rejected: bifurcates the route for a branch the orchestrator can already detect via `account_type`

---

## Error Handling Strategy

Token retrieval and refresh failures map to clear backend-side error codes that the frontend can react to with specific UX:

- **Row missing for user_id** (early install, never had an OAuth grant) → backend returns 409 `github_user_token_required` with `installation_id` in the body so the frontend knows which install to reinstall.
- **Refresh-token expired** (6-month TTL hit) → same 409 `github_user_token_required` — the recovery is identical (reinstall to re-authorize).
- **Refresh-token revoked by GitHub** (user revoked the App's authorization in their GitHub settings) → GitHub returns a 400 from the token-exchange endpoint with `error=bad_refresh_token`; we delete the row and return 409 `github_user_token_required`.
- **GitHub `POST /user/repos` returns 403** despite us using a user token (scope was downgraded, App de-authorized mid-flight) → orchestrator returns 502 with `reason=api_status status=403` exactly as today; backend logs and surfaces a generic "GitHub rejected the request" toast. A retry won't fix it; the user needs to reauthorize. Frontend can detect status 403 in the orchestrator's structured response and offer the reinstall CTA as a softer recovery.
- **GitHub `POST /user/repos` returns 422** (repo name taken, name invalid) → orchestrator returns 502 with `reason=api_status status=422 body=<github error>`; backend extracts the GitHub-provided message and surfaces it on the dialog field-level error.

No silent retries on auth failures — the token is either valid or the user needs to reauthorize. Network-class failures (timeouts, transport errors) get one retry at the backend layer.

## Risks and Unknowns

- **Refresh-token 6-month TTL is a recurring footgun.** Users who set up Perpetuity once and only return after six months will hit the reinstall flow. We surface it as a clear 409 with a reinstall button, but it will be a confusing moment if they forget what they configured. Mitigation: clear copy in the 409 message; later milestones could add a passive "your GitHub access expires in N days, reauthorize?" banner.
- **`POST /user/repos` private-repo behavior depends on OAuth scope grant.** If the GitHub App is configured without `repo` scope (only `public_repo`), private repo creation will fail with a 403 we cannot recover from at runtime. Mitigation: a startup check that the configured App has the right scopes, surfaced as a system-settings warning; OR a runbook that documents the required scopes.
- **Multiple Perpetuity users sharing one GitHub account.** Two Perpetuity users who both install the App from their own Perpetuity logins onto the *same* personal GitHub account will both store a token row. They will each be able to create repos against that account. This is fine and intentional, but worth flagging.
- **Asyncpg JSONB gotcha if we ever store JSON in the token row.** Current schema design uses scalar columns only; if a future change adds a JSONB field, the existing `set_type_codec` constraint from the volume_store memory applies. Avoided by keeping the schema scalar.
- **Race between OAuth code exchange and DB upsert at install time.** Two install callbacks arriving in quick succession (network retry, double-click on install button) could both try to upsert. Solved by `ON CONFLICT (user_id) DO UPDATE` on the upsert.

## Existing Codebase / Prior Art

- `backend/app/api/routes/github.py:290-462` — current OAuth code-exchange helper; discards the access token at line 462. Will be modified to return the full token tuple.
- `backend/app/api/routes/github.py:344-397` — actual code-exchange call; the response body has the `access_token`, `refresh_token`, `expires_in`, `refresh_token_expires_in`, `scope` fields we need.
- `backend/app/core/encryption.py:75-154` — Fernet cipher wiring (`encrypt_setting`, `decrypt_setting`, `SystemSettingDecryptError`). Direct reuse for the new token table.
- `backend/app/models.py` — SQLModel definitions; the new `GitHubUserOAuthToken` model lands here following the `SystemSetting` pattern.
- `backend/app/alembic/versions/` — alembic migration tree; s09_team_secrets is the cleanest pattern to copy for an encrypted-column-on-a-new-table migration. **Gotcha:** session-scoped autouse `db` fixture in `tests/conftest.py` holds an AccessShareLock that hangs DDL tests — the new migration's test module must use the `_release_autouse_db_session` + `_restore_head_after` fixture pattern from `tests/migrations/test_s01_migration.py`.
- `orchestrator/orchestrator/routes_github.py:330-358` — current branch on `account_type` and the `POST /user/repos` call that 403s. Will be modified to prefer `X-GitHub-User-Token` when present and install is personal.
- `frontend/src/components/Teams/Projects/CreateGitHubRepoDialog.tsx` — the modal that needs new 409 handling and reinstall CTA.
- `backend/tests/migrations/test_s09_team_secrets_migration.py` — pattern for testing encrypted-column migrations.
- `docs/runbooks/m004-secrets-rotation.md` — pattern for GitHub-App-config-change runbooks; new runbook for "enable OAuth + repo scope on the GitHub App" lands alongside it.

## Relevant Requirements

- R??? (project repo creation from personal GitHub installs) — fully advanced by this milestone. *(Confirm requirement IDs against `REQUIREMENTS.md` during S01 planning; if no requirement exists yet, file one as part of S01.)*

## Scope

### In Scope

- New table `github_user_oauth_tokens` with Fernet-encrypted access + refresh token columns
- Modifying the OAuth code-exchange helper to persist tokens
- A `github_user_tokens.get_user_access_token` helper with refresh-on-expiry
- Backend forwarding of the user token to the orchestrator via `X-GitHub-User-Token` header
- Orchestrator preference of the user token over the install token for personal installs
- Frontend 409 handling with a reinstall CTA in `CreateGitHubRepoDialog.tsx`
- One-time admin runbook for enabling OAuth + `repo` scope on the GitHub App
- A `GET /user` call at install time to record the GitHub user id alongside the token (needed to detect "wrong user reinstalled" scenarios cleanly in error messages)

### Out of Scope / Non-Goals

- GitHub OAuth as a Perpetuity login method
- Token sharing across teams or across users
- Org-install behavior changes (untouched)
- A standalone "authorize without reinstalling" flow
- Refresh-token expiry pre-warning UI (deferred; flagged in Risks)
- Multi-account-per-user token storage

## Technical Constraints

- The new table must encrypt at rest using the existing Fernet cipher; no new key material.
- The orchestrator route signature is unchanged — only a new optional header is introduced. Org installs MUST behave byte-identically to M005-sqm8et.
- All token persistence happens inside the same database transaction as the install callback; a partial install where the installation_id is recorded but the token is not should not occur.
- All token decryption is per-call; tokens are never held in memory longer than the lifetime of a single HTTP request.
- No token value (access, refresh, raw response body containing tokens) is logged. Log only token prefixes (`token_prefix=ghu_...`) and the user_id.
- The migration must follow the `tests/migrations/test_s01_migration.py` fixture pattern to release the session-scoped autouse DB lock; otherwise DDL tests hang.

## Integration Points

- **GitHub OAuth token-exchange endpoint** (`github.com/login/oauth/access_token`) — for both initial code exchange and refresh-token grant.
- **GitHub `GET /user`** (`api.github.com/user`) — for capturing `github_user_id` alongside the token.
- **GitHub `POST /user/repos`** — the target call that the user token is used for.
- **Orchestrator `POST /v1/installations/{iid}/create-repository`** — receives the new `X-GitHub-User-Token` header.
- **Backend `POST /api/v1/teams/{tid}/github/installations/{iid}/create-repository`** — gates on `account_type` lookup and resolves the user token before forwarding.
- **`CreateGitHubRepoDialog.tsx`** — handles the new 409 response shape.

## Testing Requirements

- **Unit:** Fernet round-trip for the new table; refresh-helper happy path, expired-access-refresh-success, expired-refresh-error, missing-row.
- **Migration:** new table created with the right column types and indexes; uses `_release_autouse_db_session` fixture pattern.
- **Backend integration:** install callback persists token; create-repository route forwards header for personal install; returns 409 when row missing.
- **Orchestrator integration:** with a fake GitHub server, personal install uses the forwarded user token; org install uses the install token; no header present + personal install returns 422 with `user_token_required_for_personal_install` (defense in depth — should never happen if backend is wired correctly).
- **Frontend:** Playwright mocks the 409 response; CTA renders; clicking it opens the install URL.
- **Manual UAT:** the three Final Integrated Acceptance scenarios against real GitHub.com.

## Acceptance Criteria

Per-slice acceptance criteria are defined in the ROADMAP slice list. The milestone-level acceptance criteria are the three scenarios in "Final Integrated Acceptance" above, each demonstrated on real GitHub.com.

## Open Questions

- **Where does the runbook live?** `docs/runbooks/m006-github-oauth-setup.md` proposed; alternative is `docs/runbooks/github-app-configuration.md` as a single living doc that M004's secrets rotation runbook can also link to. Resolve during S02 planning.
- **Should the backend pre-flight call `GET /installation/{id}` to confirm the install still exists before resolving the user token?** Adds latency to every create call. Decision: skip; let the eventual `POST /user/repos` fail and surface the GitHub error. Confirm during S04 planning.
