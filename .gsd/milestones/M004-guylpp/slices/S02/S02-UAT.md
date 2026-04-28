# S02: Per-team GitHub connections (install flow + installation tokens) — UAT

**Milestone:** M004-guylpp
**Written:** 2026-04-26T01:23:32.742Z

# S02 UAT — Per-team GitHub connections (install flow + installation tokens)

## Preconditions

- Compose stack up: `docker compose up -d db redis backend orchestrator` (Postgres on POSTGRES_PORT=5432; redis healthy; backend reachable on the published API port; orchestrator on perpetuity_default network).
- Baked images current: `docker compose build backend orchestrator` so `backend:latest` ships the `s06b_github_app_installations` alembic revision and `orchestrator:latest` ships `orchestrator/orchestrator/github_tokens.py`.
- A system-admin account exists (or a fresh signup is performed via the e2e flow).
- A team exists owned by an admin user (or the test creates one via signup).
- All four GitHub App credentials are present in `system_settings` for the install-url path (T04 seeds these per-test): `github_app_id` (int), `github_app_client_id` (str), `github_app_private_key` (PEM, sensitive), and ideally `github_app_webhook_secret` (sensitive, generated separately in S01 — not required for S02 paths).
- The slice is exercised end-to-end by `backend/tests/integration/test_m004_s02_github_install_e2e.py` (e2e marker, ~23s) — that is the authoritative UAT script. The numbered cases below mirror its scenarios so they can be re-driven by hand or in CI.

## Scenarios

### Case 1 — Install URL + state JWT shape (Scenario A)

1. As a system_admin, signup and seed all four github_app_* settings via PUT /api/v1/admin/settings/{key}.
2. Signup as a team admin and ensure a team exists.
3. GET /api/v1/teams/{team_id}/github/install-url with the team-admin cookie.
   - **Expected:** 200 with `{install_url, state, expires_at}`. `install_url` starts with `https://github.com/apps/<client_id>/installations/new?state=` (or whatever `GITHUB_APP_INSTALL_URL_BASE` is overridden to). `state` is a JWT.
4. Decode `state` against `SECRET_KEY` with `algorithms=['HS256']`, `audience='github-install'`.
   - **Expected:** payload contains `team_id` matching the path team, `iss='perpetuity-install'`, `jti` (16-char urlsafe), and `exp` ~10 minutes in the future.
5. Backend logs INFO `github_install_url_issued team_id=<uuid> actor_id=<uuid> state_jti=<first8>`. The full JWT MUST NOT appear in any log line.

**Edge case 1a — client_id missing:** delete `github_app_client_id` and re-issue the call. **Expected:** 404 `{detail:'github_app_not_configured'}`.

**Edge case 1b — non-admin caller:** authenticate as a plain team member and call the same URL. **Expected:** 403.

### Case 2 — Install-callback round-trip (Scenario B)

1. With the state JWT from Case 1, POST /api/v1/github/install-callback `{installation_id: 42, setup_action: 'install', state: <jwt>}` (NO auth cookie — public endpoint).
2. The endpoint calls orchestrator GET /v1/installations/42/lookup with `X-Orchestrator-Key`. In e2e, the mock-github sidecar returns `{account: {login: 'test-org', type: 'Organization'}, id: 42}`.
   - **Expected:** 200 with `GitHubAppInstallationPublic` carrying `installation_id=42`, `account_login='test-org'`, `account_type='Organization'`, `team_id=<the team>`.
3. GET /api/v1/teams/{team_id}/github/installations as team admin → 200 envelope `{data: [...], count: 1}` containing the row.
4. Backend logs INFO `github_install_callback_accepted team_id=<uuid> installation_id=42 account_login=test-org account_type=Organization state_jti=<first8>`.

### Case 3 — Idempotent duplicate install-callback (Scenario C)

1. Mint a fresh state JWT for the same team and POST install-callback again with the same `installation_id=42`.
   - **Expected:** 200; list endpoint still shows exactly 1 row (UPSERT keyed on installation_id).
2. If the state's team_id differs from the existing row's team, the new team wins and a WARNING `github_install_callback_team_reassigned` log line is emitted.

### Case 4 — Installation token mint + Redis cache (Scenario D)

1. Hit ephemeral-orchestrator GET /v1/installations/42/token with `X-Orchestrator-Key` (in e2e via `docker exec <eph_name> python3 -c '<urllib probe>'`).
   - **Expected (first call):** 200 `{token: <fixed_mock_token>, source: 'mint', expires_at: <iso8601>}`. Orchestrator logs INFO `installation_token_minted installation_id=42 token_prefix=<first4>...`.
2. Hit it again within the same test run.
   - **Expected (second call):** 200 same token, `source: 'cache'`. Orchestrator logs INFO `installation_token_cache_hit installation_id=42 token_prefix=<first4>...`.
3. `docker exec perpetuity-redis-1 redis-cli -a <pw> KEYS 'gh:installtok:*'` → exactly 1 match: `gh:installtok:42`.
4. `docker exec perpetuity-redis-1 redis-cli -a <pw> TTL 'gh:installtok:42'` → integer in `(1, 3001]` (50-minute TTL, accounting for elapsed test time).

