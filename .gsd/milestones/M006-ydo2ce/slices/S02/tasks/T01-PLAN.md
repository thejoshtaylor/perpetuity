---
estimated_steps: 1
estimated_files: 2
skills_used: []
---

# T01: Extend install-state JWT to carry `user_id` + update mint/decode + install-url route

S01 keys the token table on user_id; the install callback must know which Perpetuity user is doing the install. The only durable carrier across the GitHub redirect is the signed state JWT. Change _mint_install_state(team_id) to _mint_install_state(team_id, user_id); add user_id claim to payload. Update install-url route at :502 to pass current_user.id. In _decode_install_state, after the signature-verified jwt.decode block, validate user_id claim is present and parseable as a UUID; on missing or unparseable, raise HTTPException(400, detail=install_state_user_unknown). Add unit tests for round-trip, missing-user_id rejection, and malformed-user_id rejection.

## Inputs

- `backend/app/api/routes/github.py:113-134 (_mint_install_state, _decode_install_state)`
- `backend/app/api/routes/github.py:473-518 (get_github_install_url)`

## Expected Output

- `_mint_install_state and _decode_install_state require user_id`
- `install-url route passes current_user.id to mint helper`
- `Legacy-shape JWTs (no user_id) are rejected with install_state_user_unknown detail`

## Verification

cd backend && uv run pytest tests/api/routes/test_github_state_jwt.py -v
