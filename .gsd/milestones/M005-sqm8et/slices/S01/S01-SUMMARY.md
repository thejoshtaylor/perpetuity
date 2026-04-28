---
id: S01
parent: M005-sqm8et
milestone: M005-sqm8et
provides:
  - ["team_secrets table with composite PK (team_id, key) and FK CASCADE","get_team_secret(session, team_id, key) -> str helper raising team-scoped MissingTeamSecretError / TeamSecretDecryptError","set_team_secret / delete_team_secret / list_team_secret_status helpers","TeamSecret SQLModel + TeamSecretPublic / TeamSecretStatus / TeamSecretPut DTOs","Per-key validator registry with claude_api_key and openai_api_key registered","PUT/GET-single/GET-list/DELETE /api/v1/teams/{team_id}/secrets/{key} routes","Global TeamSecretDecryptError → 503 and MissingTeamSecretError → 404 exception handlers","INFO/ERROR log taxonomy (team_secret_set, team_secret_deleted, team_secret_decrypt_failed)","Frontend TeamSecretsPanel + paste-once dialog + role-aware UI","Extended scripts/redaction-sweep.sh covering sk-ant- and sk- prefixes","Local-only system_admin-gated GET /_test_decrypt endpoint for e2e helper round-trip","integration conftest POSTGRES_DB env override (MEM420)"]
requires:
  []
affects:
  - ["S02 — dashboard 'Run Claude' / 'Run Codex' buttons read claude_api_key / openai_api_key via get_team_secret; missing-key surfaces as step failure with error_class='missing_team_secret'.","S03 — workflow Celery executors for `claude` and `codex` step types read team API keys via the same helper.","S04 — webhook-dispatched workflows targeting team-mirror containers consume team secrets identically.","S05 — run history UI must keep value redaction guarantees end-to-end; redaction sweep extension stays in force.","S06 — final integration acceptance against real Anthropic + OpenAI exercises the round-trip from this storage boundary."]
key_files:
  - ["backend/app/alembic/versions/s09_team_secrets.py", "backend/app/api/team_secrets_registry.py", "backend/app/api/team_secrets.py", "backend/app/api/routes/team_secrets.py", "backend/app/main.py", "frontend/src/components/team/TeamSecretsPanel.tsx", "backend/tests/integration/test_m005_s01_team_secrets_e2e.py", "scripts/redaction-sweep.sh"]
key_decisions:
  - ["Composite PK (team_id, key) with FK CASCADE on team delete + value_encrypted NOT NULL — row absence is the canonical 'not set' state.","TeamSecretPublic and TeamSecretStatus structurally OMIT value_encrypted (not just exclude=True) so model_validate cannot serialize ciphertext.","Per-key validator registry mirrors M004 system_settings _VALIDATORS shape; lookup() raises typed UnregisteredTeamSecretKeyError; validators raise InvalidTeamSecretValueError carrying short shape-only reasons (bad_prefix/too_short/must_be_string) never the plaintext.","Team-scoped TeamSecretDecryptError and MissingTeamSecretError distinct from M004's SystemSettingDecryptError so dashboards/log searches can disambiguate scope; ERROR log emission lives in the helper not the route so a caller that catches+retries cannot silently lose the corruption signal.","get_team_secret catches BOTH SystemSettingDecryptError AND raw cryptography.fernet.InvalidToken — defense-in-depth against a future drift where the encryption module's contract changes.","Inlined _assert_team_member/_assert_team_admin guards in routes/team_secrets.py rather than reusing team_access.assert_caller_is_team_admin (whose 403 detail bakes 'Only team admins can invite') because the slice plan locks `team_admin_required` / `not_team_member` discriminators (MEM411).","PUT response uses TeamSecretStatus shape so the frontend's React Query cache update can use the PUT response verbatim — no follow-up GET needed.","Local-only test endpoint /_test_decrypt triple-gated: settings.ENVIRONMENT=='local' + include_in_schema=False + system_admin role — keeps the surface dead in production and harmless even if any single gate ever opens (MEM419).","Frontend extractDetail unwraps BOTH flat `detail: string` AND nested `detail: {detail, hint, key}` so FastAPI HTTPException(detail=<dict>) discriminator reaches the operator (MEM412).","Integration conftest honors POSTGRES_DB env override defaulting to 'app' so operators can route the ephemeral backend at a clean perpetuity_app DB when MEM348's CRM-schema contamination of shared 'app' blocks alembic prestart (MEM420)."]