### Case 5 — Expired state token (Scenario E)

1. Mint a fresh state JWT in-test with `exp=now-60` against `SECRET_KEY`.
2. POST install-callback with that state.
   - **Expected:** 400 `{detail:'install_state_expired'}`. Backend logs WARNING `github_install_callback_state_invalid reason=expired presented_jti=<first8>`.

### Case 6 — Decrypt-failure surfaces 503 over HTTP (Scenario F — closes S01 known-limitation)

1. Corrupt the encrypted private key: `psql -c "UPDATE system_settings SET value_encrypted = E'\\xdeadbeef' WHERE key='github_app_private_key';"` (note: even hex digit count required — `E'\\x00bad'` is rejected; see MEM259).
2. Flush the install-token cache: `redis-cli -a <pw> DEL 'gh:installtok:42'`.
3. Hit ephemeral-orchestrator GET /v1/installations/42/token (via docker exec urllib probe).
   - **Expected:** 503 `{detail:'system_settings_decrypt_failed', key:'github_app_private_key'}`. Orchestrator logs ERROR `system_settings_decrypt_failed key=github_app_private_key`.

### Case 7 — Public-callback bypasses auth (negative auth test)

1. POST install-callback with NO cookies set and a valid state JWT.
   - **Expected:** 200 (public endpoint — state JWT IS the auth carrier). Verifies that no FastAPI auth dependency is mounted on this route.

### Case 8 — DELETE installation (no cross-team enumeration)

1. Setup: alice's team has installation `<row_id_a>` and bob's team has installation `<row_id_b>`.
2. DELETE /api/v1/teams/<bob_team>/github/installations/<row_id_a> as bob (bob is admin of his team but the row belongs to alice's team).
   - **Expected:** 404 with the same body shape returned for a row that simply does not exist. No information leaked about whether <row_id_a> exists.
3. DELETE /api/v1/teams/<alice_team>/github/installations/<row_id_a> as alice → 200; subsequent list shows the row removed.
4. Backend logs INFO `github_installation_deleted actor_id=<uuid> team_id=<uuid> installation_id=<id>` on success.

### Case 9 — Orchestrator lookup failure modes (negative integration tests)

For each row of the failure-modes table the install-callback MUST NOT persist:

| Orchestrator response | Expected callback response |
|-----------------------|----------------------------|
| 503                   | 502 `{detail:'github_lookup_failed', reason:'503'}` |
| Connect/read timeout  | 502 `{detail:'github_lookup_failed', reason:'timeout'}` |
| Non-JSON body         | 502 `{detail:'github_lookup_failed', reason:'malformed_lookup_response'}` |
| Missing account_login/type keys | 502 `{detail:'github_lookup_failed', reason:'malformed_lookup_response'}` |
| Transport error       | 502 `{detail:'github_lookup_failed', reason:'transport'}` |

Each failure path leaves zero rows in `github_app_installations` (UPSERT only fires after lookup succeeds).

### Case 10 — Redis-unreachable on token GET (orchestrator degradation)

1. Stop redis (or simulate via a `_BrokenRedis` shim in unit tests).
2. Hit /v1/installations/42/token.
   - **Expected:** 200 with `source='mint'` (mint succeeds) but no caching side-effect. Orchestrator logs WARNING `redis_unreachable op=installation_token_get` (and `op=installation_token_setex` if the SETEX path also fails).

## Final Redaction Sweep (slice-wide assertion)

After running every scenario above, dump the joined log blob:

```
docker logs <sibling_backend_container_id>
docker logs <ephemeral_orchestrator_container_id>
```

(Mock-github sidecar logs are excluded — they contain the canned token by design.)

The blob MUST satisfy:

- Zero occurrences of the GitHub token-prefix family `gho_`, `ghu_`, `ghr_`, `github_pat_`.
- Zero occurrences of any token plaintext (use the test's `MOCK_FIXED_TOKEN` as a needle).
- `ghs_` permitted ONLY when it appears inside a `token_prefix=` substring (the canonical 4-char prefix shape used by `installation_token_minted` / `installation_token_cache_hit` log lines).
- Zero occurrences of the synthetic-PEM body sentinel (an arbitrary string spliced into the test key as a redaction probe).
- Zero occurrences of `-----BEGIN` (PEM header).

The blob MUST contain at least one occurrence of each:

- `github_install_url_issued`
- `github_install_callback_accepted`
- `installation_token_minted`
- `installation_token_cache_hit`
- `system_settings_decrypt_failed key=github_app_private_key`

## Pass/fail

PASS iff every numbered case above produces the expected response and log line, AND the final redaction sweep returns zero matches for the forbidden patterns AND ≥1 match for each required positive marker. The reference automated proof is `backend/tests/integration/test_m004_s02_github_install_e2e.py::test_m004_s02_github_install_e2e` — a green run of that single test is sufficient to call the slice UAT'd.
