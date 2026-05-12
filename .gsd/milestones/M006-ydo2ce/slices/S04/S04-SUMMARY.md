---
id: S04
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
completed_at: 2026-05-12T22:34:55.474Z
blocker_discovered: false
---

# S04: Backend forwards X-GitHub-User-Token to orchestrator for personal installs

**Backend route branches on install account_type, resolves personal user tokens via S03 helpers, forwards them to orchestrator as X-GitHub-User-Token header, and surfaces 5 exception paths as clean HTTP responses with observability logging.**

## What Happened

S04 delivers the backend half of personal-install token forwarding. The `create_github_repository` route now examines the resolved `GitHubAppInstallation` record's `account_type`: for personal (non-Organization) installs, it calls `get_user_access_token(session, current_user.id)` to retrieve the current user's stored OAuth access token via S03; for org installs, `user_token` remains `None` and the call path is unchanged from M005-sqm8et.

Three exception classes are caught and mapped to HTTP responses:
- `UserTokenUnavailable` with reason='refresh_transient' → 502 Bad Gateway (temporary, retry-able)
- `UserTokenUnavailable` with other reasons ('row_missing', 'bad_refresh_token', 'expired') → 409 Conflict with documented body `{"detail": "github_user_token_required", "installation_id": <int>, "reason": <reason>}`
- `GitHubUserTokenDecryptError` → 503 Service Unavailable (permanent, admin remediation)

Every exception path logs with `user_id + installation_id + reason` for observability; no token plaintext or ciphertext is captured in any log record.

The route then calls `_orch_create_repository` (extended in T01) with `user_token=<resolved_token_or_None>`, which conditionally inserts the `X-GitHub-User-Token` header only when `user_token is not None`. A defense-in-depth assertion immediately before the call verifies that org installs never forward a user token.

All 12 tests pass:
- 3 unit tests for T01 (_orch_create_repository header logic)
- 6 integration tests for T02/T03 route branching, exception mapping, and decision-tree coverage
- 3 pre-existing validation tests (regression clean)

## Verification

**T01 Verification:** `cd backend && uv run pytest tests/api/routes/test_github_orch_create_repository.py -v` — 3 passed in 0.14s
- test_orch_create_repository_no_user_token_omits_header ✓
- test_orch_create_repository_with_user_token_sets_header ✓
- test_orch_create_repository_orchestrator_key_always_present ✓

**T02 Verification:** `cd backend && uv run pytest tests/api/routes/test_github_create_repository.py -q --tb=short` — 11 passed in 1.02s

**T03 Verification:** `cd backend && uv run pytest tests/api/routes/test_github_create_repository.py -v` — 9 passed in 0.85s

**Coverage matrix:**
- Personal install + token row present → 201 with X-GitHub-User-Token header forwarded ✓
- Personal install + no token row → 409 github_user_token_required (no orch call) ✓
- Org install → 201 without user token header (M005-sqm8et regression clean) ✓
- Transient token refresh failure → 502 (no orch call) ✓
- Token decryption failure → 503 (no orch call) ✓
- Bad refresh token from GitHub → 409 with reason field (no orch call) ✓
- Token plaintext/ciphertext redaction verified by caplog ✓
- Defense-in-depth assertion prevents org→user_token bugs ✓

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
