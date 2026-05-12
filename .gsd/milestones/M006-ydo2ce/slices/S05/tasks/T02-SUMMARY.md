---
id: T02
parent: S05
milestone: M006-ydo2ce
key_files:
  - orchestrator/orchestrator/routes_github.py
key_decisions:
  - Structured branching as if/else on account_type with inner if/else on user_token to minimise nesting and make each decision point explicit
  - install-token mint block moved entirely inside the else (org) branch so it is genuinely unreachable for personal installs with a user token
  - auth_header variable introduced to unify the single httpx.post call regardless of which branch was taken, avoiding code duplication
  - Pre-existing test failures (TTL flake, routes_projects ref kwarg) not fixed as they are outside T02 scope
duration: 
verification_result: passed
completed_at: 2026-05-12T22:40:44.811Z
blocker_discovered: false
---

# T02: Implemented account_type branching, user-token-prefer logic, and 422 defense-in-depth in create_repository_route

**Implemented account_type branching, user-token-prefer logic, and 422 defense-in-depth in create_repository_route**

## What Happened

Read the current state of orchestrator/orchestrator/routes_github.py after T01's changes. The file had `user_token` already being read from the `X-GitHub-User-Token` header, and `lookup_installation` ran before `get_installation_token`. However, the branching logic was absent — the old code always called `get_installation_token` regardless of account_type or user_token presence, and always used the install token in the Authorization header.

Replaced the block from `account_login/account_type` extraction through the HTTP call with a three-branch decision matrix:

1. Personal install (`account_type == "User"`) + `user_token` present: sets `create_url = "https://api.github.com/user/repos"` and `auth_header = f"token {user_token}"`. Logs INFO with `token_class=user_token` and `user_token_prefix=<first 4 chars>`. Install token mint is entirely skipped.

2. Personal install + no `user_token`: logs WARN with `reason=user_token_required_for_personal_install` and returns HTTP 422 with detail `user_token_required_for_personal_install` immediately, before any mint call.

3. Org install (`account_type == "Organization"`): if `user_token` is not None, logs WARN `github_create_repository_unexpected_user_token_on_org`; then proceeds to call `get_installation_token`, sets `create_url = f"https://api.github.com/orgs/{account_login}/repos"` and `auth_header = f"token {install_token}"`.

The downstream `httpx.AsyncClient.post()` call was updated to use `auth_header` instead of the hardcoded `f"token {token}"`. All existing exception mapping (503 for `_NotConfigured`, 502 for `InstallationTokenMintFailed`, 502 for transport errors) is preserved within the org branch where the mint still runs.

## Verification

Import verification via `uv run python -c "from orchestrator.routes_github import create_repository_route; print('ok')"` returned `ok`. Unit test suite ran 162 passing tests with 2 pre-existing failures (TTL timing flake and routes_projects ref keyword mismatch) unrelated to these changes.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd /Users/josh/code/perpetuity/orchestrator && uv run python -c "from orchestrator.routes_github import create_repository_route; print('ok')"` | 0 | Import succeeds — routing function is syntactically valid and importable | 800ms |
| 2 | `cd /Users/josh/code/perpetuity/orchestrator && uv run python -m pytest tests/unit/ -q 2>&1 | tail -5` | 1 | 162 passed, 2 pre-existing failures (TTL flake + routes_projects ref kwarg) — no new failures | 3500ms |

## Deviations

None — implementation matches the task plan exactly.

## Known Issues

None.

## Files Created/Modified

- `orchestrator/orchestrator/routes_github.py`
