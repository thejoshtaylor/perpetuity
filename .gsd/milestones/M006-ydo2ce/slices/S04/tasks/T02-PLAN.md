---
estimated_steps: 1
estimated_files: 1
skills_used: []
---

# T02: Wire `account_type` branch + `get_user_access_token` + exception mapping into the route

This is the slice's user-visible substance. Each exception class has a distinct HTTP status, and each has to be surfaced separately so S06's CTA logic can branch on detail accurately. After the installation_not_found check at :992-993, before body validation at :996, add new section. If installation.account_type != Organization: try user_token = await get_user_access_token(session, current_user.id); catch UserTokenUnavailable and GitHubUserTokenDecryptError and map them to documented HTTP responses. Else: user_token = None. Pass user_token=user_token to _orch_create_repository call at :1019. Add defense-in-depth assertion from must-have (10) immediately before that call.

## Inputs

- `S03's get_user_access_token, UserTokenUnavailable, GitHubUserTokenDecryptError`
- `T01's _orch_create_repository signature change`
- `backend/app/api/routes/github.py:966-1034 (existing create_github_repository)`

## Expected Output

- `account_type branch resolves user_token for personal installs, None for org`
- `UserTokenUnavailable mapped: row_missing/bad_refresh_token/refresh_rejected/refresh_unexpected_response -> 409 github_user_token_required with installation_id and reason in body`
- `UserTokenUnavailable(refresh_transient) -> 502 github_token_refresh_transient`
- `GitHubUserTokenDecryptError -> 503 github_user_token_decrypt_failed (ERROR log; no token bytes)`
- `Defense-in-depth assertion immediately before _orch_create_repository call`
- `Inline comment about skipping GET /installation/{id} pre-flight`

## Verification

cd backend && uv run python -c "from app.api.routes.github import create_github_repository; print('ok')"
