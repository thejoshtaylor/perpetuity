---
id: S01
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
completed_at: 2026-05-12T21:16:23.844Z
blocker_discovered: false
---

# S01: Encrypted `github_user_oauth_tokens` table + model

**Landed the encrypted github_user_oauth_tokens table, GitHubUserOAuthToken SQLModel with Fernet encrypt/decrypt helpers, and comprehensive migration test with cascade semantics and downgrade round-trip.**

## What Happened

S01 delivers the complete data layer contract for storing GitHub user OAuth tokens with Fernet encryption. The milestone-critical foundation proves: (1) alembic migration s17 creates the table at the correct revision with all 10 columns, primary key on user_id with CASCADE delete, foreign key to users(id), server defaults for created_at/updated_at; (2) GitHubUserOAuthToken SQLModel round-trips plaintext tokens through encrypt_user_token/decrypt_user_token with zero plaintext leakage and strong error semantics; (3) migration up/down cycle is idempotent and preserves schema bit-for-bit; (4) DDL tests run without the session-lock hang that plagued earlier migrations — the fixture copy from s09 (_release_autouse_db_session / _restore_head_after) solved the session holding AccessShareLock issue.

All 3 tasks completed and verified: T01 covers 8 migration test cases (column shape, PK, FK CASCADE, duplicate PK violation, cascade-on-user-delete, server defaults, downgrade, round-trip idempotence); T02 delivers the SQLModel class with all 10 fields + the GitHubUserOAuthTokenStatus DTO that redacts both *_encrypted columns; T03 implements the crypto boundary (encrypt_user_token, decrypt_user_token, GitHubUserTokenDecryptError) with 6 unit tests proving round-trip correctness, plaintext non-leakage, error class distinctness, and model registration. No production code path reads/writes the table yet — S02–S07 depend on this contract.

The slice closes with zero observability gaps, zero known issues, and zero deviations.

## Verification

**T01 Migration Tests:** `cd backend && uv run pytest tests/migrations/test_s17_github_user_oauth_tokens_migration.py -v` → 8/8 passed in 0.52s. Verifies: (1) table exists with correct columns (user_id UUID PK, installation_id BIGINT, github_user_id BIGINT, access_token_encrypted BYTEA, refresh_token_encrypted BYTEA, access_token_expires_at TIMESTAMPTZ, refresh_token_expires_at TIMESTAMPTZ, scopes TEXT, created_at TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now()); (2) PK is user_id; (3) FK on user_id with CASCADE delete; (4) duplicate user_id violation; (5) cascade delete on user removal; (6) server defaults fire; (7) downgrade drops table; (8) upgrade+downgrade round-trip preserves schema.

**T02 SQLModel & DTO:** `uv run python -c "from app.models import GitHubUserOAuthToken, GitHubUserOAuthTokenStatus; ..."` confirms tablename="github_user_oauth_tokens" and GitHubUserOAuthTokenStatus.model_fields excludes both *_encrypted columns (verified: intersection with {'access_token_encrypted','refresh_token_encrypted'} is empty set).

**T03 Crypto Unit Tests:** `cd backend && uv run pytest tests/unit/test_github_user_tokens_crypto.py -v` → 6/6 passed in 0.11s. Verifies: (1) round-trip returns exact plaintext; (2) ciphertext contains no plaintext bytes; (3) bad ciphertext raises GitHubUserTokenDecryptError; (4) error class is distinct from SystemSettingDecryptError; (5) GitHubUserTokenDecryptError carries optional user_id; (6) GitHubUserOAuthToken registered in SQLModel metadata.

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
