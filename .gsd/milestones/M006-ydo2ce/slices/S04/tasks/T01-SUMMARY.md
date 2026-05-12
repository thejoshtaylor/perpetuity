---
id: T01
parent: S04
milestone: M006-ydo2ce
key_files:
  - backend/app/api/routes/github.py
  - backend/tests/api/routes/test_github_orch_create_repository.py
key_decisions:
  - Added user_token as a keyword-only argument with default None to preserve backward compatibility with all existing call sites
  - Used conditional header insertion (if user_token is not None) rather than a falsy check so an empty string would not silently be forwarded as a header value
  - Tests use anyio + monkeypatched httpx.AsyncClient.post to stay fully unit-level without a live orchestrator or database
duration: 
verification_result: passed
completed_at: 2026-05-12T22:00:31.666Z
blocker_discovered: false
---

# T01: Extended `_orch_create_repository` to accept and forward optional `user_token` as `X-GitHub-User-Token` header

**Extended `_orch_create_repository` to accept and forward optional `user_token` as `X-GitHub-User-Token` header**

## What Happened

Read the existing `_orch_create_repository` function at line 1061 of `backend/app/api/routes/github.py`. Added `user_token: str | None = None` as a fifth keyword argument with a default of `None`. Changed the headers dict type annotation to `dict[str, str]` and added a conditional `headers["X-GitHub-User-Token"] = user_token` block that only fires when `user_token is not None`. `X-Orchestrator-Key` was already present unconditionally and remains so. No other behavior was changed; the call site at line 1223 continues to work with its existing positional arguments (user_token defaults to None). Created `backend/tests/api/routes/test_github_orch_create_repository.py` with three anyio unit tests that directly call the async helper with a monkeypatched `httpx.AsyncClient.post` to capture request headers: (a) user_token=None omits X-GitHub-User-Token, (b) user_token="ghu_test" sets X-GitHub-User-Token: ghu_test, (c) X-Orchestrator-Key is always present for both None and non-None user_token values.

## Verification

Ran `cd backend && uv run pytest tests/api/routes/test_github_orch_create_repository.py -v` — all 3 tests collected and passed in 0.14s.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd /Users/josh/code/perpetuity/backend && uv run pytest tests/api/routes/test_github_orch_create_repository.py -v` | 0 | 3 passed, 3 warnings in 0.14s | 4200ms |

## Deviations

None — implemented exactly as specified in the task plan.

## Known Issues

None.

## Files Created/Modified

- `backend/app/api/routes/github.py`
- `backend/tests/api/routes/test_github_orch_create_repository.py`
