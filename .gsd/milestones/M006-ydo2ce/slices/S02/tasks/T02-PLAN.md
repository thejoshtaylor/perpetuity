---
estimated_steps: 1
estimated_files: 2
skills_used: []
---

# T02: Refactor `_resolve_installation_id_from_oauth_code` to return `ResolvedOAuthInstall`

The function already POSTs to the GitHub token endpoint and receives the full payload but throws away every field except the access token. S02 needs all four token-payload fields downstream. Define @dataclass ResolvedOAuthInstall (installation_id, access_token, refresh_token, expires_in, refresh_token_expires_in, scope). Read all five token-payload fields from token_body; if any is missing or wrong-type, raise HTTPException(502, detail=github_oauth_exchange_failed) with new log reason token_payload_incomplete field=<name>. Return the dataclass.

## Inputs

- `backend/app/api/routes/github.py:290-462 (existing _resolve_installation_id_from_oauth_code)`

## Expected Output

- `ResolvedOAuthInstall dataclass defined near the function`
- `Function signature returns ResolvedOAuthInstall`
- `Defensive parsing for missing/wrong-typed fields with 502 reason token_payload_incomplete`
- `Tests cover happy path + missing refresh_token + missing scope`

## Verification

cd backend && uv run pytest tests/api/routes/test_github_oauth_resolve.py -v
