---
id: T02
parent: S03
milestone: M006-ydo2ce
key_files:
  - backend/app/core/github_user_tokens.py
  - backend/app/models.py
  - backend/tests/unit/test_github_user_tokens_refresh.py
key_decisions:
  - Added access_token_expires_at and refresh_token_expires_at as datetime | None fields to GitHubUserOAuthToken model (the migration had them but the SQLModel class was missing them)
  - Used asyncio.run() via _run() helper for async tests instead of pytest.mark.asyncio (pytest-asyncio not installed; pattern matches test_github_oauth_resolve.py)
  - Stubbed expired-access path with reason='refresh_required' so T03 can implement it without T02 tests accidentally covering that branch
  - Patched httpx.AsyncClient.post at class level to assert no network calls in happy/row_missing paths
duration: 
verification_result: passed
completed_at: 2026-05-12T21:48:01.292Z
blocker_discovered: false
---

# T02: Added UserTokenUnavailable, get_user_access_token (happy/row_missing paths), module constants, and 5-test unit suite — all 5 pass with no GitHub HTTP calls made.

**Added UserTokenUnavailable, get_user_access_token (happy/row_missing paths), module constants, and 5-test unit suite — all 5 pass with no GitHub HTTP calls made.**

## What Happened

Explored the codebase to confirm: S01 (T01–T03) was complete — GitHubUserOAuthToken model in models.py, encrypt/decrypt helpers in github_user_tokens.py, and the S03/T01 github_app_oauth.py extraction were all done. The GitHubUserOAuthToken model was missing access_token_expires_at and refresh_token_expires_at fields (the migration added them but T02 of S01 hadn't added them to the SQLModel class). Added both datetime | None fields with sa_type=DateTime(timezone=True) to models.py. Extended backend/app/core/github_user_tokens.py with: _ACCESS_TOKEN_SKEW_SECONDS = 60 constant, _GITHUB_TOKEN_URL constant, UserTokenUnavailable exception class with user_id and reason attributes, and async get_user_access_token(session, user_id) -> str implementing row-fetch via await session.get(), row_missing raise, and the happy-path branch (now < expires_at - 60s → decrypt and return, no network call). The expired-access path is stubbed with reason='refresh_required' for T03 to implement. Created backend/tests/unit/test_github_user_tokens_refresh.py with 5 tests: happy path asserts token returned and httpx.AsyncClient.post not called; row_missing asserts UserTokenUnavailable raised with correct user_id/reason and no HTTP call; class contract tests; module constants test. Used asyncio.run() pattern (matching test_github_oauth_resolve.py) instead of pytest-asyncio since the latter is not installed.

## Verification

cd backend && uv run pytest tests/unit/test_github_user_tokens_refresh.py -v -k "happy or row_missing" — 2 passed. Full file: 5 passed. Regression: tests/unit/test_github_user_tokens_crypto.py — 6 passed.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd /Users/josh/code/perpetuity/backend && uv run pytest tests/unit/test_github_user_tokens_refresh.py -v -k 'happy or row_missing'` | 0 | 2 passed, 3 deselected | 100ms |
| 2 | `cd /Users/josh/code/perpetuity/backend && uv run pytest tests/unit/test_github_user_tokens_refresh.py -v` | 0 | 5 passed | 100ms |
| 3 | `cd /Users/josh/code/perpetuity/backend && uv run pytest tests/unit/test_github_user_tokens_crypto.py -v` | 0 | 6 passed (no regressions) | 100ms |

## Deviations

GitHubUserOAuthToken model needed access_token_expires_at and refresh_token_expires_at added (S01/T02 only added columns present in the original task spec but the migration had these timestamp columns). This was a prerequisite fix, not a deviation from T02's intent.

## Known Issues

None.

## Files Created/Modified

- `backend/app/core/github_user_tokens.py`
- `backend/app/models.py`
- `backend/tests/unit/test_github_user_tokens_refresh.py`
