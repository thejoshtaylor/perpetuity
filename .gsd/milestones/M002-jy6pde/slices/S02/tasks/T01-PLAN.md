---
estimated_steps: 12
estimated_files: 3
skills_used: []
---

# T01: Add workspace_volume Postgres table + SQLModel + s04 alembic migration

Land the persistence shape D014 calls for: a `workspace_volume` row per (user, team) recording the effective per-volume size_gb and the host img_path. This task is Postgres-only — no orchestrator changes — so it can be reviewed and tested via the existing migration-test pattern (MEM016/MEM025) before the loopback machinery in T02/T03 references the schema.

Schema:
  - `id` UUID PK (default uuid4)
  - `user_id` UUID NOT NULL, FK user.id ON DELETE CASCADE
  - `team_id` UUID NOT NULL, FK team.id ON DELETE CASCADE
  - `size_gb` INTEGER NOT NULL (effective per-volume cap; 1..256 range enforced at app level)
  - `img_path` VARCHAR(512) NOT NULL UNIQUE — the on-disk .img file path; uniqueness is the canonical 'one volume per file' invariant
  - `created_at` TIMESTAMPTZ default now()
  - UniqueConstraint(user_id, team_id) NAMED `uq_workspace_volume_user_team` — exactly one volume per (user, team) is the D004/MEM004 invariant
  - Index `ix_workspace_volume_user_id`, `ix_workspace_volume_team_id` for the orchestrator's lookup-by-(user,team) call

Migration discipline (MEM016): the autouse `db` fixture holds an AccessShareLock; the migration test must release+dispose engine before alembic, then dispose again on restore. Copy the pattern from `backend/tests/migrations/test_s01_migration.py::_release_autouse_db_session` and `_restore_head_after`. Migration file name is `s04_workspace_volume.py` per the M001 series convention; revision id `s04_workspace_volume`, down_revision `s03_team_invites`. Downgrade drops the table and both indexes by name (MEM025: explicit names so downgrade can drop them deterministically).

Model: add `WorkspaceVolume(SQLModel, table=True)` to `backend/app/models.py` with the same fields. No public Pydantic shape needed yet — S03 owns the admin API surface; the orchestrator reads via raw SQL through asyncpg in T03 so this model exists for ORM use from backend test code only.

## Inputs

- ``backend/app/alembic/versions/s03_team_invites.py``
- ``backend/app/models.py``
- ``backend/tests/migrations/test_s01_migration.py``
- ``backend/tests/migrations/test_s03_migration.py``

## Expected Output

- ``backend/app/alembic/versions/s04_workspace_volume.py``
- ``backend/app/models.py``
- ``backend/tests/migrations/test_s04_migration.py``

## Verification

cd backend && uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head && uv run pytest tests/migrations/test_s04_migration.py -v

## Observability Impact

Migration logs `S04 migration: created workspace_volume table + uq_workspace_volume_user_team` via the existing `alembic.runtime.migration.s04` logger pattern from s03. No runtime observability impact — the table is read by T03 via asyncpg.
