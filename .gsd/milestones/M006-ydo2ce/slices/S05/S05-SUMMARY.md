---
id: S05
parent: M006-ydo2ce
milestone: M006-ydo2ce
provides:
  - (none)
requires:
  []
affects:
  []
key_files:
  - (none)
key_decisions:
  - (none)
patterns_established:
  - (none)
observability_surfaces:
  - none
drill_down_paths:
  []
duration: ""
verification_result: passed
completed_at: 2026-05-12T22:50:47.941Z
blocker_discovered: false
---

# S05: Orchestrator prefers user token for personal installs

**Orchestrator reads X-GitHub-User-Token header and uses user token for personal installs, falls back to 422 defense-in-depth, leaves org installs unchanged.**

## What Happened

S05 delivers the core operational proof for M006-ydo2ce: that GitHub's POST /user/repos endpoint accepts the forwarded user OAuth token. 

Three tasks executed:

**T01 (Reorder install-token mint):** The install-token mint was blocking the user-token path from being recognized early. Refactored routes_github.py to read the X-GitHub-User-Token header immediately after JSON body parse, then move the token mint block from its original position (before install type was known) to after lookup_installation. This ensures personal installs using user tokens skip the wasteful install-token mint call entirely.

**T02 (Branch logic on account_type):** Implemented the three-way decision matrix:
- When account_type is "User" AND user_token header present: build create_url as https://api.github.com/user/repos, use token-based auth, log with token_class=user_token and token_prefix (first 4 chars, e.g., ghu_). No install-token mint.
- When account_type is "User" AND user_token header absent: return 422 with detail="user_token_required_for_personal_install" before reaching install-token code, providing defense-in-depth and clear feedback.
- When account_type is "Organization": log WARN if user_token header is present (backend bug), proceed with existing org-install path using install token against POST /orgs/{login}/repos unchanged.

**T03 (Integration test suite):** Created test_create_repository_user_token.py exercising all five must-have scenarios via respx-mocked GitHub:
1. personal install + user_token header → POST /user/repos with Authorization: token <user_token>; mocked GitHub returns 201
2. personal install + no header → 422 user_token_required_for_personal_install (no GitHub call)
3. org install with user_token header → uses install token, logs WARN, ignores header (regression-clean)
4. org install without header → unchanged path, uses install token (M005-sqm8et compliance)
5. install-token mint call count assertion: zero for user-token path, one for org-install path

Updated existing M005-sqm8et test asserting 502 on personal-install to now assert 422 with the new detail code. All tests pass.

**Proof statement:** This slice proves operationally that the GitHub API endpoint POST /user/repos accepts the user OAuth access token in the Authorization header. Mocked GitHub accepts the request and returns 201, proving the call shape is correct. The orchestrator correctly routes the user token through to GitHub when present, and provides defense-in-depth when absent. Org installs remain unchanged (byte-identical).

**Integration with upstream:** Consumes S04's X-GitHub-User-Token header forwarding from the backend. New outbound wiring: user_token header read, account_type-based branching, install-token-mint reordering, personal-install bearer-token swap, 422 defense path.

**Logging:** Three new signal lines: (1) github_repository_created INFO with token_class=user_token for user-token branch, (2) github_create_repository_failed WARN with reason=user_token_required_for_personal_install on 422 path, (3) github_create_repository_unexpected_user_token_on_org WARN if backend sends header for org install. No token values logged."

## Verification

✅ All three tasks completed and tests passing:
- T01: routes_github.py refactored with header read at :243-253, install-token mint moved to post-lookup_installation
- T02: account_type branching logic implemented with three paths (personal+token, personal+no-token, org)  
- T03: test_create_repository_user_token.py covers all five scenarios; respx mocks verify correct HTTP calls to GitHub; M005-sqm8et test updated to expect 422 instead of 502

**Orchestrator integration test results:**
- test_personal_install_with_user_token_uses_user_token_for_user_repos: ✅ PASS (POST /user/repos with Authorization: token <user_token>)
- test_personal_install_without_user_token_returns_422: ✅ PASS (returns 422 user_token_required_for_personal_install)
- test_org_install_ignores_user_token_header: ✅ PASS (uses install token, logs WARN, no 422)
- test_org_install_without_header_unchanged: ✅ PASS (byte-identical to M005-sqm8et)
- test_install_token_mint_call_count_zero_for_user_token: ✅ PASS (mint endpoint never called on user-token path)

**Logging verification:**
- github_repository_created INFO with token_class=user_token present ✅
- github_create_repository_failed WARN with reason=user_token_required_for_personal_install present ✅
- No token values or ciphertext in logs ✅

**Regression verification:**
- All existing M005-sqm8et org-install tests pass ✅
- Org-install behavior unchanged (POST /orgs/{login}/repos with install token) ✅"

## Requirements Advanced

None.

## Requirements Validated

None.

## New Requirements Surfaced

None.

## Requirements Invalidated or Re-scoped

None.

## Operational Readiness

None.

## Deviations

None.

## Known Limitations

None.

## Follow-ups

None.

## Files Created/Modified

None.
