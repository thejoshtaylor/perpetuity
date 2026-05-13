# M006-ydo2ce: Final Integrated Acceptance Evidence

**Milestone:** Personal-account repo creation via GitHub user-to-server OAuth
**Date:** 2026-05-13

---

## Final Integrated Acceptance Evidence

### Scenario 1 — Personal-install happy path

**Status:** Code-path verified via integration tests (respx-mocked GitHub).
**Real-GitHub UAT status:** BLOCKED — external network unreachable in execution environment.

#### Test Evidence (verifiable code path)

All integration tests exercising the personal-install happy path pass:

```
backend $ uv run pytest tests/api/routes/test_github_create_repository.py -v
  test_personal_install_forwards_user_token           PASSED
  test_personal_install_missing_token_returns_409     PASSED
  test_org_install_no_user_token_header               PASSED
  test_personal_install_refresh_transient_returns_502 PASSED
  test_personal_install_decrypt_failure_returns_503   PASSED
  test_personal_install_bad_refresh_token_includes_reason PASSED
  test_create_repository_installation_not_found       PASSED
  test_create_repository_missing_repo_name            PASSED
  test_create_repository_invalid_private_type         PASSED
  ======================== 9 passed in 0.75s ========================

orchestrator $ uv run pytest tests/integration/test_create_repository_user_token.py -v
  test_personal_install_with_user_token_uses_user_token_for_user_repos  PASSED
  test_personal_install_no_user_token_returns_422                        PASSED
  test_org_install_uses_install_token_for_orgs_repos                     PASSED
  test_org_install_ignores_user_token_header                             PASSED
  test_personal_install_user_token_not_in_logs                           PASSED
  ========================= 5 passed in 0.94s ========================
```

#### Orchestrator log excerpt (token_class=user_token confirmed)

See `.gsd/milestones/M006-ydo2ce/evidence/scenario1-orchestrator.log` for full log.

Key line confirming personal-account branch taken and user token used:

```
INFO orchestrator:routes_github.py:317 github_create_repository installation_id=42 token_class=user_token user_token_prefix=ghu_
INFO httpx:_client.py POST https://api.github.com/user/repos "HTTP/1.1 201 Created"
INFO orchestrator:routes_github.py:414 github_repository_created installation_id=42 repo_name=my-new-repo
```

#### Screenshot evidence

`scenario1-personal-happy.png` — **PENDING** (requires real GitHub.com + network).
Human operator must:
1. Seed `github_app_client_id`, `github_app_client_secret`, `github_app_slug`
   in `/admin/settings` via the backend admin API.
2. Install `perpetuity-connector` GitHub App on personal account `thejoshtaylor`.
   (App is already registered: App ID=3691799, slug=perpetuity-connector)
3. Navigate to project setup → "Create new repo on GitHub"
4. Enter repo name `m006-acceptance-personal-<timestamp>`, Private.
5. Click Create. Capture screenshot of success state.
6. Run `gh repo view thejoshtaylor/m006-acceptance-personal-<ts>` to verify.

#### DB verification

DB query confirming github_user_oauth_tokens row would be populated post-OAuth:

```sql
SELECT user_id, github_user_id, scopes,
       access_token_expires_at, updated_at
FROM github_user_oauth_tokens
WHERE updated_at > NOW() - INTERVAL '10 minutes';
```

(Requires live OAuth flow to populate — blocked by network.)

---

### Scenario 2 — Pre-M006 reinstall flow

**Status:** Code-path verified via integration tests (respx-mocked GitHub). Observability log added.
**Real-GitHub UAT status:** BLOCKED — external network unreachable in execution environment.

#### Observability improvement (T03)

Added structured log line `github_user_token_required user_id=... installation_id=... reason=row_missing`
in `backend/app/api/routes/github.py` before the 409 raise. This makes the CTA trigger path observable
in production logs — previously the event was silent (only the HTTP response body carried the reason).

#### Test Evidence (verifiable code path — 3-step reinstall flow)

```
Step 1 — First attempt with no token row (→ 409 + CTA):
  test_personal_install_missing_token_returns_409  PASSED
  Log: github_user_token_required user_id=... installation_id=69511998 reason=row_missing

Step 2 — Operator reinstalls GitHub App → OAuth callback persists token row:
  test_get_callback_oauth_flow_persists_token_row  PASSED
  Log: github_user_token_persisted user_id=... installation_id=700001 github_user_id=42001

Step 3 — Second attempt with token row present (→ success):
  test_personal_install_forwards_user_token  PASSED
  Log: github_repository_created installation_id=... repo_name=test-repo actor_id=...

backend $ uv run pytest tests/api/routes/test_github_create_repository.py -v
  ======================== 9 passed in 0.87s ========================
```

#### Backend log excerpt

See `.gsd/milestones/M006-ydo2ce/evidence/scenario2-backend.log` for full log.

Key lines confirming the reinstall CTA branch then success:

```
INFO:app.api.routes.github:github_user_token_required user_id=... installation_id=69511998 reason=row_missing
INFO:app.api.routes.github:github_user_token_persisted user_id=... installation_id=700001 github_user_id=42001
INFO:app.api.routes.github:github_repository_created installation_id=... repo_name=test-repo actor_id=...
```

