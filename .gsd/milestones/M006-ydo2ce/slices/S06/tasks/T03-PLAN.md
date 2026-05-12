---
estimated_steps: 1
estimated_files: 1
skills_used: []
---

# T03: Component tests for all four error branches + 409-no-reason case

Must-have (8)'s five test cases; each maps to one user-visible state that S06 must guarantee. The window.open spy and fetch mock combination is the linchpin proving the install URL actually flows through. Use existing component test harness (Vitest + React Testing Library OR Playwright component testing). Mock fetch per test case to return documented response shape. For window.open test, use vi.spyOn(window, 'open').mockImplementation. For install-url fetch in test (b), mock a second fetch response keyed on URL pattern. Each test case from must-have (8) gets its own it(...) block.

## Inputs

- `T01 and T02 implementations`
- `Existing component test harness pattern in frontend/tests/components/`

## Expected Output

- `test_renders_reinstall_cta_on_409_github_user_token_required`
- `test_click_reinstall_fetches_install_url_and_opens_tab (asserts window.open called with the right url + noopener,noreferrer)`
- `test_502_refresh_transient_shows_generic_retry_message_not_cta`
- `test_503_decrypt_failed_shows_operator_notified_message_not_cta`
- `test_409_reason_field_is_optional`
- `Existing M005-sqm8et happy-path test (if present) still passes`

## Verification

cd frontend && npm test -- CreateGitHubRepoDialog
