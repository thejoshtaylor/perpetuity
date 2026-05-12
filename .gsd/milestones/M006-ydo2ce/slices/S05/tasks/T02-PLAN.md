---
estimated_steps: 1
estimated_files: 1
skills_used: []
---

# T02: Branch on `account_type` + user-token-prefer logic + 422 defense-in-depth

The slice's substance — every documented branch combination of (account_type, user_token) reaches the right HTTP call. Implement must-have (3) decision matrix. For personal-install + user-token-present branch: build create_url = https://api.github.com/user/repos and auth_header = token <user_token>. For personal-install + no-token branch: return 422 with documented detail BEFORE install-token mint code path is reached. For org installs: log WARN if user_token is not None and continue with install-token path. Success-log line for user-token branch includes token_class=user_token user_token_prefix=<first 4 chars>.

## Inputs

- `T01's reordering`

## Expected Output

- `Personal install + user_token -> POST /user/repos with Authorization: token <user_token>; install-token mint genuinely skipped`
- `Personal install + no header -> 422 user_token_required_for_personal_install before mint`
- `Org install: WARN github_create_repository_unexpected_user_token_on_org if user_token present; install-token path used`
- `INFO log carries token_class=user_token user_token_prefix=<first-4-chars>`

## Verification

cd orchestrator && uv run python -c "from orchestrator.routes_github import create_repository_route; print('ok')"