patterns_established:
  - ["Per-team encrypted secret storage: (team_id, key) composite PK + Fernet at-rest + decrypt-at-call-site + global handler for decrypt-failure 503 + global handler for missing-key 404. Future M005+ slices that add registered keys (e.g. github_pat in M006) extend the registry and ship with the same shape.","Validator registry pattern with shape-only failure tokens (bad_prefix/too_short/must_be_string) carried via a typed exception's .reason attribute — API layer maps directly to {detail: invalid_value_shape, key, hint} without re-parsing or risk of leaking plaintext.","Local-only test endpoint triple-gating (ENVIRONMENT==local + include_in_schema=False + system_admin role) for e2e tamper-detect paths — applicable to any future endpoint that needs to drive an internal helper from outside the process boundary.","Frontend nested-detail unwrap for FastAPI HTTPException(detail=<dict>) discriminators."]
observability_surfaces:
  - ["INFO log: team_secret_set team_id=<uuid> key=<key> (PUT success, never the value)","INFO log: team_secret_deleted team_id=<uuid> key=<key> (DELETE success)","ERROR log: team_secret_decrypt_failed team_id=<uuid> key=<key> (decrypt failure, never the value or value_prefix)","HTTP 503 response shape: {detail: 'team_secret_decrypt_failed', key} via global handler","HTTP 404 response shape: {detail: 'team_secret_not_set', key} via global handler","HTTP 400 response shape: {detail: 'invalid_value_shape', key, hint} for validator failures","HTTP 400 response shape: {detail: 'unregistered_key', key} for registry miss","HTTP 403 response shapes: {detail: 'team_admin_required'} (write paths), {detail: 'not_team_member'} (read paths)","Redaction sweep: scripts/redaction-sweep.sh covers sk-ant-/sk- prefixes in addition to existing GitHub/VAPID/multipart/push-endpoint patterns; all M005 e2e logs from S01 onward must pass"]
drill_down_paths:
  []
duration: ""
verification_result: passed
completed_at: 2026-04-28T22:30:59.155Z
blocker_discovered: false
---

# S01: Per-team AI credentials at rest

**Shipped team_secrets table + Fernet at-rest encryption + team-admin-only API + paste-once frontend panel for claude_api_key and openai_api_key, locking the credential-storage boundary that S02–S06 read from.**

## What Happened

S01 lands the credential-storage spine M005 needs: a per-team, per-key encrypted secrets store that downstream slices (S02 dashboard buttons, S03 workflow engine, S04 webhook dispatch) read from via a single locked helper.

T01 created alembic revision `s09_team_secrets` adding the `team_secrets(team_id, key, value_encrypted, has_value, sensitive, created_at, updated_at)` table with composite PK `(team_id, key)` and FK CASCADE on team delete (so orphan ciphertext can never linger — it would be unrecoverable anyway). `value_encrypted BYTEA NOT NULL` makes row-absence the canonical 'not set' state. Added `TeamSecret` SQLModel and three Pydantic DTOs (`TeamSecretPublic`, `TeamSecretStatus`, `TeamSecretPut`) that structurally OMIT `value_encrypted` so model_validate cannot accidentally serialize ciphertext. Migration upgrade-from-s08 + downgrade round-trip test green.

