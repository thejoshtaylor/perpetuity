---
id: T03
parent: S05
milestone: M006-ydo2ce
key_files:
  - orchestrator/tests/integration/test_create_repository_user_token.py
  - orchestrator/tests/integration/test_create_repository.py
key_decisions:
  - Used assert_all_called=False on the respx.mock context for tests where access_tokens must NOT be called, then explicitly asserted mint_route.called == False — avoids false failures from the route being registered but legitimately uncalled
  - Placed tests in orchestrator/tests/integration/ following slice plan spec; the integration conftest autouse skip only fires on SKIP_INTEGRATION=1, so these TestClient+respx tests run hermetically without docker
  - test_create_repository.py documents the 502→422 behavior change inline via a comment referencing M006-ydo2ce S05 so future readers understand why the baseline changed
duration: 
verification_result: passed
completed_at: 2026-05-12T22:48:21.770Z
blocker_discovered: false
---

# T03: Created test_create_repository_user_token.py (5 tests proving user-token forwarding to POST /user/repos) and test_create_repository.py (6 regression tests including 502→422 update for personal install without user token); all 11 pass

**Created test_create_repository_user_token.py (5 tests proving user-token forwarding to POST /user/repos) and test_create_repository.py (6 regression tests including 502→422 update for personal install without user token); all 11 pass**

## What Happened

Read the T02 implementation in routes_github.py and the existing unit test harness pattern in test_github_tokens.py to understand the FakePool/FakeRedis/TestClient/_install_state pattern. Neither test_create_repository_user_token.py nor test_create_repository.py existed in orchestrator/tests/integration/ — both were created from scratch.

test_create_repository_user_token.py implements the 5 milestone-proof tests:
1. test_personal_install_with_user_token_uses_user_token_for_user_repos: registers respx mocks for /app/installations/42 (lookup) and /user/repos, captures URL and Authorization header, asserts URL == .../user/repos, auth == 'token ghu_user_token_abc', and access_tokens route call count == 0 (mint skipped on personal path).
2. test_personal_install_no_user_token_returns_422: asserts 422 with detail == 'user_token_required_for_personal_install'; both access_tokens and /user/repos routes are asserted uncalled.
3. test_org_install_uses_install_token_for_orgs_repos: all three GitHub routes (lookup, access_tokens, /orgs/{login}/repos) called; URL and auth verified against install token.
4. test_org_install_ignores_user_token_header: org install with X-GitHub-User-Token present; captures auth header, verifies user token absent, verifies WARN log 'github_create_repository_unexpected_user_token_on_org'.
5. test_personal_install_user_token_not_in_logs: caplog sweep at DEBUG level; asserts no log record contains the literal user token string 'ghu_user_token_abc'.

test_create_repository.py implements the M005-sqm8et baseline regression suite (6 tests):
- org install 201 happy path
- org install 502 when GitHub returns non-201
- GitHub App not configured -> 503
- Personal install + no user token -> 422 user_token_required_for_personal_install (updated from the M005-sqm8et 502 expectation; comment in test documents the M006-ydo2ce S05 change)
- Missing repo_name -> 422 repo_name_required
- Invalid private type -> 422 private_must_be_boolean

Initial run had 2 failures: test_personal_install_with_user_token_uses_user_token_for_user_repos and test_personal_install_user_token_not_in_logs both failed because the access_tokens route was registered inside a respx.mock(assert_all_called=True) context — but the personal-install-with-user-token path correctly does NOT call access_tokens, so assert_all_called fired. Fixed by switching those two tests to assert_all_called=False and relying on explicit mint_route.called assertions. All 11 tests then passed.

## Verification

cd orchestrator && uv run pytest tests/integration/test_create_repository_user_token.py tests/integration/test_create_repository.py -v — 11 passed in 0.96s. Redaction sweep test (test_personal_install_user_token_not_in_logs) passed, confirming no token plaintext in orchestrator logs. access_tokens mint-call-count assertion confirmed zero for personal-install-with-user-token path.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd orchestrator && uv run pytest tests/integration/test_create_repository_user_token.py tests/integration/test_create_repository.py -v` | 0 | 11 passed in 0.96s | 960ms |

## Deviations

none — both files were created new as specified; the assert_all_called fix was ordinary debugging during implementation, not a plan deviation

## Known Issues

The pre-existing test_get_installation_token_cache_miss_setex_ttl timing flake (TTL 2999 vs 3000) in test_github_tokens.py is unrelated to this task and was not touched.

## Files Created/Modified

- `orchestrator/tests/integration/test_create_repository_user_token.py`
- `orchestrator/tests/integration/test_create_repository.py`
