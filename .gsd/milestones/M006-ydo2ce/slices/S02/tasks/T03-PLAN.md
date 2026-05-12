---
estimated_steps: 1
estimated_files: 1
skills_used: []
---

# T03: `_fetch_github_user_id` helper + token persistence in `_process_install_callback`

This is the slice's main effect — installs cause token rows. Both the GitHub GET /user call and the DB upsert live here so the transactional guarantee in must-have (6) holds. Add async _fetch_github_user_id(access_token: str) -> int colocated with _resolve_installation_id_from_oauth_code. Change _process_install_callback signature to (session, installation_id, state, oauth_tuple: ResolvedOAuthInstall | None = None). After existing github_app_installations upsert, if oauth_tuple is not None: call _fetch_github_user_id, build upsert payload for github_user_oauth_tokens, encrypt tokens via encrypt_user_token, compute *_expires_at, execute INSERT ... ON CONFLICT (user_id) DO UPDATE on same session. Single session.commit() at end commits BOTH writes.

## Inputs

- `S01's encrypt_user_token`
- `T01's _decode_install_state with user_id`
- `T02's ResolvedOAuthInstall dataclass`
- `backend/app/api/routes/github.py:619-695 (GET install-callback path)`

## Expected Output

- `_fetch_github_user_id helper raising 502 github_user_lookup_failed on any non-200 or malformed response`
- `_process_install_callback upserts token row inside same transaction as install row upsert`
- `GET callback path captures full dataclass and passes to _process_install_callback`
- `POST path keeps oauth_tuple=None and skips token persistence`

## Verification

cd backend && uv run pytest tests/api/routes/test_github_install_callback.py -v && uv run pytest tests/api/routes/ -v -k oauth
