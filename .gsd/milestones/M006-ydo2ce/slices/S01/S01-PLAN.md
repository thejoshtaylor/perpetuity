# S01: Encrypted `github_user_oauth_tokens` table + model

**Goal:** Land the `github_user_oauth_tokens` table, the `GitHubUserOAuthToken` SQLModel with Fernet encrypt/decrypt helpers, and the migration test — using the existing `SYSTEM_SETTINGS_ENCRYPTION_KEY` Fernet cipher from `backend/app/core/encryption.py`. No behavior change visible to users yet; S02–S07 read and write through this contract.
**Demo:** `alembic upgrade head` against a fresh DB creates the table at revision `s17_github_user_oauth_tokens`. A unit test round-trips a plaintext access token through `GitHubUserOAuthToken.set_access_token()` / `get_access_token()` and asserts (a) the row's `access_token_encrypted` BYTEA does not contain the plaintext, and (b) decryption returns the exact input. The migration test exercises upgrade-from-s16 + downgrade round-trip without the autouse-`db`-session DDL hang.

## Must-Haves

- alembic upgrade applies the table at s17_github_user_oauth_tokens revision; SQLModel round-trips through Fernet encrypt/decrypt; migration test runs without DDL hang and covers PK/FK CASCADE/server-defaults/downgrade-roundtrip; no production code path reads/writes the table yet.

## Proof Level

- This slice proves: Contract — Fernet round-trip works; migration applies and rolls back; schema constraints (PK, FK CASCADE) hold at the storage layer. No real runtime or UAT required.

## Integration Closure

Upstream surfaces consumed: M004/S01 encryption helpers; existing user table for FK target. New wiring: the new SQLModel class and the new github_user_tokens module — both imported at startup via app/models.py's normal import chain. The migration is the only runtime change visible after deploy.

## Verification

- No new production code path. GitHubUserTokenDecryptError is a distinct exception class from SystemSettingDecryptError so a future ERROR log line can pinpoint which table's ciphertext failed to decrypt. Plaintext access/refresh tokens MUST NOT appear in any log line — only token prefixes and the user_id UUID are loggable in later slices.

## Tasks

- [x] **T01: Alembic revision `s17_github_user_oauth_tokens` + migration test** `est:2h`
  Lands the table that S02–S07 will read/write; without it nothing else in the milestone can proceed. Copy structure of backend/app/alembic/versions/s09_team_secrets.py for the upgrade/downgrade pair. Use sa.Uuid() for user_id and sa.BigInteger() for both installation_id and github_user_id. Use sa.LargeBinary() for both ciphertext columns. Use sa.DateTime(timezone=True) with server_default=sa.text("NOW()"). FK constraint on user_id with ondelete=CASCADE inline on op.create_table (match s09 pattern). Migration test copies fixtures _release_autouse_db_session and _restore_head_after verbatim from test_s09_team_secrets_migration.py. At least 7 distinct test functions: column shape, PK, FK CASCADE, duplicate-user_id violation, cascade-on-user-delete, server-defaults, downgrade-roundtrip.
  - Files: `backend/app/alembic/versions/s17_github_user_oauth_tokens.py`, `backend/tests/migrations/test_s17_github_user_oauth_tokens_migration.py`
  - Verify: cd backend && uv run pytest tests/migrations/test_s17_github_user_oauth_tokens_migration.py -v

- [x] **T02: SQLModel `GitHubUserOAuthToken` + `GitHubUserOAuthTokenStatus` DTO** `est:45m`
  Production code reads the row through SQLModel/SQLAlchemy in S02 and S03; raw SQL would force per-call column lists that drift from the schema. Add SQLModel class with __tablename__ = github_user_oauth_tokens, all 10 fields matching the migration exactly. user_id is PK with foreign_key=user.id and ondelete=CASCADE. Use default_factory=lambda: datetime.now(timezone.utc) for created_at/updated_at. Add GitHubUserOAuthTokenStatus Pydantic model that explicitly omits both *_encrypted columns (copy SystemSettingPublic pattern). No 'public with plaintext' DTO.
  - Files: `backend/app/models.py`
  - Verify: cd backend && uv run python -c "from app.models import GitHubUserOAuthToken, GitHubUserOAuthTokenStatus; print(GitHubUserOAuthToken.__tablename__); print(set(GitHubUserOAuthTokenStatus.model_fields.keys()) & {'access_token_encrypted','refresh_token_encrypted'})"

- [ ] **T03: `app/core/github_user_tokens.py` crypto helpers + sentinel exception + unit tests** `est:1h`
  Lock the encrypt/decrypt boundary that S02 (persist) and S03 (refresh) will both call through; the sentinel exception lets the ERROR log in S03 distinguish a user-token decrypt failure from a system-settings decrypt failure. Create module with class GitHubUserTokenDecryptError(Exception) (constructor takes optional user_id: uuid.UUID | None). Export encrypt_user_token(plain: str) -> bytes that calls encrypt_setting. Export decrypt_user_token(cipher: bytes) -> str that calls decrypt_setting and catches SystemSettingDecryptError to re-raise as GitHubUserTokenDecryptError. Unit test covers the 5 cases from must-have (6) plus test_model_registered.
  - Files: `backend/app/core/github_user_tokens.py`, `backend/tests/unit/test_github_user_tokens_crypto.py`
  - Verify: cd backend && uv run pytest tests/unit/test_github_user_tokens_crypto.py -v

## Files Likely Touched

- backend/app/alembic/versions/s17_github_user_oauth_tokens.py
- backend/tests/migrations/test_s17_github_user_oauth_tokens_migration.py
- backend/app/models.py
- backend/app/core/github_user_tokens.py
- backend/tests/unit/test_github_user_tokens_crypto.py