T02 added `backend/app/api/team_secrets_registry.py` and `backend/app/api/team_secrets.py`. The registry mirrors M004's `system_settings._VALIDATORS` shape with two registered keys: `claude_api_key` (sk-ant- prefix, length ≥ 40) and `openai_api_key` (sk- prefix, length ≥ 40), both `sensitive=True`. `lookup(key)` raises typed `UnregisteredTeamSecretKeyError` (KeyError subclass) instead of returning None — call sites cannot accidentally treat 'no spec' as 'skip validation'. Validators raise `InvalidTeamSecretValueError(key, reason)` with short shape-only tokens (`bad_prefix`/`too_short`/`must_be_string`); the plaintext never appears in the message. Service helpers: `set_team_secret` (single INSERT … ON CONFLICT (team_id, key) DO UPDATE — composite PK matches conflict target so upsert is one round-trip with no read-then-write race window), `get_team_secret` (decrypts at call site, catches BOTH `SystemSettingDecryptError` AND raw `cryptography.fernet.InvalidToken` for defense-in-depth, raises team-scoped `TeamSecretDecryptError` and `MissingTeamSecretError`), `delete_team_secret`, `list_team_secret_status`. ERROR log emission for decrypt failure lives in the helper (not the route) so a caller that catches and retries cannot silently lose the corruption signal.

T03 added the FastAPI router at `backend/app/api/routes/team_secrets.py` exposing `PUT/GET-single/GET-list/DELETE /api/v1/teams/{team_id}/secrets/{key}`. Inlined `_assert_team_member` / `_assert_team_admin` guards (MEM411) rather than reusing `team_access.assert_caller_is_team_admin` because the shared helper bakes 'Only team admins can invite' into its 403 detail string and the slice plan locks `team_admin_required` / `not_team_member` discriminators for the frontend. Added two global exception handlers in `app/main.py` mirroring M004's `SystemSettingDecryptError` pattern: `TeamSecretDecryptError` → 503 `team_secret_decrypt_failed`; `MissingTeamSecretError` → 404 `team_secret_not_set`. PUT response uses `TeamSecretStatus` (same shape as GET-single) so the frontend's React Query cache update can use the PUT response verbatim without a follow-up GET. INFO logs `team_secret_set` and `team_secret_deleted` emitted with team_id + key only — never the value. 27 route tests including three observability assertions guarding against future refactors that might move `logger.info` above the validator boundary.

T04 wired the frontend panel. Added `frontend/src/components/team/TeamSecretsPanel.tsx` rendered inside `frontend/src/routes/_layout/teams_.$teamId.tsx`, a paste-once `PasteSecretDialog` using PasswordInput (eye toggle) for the value field, and `frontend/src/api/teamSecrets.ts` for the React Query mutations. Panel always renders both registered keys (placeholder fallback if GET errors) so operators always see panel shape and error together. `extractDetail` unwraps BOTH flat `detail: string` and nested `detail: {detail, hint, key}` shapes (MEM412) so backend's `invalid_value_shape: bad_prefix` actually reaches the operator. Non-admin sees read-only badges; admin sees Replace + Delete buttons. 5 Playwright cases covering admin sees buttons, non-admin sees read-only, paste-once submit + close + refresh, validator-error display.

T05 closed with the slice e2e and operational extensions. `backend/tests/integration/test_m005_s01_team_secrets_e2e.py` exercises all 8 cases the plan locks against the live compose stack: paste, GET shows has_value=true, replace bumps updated_at, DELETE clears, non-admin → 403 `team_admin_required`, bad-prefix → 400 `invalid_value_shape` with `hint=bad_prefix`, round-trip via the new local-only `_test_decrypt` endpoint, tamper-detect 503 `team_secret_decrypt_failed`, and final regex sweep over `docker logs <backend>` finds zero `sk-ant-` matches and zero bearer-shape `sk-` matches plus the slice's INFO/ERROR log keys all present. The test endpoint `GET /api/v1/teams/{team_id}/secrets/{key}/_test_decrypt` (MEM419) is registered conditionally only when `settings.ENVIRONMENT == "local"`, with `include_in_schema=False`, AND requires `current_user.role == UserRole.system_admin` — triple-gating ensures even a team admin cannot use it to siphon their own team's plaintext, and the surface is dead in any non-local deploy. Extended `scripts/redaction-sweep.sh` with `sk-ant-` and `sk-` checks (gated to logger.*/console.* call sites following MEM400's pattern). Skip-guard fixture (MEM162) probes `backend:latest` for `s09_team_secrets` and skips with the canonical rebuild hint when stale.

Two infrastructure issues surfaced during T05 verification and were resolved durably. First, the shared compose `app` database is contaminated with an unrelated CRM schema (alembic version `z3b_m041_ghl_push_concurrency_index` not in this codebase) — MEM348 redux. Resolved by making the integration conftest honor a `POSTGRES_DB` env override (defaults to `app`) so the ephemeral backend container points at the clean `perpetuity_app` DB that already exists on `perpetuity-db-1`. Captured as MEM420; this unblocks every existing M002+ e2e too. Second, my initial team_member seeding insert failed silently because the table's PK is a generated UUID `id` column (not the `(user_id, team_id)` composite — that's a UNIQUE constraint only). Captured as MEM421.

