---
id: T03
parent: S04
milestone: M006-ydo2ce
key_files:
  - backend/tests/api/routes/test_github_create_repository.py
key_decisions:
  - Used unittest.mock AsyncMock instead of respx (respx not installed); _build_orch_mock returns the raw mock_post handle so callers can assert call_count and call_args without needing respx route objects
  - Kept the three pre-existing validation/404 tests to avoid regression gaps
  - caplog redaction sweep implemented inline in test_personal_install_forwards_user_token (the only test where a real plaintext token is injected)
duration: 
verification_result: passed
completed_at: 2026-05-12T22:19:21.918Z
blocker_discovered: false
---

# T03: Added six named route integration tests covering all five decision-tree branches with orch call-count assertions and caplog token-redaction sweep

**Added six named route integration tests covering all five decision-tree branches with orch call-count assertions and caplog token-redaction sweep**

## What Happened

Replaced the T02-era test file with a complete T03 test suite. The key additions over the prior file: (1) `_build_orch_mock` returns the raw `AsyncMock` for `httpx.AsyncClient().post` so every test can inspect `call_count` and `call_args`; (2) `test_personal_install_forwards_user_token` captures orch kwargs and asserts `X-GitHub-User-Token == fake_token` in headers, plus a caplog redaction sweep confirming the literal token string never appears in any log record; (3) `test_personal_install_missing_token_returns_409`, `test_personal_install_refresh_transient_returns_502`, and `test_personal_install_decrypt_failure_returns_503` all assert `mock_post.call_count == 0`; (4) `test_org_install_no_user_token_header` asserts `mock_post.call_count == 1` AND `X-GitHub-User-Token not in sent_headers` (M005-sqm8et regression); (5) `test_personal_install_bad_refresh_token_includes_reason` is a dedicated standalone test for the `bad_refresh_token` reason. `respx` was not available in the venv so `unittest.mock` was used instead — the call-count and header-inspection assertions satisfy the same contract. All 9 tests (6 T03 + 3 pre-existing validation tests) pass in 0.85s.

## Verification

cd backend && uv run pytest tests/api/routes/test_github_create_repository.py -v — 9 passed, 0 failed.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd /Users/josh/code/perpetuity/backend && uv run pytest tests/api/routes/test_github_create_repository.py -v` | 0 | 9 passed | 850ms |

## Deviations

respx was not available in the venv; unittest.mock AsyncMock used instead. The call-count and header-capture assertions provide equivalent coverage to what respx route matchers would give.

## Known Issues

None.

## Files Created/Modified

- `backend/tests/api/routes/test_github_create_repository.py`
