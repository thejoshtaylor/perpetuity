---
estimated_steps: 26
estimated_files: 3
skills_used: []
---

# T04: End-to-end install-flow + token-cache proof against compose stack with mock-github sidecar

Add backend/tests/integration/test_m004_s02_github_install_e2e.py that exercises the full install + token-mint + cache contract end-to-end. Pieces:

1. Skip-guard probing baked images for both s06b alembic revision (in backend:latest) and the new orchestrator/orchestrator/github_tokens.py module (in orchestrator:latest). Hint: docker compose build backend orchestrator.
2. Autouse cleanup fixture DELETEs github_app_installations rows AND the four github_app_* system_settings rows before AND after each test (mirrors MEM246 pattern from S01).
3. Module-local helper _seed_github_app_credentials(backend_url, admin_token, public_key_pem, private_key_pem, app_id) PUTs github_app_id (int), github_app_client_id ('perpetuity-test'), github_app_private_key (the synthetic RSA key the test generates).
4. Module-local helper _boot_mock_github(public_key_pem, fixed_token, app_id) that starts a tiny FastAPI app inside a python:3.12-slim sibling container on perpetuity_default (named mock-github-<uuid>) by mounting backend/tests/integration/fixtures/mock_github_app.py and running uvicorn. The mock app reads PUBLIC_KEY_PEM, FIXED_TOKEN, GITHUB_APP_ID from env, exposes POST /app/installations/{id}/access_tokens (verifies inbound RS256 JWT against PUBLIC_KEY_PEM with iss=<app_id>; on success returns {'token': fixed_token, 'expires_at': '<iso8601 +1h>'}) and GET /app/installations/{id} (returns {'account': {'login': 'test-org', 'type': 'Organization'}, 'id': id}). Yields the container's compose-DNS URL http://mock-github-<uuid>:8080.
5. Module-local helper _boot_orch_with_mock(mock_github_url) parameterizes the existing ephemeral-orchestrator pattern (MEM197) to set GITHUB_API_BASE_URL=<mock_github_url>, ORCHESTRATOR_API_KEY=<test-only random>, SYSTEM_SETTINGS_ENCRYPTION_KEY=SYSTEM_SETTINGS_ENCRYPTION_KEY_TEST. Proves readiness via the MEM198 docker-exec urllib probe.
6. Module-local helper _boot_sibling_backend(orch_url, orch_key) boots a sibling backend pointed at the ephemeral orchestrator (NOT the compose orchestrator) so the install-callback's lookup call hits the mock-github through the right orchestrator. Reuses the conftest backend_url shape but parameterized.
7. Test scenario A — install URL + state JWT shape: signup superuser, seed credentials, signup team-admin user with a team, GET /api/v1/teams/{tid}/github/install-url, decode the state JWT in-test against SECRET_KEY (audience='github-install'), assert team_id matches, assert exp is ~10m in the future.
8. Test scenario B — install-callback round-trip: POST install-callback {installation_id:42, setup_action:'install', state:<jwt from A>}; assert 200 with account_login='test-org' account_type='Organization'; GET /api/v1/teams/{tid}/github/installations returns 1 row.
9. Test scenario C — duplicate install-callback (same installation_id) → 200 idempotent, list still 1 row.
10. Test scenario D — installation token mint + cache: hit ephemeral orchestrator GET /v1/installations/42/token (with X-Orchestrator-Key) → expect 200 source='mint' token=fixed_token; second call within the same test → 200 source='cache' same token. Use docker exec into compose redis to verify KEYS gh:installtok:* has one match and TTL is in (1, 3001).
11. Test scenario E — expired state token: re-sign a state JWT with exp=now-60 using SECRET_KEY; POST install-callback → expect 400 detail='install_state_expired'.
12. Test scenario F — decrypt-failure surfaces 503 over HTTP (closes S01 known-limitation): UPDATE system_settings via psql to set value_encrypted = E'\\x00bad' for github_app_private_key; flush the orchestrator's redis cache key for installation 42; then call ephemeral orchestrator GET /v1/installations/42/token → expect 503 detail='system_settings_decrypt_failed' key='github_app_private_key'. ERROR log line system_settings_decrypt_failed key=github_app_private_key MUST appear in docker logs <ephemeral_orchestrator>.
13. Final redaction sweep: docker logs <sibling_backend> and docker logs <ephemeral_orchestrator> MUST NOT contain gho_, ghs_, ghu_, ghr_, github_pat_ (note: the mock-github container's logs DO contain the issued token by design — only backend/orchestrator logs are swept); MUST NOT contain -----BEGIN. Required positive markers: github_install_url_issued, github_install_callback_accepted, installation_token_minted, installation_token_cache_hit, system_settings_decrypt_failed key=github_app_private_key. The mock-github cleanup fixture stops the mock-github container regardless of test outcome.

## Failure Modes

| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| compose db/redis/orchestrator | _e2e_env_check skip | Same | Same |
| Baked images missing s06b/github_tokens.py | Skip with `docker compose build backend orchestrator` hint | N/A | N/A |
| mock-github container fails to start | pytest.fail with mock-github docker logs tail | 30s readiness deadline | N/A |
| ephemeral orchestrator boot | pytest.fail with orchestrator docker logs tail | 60s | N/A |

## Load Profile

- This is a single-process e2e — no concurrency concerns inside the test.
- Fixed installation_id=42 keeps the cache key deterministic for KEYS/TTL probes.

## Negative Tests

Covered by the scenario list above (E expired state, F decrypt failure, redaction sweep). Optional scenario G — orchestrator returns 502 on GitHub 401 (mock-github toggled to reject the JWT); install-callback's lookup propagates as 502 github_lookup_failed.

## Inputs

- ``backend/tests/integration/conftest.py` — backend_url fixture and SYSTEM_SETTINGS_ENCRYPTION_KEY_TEST; pattern for sibling backend boot`
- ``backend/tests/integration/test_m002_s05_two_key_rotation_e2e.py` — pattern for ephemeral orchestrator with overridden env (MEM197) + MEM198 readiness probe`
- ``backend/tests/integration/test_m004_s01_sensitive_settings_e2e.py` — pattern for autouse cleanup of system_settings rows + skip-guard probing baked image (MEM246/MEM247)`
- ``backend/app/api/routes/github.py` — endpoints under test (T02 output)`
- ``orchestrator/orchestrator/routes_github.py` — endpoints under test (T03 output)`
- ``orchestrator/orchestrator/github_tokens.py` — token mint surface under test (T03 output)`
- ``backend/app/alembic/versions/s06b_github_app_installations.py` — migration that the skip-guard probes for`

## Expected Output

- ``backend/tests/integration/test_m004_s02_github_install_e2e.py` — single test function or small set of test functions covering scenarios A–F (and optionally G) + redaction sweep + log marker assertions; passes against compose db/redis with sibling backend + ephemeral orchestrator + mock-github sidecar`
- ``backend/tests/integration/fixtures/mock_github_app.py` — minimal FastAPI app exporting two routes that verify inbound RS256 JWT against PUBLIC_KEY_PEM env var and return canned responses; runnable as `python -m uvicorn mock_github_app:app --host 0.0.0.0 --port 8080``
- ``backend/tests/integration/fixtures/__init__.py` — empty package marker`

## Verification

docker compose build backend orchestrator && cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m004_s02_github_install_e2e.py -v