This slice closes the credential-storage boundary that S02–S06 read from. Every downstream slice that calls `claude` or `codex` reads via `get_team_secret(session, team_id, key)`; missing-key surfaces as `error_class='missing_team_secret'`; decrypt-corruption surfaces as 503. The validator registry shape is locked here so M006+ slices can extend (e.g. `github_pat`) without rewriting.

## Verification

**Slice verification command — both clean.**

1. `cd backend && POSTGRES_DB=perpetuity_app uv run pytest tests/integration/test_m005_s01_team_secrets_e2e.py -v` → 1 passed in 7.97s. The single test exercises all 8 plan-locked cases: (a) paste-once + GET has_value=true, (b) replace bumps updated_at, (c) DELETE clears + GET-single 404, (d) non-admin PUT → 403 `team_admin_required`, (e) bad-prefix value → 400 `invalid_value_shape` hint=bad_prefix, (f) get_team_secret round-trip via local-only system_admin-gated `_test_decrypt`, (g) corrupt value_encrypted via psql + call test endpoint → 503 `team_secret_decrypt_failed` plus the locked ERROR log line, (h) final docker-logs regex sweep finds zero sk-ant-/sk- matches plus all three INFO/ERROR markers (`team_secret_set`, `team_secret_deleted`, `team_secret_decrypt_failed`) present.

2. `bash scripts/redaction-sweep.sh` → 7 PASS lines including the two new ones for `sk-ant-` (Anthropic) and `sk-` (OpenAI). No matches in source.

**Note on test command path.** The task plan's verify command read `cd backend && uv run pytest tests/integration/test_m005_s01_team_secrets_e2e.py -v`; the harness ran it as two separate commands and the pytest invocation hit "file or directory not found" because it was executed from repo root. Re-run with explicit `cd /Users/josh/code/perpetuity/backend && POSTGRES_DB=perpetuity_app uv run pytest tests/integration/test_m005_s01_team_secrets_e2e.py -v` succeeds.

**Per-task verification.** T01 migration round-trip (`tests/migrations/test_s09_team_secrets_migration.py`) green. T02 helper unit tests (17 cases, `tests/api/test_team_secrets_helpers.py`) green including decrypt-tamper path. T03 route tests (27 cases, `tests/api/test_team_secrets_routes.py`) green covering admin gate, validator failure modes, and value-redacted observability logs. T04 frontend Playwright (5 cases, `frontend/tests/components/TeamSecretsPanel.spec.ts`) green covering admin-buttons-render, non-admin-read-only, paste-once submit+close+refresh, validator-error display, and 503-error-card-with-empty-state.

**Non-regression.** Pre-existing `test_team_secrets_helpers.py` (17) and `test_team_secrets_routes.py` (27) all still pass after T05's local-only endpoint added — zero footprint on the non-local route table.

**Observability.** Slice INFO/ERROR taxonomy verified live: `team_secret_set` (PUT success), `team_secret_deleted` (DELETE success), `team_secret_decrypt_failed` (503 path) all emit with team_id + key and never the value or value-prefix. Distinct from M004's `system_settings_decrypt_failed` so dashboards can disambiguate scope. Redaction sweep gated to logger.*/console.* call sites (MEM400) so source-level regex sweep stays clean.

