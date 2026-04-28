---
estimated_steps: 11
estimated_files: 4
skills_used: []
---

# T01: Introduce UserRole/TeamRole enums, TeamMember + minimal Team tables, and data-migrate is_superuser → role

Replace `is_superuser` with a `UserRole` enum on `User`, introduce `TeamRole` enum, and add `TeamMember` + a minimal `Team` stub table so FKs resolve in this slice and S02 can extend `Team` with real columns. Single Alembic migration that: (1) creates both enum types, (2) adds `role` column to `user` with a data migration (`is_superuser=True → system_admin`, else `user`), (3) drops `is_superuser`, (4) creates `team` stub (`id UUID PK`, `created_at`), (5) creates `team_member` (`user_id`, `team_id`, `role`, `created_at`, composite PK or unique constraint on `(user_id, team_id)`). Must be fully reversible (downgrade restores `is_superuser` bool by mapping `system_admin → True`, others → False; drops new tables/enums).

Failure Modes:
| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| Postgres migration | Fail loudly and roll back transaction | N/A (local DDL) | N/A |
| Existing superuser rows | Map to `system_admin`; log count of migrated rows | N/A | N/A (rows are already validated by SQLModel) |

Load Profile: negligible — one-shot migration against a small user table; single transaction.

Negative Tests:
- Migration against a DB with zero users, one user, and multiple users (mix of superuser/non)
- Downgrade from head back to previous revision and re-upgrade (idempotent round trip)
- Enum value not in allowed set rejected by SQLModel/Postgres

## Inputs

- ``backend/app/models.py``
- ``backend/app/alembic/versions/fe56fa70289e_add_created_at_to_user_and_item.py``
- ``backend/app/alembic/env.py``
- ``backend/app/initial_data.py``
- ``backend/app/crud.py``

## Expected Output

- ``backend/app/models.py``
- ``backend/app/alembic/versions/s01_auth_and_roles.py``
- ``backend/app/initial_data.py``
- ``backend/app/crud.py``

## Verification

cd backend && uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head && uv run python -c "from app.models import User, UserRole, TeamRole, TeamMember, Team; assert UserRole.system_admin.value == 'system_admin'; assert TeamRole.admin.value == 'admin'"

## Observability Impact

Migration logs row count of `is_superuser=True` → `system_admin` conversions via `op.execute` + `connection.execute(...).scalar()` count; downgrade logs reverse count. No secrets logged.
