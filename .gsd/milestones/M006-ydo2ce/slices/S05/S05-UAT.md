# S05: Orchestrator prefers user token for personal installs — UAT

**Milestone:** M006-ydo2ce
**Written:** 2026-05-12T22:50:47.942Z

# S05 UAT: Orchestrator Personal-Install User Token Handling

## Preconditions
- Perpetuity orchestrator running (docker compose orchestrator service)
- respx mocking fixtures configured for api.github.com
- S04 backend changes deployed (X-GitHub-User-Token header forwarding)
- S01-S03 database migrations applied (github_user_oauth_tokens table exists)

## Test Scenarios

### Scenario 1: Personal Install with User Token Header
**Preconditions:** installation_type = "User" (personal), X-GitHub-User-Token: ghu_abc123def456 header present, installation account with valid GitHub user ID.

**Steps:**
1. Backend routes POST /api/teams/{teamId}/repositories with GitHub user_token stored in database
2. Backend calls orchestrator POST /github/repositories with JSON body: {"installation_id": 123, "name": "test-repo", ...} and header X-GitHub-User-Token: ghu_abc123def456
3. Orchestrator reads X-GitHub-User-Token header (not None)
4. Orchestrator calls lookup_installation(123) → returns account_type="User"
5. Orchestrator skips install-token mint call
6. Orchestrator builds create_url = https://api.github.com/user/repos
7. Orchestrator calls POST with Authorization: token ghu_abc123def456

**Expected Outcome:**
- Respx mock GitHub receives POST https://api.github.com/user/repos with Authorization: token ghu_abc123def456
- GitHub mock returns 201 Created with repo ID
- Orchestrator returns 201 {"repository_id": 999, "name": "test-repo"} to backend
- Orchestrator logs INFO github_repository_created with token_class=user_token, user_token_prefix=ghu_a

**Edge Cases:**
- User token expired but refresh token valid → S03 refresh logic in backend handles, fresh token passed to orchestrator
- User token entirely missing (S03 raises UserTokenUnavailable) → backend returns 409 to frontend, S06 renders reinstall CTA

---

### Scenario 2: Personal Install without User Token Header
**Preconditions:** installation_type = "User", X-GitHub-User-Token header absent or empty, no user OAuth token in database.

**Steps:**
1. Backend routes POST /api/teams/{teamId}/repositories
2. S03/S04 logic: user_token lookup returns UserTokenUnavailable or None
3. Backend returns 409 to frontend (S06 handles)
4. If backend mistakenly sends orchestrator request without header:
   - Orchestrator reads X-GitHub-User-Token header → None
   - Orchestrator calls lookup_installation(123) → returns account_type="User"
   - Orchestrator does NOT attempt install-token mint
   - Orchestrator does NOT call GitHub

**Expected Outcome:**
- Orchestrator returns 422 {"detail": "user_token_required_for_personal_install"} BEFORE any GitHub API call
- Orchestrator logs WARN github_create_repository_failed with reason=user_token_required_for_personal_install
- Backend already handled this (409); orchestrator 422 is defense-in-depth only

---

### Scenario 3: Organization Install with User Token Header (Backend Bug)
**Preconditions:** installation_type = "Organization", X-GitHub-User-Token header present (indicates backend bug — should never send user token for org install), installation account with valid GitHub org login.

**Steps:**
1. Backend mistakenly calls orchestrator with X-GitHub-User-Token: ghu_xxx for an org install
2. Orchestrator reads X-GitHub-User-Token header → not None
3. Orchestrator calls lookup_installation(456) → returns account_type="Organization", org_login="acme-corp"
4. Orchestrator logs WARN github_create_repository_unexpected_user_token_on_org
5. Orchestrator continues with existing org-install path (ignores user_token header)
6. Orchestrator calls get_installation_token(456) → receives install token
7. Orchestrator builds create_url = https://api.github.com/orgs/acme-corp/repos
8. Orchestrator calls POST with Authorization: token <install_token>

**Expected Outcome:**
- Respx mock GitHub receives POST https://api.github.com/orgs/acme-corp/repos with install-token auth
- NO Authorization: token ghu_xxx header sent (user token ignored)
- GitHub mock returns 201
- Orchestrator logs INFO github_repository_created (no token_class field, standard org-install log)
- Orchestrator logs WARN github_create_repository_unexpected_user_token_on_org

---

### Scenario 4: Organization Install without User Token Header (Standard M005-sqm8et Path)
**Preconditions:** installation_type = "Organization", no X-GitHub-User-Token header, installation account with valid GitHub org login.

**Steps:**
1. Backend calls orchestrator POST /github/repositories without X-GitHub-User-Token header
2. Orchestrator reads X-GitHub-User-Token header → None
3. Orchestrator calls lookup_installation(456) → returns account_type="Organization", org_login="acme-corp"
4. Orchestrator calls get_installation_token(456) → receives install token
5. Orchestrator builds create_url = https://api.github.com/orgs/acme-corp/repos
6. Orchestrator calls POST with Authorization: token <install_token>

**Expected Outcome:**
- Respx mock GitHub receives POST https://api.github.com/orgs/acme-corp/repos with install-token auth
- GitHub mock returns 201
- Orchestrator returns 201 to backend
- Orchestrator logs INFO github_repository_created (byte-identical to M005-sqm8et, no token_class field)
- Install-token mint call count = 1 (normal)

---

## UAT Type
**Integration**: Orchestrator routes layer against respx-mocked GitHub; S04 backend integration tested separately.

## Not Proven By This UAT
- S06 frontend dialog rendering (separate Playwright test)
- S07 real GitHub.com acceptance (requires real app + org)
- S02 OAuth callback token storage (separate S02 test)
- S03 token refresh logic (separate S03 test)
- S04 backend routing (separate S04 integration test)
- End-to-end real-user flow (S07 responsibility)
