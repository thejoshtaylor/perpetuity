---
estimated_steps: 1
estimated_files: 1
skills_used: []
---

# T02: SQLModel `GitHubUserOAuthToken` + `GitHubUserOAuthTokenStatus` DTO

Production code reads the row through SQLModel/SQLAlchemy in S02 and S03; raw SQL would force per-call column lists that drift from the schema. Add SQLModel class with __tablename__ = github_user_oauth_tokens, all 10 fields matching the migration exactly. user_id is PK with foreign_key=user.id and ondelete=CASCADE. Use default_factory=lambda: datetime.now(timezone.utc) for created_at/updated_at. Add GitHubUserOAuthTokenStatus Pydantic model that explicitly omits both *_encrypted columns (copy SystemSettingPublic pattern). No 'public with plaintext' DTO.

## Inputs

- `backend/app/models.py (existing SystemSetting / SystemSettingPublic pattern)`
- `T01's migration column definitions`

## Expected Output

- `GitHubUserOAuthToken SQLModel class registered in backend/app/models.py`
- `GitHubUserOAuthTokenStatus Pydantic class with no *_encrypted fields`
- `Verify command prints `github_user_oauth_tokens` then `set()``

## Verification

cd backend && uv run python -c "from app.models import GitHubUserOAuthToken, GitHubUserOAuthTokenStatus; print(GitHubUserOAuthToken.__tablename__); print(set(GitHubUserOAuthTokenStatus.model_fields.keys()) & {'access_token_encrypted','refresh_token_encrypted'})"