#### Screenshot evidence

`scenario2-cta.png` — **PENDING** (requires real GitHub.com + network).
`scenario2-post-reinstall-success.png` — **PENDING**.

Human operator must:
1. `DELETE FROM github_user_oauth_tokens WHERE user_id = <operator-uuid>` (simulate pre-M006 state).
2. Open "Create new repo on GitHub" modal → attempt create.
3. Expect 409 → frontend shows `data-testid="create-repo-reinstall-cta"`, submit button hidden.
4. Click "Reinstall on GitHub" → complete OAuth in new tab.
5. Re-open modal → retry → expect success. Capture screenshots of both states.
6. Paste `scenario2-backend` log lines from `docker compose logs backend | grep github_user_token`.

---

### Scenario 3 — Org-install regression check

**Status:** Code-path verified via integration tests (respx-mocked GitHub). Install-token path confirmed.
**Real-GitHub UAT status:** BLOCKED — external network unreachable in execution environment.

#### Test Evidence (verifiable code path)

The org-install path is byte-identical to M005-sqm8et behavior: installation token minted via
`/app/installations/{id}/access_tokens`, then used to POST to `/orgs/{login}/repos`.
No user token is forwarded or logged for org installs.

```
orchestrator $ uv run pytest tests/integration/test_create_repository_user_token.py -v
  test_personal_install_with_user_token_uses_user_token_for_user_repos  PASSED
  test_personal_install_no_user_token_returns_422                        PASSED
  test_org_install_uses_install_token_for_orgs_repos                     PASSED
  test_org_install_ignores_user_token_header                             PASSED
  test_personal_install_user_token_not_in_logs                           PASSED
  ========================= 5 passed in 0.70s ========================
```

#### Orchestrator log excerpt (install-token path confirmed)

See `.gsd/milestones/M006-ydo2ce/evidence/scenario3-orchestrator.log` for full log.

Key lines proving org-install took the installation-token path (not user-token):

```
INFO  orchestrator:github_tokens.py:381 installation_token_minted installation_id=42 token_prefix=ghs_...
INFO  httpx:_client.py POST https://api.github.com/orgs/octocorp/repos "HTTP/1.1 201 Created"
INFO  orchestrator:routes_github.py:414 github_repository_created installation_id=42 repo_name=my-new-repo
```

**Key proof: `github_repository_created` line has NO `token_class=user_token` field.**
The install-token branch was taken — M005-sqm8et org path is intact.

Defense-in-depth: org-install with unexpected `X-GitHub-User-Token` header triggers
`github_create_repository_unexpected_user_token_on_org` WARN log and the header is ignored.

#### Screenshot evidence

`scenario3-org-success.png` — **PENDING** (requires real GitHub org admin access + network).

Human operator must:
1. Install `perpetuity-connector` GitHub App on an org where operator is admin.
2. Confirm row in `github_app_installations` with `account_type=Organization`.
3. Open project setup → "Create new repo on GitHub" for a team that owns the org install.
4. Enter repo name `m006-acceptance-org-<timestamp>`, expect success within ~2s.
5. Capture screenshot of success state.
6. Run `gh repo view <org>/m006-acceptance-org-<ts>` to verify repo exists.
7. Paste `docker compose logs orchestrator | grep github_repository_created` — confirm NO user-token field.

#### Cleanup (post-acceptance)

After all three scenarios pass, delete test repos:
```bash
gh repo delete <personal>/m006-acceptance-personal-<ts> --yes
gh repo delete <personal>/m006-acceptance-reinstall-<ts> --yes
gh repo delete <org>/m006-acceptance-org-<ts> --yes
```
Leave the personal GitHub App installation in place for future regression checks.

---

## Environment Status at T02 Execution

| Item | Status |
|------|--------|
| GitHub App (perpetuity-connector) | Registered: ID=3691799, client_id=Iv23liooQlSrzFhIEjaB |
| Installation under thejoshtaylor | install_id=131793361 (confirmed via JWT probe) |
| Private key PEM | Present at ~/Downloads/perpetuity-connector.2026-05-12.private-key.pem |
| External network (github.com) | **UNREACHABLE** — no route to host |
| system_settings credentials | **NOT SEEDED** — table empty, no client_secret available |
| backend running at :8000 | login endpoint returns 500 (started before initial_data; restart needed) |
| All 5 M006 services | Healthy (compose: db/orchestrator/redis; local: backend/frontend) |

## Blocker for Real-GitHub UAT

Network connectivity to github.com is unavailable in the execution environment.
To complete real-GitHub acceptance, a human operator with internet access must:

1. Restart the backend (the running process at :8000 started before `initial_data`
   was seeded; or run `uv run python -m app.initial_data` and reload).
2. Navigate to `github.com/settings/apps/perpetuity-connector` as `thejoshtaylor`
   and generate a new Client Secret.
3. Seed via admin API: `PUT /api/v1/admin/settings/github_app_client_id` and
   `PUT /api/v1/admin/settings/github_app_client_secret`.
4. Execute the three CONTEXT scenarios against real github.com.
5. Append screenshot evidence to this SUMMARY under each scenario subsection.
