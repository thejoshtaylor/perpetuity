---
estimated_steps: 1
estimated_files: 1
skills_used: []
---

# T03: Route integration tests covering all five branches + org-install regression

The contract surface this slice ships is a 1-of-5 HTTP response decision tree; every leaf needs an explicit test, including the no-side-effects assertions (orchestrator NOT called on the 409/502/503 branches). Implement the six test cases from must-have (7): test_personal_install_forwards_user_token, test_personal_install_missing_token_returns_409, test_org_install_no_user_token_header, test_personal_install_refresh_transient_returns_502, test_personal_install_decrypt_failure_returns_503, test_personal_install_bad_refresh_token_includes_reason. Use respx for orchestrator mock; assert call count of zero when route is expected to short-circuit. Use caplog to assert no captured log message contains the literal mocked token plaintext.

## Inputs

- `T01 and T02 implementations`
- `Existing test fixtures for current_user + team + github_app_installations`

## Expected Output

- `Six test cases per must-have (7) all passing`
- `M005-sqm8et org-path test still passes (assert X-GitHub-User-Token not in request.headers)`
- `Redaction sweep against caplog records returns zero matches for any mocked token string`

## Verification

cd backend && uv run pytest tests/api/routes/test_github_create_repository.py -v
