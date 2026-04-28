---
id: T05
parent: S01
milestone: M005-sqm8et
key_files:
  - backend/tests/integration/test_m005_s01_team_secrets_e2e.py
  - backend/tests/integration/conftest.py
  - backend/app/api/routes/team_secrets.py
  - scripts/redaction-sweep.sh
key_decisions:
  - Test-only decrypt endpoint registered conditionally in routes/team_secrets.py only when settings.ENVIRONMENT == 'local', with include_in_schema=False AND a system_admin role gate. Triple gating (env + schema-hide + role) keeps the surface dead in production and harmless even if any single gate ever opens, while still letting the e2e drive get_team_secret round-trip and tamper-detect 503 against a real HTTP boundary.
  - Made the integration conftest honor a POSTGRES_DB env override (defaults to 'app') so operators can route the ephemeral backend at a clean perpetuity_app DB when MEM348's z2y/z3b CRM-schema contamination of the shared 'app' DB blocks alembic prestart. Unblocks every M002+ e2e while preserving the default behaviour for clean environments.
  - Bad-prefix probe uses an `xai-`-shaped value rather than just stripping the `sk-ant-` prefix — a Grok-shape body unambiguously fails the validator's `bad_prefix` check and is itself a bearer-key shape the redaction sweep would catch if it leaked, giving a stronger negative-test guarantee.
duration: 
verification_result: passed
completed_at: 2026-04-28T22:24:47.237Z
blocker_discovered: false
---

# T05: Added M005/S01 e2e covering all 8 team_secrets cases (set/replace/delete/role-gate/validator/round-trip/tamper-503/redaction), extended redaction-sweep.sh with sk-ant-/sk- patterns, and added a local-only system_admin-gated test-decrypt endpoint to drive the tamper-detect 503 path.

**Added M005/S01 e2e covering all 8 team_secrets cases (set/replace/delete/role-gate/validator/round-trip/tamper-503/redaction), extended redaction-sweep.sh with sk-ant-/sk- patterns, and added a local-only system_admin-gated test-decrypt endpoint to drive the tamper-detect 503 path.**

## What Happened

Implemented the slice's closing e2e test against the live compose stack (sibling backend container fixture in `tests/integration/conftest.py`) covering every case the slice plan locks: (a) admin pastes Claude+OpenAI keys, has_value flips on subsequent GETs; (b) replace PUT bumps updated_at; (c) DELETE clears presence and 404s on subsequent GET-single; (d) non-admin member PUT yields 403 `team_admin_required`; (e) bad-prefix value yields 400 `invalid_value_shape` with `hint=bad_prefix` and never echoes the offending value; (f) round-trip via the new local-only `_test_decrypt` endpoint exercises `get_team_secret` and returns the original plaintext; (g) corrupting the row's `value_encrypted` via psql then calling the same endpoint yields 503 `team_secret_decrypt_failed` plus the locked ERROR log line `team_secret_decrypt_failed team_id=<...> key=claude_api_key`; (h) final regex sweep over `docker logs <backend>` finds zero `sk-ant-` matches and zero bearer-shape `sk-` matches, plus the slice's INFO/ERROR taxonomy markers (`team_secret_set`, `team_secret_deleted`, `team_secret_decrypt_failed`) all present.

The test endpoint (`GET /api/v1/teams/{team_id}/secrets/{key}/_test_decrypt`) is registered conditionally in `routes/team_secrets.py` only when `settings.ENVIRONMENT == "local"` (mirrors `routes/private.py`'s production-exclusion pattern), with `include_in_schema=False` so it's invisible in the production OpenAPI tree, AND requires `current_user.role == UserRole.system_admin`. Triple gating ensures a team admin cannot use the surface to siphon their own team's plaintext, and the surface is dead in any non-local deploy. Captured as MEM414.

Extended `scripts/redaction-sweep.sh` with two new source-file checks (Anthropic `sk-ant-`-prefixed bearer keys and OpenAI `sk-...{20,}`-prefixed bearer keys, both gated to logger.*/console.* call sites following MEM400's pattern). Sweep stays clean against current source.

Hit two infrastructure issues during verification. First: the shared compose `app` database was contaminated by M041's CRM schema (MEM348 — alembic version `z3b_m041_ghl_push_concurrency_index`), causing prestart to fail with "Can't locate revision". Fixed by making the integration conftest honor a `POSTGRES_DB` env override (defaults to `app`) so operators can route the ephemeral backend at a clean `perpetuity_app` DB on the same `perpetuity-db-1` container; the test mirrors the override via `_PG_DB = os.environ.get("POSTGRES_DB", "app")` for its psql probes. This unblocks every existing M002+ e2e too. Captured as MEM415. Second: my initial `INSERT INTO team_member (user_id, team_id, role) ...` failed silently — the table's PK is a generated UUID `id` column, not the `(user_id, team_id)` composite (that's a UNIQUE constraint only). The silent failure landed the test on a 403 `not_team_member` instead of the intended `team_admin_required`. Fixed by generating the uuid client-side and including `created_at` (no DB default). Captured as MEM416.

Skip-guard fixture (MEM162 pattern) probes `backend:latest` for the `s09_team_secrets.py` revision file and skips with the canonical `docker compose build backend` hint when missing. Verified the skip path fires correctly against a stale image, then rebuilt and confirmed the test runs green in 7.98s — well under the slice's ≤30s budget.

## Verification

Slice verification command (`cd backend && uv run pytest tests/integration/test_m005_s01_team_secrets_e2e.py -v && bash scripts/redaction-sweep.sh`) ran end-to-end clean against `POSTGRES_DB=perpetuity_app`. The e2e exercised all 8 cases (a–h) and finished in 7.98s. The redaction sweep emitted 7 PASS lines including the two new ones for `sk-ant-` and `sk-`. Pre-existing team_secrets unit + route tests (44 cases across `test_team_secrets_helpers.py` and `test_team_secrets_routes.py`) all still pass — the new local-only endpoint adds zero footprint to the non-local route table. Skip-guard fixture verified to fire correctly against a stale `backend:latest` (s06-baked) image before rebuilding to s09.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app uv run pytest -m e2e tests/integration/test_m005_s01_team_secrets_e2e.py -v` | 0 | pass | 7980ms |
| 2 | `bash scripts/redaction-sweep.sh` | 0 | pass | 800ms |
| 3 | `cd backend && uv run pytest tests/api/test_team_secrets_helpers.py tests/api/test_team_secrets_routes.py -q` | 0 | pass | 1520ms |
| 4 | `cd backend && uv run pytest -m e2e tests/integration/test_m005_s01_team_secrets_e2e.py -v (against pre-rebuild backend:latest with s06)` | 0 | pass-skipped-as-designed | 1450ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `backend/tests/integration/test_m005_s01_team_secrets_e2e.py`
- `backend/tests/integration/conftest.py`
- `backend/app/api/routes/team_secrets.py`
- `scripts/redaction-sweep.sh`
