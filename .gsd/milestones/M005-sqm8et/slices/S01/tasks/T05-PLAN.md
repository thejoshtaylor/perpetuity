---
estimated_steps: 1
estimated_files: 3
skills_used: []
---

# T05: Integration e2e + redaction sweep extension + alembic skip-guard

Add `backend/tests/integration/test_m005_s01_team_secrets_e2e.py` running against the full compose stack. Test plan: (a) team admin pastes Claude + OpenAI keys via PUT; GET shows has_value=true; (b) PUT replace bumps updated_at; (c) DELETE clears; subsequent GET shows has_value=false; (d) non-admin PUT → 403; (e) bad-prefix value → 400; (f) round-trip via `get_team_secret` from a test-only endpoint that returns the helper's plaintext to a system_admin caller (gated for tests only — NOT shipped in production routes); (g) tamper test — directly UPDATE the row's value_encrypted to garbage, call the test endpoint, expect 503 `team_secret_decrypt_failed`; (h) at end of test, run `scripts/redaction-sweep.sh` against `docker compose logs` and assert no `sk-ant-` or `sk-` matches. Include MEM162 alembic skip-guard autouse fixture probing for `s09_team_secrets` revision in `backend:latest`. Extend `scripts/redaction-sweep.sh` to grep for `sk-ant-` and `sk-` (in addition to existing `gho_/ghu_/ghr_/github_pat_/-----BEGIN`).

## Inputs

- `Existing M004 e2e tests (`test_m004_s01_*.py`) for compose-stack fixture pattern`
- `MEM162 alembic skip-guard pattern from M004 e2es`
- `MEM134 / R054 redaction discipline`
- `T01–T04 outputs`

## Expected Output

- `e2e passes in <30s against full compose stack`
- `Redaction sweep finds zero `sk-ant-` or `sk-` occurrences in container logs after the e2e run`
- `Skip-guard fires (test SKIPPED with build instruction) when `backend:latest` lacks the s09 revision`
- `All 8 test cases (a–h) pass green`

## Verification

cd backend && uv run pytest tests/integration/test_m005_s01_team_secrets_e2e.py -v && bash scripts/redaction-sweep.sh

## Observability Impact

Verifies T03's log lines redact correctly (sweep clean post-test). The test-only endpoint `/api/v1/teams/{id}/secrets/{key}/_test_decrypt` is registered behind a `pytest`-only gate — emits no log lines and is removed from the OpenAPI schema in production.
