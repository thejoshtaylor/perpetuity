---
id: T05
parent: S02
milestone: M006-ydo2ce
key_files:
  - backend/tests/api/routes/test_github_install_callback.py
key_decisions:
  - Used installation_id (not code) in the legacy-state JWT test so the GET handler bypasses the OAuth exchange and routes directly into _process_install_callback → _decode_install_state, where the missing user_id triggers the 400 → redirect-with-error path.
  - No orch mock routes needed for the legacy-state test because _decode_install_state raises before any HTTP call to the orchestrator.
duration: 
verification_result: passed
completed_at: 2026-05-12T21:41:49.438Z
blocker_discovered: false
---

# T05: Added legacy-state JWT rejection test and org-install regression guard to test_github_install_callback.py; 13/13 pass.

**Added legacy-state JWT rejection test and org-install regression guard to test_github_install_callback.py; 13/13 pass.**

## What Happened

Read T05-PLAN.md and prior task summaries (T01–T04) to understand the existing test structure and the state-JWT contract changes from T01.

Two tests were appended to `tests/api/routes/test_github_install_callback.py`:

1. **test_get_install_callback_legacy_state_jwt_rejected**: Manually constructs a JWT with `jwt.encode` using the correct secret/aud/iss but deliberately omitting the `user_id` claim (the M005-era shape). Issues a GET to `/github/install-callback` with `installation_id` (not `code`) so the OAuth exchange is skipped and `_decode_install_state` fires immediately. Asserts the 302 redirect location contains `github_install_error=install_state_user_unknown`. A `FakeAsyncClient({})` is installed but never called because the rejection fires before any HTTP hop.

2. **test_post_install_callback_org_install_path_unchanged**: Exercises the POST `/github/install-callback` path (org install, no OAuth code) end-to-end with a real team, a freshly minted state JWT, and a mock orch lookup. Asserts 200, the expected JSON body fields (`installation_id`, `team_id`, `account_login`, `account_type`), a persisted `github_app_installations` row, and zero rows in `github_user_oauth_tokens` — confirming S02's token-persistence work did not break the M005 baseline.

Initial attempt used `code=ghu_legacycode` for the legacy-state test; this caused a POST to `github.com/login/oauth/access_token` before `_decode_install_state` could fire. Fixed by using `installation_id` instead, which skips the OAuth exchange path entirely.

## Verification

Ran the verification command from the task plan:
`cd backend && uv run pytest tests/api/routes/test_github_install_callback.py -v -k "legacy_state or org_install"`
Result: 2 passed.

Also ran the full file to confirm no regressions:
`uv run pytest tests/api/routes/test_github_install_callback.py -v`
Result: 13 passed, 0 failed.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd /Users/josh/code/perpetuity/backend && uv run pytest tests/api/routes/test_github_install_callback.py -v -k 'legacy_state or org_install'` | 0 | 2 passed | 420ms |
| 2 | `cd /Users/josh/code/perpetuity/backend && uv run pytest tests/api/routes/test_github_install_callback.py -v` | 0 | 13 passed | 620ms |

## Deviations

Initial attempt used code param for the legacy-state test, causing the route to attempt the OAuth exchange before reaching _decode_install_state. Fixed by switching to installation_id param, which matches the Setup URL flow and goes directly to state validation.

## Known Issues

none

## Files Created/Modified

- `backend/tests/api/routes/test_github_install_callback.py`