## Requirements Advanced

- R011 — S01 ships the per-team credential boundary that S04's webhook-triggered workflows consume — AI keys readable via get_team_secret are exactly the keys S04's webhook executors need.

## Requirements Validated

None.

## New Requirements Surfaced

None.

## Requirements Invalidated or Re-scoped

None.

## Operational Readiness

None.

## Deviations

"None. The slice plan was implemented as locked. Two verification-time infrastructure issues (MEM348-redux CRM contamination of shared 'app' DB; team_member PK gotcha) were resolved durably with conftest changes captured as MEM420 and a memory note for future test authors (MEM421) — neither changed the slice's user-facing contract."

## Known Limitations

"None affecting the slice's locked contract. Operational note: e2e must run with POSTGRES_DB=perpetuity_app while the shared 'app' DB on perpetuity-db-1 carries z2y/z3b CRM contamination (MEM420) — captured in conftest with default fallback to 'app' so clean environments work unchanged."

## Follow-ups

"None for this slice. S02 will start by writing its first executor against get_team_secret with `error_class='missing_team_secret'` for missing-key step failures — the contract is locked here."

## Files Created/Modified

- `backend/app/alembic/versions/s09_team_secrets.py` — New alembic revision adding team_secrets table with composite PK + FK CASCADE.
- `backend/app/models.py` — Added TeamSecret SQLModel + TeamSecretPublic / TeamSecretStatus / TeamSecretPut DTOs (value_encrypted structurally absent).
- `backend/app/api/team_secrets_registry.py` — New module: per-key validator registry with claude_api_key + openai_api_key.
- `backend/app/api/team_secrets.py` — New module: set/get/delete/list_status helpers + team-scoped exception types.
- `backend/app/api/routes/team_secrets.py` — New router: PUT/GET-single/GET-list/DELETE + local-only system_admin-gated _test_decrypt endpoint.
- `backend/app/api/main.py` — Registered team_secrets router.
- `backend/app/main.py` — Added two global exception handlers (TeamSecretDecryptError → 503, MissingTeamSecretError → 404).
- `backend/tests/migrations/test_s09_team_secrets_migration.py` — Migration upgrade-from-s08 + downgrade round-trip test.
- `backend/tests/api/test_team_secrets_helpers.py` — 17 unit tests for service helpers including decrypt-tamper path.
- `backend/tests/api/test_team_secrets_routes.py` — 27 route tests including admin-gate, validator failure modes, value-redacted observability assertions.
- `backend/tests/integration/test_m005_s01_team_secrets_e2e.py` — Slice e2e covering all 8 cases against live compose stack.
- `backend/tests/integration/conftest.py` — Added POSTGRES_DB env override (defaults to 'app') so e2e can route at clean perpetuity_app DB when MEM348 contaminates shared 'app'.
- `frontend/src/api/teamSecrets.ts` — React Query mutations + queries for team_secrets endpoints with nested-detail extractDetail.
- `frontend/src/components/team/TeamSecretsPanel.tsx` — Panel rendering both registered keys with role-aware Replace/Delete buttons.
- `frontend/src/components/team/PasteSecretDialog.tsx` — Paste-once dialog with PasswordInput + eye toggle.
- `frontend/src/routes/_layout/teams_.$teamId.tsx` — Wired TeamSecretsPanel into team detail route.
- `frontend/openapi.json` — Regenerated OpenAPI snapshot.
- `frontend/src/client/sdk.gen.ts` — Regenerated SDK with team_secrets endpoints.
- `frontend/src/client/types.gen.ts` — Regenerated types with team_secrets DTOs.
- `frontend/tests/components/TeamSecretsPanel.spec.ts` — 5 Playwright cases covering admin/non-admin/paste-once/validator-error/error-card.
- `scripts/redaction-sweep.sh` — Extended with sk-ant- (Anthropic) and sk- (OpenAI) bearer-key checks gated to logger.*/console.* call sites.
