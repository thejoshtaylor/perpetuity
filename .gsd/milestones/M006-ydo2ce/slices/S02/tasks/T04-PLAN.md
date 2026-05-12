---
estimated_steps: 1
estimated_files: 2
skills_used: []
---

# T04: Integration test `test_github_oauth_token_persistence.py` + redaction-sweep extension

Proves the cross-cutting invariant: GET install callback through respx-mocked GitHub ends with a decryptable token row, no plaintext anywhere in logs. Model on backend/tests/integration/test_m005_s01_team_secrets_e2e.py for stack-bringup discipline and on existing M005-sqm8et OAuth tests for respx mock shape. Test covers must-have (8) cases (a)-(g). Include MEM162 alembic skip-guard probing for s17_github_user_oauth_tokens revision in backend:latest. Extend scripts/redaction-sweep.sh to grep for ghu_ and ghr_ token prefixes IN COMBINATION with literal mocked test-token suffix.

## Inputs

- `T01, T02, T03 implementations`
- `backend/tests/integration/test_m005_s01_team_secrets_e2e.py (stack-bringup template)`

## Expected Output

- `Test file exercises both success path with token persisted AND reinstall-overwrite path`
- `Redaction sweep finds zero matches for literal mocked test tokens`
- `Test asserts decrypted ciphertext equals mocked access/refresh tokens, github_user_id=42, scopes string, expires_at within 2s of expected NOW()+TTL`

## Verification

cd backend && uv run pytest tests/integration/test_github_oauth_token_persistence.py -v && bash scripts/redaction-sweep.sh
