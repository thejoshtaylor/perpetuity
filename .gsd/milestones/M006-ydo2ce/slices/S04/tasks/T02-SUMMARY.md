---
id: T02
parent: S04
milestone: M006-ydo2ce
key_files:
  - /Users/josh/code/perpetuity/backend/app/api/routes/github.py
  - /Users/josh/code/perpetuity/backend/tests/api/routes/test_github_create_repository.py
  - /Users/josh/code/perpetuity/backend/tests/utils/user.py
key_decisions:
  - Used `!= 'Organization'` branch condition (covers User and any future account types) rather than `== 'User'`
  - 409 body is a dict with code/installation_id/reason so S06 CTA logic can branch on all fields
  - Defense-in-depth assertion uses org==None invariant to catch wiring bugs without hiding them
  - Fixed pre-existing test file import error by adding create_test_user/create_test_team helpers to tests/utils/user.py rather than creating a new utility file
duration: 
verification_result: passed
completed_at: 2026-05-12T22:17:22.900Z
blocker_discovered: false
---

# T02: Wired account_type branch, get_user_access_token, and exception mapping into create_github_repository route

**Wired account_type branch, get_user_access_token, and exception mapping into create_github_repository route**

## What Happened

Read the existing create_github_repository route (lines ~1180-1249) and confirmed _orch_create_repository already accepted user_token=None from T01. Added imports for GitHubUserTokenDecryptError, UserTokenUnavailable, and get_user_access_token from app.core.github_user_tokens. After the installation_not_found check and before body validation, inserted the account_type branch: if account_type != "Organization", calls get_user_access_token(session, current_user.id) and maps UserTokenUnavailable(refresh_transient) → 502, all other UserTokenUnavailable reasons → 409 with code/installation_id/reason in body, and GitHubUserTokenDecryptError → 503 with ERROR log (no token bytes logged). Org installs set user_token=None. Added defense-in-depth assertion immediately before _orch_create_repository call verifying the org==None invariant. Passed user_token=user_token to _orch_create_repository. Added inline comment about skipping GET /installation/{id} pre-flight. Fixed a pre-existing import error in tests/api/routes/test_github_create_repository.py (missing create_test_user/create_test_team helpers) by adding them to tests/utils/user.py, and rewrote the test file to use the cookie-based /auth/login endpoint and cover all new T02 behaviors: org skips token fetch, personal fetches and forwards token, all 4 non-transient UserTokenUnavailable reasons → 409 with correct body fields, refresh_transient → 502, GitHubUserTokenDecryptError → 503, plus existing validation/404 tests.

## Verification

Ran canonical verification command: `cd backend && uv run python -c "from app.api.routes.github import create_github_repository; print('ok')"` — printed ok. Ran all 11 new tests in test_github_create_repository.py — all passed.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd /Users/josh/code/perpetuity/backend && uv run python -c "from app.api.routes.github import create_github_repository; print('ok')"` | 0 | Import succeeded, printed ok | 1200ms |
| 2 | `cd /Users/josh/code/perpetuity/backend && uv run pytest tests/api/routes/test_github_create_repository.py -q --tb=short` | 0 | 11 passed, 36 warnings | 1020ms |

## Deviations

Fixed a pre-existing broken test file (missing helper imports) that was not in scope but blocked test collection for the entire suite.

## Known Issues

None.

## Files Created/Modified

- `/Users/josh/code/perpetuity/backend/app/api/routes/github.py`
- `/Users/josh/code/perpetuity/backend/tests/api/routes/test_github_create_repository.py`
- `/Users/josh/code/perpetuity/backend/tests/utils/user.py`
