---
id: T02
parent: S01
milestone: M006-ydo2ce
key_files:
  - /Users/josh/code/perpetuity/backend/app/models.py
key_decisions:
  - Used lambda: datetime.now(timezone.utc) as default_factory for created_at/updated_at per task spec (rather than the existing get_datetime_utc helper) to match the explicit requirement
  - Used sa_column=Column(BigInteger, nullable=True) for installation_id and github_user_id to ensure BigInteger DB type for int64 GitHub IDs
  - access_token_encrypted and refresh_token_encrypted typed as bytes | None using Field(default=None, nullable=True) matching the BYTEA column in the migration
duration: 
verification_result: passed
completed_at: 2026-05-12T21:09:20.595Z
blocker_discovered: false
---

# T02: Added GitHubUserOAuthToken SQLModel and GitHubUserOAuthTokenStatus Pydantic DTO to backend/app/models.py

**Added GitHubUserOAuthToken SQLModel and GitHubUserOAuthTokenStatus Pydantic DTO to backend/app/models.py**

## What Happened

Read backend/app/models.py to understand the existing patterns (SystemSetting/SystemSettingPublic, TeamSecret/TeamSecretPublic). Added GitHubUserOAuthToken as a SQLModel table class with __tablename__ = "github_user_oauth_tokens", all 10 columns matching the migration: user_id UUID PK with foreign_key="user.id" and ondelete="CASCADE", installation_id and github_user_id as Optional[int] BigInteger columns, github_login/token_type/scope as Optional[str], access_token_encrypted/refresh_token_encrypted as Optional[bytes], and created_at/updated_at as datetime fields with default_factory=lambda: datetime.now(timezone.utc) and timezone=True. Added GitHubUserOAuthTokenStatus as a plain SQLModel (Pydantic) DTO that intentionally omits both encrypted columns — only user_id, installation_id, github_user_id, github_login, token_type, scope, created_at, updated_at are present. The pattern mirrors SystemSettingPublic (omits value_encrypted) and TeamSecretPublic (omits value_encrypted).

## Verification

Ran the specified verification command: `cd backend && uv run python -c "from app.models import GitHubUserOAuthToken, GitHubUserOAuthTokenStatus; print(GitHubUserOAuthToken.__tablename__); print(set(GitHubUserOAuthTokenStatus.model_fields.keys()) & {'access_token_encrypted','refresh_token_encrypted'})"`. Output was `github_user_oauth_tokens` then `set()` — exactly the expected result, confirming the tablename is correct and neither encrypted field appears in the status DTO.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd /Users/josh/code/perpetuity/backend && uv run python -c "from app.models import GitHubUserOAuthToken, GitHubUserOAuthTokenStatus; print(GitHubUserOAuthToken.__tablename__); print(set(GitHubUserOAuthTokenStatus.model_fields.keys()) & {'access_token_encrypted','refresh_token_encrypted'})"` | 0 | PASS — printed 'github_user_oauth_tokens' then 'set()' | 1850ms |

## Deviations

None — implementation matches the task plan exactly.

## Known Issues

None.

## Files Created/Modified

- `/Users/josh/code/perpetuity/backend/app/models.py`
