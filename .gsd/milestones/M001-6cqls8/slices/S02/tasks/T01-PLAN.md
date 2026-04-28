---
estimated_steps: 24
estimated_files: 3
skills_used: []
---

# T01: Extend Team model + migration s02_team_columns

Promote the S01 Team stub to a real model by adding `name`, `slug`, and `is_personal` columns and shipping the corresponding Alembic migration. This task owns the DB schema change only — no endpoint work, no signup mutation. Every later task in S02 depends on these columns existing.

**Context from S01:** `team` currently has only `id` (UUID PK) + `created_at`. `team_member` (id, user_id, team_id, role enum, created_at, unique(user_id, team_id)) already exists. Migration chain head is `s01_auth_and_roles`. The migration test harness (`tests/migrations/test_s01_migration.py`) solved the AccessShareLock problem with an autouse fixture that releases the session-scoped db session + `engine.dispose()` before alembic runs (MEM016) — reuse that pattern.

**Schema decisions:**
- `name: str` NOT NULL, length 1..255, Postgres VARCHAR(255).
- `slug: str` NOT NULL, length 1..64, UNIQUE + indexed, Postgres VARCHAR(64). Server-generated, never client-supplied.
- `is_personal: bool` NOT NULL, default FALSE at DB level (so later inserts that forget it fail open in the `is not personal` direction — safer default).
- No foreign key to User (team is independent of creator; membership is via TeamMember; this matches the shared-team model where admins can be removed).

**Migration strategy (nullable→backfill→NOT NULL pattern per MEM025-style convention from S01):**
1. Add each column nullable.
2. Backfill rows: in practice, fresh DBs have 0 rows in `team` — the S01 migration created the table empty. Still, write defensive UPDATE: `UPDATE "team" SET name = 'Legacy Team ' || substr(id::text, 1, 8), slug = 'legacy-' || substr(id::text, 1, 8), is_personal = FALSE WHERE name IS NULL` so a manually seeded row doesn't break the migration.
3. ALTER COLUMN … SET NOT NULL for all three.
4. CREATE UNIQUE INDEX ix_team_slug on (slug).
5. Log backfilled rowcount via `logger.info` so ops can see migration impact.

**Downgrade:** drop the unique index, drop all three columns — no data preservation required (S02 introduces these; downgrade returns to S01 shape).

**Model update (`app/models.py`):**
- Add `name: str = Field(min_length=1, max_length=255)`, `slug: str = Field(min_length=1, max_length=64, unique=True, index=True)`, `is_personal: bool = Field(default=False)` to `Team`.
- Add a `TeamPublic(SQLModel)` response model: `id, name, slug, is_personal, created_at`.
- Add a `TeamCreate(SQLModel)` request model: `name: str = Field(min_length=1, max_length=255)`.
- Add a `TeamWithRole(SQLModel)` response model: all TeamPublic fields + `role: TeamRole` (for GET /teams which joins membership).

Do NOT change TeamMember or any other model.

**Must-haves (self-audit):**
- Model imports don't change — only Team extended; existing S01 tests still pass.
- Migration is reversible and the round-trip test below proves it.
- Slug uniqueness enforced at DB level — duplicate slug raises IntegrityError, not a silent overwrite.

## Inputs

- ``backend/app/models.py` — add name/slug/is_personal to existing Team; add TeamPublic/TeamCreate/TeamWithRole response models.`
- ``backend/app/alembic/versions/s01_auth_and_roles.py` — reference the parent revision id (`s01_auth_and_roles`) for `down_revision`.`
- ``backend/tests/migrations/test_s01_migration.py` — copy the autouse fixture pattern that releases the session-scoped db + engine.dispose() before alembic runs (MEM016). The new migration test MUST use the same pattern or DROP COLUMN will hang.`

## Expected Output

- ``backend/app/models.py` — Team class extended with name, slug, is_personal fields; TeamPublic, TeamCreate, TeamWithRole response models added.`
- ``backend/app/alembic/versions/s02_team_columns.py` — new migration file with revision='s02_team_columns', down_revision='s01_auth_and_roles', full upgrade/downgrade implementing nullable→backfill→NOT NULL pattern with unique index on slug.`
- ``backend/tests/migrations/test_s02_migration.py` — round-trip test asserting column presence/nullability after upgrade, column absence after downgrade, unique slug enforcement.`

## Verification

cd backend && uv run alembic upgrade head && uv run pytest tests/migrations/test_s02_migration.py -v && uv run pytest tests/ -v (expect previous 76 + new migration test passing; zero regressions)

## Observability Impact

Signals added: `logger.info('S02 migration: backfilled %d team rows with legacy names', n)` on upgrade; `logger.info('S02 downgrade: dropped name/slug/is_personal from %d team rows', n)` on downgrade. Inspection: `information_schema.columns` reveals column shape; alembic_version reveals current head (s02_team_columns post-upgrade). Failure state: alembic logs the exact SQL that failed + per-step rowcount from the backfill UPDATE.
