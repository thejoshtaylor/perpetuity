---
estimated_steps: 1
estimated_files: 2
skills_used: []
---

# T01: Alembic revision `s17_github_user_oauth_tokens` + migration test

Lands the table that S02–S07 will read/write; without it nothing else in the milestone can proceed. Copy structure of backend/app/alembic/versions/s09_team_secrets.py for the upgrade/downgrade pair. Use sa.Uuid() for user_id and sa.BigInteger() for both installation_id and github_user_id. Use sa.LargeBinary() for both ciphertext columns. Use sa.DateTime(timezone=True) with server_default=sa.text("NOW()"). FK constraint on user_id with ondelete=CASCADE inline on op.create_table (match s09 pattern). Migration test copies fixtures _release_autouse_db_session and _restore_head_after verbatim from test_s09_team_secrets_migration.py. At least 7 distinct test functions: column shape, PK, FK CASCADE, duplicate-user_id violation, cascade-on-user-delete, server-defaults, downgrade-roundtrip.

## Inputs

- `backend/app/alembic/versions/s09_team_secrets.py (template)`
- `backend/tests/migrations/test_s09_team_secrets_migration.py (fixture template)`
- `backend/app/alembic/versions/s16_workflow_run_rejected_status.py (down_revision)`

## Expected Output

- `backend/app/alembic/versions/s17_github_user_oauth_tokens.py with revision = s17_github_user_oauth_tokens and down_revision = s16_workflow_run_rejected_status`
- `github_user_oauth_tokens table with 10 columns + PK on user_id + FK to user(id) ON DELETE CASCADE + server-defaults on created_at/updated_at`
- `backend/tests/migrations/test_s17_github_user_oauth_tokens_migration.py with autouse fixtures _release_autouse_db_session and _restore_head_after + at least 7 test functions`

## Verification

cd backend && uv run pytest tests/migrations/test_s17_github_user_oauth_tokens_migration.py -v
