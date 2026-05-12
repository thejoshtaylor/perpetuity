---
estimated_steps: 1
estimated_files: 2
skills_used: []
---

# T03: Integration tests `test_create_repository_user_token.py` + M005-sqm8et 502->422 update

This is where the milestone's core proof lands — must-have (6) test_personal_install_with_user_token_uses_user_token_for_user_repos is the assertion that POST /user/repos accepts the forwarded token. Until this passes, every other slice in M006-ydo2ce is plumbing for a claim we have not yet proven. Implement all five tests in must-have (6) using respx against api.github.com so the test asserts the exact URL hit by the orchestrator. For the installation-token-mint mock, intercept api.github.com/app/installations/<id>/access_tokens and assert call count of zero in the personal-install-with-user-token test. Update existing M005-sqm8et test that asserts 502 on personal-install create-repository to assert 422 with the new detail.

## Inputs

- `T01 and T02 implementations`
- `Existing orchestrator integration harness pattern`

## Expected Output

- `Five new tests in test_create_repository_user_token.py all passing`
- `M005-sqm8et personal-install 502 test updated to assert 422 with comment linking to M006-ydo2ce S05`
- `Redaction sweep against captured orchestrator logs returns zero matches for any literal mocked token string`

## Verification

cd orchestrator && uv run pytest tests/integration/test_create_repository_user_token.py tests/integration/test_create_repository.py -v
