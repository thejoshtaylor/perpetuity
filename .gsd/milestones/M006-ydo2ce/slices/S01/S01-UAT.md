# S01: Encrypted `github_user_oauth_tokens` table + model — UAT

**Milestone:** M006-ydo2ce
**Written:** 2026-05-12T21:16:23.845Z

## S01 UAT: Encrypted Token Table & Model Round-Trip

**Preconditions:**
- Fresh database with alembic upgraded to HEAD (s17_github_user_oauth_tokens).
- Valid Fernet SYSTEM_SETTINGS_ENCRYPTION_KEY in environment.

**Steps:**

1. Run `cd backend && uv run alembic upgrade head` from a fresh database.
   - **Expected:** Migration completes without error. Table `github_user_oauth_tokens` exists in PostgreSQL.

2. Insert a test user into the database:
   ```sql
   INSERT INTO "user" (id, email, username, hashed_password) 
   VALUES ('f47ac10b-58cc-4372-a567-0e02b2c3d479', 'test@example.com', 'testuser', 'dummy_hash');
   ```

3. From Python REPL, instantiate and persist a token row:
   ```python
   from app.models import GitHubUserOAuthToken
   from app.core.github_user_tokens import encrypt_user_token
   from datetime import datetime, timezone
   from sqlalchemy.orm import Session
   
   token = GitHubUserOAuthToken(
       user_id='f47ac10b-58cc-4372-a567-0e02b2c3d479',
       github_user_id=42,
       access_token_encrypted=encrypt_user_token('ghu_plaintext_token_abc'),
       refresh_token_encrypted=encrypt_user_token('ghu_refresh_xyz'),
       access_token_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
       refresh_token_expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
       scopes='repo,read:user'
   )
   session.add(token)
   session.commit()
   ```

4. Query the row and decrypt:
   ```python
   from app.core.github_user_tokens import decrypt_user_token
   
   row = session.query(GitHubUserOAuthToken).filter_by(
       user_id='f47ac10b-58cc-4372-a567-0e02b2c3d479'
   ).one()
   
   plaintext_access = decrypt_user_token(row.access_token_encrypted)
   plaintext_refresh = decrypt_user_token(row.refresh_token_encrypted)
   ```

5. Verify decrypted tokens match the originals.
   - **Expected:** plaintext_access == 'ghu_plaintext_token_abc', plaintext_refresh == 'ghu_refresh_xyz'.

6. Query the raw row directly in PostgreSQL:
   ```sql
   SELECT access_token_encrypted, refresh_token_encrypted FROM github_user_oauth_tokens 
   WHERE user_id = 'f47ac10b-58cc-4372-a567-0e02b2c3d479';
   ```

7. Verify the BYTEA columns are not human-readable and do not contain the plaintext.
   - **Expected:** Columns are opaque binary data. No occurrence of 'ghu_plaintext_token_abc' or 'ghu_refresh_xyz' in the hex dump.

8. Run migration downgrade:
   ```bash
   cd backend && uv run alembic downgrade -1
   ```
   - **Expected:** Downgrade completes. Table is dropped. No data loss on re-upgrade.

9. Re-upgrade:
   ```bash
   cd backend && uv run alembic upgrade head
   ```
   - **Expected:** Upgrade recreates table with identical schema. All tests pass again.

**Edge Cases & Assumptions:**

- **Assumption:** Tests run against ephemeral test databases (via pytest fixtures) to avoid polluting production. The DDL hang fix (session release before alembic) is critical and verified by the 8-test migration suite passing without hangs.
- **Not Proven By This UAT:** End-to-end token refresh flow (S03), backend/orchestrator integration (S04–S05), or frontend reinstall CTA (S06). Those are downstream slice responsibilities.

**UAT Type:** Functional — round-trip encryption, schema shape, migration idempotence.

**Artifacts:**
- All 6 unit tests in `backend/tests/unit/test_github_user_tokens_crypto.py` pass.
- All 8 migration tests in `backend/tests/migrations/test_s17_github_user_oauth_tokens_migration.py` pass.
- SQLModel class `GitHubUserOAuthToken` and DTO `GitHubUserOAuthTokenStatus` are properly defined and tested.
