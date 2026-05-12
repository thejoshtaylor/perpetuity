# S04: Backend forwards X-GitHub-User-Token to orchestrator for personal installs — UAT

**Milestone:** M006-ydo2ce
**Written:** 2026-05-12T22:34:55.475Z

# S04 User Acceptance Tests

## Preconditions
- Backend running against real PostgreSQL with S01-S03 migrations applied
- GitHub App installation exists for both personal (User) and org (Organization) account types
- Current user has valid OAuth token row in github_user_oauth_tokens table
- Test client configured with httpOnly cookie auth

## Test Cases

### Test 1: Personal Install with Valid User Token (Happy Path)
**Steps:**
1. User authenticates and creates a personal GitHub App installation
2. Verify github_user_oauth_tokens row exists with user_id = current_user.id
3. Call POST /api/v1/teams/{id}/github/installations/{installation_id}/create-repository
4. Observe orchestrator HTTP call with X-GitHub-User-Token header present
5. Orchestrator mock returns 201 Created

**Expected Outcome:**
- Backend returns 201 status
- Orchestrator called with X-GitHub-User-Token header
- No token plaintext in logs

### Test 2: Personal Install without Token Row (409 Path)
**Expected Outcome:**
- Backend returns 409 Conflict
- Body: {"detail": "github_user_token_required", "installation_id": <int>, "reason": "row_missing"}
- Orchestrator NOT called

### Test 3: Org Install Does Not Use User Token (Regression)
**Expected Outcome:**
- Backend returns 201
- Orchestrator called without X-GitHub-User-Token header
- M005-sqm8et behavior unchanged

### Test 4: Refresh Token Expired (502 Path)
**Expected Outcome:**
- Backend returns 502 Bad Gateway
- Orchestrator NOT called
- WARN log with user_id + installation_id

### Test 5: Token Decryption Failure (503 Path)
**Expected Outcome:**
- Backend returns 503 Service Unavailable
- Orchestrator NOT called
- ERROR log with user_id + installation_id

### Test 6: Bad Refresh Token from GitHub (409 with reason)
**Expected Outcome:**
- Backend returns 409
- Body includes reason: "bad_refresh_token"
- Orchestrator NOT called

## UAT Type
Integration — Route integration tests using test client + mocked orchestrator + real Postgres.

## Not Proven By This UAT
- S05 orchestrator branch
- S06 frontend CTA rendering
- S07 end-to-end with real GitHub.com
