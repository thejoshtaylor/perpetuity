---
estimated_steps: 1
estimated_files: 2
skills_used: []
---

# T01: Extend `_orch_create_repository` to accept and forward optional `user_token`

Lock the helper's signature change before changing the route, so the route change is the only place exception mapping matters. Add user_token: str | None = None to the function signature. Build headers = {X-Orchestrator-Key: settings.ORCHESTRATOR_API_KEY} then if user_token is not None: headers[X-GitHub-User-Token] = user_token. No other behavior change. Add unit test that asserts: (a) calling with user_token=None produces a request without the new header, (b) calling with user_token=ghu_test produces a request with X-GitHub-User-Token: ghu_test, (c) X-Orchestrator-Key header is always present.

## Inputs

- `backend/app/api/routes/github.py:857-934 (existing _orch_create_repository)`

## Expected Output

- `_orch_create_repository signature includes user_token: str | None = None kwarg with default None`
- `Header X-GitHub-User-Token is set if user_token is not None, omitted otherwise`
- `X-Orchestrator-Key always present`

## Verification

cd backend && uv run pytest tests/api/routes/test_github_orch_create_repository.py -v
