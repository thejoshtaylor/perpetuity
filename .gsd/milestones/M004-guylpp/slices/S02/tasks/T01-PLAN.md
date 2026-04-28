---
estimated_steps: 4
estimated_files: 3
skills_used: []
---

# T01: Add github_app_installations migration + SQLModel

Create alembic revision s06b_github_app_installations (down_revision=s06_system_settings_sensitive) that creates the github_app_installations table: id UUID PK, team_id UUID FK→team(id) ON DELETE CASCADE, installation_id BIGINT UNIQUE NOT NULL, account_login VARCHAR(255) NOT NULL, account_type VARCHAR(64) NOT NULL (CHECK in {'Organization','User'}), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(). Add SQLModel GitHubAppInstallation and the public projection GitHubAppInstallationPublic to backend/app/models.py. Add a migration test mirroring the M002 pattern (release the autouse db Session + dispose engine before alembic, restore head after — see test_s01_migration.py). The model layer stays purely declarative — no API logic here. Keep installation_id BIGINT (GitHub installation ids are int64); pydantic-validate as int.

## Negative Tests

- **Boundary conditions**: a second insert with the same installation_id MUST raise IntegrityError (UNIQUE); inserting with account_type='Bot' MUST raise CheckViolation; deleting the parent team MUST cascade-delete the installation row.
- **Migration reversibility**: downgrade then re-upgrade must leave schema byte-identical (snapshot via information_schema query before/after).

## Inputs

- ``backend/app/alembic/versions/s06_system_settings_sensitive.py` — prior head; new revision's down_revision`
- ``backend/app/models.py` — add GitHubAppInstallation + GitHubAppInstallationPublic next to SystemSetting`
- ``backend/tests/migrations/test_s01_migration.py` — fixture pattern (_release_autouse_db_session, _restore_head_after) to copy`
- ``backend/tests/conftest.py` — session-scoped autouse db fixture that the migration test must release`

## Expected Output

- ``backend/app/alembic/versions/s06b_github_app_installations.py` — new alembic revision (down_revision='s06_system_settings_sensitive', revision='s06b_github_app_installations'), creates table with UNIQUE(installation_id) + CHECK on account_type`
- ``backend/app/models.py` — GitHubAppInstallation(SQLModel, table=True) and GitHubAppInstallationPublic(SQLModel) added; ForeignKey to team; ondelete=CASCADE`
- ``backend/tests/migrations/test_s06b_github_app_installations_migration.py` — upgrade then downgrade test confirming columns + UNIQUE + CHECK; passes against compose db on POSTGRES_PORT=5432`

## Verification

cd backend && POSTGRES_PORT=5432 uv run alembic heads | grep -q 's06b_github_app_installations' && POSTGRES_PORT=5432 uv run pytest tests/migrations/test_s06b_github_app_installations_migration.py -v
