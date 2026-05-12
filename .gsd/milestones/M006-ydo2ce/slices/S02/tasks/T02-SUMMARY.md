---
id: T02
parent: S02
milestone: M006-ydo2ce
key_files:
  - backend/app/api/routes/github.py
  - backend/tests/api/routes/test_github_oauth_resolve.py
key_decisions:
  - scope field accepts empty string (GitHub Apps that request no additional scopes return scope='') — validated by key presence and isinstance str check only, not truthiness
  - Used inner helper functions _require_str/_require_int to DRY the field validation loop
  - autouse fixture injects fresh Fernet key + clears functools.cache to avoid cross-test interference, matching the established pattern in test_github_user_tokens_crypto.py
  - asyncio.run() used instead of deprecated asyncio.get_event_loop().run_until_complete()
duration: 
verification_result: passed
completed_at: 2026-05-12T21:27:11.315Z
blocker_discovered: false
---

# T02: Refactored _resolve_installation_id_from_oauth_code to return ResolvedOAuthInstall dataclass with full token payload validation

**Refactored _resolve_installation_id_from_oauth_code to return ResolvedOAuthInstall dataclass with full token payload validation**

## What Happened

Defined the @dataclass ResolvedOAuthInstall (fields: installation_id, access_token, refresh_token, expires_in, refresh_token_expires_in, scope) near the helper function in backend/app/api/routes/github.py. Added `from dataclasses import dataclass` import. Refactored _resolve_installation_id_from_oauth_code to validate all six token fields — access_token (str, non-empty), refresh_token (str, non-empty), expires_in (int), refresh_token_expires_in (int), and scope (str, may be empty) — raising HTTPException(502, detail="github_oauth_exchange_failed") with log reason=token_payload_incomplete field=<name> on any missing or wrong-typed field. The function now returns the full ResolvedOAuthInstall dataclass instead of just an int. Updated the single caller in the GET /github/install-callback handler to extract .installation_id from the returned dataclass. Created tests/api/routes/test_github_oauth_resolve.py with 7 pure unit tests (no DB, no real network) using a mock session, monkeypatched httpx.AsyncClient, and autouse fixture that injects a fresh Fernet key and clears the _load_key cache — following the same pattern as test_github_user_tokens_crypto.py.

## Verification

Ran: cd /Users/josh/code/perpetuity/backend && uv run pytest tests/api/routes/test_github_oauth_resolve.py -v. Result: 7 passed, 0 failed, 0 warnings (config warnings only). Tests are self-contained — no external SYSTEM_SETTINGS_ENCRYPTION_KEY env var required.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd /Users/josh/code/perpetuity/backend && uv run pytest tests/api/routes/test_github_oauth_resolve.py -v` | 0 | 7 passed | 10000ms |

## Deviations

None — implemented exactly as specified in T02-PLAN.md

## Known Issues

None.

## Files Created/Modified

- `backend/app/api/routes/github.py`
- `backend/tests/api/routes/test_github_oauth_resolve.py`
