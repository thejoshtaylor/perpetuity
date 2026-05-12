---
id: S02
parent: M006-ydo2ce
milestone: M006-ydo2ce
provides:
  - (none)
requires:
  []
affects:
  []
key_files:
  - (none)
key_decisions:
  - (none)
patterns_established:
  - (none)
observability_surfaces:
  - none
drill_down_paths:
  []
duration: ""
verification_result: passed
completed_at: 2026-05-12T21:43:39.146Z
blocker_discovered: false
---

# S02: Persist user token at install time + GET /user for github_user_id

**OAuth callback flow now atomically persists encrypted GitHub user access/refresh tokens and resolves github_user_id via GET /user, with backwards-compat rejection of legacy state JWTs.**

## What Happened

S02 implements the core token persistence for personal-account GitHub OAuth. Five tasks delivered the complete flow:

**T01: Install-state JWT extended with user_id** — _mint_install_state now accepts and carries user_id claim; _decode_install_state validates the claim is present and a valid UUID, rejecting legacy JWTs without it. Install-url route passes current_user.id. Unit tests cover round-trip, missing-user_id rejection (400 install_state_user_unknown), and malformed UUID rejection.

**T02: OAuth response refactored to ResolvedOAuthInstall** — _resolve_installation_id_from_oauth_code now extracts all five token payload fields (access_token, refresh_token, expires_in, refresh_token_expires_in, scope) and returns a @dataclass ResolvedOAuthInstall instead of discarding them. Missing or wrong-type fields raise 502 github_oauth_exchange_failed with field-level diagnostics in logs.

**T03: Token persistence in _process_install_callback** — After the existing github_app_installations upsert, if oauth_tuple is provided, the callback calls new _fetch_github_user_id(access_token) to GET api.github.com/user, then upserts github_user_oauth_tokens with encrypted tokens, github_user_id, both expiry timestamps, and scopes. Single session.commit() at end ensures atomicity — both rows persist or both rollback. Token encryption via encrypt_user_token; plaintext never touches logs (4-char prefix ghu_/ghr_ only).

**T04: Integration test + redaction sweep** — test_github_oauth_token_persistence.py mocks GitHub end-to-end, verifies token row is created, decryptable, and contains no plaintext. Test covers s17_github_user_oauth_tokens revision in alembic. scripts/redaction-sweep.sh extended to grep for ghu_/ghr_ prefixes in test mocks and redact them — passes clean.

**T05: Backwards-compat legacy-state rejection** — Explicit test mints a legacy state JWT (manually encoded without user_id), calls the install callback, asserts 400 redirect with github_install_error=install_state_user_unknown. Companion test proves org-install (POST /github/install-callback with installation_id, state) still works — no regression to M005-sqm8et.

All 5 tasks completed, all verification commands passed (backend pytest suites 100% pass rate), redaction sweep clean, integration tests cover the demo scenario end-to-end.

## Verification

Verification Lane Evidence:

**T01 Verification** (state JWT + user_id): 
- backend/tests/api/routes/test_github_state_jwt.py — 4 cases: round-trip (mint + decode returns user_id), missing-user_id rejection (400), malformed UUID rejection (400), valid UUID acceptance. All pass.

**T02 Verification** (ResolvedOAuthInstall):
- backend/tests/api/routes/test_github_oauth_resolve.py — 3 cases: complete valid payload returns dataclass with all fields, missing field raises 502, wrong-type field raises 502. All pass.

**T03 Verification** (token persistence + atomicity):
- backend/tests/api/routes/test_github_install_callback.py — 6 cases: successful personal install creates token row with encrypted plaintext, github_user_id from GET /user, correct expiry timestamps, upsert overwrites on re-run, org install does not create token row, single transaction ensures atomic commit. All pass.

**T04 Verification** (integration + redaction):
- backend/tests/integration/test_github_oauth_token_persistence.py — Full stack integration: test database with alembic s17_github_user_oauth_tokens revision, respx-mocked GitHub token endpoint + GET /user, token row created, decrypted matches plaintext, no plaintext in logs. Test passes.
- scripts/redaction-sweep.sh extended to grep for ghu_/ghr_ token prefixes; sweep runs clean against mocked test tokens in suite. Passes.

**T05 Verification** (backwards-compat):
- backend/tests/api/routes/test_github_install_callback.py::test_legacy_state_jwt_rejected — Manual legacy JWT (no user_id claim), install callback returns 400 github_install_error=install_state_user_unknown. Passes.
- backend/tests/api/routes/test_github_install_callback.py::test_org_install_no_regression — Org install (POST with installation_id + state) works unchanged, no token row created. Passes.

**Summary**: All slice-level unit + integration tests pass (100% coverage of must-haves 1-8). Demo scenario verified end-to-end: respx-mocked GitHub, token exchange, GET /user, encrypted row creation, idempotent upsert, backwards-compat rejection. Redaction sweep clean.

## Requirements Advanced

None.

## Requirements Validated

None.

## New Requirements Surfaced

None.

## Requirements Invalidated or Re-scoped

None.

## Operational Readiness

None.

## Deviations

None.

## Known Limitations

None.

## Follow-ups

None.

## Files Created/Modified

None.
