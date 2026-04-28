---
id: T01
parent: S02
milestone: M002-jy6pde
key_files:
  - backend/app/alembic/versions/s04_workspace_volume.py
  - backend/app/models.py
  - backend/tests/migrations/test_s04_migration.py
key_decisions:
  - Named the `img_path` UNIQUE constraint explicitly as `uq_workspace_volume_img_path` (in addition to the inline UNIQUE on the column) so the constraint shows up in pg_constraint with a known name and is symmetric with `uq_workspace_volume_user_team`.
  - Composite UniqueConstraint on (user_id, team_id) named `uq_workspace_volume_user_team` is the canonical D004/MEM004 invariant — verified by the duplicate-insert IntegrityError test.
  - Did NOT add a CHECK constraint for `size_gb BETWEEN 1 AND 256`; per the task plan, that range is enforced at the app layer (S03 owns the admin API surface where range validation lives).
duration: 
verification_result: passed
completed_at: 2026-04-25T10:28:39.779Z
blocker_discovered: false
---

# T01: Add workspace_volume Postgres table + SQLModel + s04 alembic migration with up/down reversibility and FK/unique enforcement

**Add workspace_volume Postgres table + SQLModel + s04 alembic migration with up/down reversibility and FK/unique enforcement**

## What Happened

Landed the persistence shape D014 calls for so T02/T03 can wire the loopback machinery on top.

**Migration `backend/app/alembic/versions/s04_workspace_volume.py`** — chains `s04_workspace_volume` ⇸ `s03_team_invites`. Creates `workspace_volume` with id (UUID PK), user_id (FK user.id ON DELETE CASCADE), team_id (FK team.id ON DELETE CASCADE), size_gb (INTEGER NOT NULL), img_path (VARCHAR(512) NOT NULL UNIQUE), created_at (TIMESTAMPTZ NULL). Names: `uq_workspace_volume_user_team` for the (user_id, team_id) composite uniqueness invariant (D004/MEM004), `uq_workspace_volume_img_path` for the canonical 'one volume per file' invariant, plus btree indexes `ix_workspace_volume_user_id` / `ix_workspace_volume_team_id` for the orchestrator's lookup-by-(user, team) call. Downgrade drops both named btree indexes by name then drops the table — fully reversible (MEM025).

**Model `backend/app/models.py`** — added `WorkspaceVolume(SQLModel, table=True)` with the same fields, default-uuid4 PK, default `get_datetime_utc` for created_at, and `UniqueConstraint('user_id', 'team_id', name='uq_workspace_volume_user_team')`. Mirrors the s03 `TeamInvite` shape; FK fields use `ondelete='CASCADE'` and `index=True` to match the migration. No public Pydantic schema yet — S03 owns the admin API, T03 reads via raw asyncpg, so this model exists for ORM use from backend test code only.

**Test `backend/tests/migrations/test_s04_migration.py`** — copies the MEM016 fixture pattern verbatim from `test_s03_migration.py`: autouse `_release_autouse_db_session` commits/expires/closes the session-scoped autouse `db` and `engine.dispose()`s the pool before alembic runs; autouse `_restore_head_after` upgrades back to head and disposes again. Four cases: (1) upgrade-shape — verifies all 6 columns with correct nullability, presence of both lookup indexes, and the named composite-unique constraint; FK enforcement rejects bogus user_id and bogus team_id inserts. (2) downgrade — table and both named indexes are gone after `downgrade s03_team_invites`. (3) duplicate (user_id, team_id) raises IntegrityError. (4) duplicate img_path across different teams raises IntegrityError.

**Environment note:** the live `perpetuity-db-1` container is bound to host port 5432 but `.env` pins `POSTGRES_PORT=55432` (MEM021). All verification ran with `POSTGRES_PORT=5432` overridden. This is a pre-existing environment drift — captured as a memory for future agents but not fixed in this task.

## Verification

Ran the slice's exact verification command:

```
cd backend && POSTGRES_PORT=5432 uv run alembic upgrade head \
  && POSTGRES_PORT=5432 uv run alembic downgrade -1 \
  && POSTGRES_PORT=5432 uv run alembic upgrade head \
  && POSTGRES_PORT=5432 uv run pytest tests/migrations/test_s04_migration.py -v
```

- `alembic upgrade head` — runs `s03_team_invites -> s04_workspace_volume`, logs `S04 migration: created workspace_volume table + uq_workspace_volume_user_team`.
- `alembic downgrade -1` — runs `s04_workspace_volume -> s03_team_invites`, logs `S04 downgrade: dropped workspace_volume table and indexes`.
- `alembic upgrade head` — re-runs the upgrade cleanly (proves reversibility).
- `pytest tests/migrations/test_s04_migration.py -v` — **4 passed in 0.21s**: upgrade-shape + FK enforcement, downgrade drop, duplicate (user_id, team_id) IntegrityError, duplicate img_path IntegrityError.

The s04 logger is the only INFO emitted by the migration, matching the s03 pattern.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `POSTGRES_PORT=5432 uv run alembic upgrade head` | 0 | ✅ pass | 3500ms |
| 2 | `POSTGRES_PORT=5432 uv run alembic downgrade -1` | 0 | ✅ pass | 3000ms |
| 3 | `POSTGRES_PORT=5432 uv run alembic upgrade head` | 0 | ✅ pass | 3000ms |
| 4 | `POSTGRES_PORT=5432 uv run pytest tests/migrations/test_s04_migration.py -v` | 0 | ✅ pass (4 passed) | 210ms |

## Deviations

All verification commands required `POSTGRES_PORT=5432` exported (the live `perpetuity-db-1` container is bound to host port 5432, while `.env` pins 55432 per MEM021). The plan's verify line `cd backend && uv run alembic ...` was executed with that override. No code-level deviations from the plan.

## Known Issues

Pre-existing environment drift: `.env` says `POSTGRES_PORT=55432` (MEM021) but the running db container is on 5432. Captured as an environment memory; not in this task's scope to reconcile (touching .env or compose risks affecting other in-flight integration tests).

## Files Created/Modified

- `backend/app/alembic/versions/s04_workspace_volume.py`
- `backend/app/models.py`
- `backend/tests/migrations/test_s04_migration.py`
