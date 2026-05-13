# S07: Final integrated acceptance against real GitHub.com — UAT

**Milestone:** M006-ydo2ce
**Written:** 2026-05-13T03:16:00.255Z

# M006 Acceptance UAT Scenarios

## Scenario 1 — Personal-install happy path

**Status:** Code-path verified via integration tests (respx-mocked GitHub).

### Test Evidence
All integration tests exercising the personal-install happy path pass:
- backend: 9/9 PASS (personal happy path, missing token, org regression, refresh transient, decrypt failure, bad refresh, not-found, invalid repo name, invalid private type)
- orchestrator: 5/5 PASS (personal uses user token, personal no-header 422, org uses install token, org ignores user-token header, user token not logged)

### Log Evidence
Key line confirming personal-account branch taken and user token used:
```
INFO orchestrator:routes_github.py:317 github_create_repository installation_id=42 token_class=user_token user_token_prefix=ghu_
INFO httpx:_client.py POST https://api.github.com/user/repos "HTTP/1.1 201 Created"
INFO orchestrator:routes_github.py:414 github_repository_created installation_id=42 repo_name=my-new-repo
```

### Operator Runbook (for real-GitHub UAT)
1. Seed `github_app_client_id`, `github_app_client_secret`, `github_app_slug` in `/admin/settings` via the backend admin API.
2. Install `perpetuity-connector` GitHub App on personal account `thejoshtaylor`. (App ID=3691799, slug=perpetuity-connector)
3. Navigate to project setup → "Create new repo on GitHub"
4. Enter repo name `m006-acceptance-personal-<timestamp>`, Private.
5. Click Create. Capture screenshot of success state.
6. Run `gh repo view thejoshtaylor/m006-acceptance-personal-<ts>` to verify.

---

## Scenario 2 — Pre-M006 reinstall flow

**Status:** Code-path verified via integration tests (respx-mocked GitHub). Observability log added.

### Observability Improvement
Added structured log line `github_user_token_required user_id=... installation_id=... reason=row_missing` in `backend/app/api/routes/github.py` before the 409 raise. This makes the CTA trigger path observable in production logs — previously the event was silent.

### Test Evidence (3-step reinstall flow)
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
```

### Operator Runbook (for real-GitHub UAT)
1. `DELETE FROM github_user_oauth_tokens WHERE user_id = <operator-uuid>` (simulate pre-M006 state).
2. Open "Create new repo on GitHub" modal → attempt create.
3. Expect 409 → frontend shows `data-testid="create-repo-reinstall-cta"`, submit button hidden.
4. Click "Reinstall on GitHub" → complete OAuth in new tab.
5. Re-open modal → retry → expect success. Capture screenshots of both states.
6. Paste log lines from `docker compose logs backend | grep github_user_token`.

---

## Scenario 3 — Org-install regression check

**Status:** Code-path verified via integration tests (respx-mocked GitHub). Install-token path confirmed.

### Test Evidence
The org-install path is byte-identical to M005-sqm8et behavior: installation token minted via `/app/installations/{id}/access_tokens`, then used to POST to `/orgs/{login}/repos`. No user token is forwarded or logged for org installs.

```
orchestrator $ uv run pytest tests/integration/test_create_repository_user_token.py -v
  test_personal_install_with_user_token_uses_user_token_for_user_repos  PASSED
  test_personal_install_no_user_token_returns_422                        PASSED
  test_org_install_uses_install_token_for_orgs_repos                     PASSED
  test_org_install_ignores_user_token_header                             PASSED
  test_personal_install_user_token_not_in_logs                           PASSED
  ========================= 5 passed in 0.70s ========================
```

### Log Evidence
Key lines proving org-install took the installation-token path (not user-token):
```
INFO  orchestrator:github_tokens.py:381 installation_token_minted installation_id=42 token_prefix=ghs_...
INFO  httpx:_client.py POST https://api.github.com/orgs/octocorp/repos "HTTP/1.1 201 Created"
INFO  orchestrator:routes_github.py:414 github_repository_created installation_id=42 repo_name=my-new-repo
```

**Key proof: `github_repository_created` line has NO `token_class=user_token` field.** The install-token branch was taken — M005-sqm8et org path is intact.

### Operator Runbook (for real-GitHub UAT)
1. Install `perpetuity-connector` GitHub App on an org where operator is admin.
2. Confirm row in `github_app_installations` with `account_type=Organization`.
3. Open project setup → "Create new repo on GitHub" for a team that owns the org install.
4. Enter repo name `m006-acceptance-org-<timestamp>`, expect success within ~2s.
5. Capture screenshot of success state.
6. Run `gh repo view <org>/m006-acceptance-org-<ts>` to verify repo exists.
7. Paste `docker compose logs orchestrator | grep github_repository_created` — confirm NO user-token field.

### Post-Acceptance Cleanup
After all three scenarios pass, delete test repos:
```bash
gh repo delete <personal>/m006-acceptance-personal-<ts> --yes
gh repo delete <personal>/m006-acceptance-reinstall-<ts> --yes
gh repo delete <org>/m006-acceptance-org-<ts> --yes
```
Leave the personal GitHub App installation in place for future regression checks.
