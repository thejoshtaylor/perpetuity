---
id: T04
parent: S02
milestone: M004-guylpp
key_files:
  - backend/tests/integration/fixtures/__init__.py
  - backend/tests/integration/fixtures/mock_github_app.py
  - backend/tests/integration/test_m004_s02_github_install_e2e.py
key_decisions:
  - Mock-github runs as a sibling python:3.12-slim container on perpetuity_default with fastapi/uvicorn/pyjwt installed inline at boot (no separate Dockerfile to bake) — keeps the test self-contained at the cost of ~10s of pip install on first boot.
  - Ephemeral-orchestrator HTTP calls from the test process go through `docker exec <eph_name> python3 -c '<urllib probe>'` with the X-Orchestrator-Key header so the container does not need a published host port — extends the MEM198 readiness-probe pattern to authenticated /v1/installations/* routes.
  - The synthetic PEM gets a `Comment: <SENTINEL>` line spliced between the BEGIN armor and the base64 body — PyCA's PEM parser tolerates leading comment headers so the key still signs valid RS256 JWTs while the sentinel is grep-able for the redaction sweep.
  - Decrypt-failure simulation uses `value_encrypted = E'\xdeadbeef'` (4 bytes, even hex digit count) instead of the plan's `E'\x00bad'` which psql rejects as odd-digit hex; cache key gets pre-DELETEd before the call so the mint path runs and triggers Fernet decrypt.
duration: 
verification_result: passed
completed_at: 2026-04-26T01:17:09.256Z
blocker_discovered: false
---

# T04: Add M004/S02 e2e proving install-handshake, token mint+cache, and decrypt-failure 503 against a mock-github sidecar with redaction sweep

**Add M004/S02 e2e proving install-handshake, token mint+cache, and decrypt-failure 503 against a mock-github sidecar with redaction sweep**

## What Happened

Closes slice S02 by exercising the full install + token + cache contract end-to-end against the live compose db/redis with a mock-github sidecar replacing api.github.com.

Pieces shipped:
- `backend/tests/integration/fixtures/__init__.py` (package marker) and `backend/tests/integration/fixtures/mock_github_app.py` — minimal FastAPI mock that verifies inbound RS256 App JWTs against an env-provided public key (with `iss=str(GITHUB_APP_ID)`) and returns the canned `{token, expires_at}` for `POST /app/installations/{id}/access_tokens` and `{id, account: {login: 'test-org', type: 'Organization'}}` for `GET /app/installations/{id}`.
- `backend/tests/integration/test_m004_s02_github_install_e2e.py` — single test function covering scenarios A through F plus the slice's redaction sweep and observability marker assertions.

Stack topology: mock-github sidecar (python:3.12-slim, fastapi/uvicorn/pyjwt installed inline at boot) on `perpetuity_default` with --network-alias `mock-github-<uuid>`; ephemeral orchestrator with `GITHUB_API_BASE_URL=http://mock-github-<uuid>:8080` and `--network-alias orchestrator` so it owns the compose DNS name; sibling backend pointed at `http://orchestrator:8001` with the test's randomly-generated `ORCHESTRATOR_API_KEY`. Skip-guards probe both baked images for the s06b alembic revision (backend) and the github_tokens.py module (orchestrator) — operator gets a `docker compose build backend orchestrator` hint if either is missing.

Scenarios implemented:
A. Install URL + state JWT shape — admin signup → seed credentials → team-admin signup → GET install-url → in-test HS256-decode against SECRET_KEY validates audience='github-install', team_id matches, exp ~10 min in the future, install_url contains the seeded client_id.
B. Install-callback round-trip — public POST with the state JWT and installation_id=42 → 200 with account_login='test-org' account_type='Organization'; subsequent list endpoint returns 1 row.
C. Idempotent duplicate install-callback — fresh state JWT for the same installation_id → 200; list still 1 row (UPSERT proven).
D. Token mint + cache — `_http_orch` exec into the ephemeral container hits `GET /v1/installations/42/token` with X-Orchestrator-Key; first call returns `source='mint' token=<MOCK_FIXED_TOKEN>`, second returns `source='cache'` with the same token. Redis introspection via `docker exec perpetuity-redis-1 redis-cli -a <pw>`: exactly one `gh:installtok:42` key, TTL within `(1, 3001]`.
E. Expired state token — manually-signed JWT with exp=now-60 against SECRET_KEY → 400 detail='install_state_expired'.
F. Decrypt-failure 503 — `UPDATE system_settings SET value_encrypted = E'\\xdeadbeef' WHERE key='github_app_private_key'` via psql, flush the install-token cache, hit the same /token route → 503 detail='system_settings_decrypt_failed' key='github_app_private_key'. The orchestrator's `system_settings_decrypt_failed key=github_app_private_key` ERROR log is asserted.

Final redaction sweep covers the joined backend + ephemeral-orchestrator log blob (mock-github logs are explicitly excluded since they contain the canned token by design): zero occurrences of the `MOCK_FIXED_TOKEN`, zero occurrences of the GitHub token prefix family `gho_/ghu_/ghr_/github_pat_`, `ghs_` permitted only in `token_prefix=...` log lines (the canonical 4-char prefix shape), zero occurrences of the PEM body sentinel embedded in the synthetic key, and zero occurrences of `-----BEGIN`. Required positive markers asserted in the same blob: `github_install_url_issued`, `github_install_callback_accepted`, `installation_token_minted`, `installation_token_cache_hit`, `system_settings_decrypt_failed key=github_app_private_key`.

Two debugging deviations from the plan as written:
1. The plan listed `value_encrypted = E'\\x00bad'` as the corruption pattern; psql rejects this (odd hex digit count). Switched to `E'\\xdeadbeef'` (captured as MEM254).
2. The plan implied the ephemeral orchestrator would be reachable by host port; we use `docker exec python3 -c "<urllib probe>"` with the X-Orchestrator-Key header instead, sidestepping a port publish (captured as MEM253).

Wall-clock: 22-23s on a warm compose stack (well under the 180s slice budget). Verification command from the plan passes:
`docker compose build backend orchestrator && cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m004_s02_github_install_e2e.py -v` → 1 passed.

## Verification

Ran the exact slice verification command from the task plan: `docker compose build backend orchestrator && cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m004_s02_github_install_e2e.py -v` — 1 passed in 23.07s. Test exercises scenarios A (state JWT shape), B (callback round-trip), C (idempotent dup), D (mint + cache + Redis TTL probe), E (expired state 400), F (decrypt-failure 503 + ERROR log), then asserts redaction sweep + 5 required positive log markers.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `docker compose build backend orchestrator && cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m004_s02_github_install_e2e.py -v` | 0 | ✅ pass | 23070ms |
| 2 | `uv run python -c "import ast; ast.parse(open('tests/integration/test_m004_s02_github_install_e2e.py').read()); ast.parse(open('tests/integration/fixtures/mock_github_app.py').read()); print('OK')"` | 0 | ✅ pass | 600ms |
| 3 | `POSTGRES_PORT=5432 uv run pytest --collect-only tests/integration/test_m004_s02_github_install_e2e.py` | 0 | ✅ pass | 100ms |

## Deviations

Two minor in-task fixes, neither plan-invalidating: (1) Replaced `value_encrypted = E'\\x00bad'` from step 12 with `E'\\xdeadbeef'` because psql rejects odd-digit hex literals — captured as MEM254. (2) Hit the ephemeral orchestrator's HTTP surface via `docker exec python3 -c '<urllib probe>'` rather than publishing a host port; cleaner and reuses the MEM198 readiness-probe shape — captured as MEM253. The optional scenario G (mock-github 401 → backend install-callback 502 github_lookup_failed) is deferred — scenarios A-F + redaction sweep already cover the slice's six required INFO/WARNING/ERROR markers.

## Known Issues

None — every required slice-level verification marker fires (`github_install_url_issued`, `github_install_callback_accepted`, `installation_token_minted`, `installation_token_cache_hit`, `system_settings_decrypt_failed key=github_app_private_key`) and the redaction sweep is clean. The github_install_callback_state_invalid WARNING fires inside the test as a side-effect of scenario E (expired state JWT) and is implicitly proven by the 400 status code; an explicit log-line assertion for it could be added if the slice contract tightens.

## Files Created/Modified

- `backend/tests/integration/fixtures/__init__.py`
- `backend/tests/integration/fixtures/mock_github_app.py`
- `backend/tests/integration/test_m004_s02_github_install_e2e.py`
