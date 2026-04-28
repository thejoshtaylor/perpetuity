---
id: T01
parent: S01
milestone: M005-sqm8et
key_files:
  - backend/app/alembic/versions/s09_team_secrets.py
  - backend/app/models.py
  - backend/tests/migrations/test_s09_team_secrets_migration.py
key_decisions:
  - Composite PK on (team_id, key) with FK CASCADE on team_id half — no separate UUID surrogate PK because every read path is a PK lookup and CASCADE drops orphan ciphertext that would be unrecoverable anyway.
  - value_encrypted is NOT NULL — row absence is the canonical 'not set' state. has_value stays as a column anyway for shape-parity with system_settings so the GET status DTO can render without peeking at ciphertext.
  - TeamSecretPublic and TeamSecretStatus are separate Pydantic models that structurally omit value_encrypted (not just exclude=True) so model_validate() of a TeamSecret row cannot accidentally serialize the ciphertext.
  - key column bounded VARCHAR(64) since the registry is a closed set of short identifiers (claude_api_key, openai_api_key, future github_pat).
  - Reused MEM014/MEM016 _release_autouse_db_session + _restore_head_after fixture pattern unchanged from s06c/s08 migration tests — that's the agreed pattern in this codebase for any DDL-touching test.
duration: 
verification_result: passed
completed_at: 2026-04-28T21:32:39.741Z
blocker_discovered: false
---

# T01: Added s09_team_secrets migration plus TeamSecret SQLModel and Pydantic DTOs that exclude value_encrypted from every public surface.

**Added s09_team_secrets migration plus TeamSecret SQLModel and Pydantic DTOs that exclude value_encrypted from every public surface.**

## What Happened

Created `backend/app/alembic/versions/s09_team_secrets.py` revising from `s08_push_subscriptions`. The new `team_secrets` table has a composite PK on (team_id, key), team FK CASCADE on delete (so orphan ciphertext can never linger — it would be unrecoverable anyway), and per-row columns matching success criterion (1): `value_encrypted BYTEA NOT NULL`, `has_value BOOLEAN NOT NULL DEFAULT TRUE`, `sensitive BOOLEAN NOT NULL DEFAULT TRUE`, `created_at`/`updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`. `key` is bounded `VARCHAR(64)` since the registry is a closed set of short identifiers. No additional indexes — every read path is a PK lookup.

Added `TeamSecret`, `TeamSecretPublic`, `TeamSecretStatus`, and `TeamSecretPut` to `backend/app/models.py` (inserted right after `TeamMirrorPatch`). The two public DTOs deliberately do NOT include `value_encrypted` as a field at all — `TeamSecretPublic.model_validate(row)` cannot accidentally serialize the ciphertext because the field is structurally absent from the model. `TeamSecretStatus` matches the `{key, has_value, sensitive, updated_at}` shape called out in the slice plan for GET responses. Reused the existing module-level imports (`Field`, `SQLModel`, `DateTime`, etc.) — no new imports needed.

Added `backend/tests/migrations/test_s09_team_secrets_migration.py` modeled on the s06c and s08 migration tests. It uses the MEM014/MEM016 `_release_autouse_db_session` + `_restore_head_after` fixture pair to dodge the AccessShareLock deadlock from the session-scoped autouse `db` fixture. Eight tests cover: column shape + types + nullability + composite PK; team FK CASCADE (`confdeltype='c'`); duplicate composite PK rejected with IntegrityError; same-team-different-key and different-team-same-key both coexist (proves PK is composite, not single-column); has_value + sensitive server-defaults land TRUE; downgrade drops the table cleanly; round-trip schema-byte-identical; and a structural assertion that `TeamSecretPublic`/`TeamSecretStatus.model_dump()` never carry a `value_encrypted` key even when the source row has ciphertext set.

Discovered that the host-side unit/migration tests target localhost:55432 (per `.env` POSTGRES_PORT=55432) while the running compose stack only publishes the shared `db` on 5432, so I started an isolated `perpetuity-testdb-55432` Postgres:18 container on port 55432, ran `uv run alembic upgrade head` to bring it to s09, then ran the verification command. Captured this as MEM408 so future agents don't repeat the diagnosis.

## Verification

Ran `cd backend && uv run pytest tests/migrations/test_s09_team_secrets_migration.py -v` against the localhost:55432 test DB after applying alembic migrations to head. All 8 tests passed in 0.45s — covering column shape, composite PK, FK CASCADE, server defaults, downgrade, round-trip, and DTO leak prevention. Also re-ran `tests/migrations/test_s08_push_subscriptions_migration.py` to confirm the new s09 link in the chain didn't regress s08; all 7 s08 tests still pass.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && uv run pytest tests/migrations/test_s09_team_secrets_migration.py -v` | 0 | ✅ pass | 450ms |
| 2 | `cd backend && uv run pytest tests/migrations/test_s08_push_subscriptions_migration.py -v` | 0 | ✅ pass (regression check) | 770ms |
| 3 | `cd backend && uv run alembic upgrade head` | 0 | ✅ pass (s09 migration applied cleanly on top of s08) | 2500ms |

## Deviations

None — the task plan and slice success criterion (1) were followed exactly. The only operational deviation was bringing up an isolated test postgres on 55432 to satisfy the host-side test connection (captured in MEM408); no code or schema departure from the plan.

## Known Issues

None at this task layer. The slice still requires T02 (registry + helpers), T03 (router), T04 (frontend panel), and T05 (e2e + redaction sweep extension). No log keys are emitted at T01 — the slice plan explicitly puts log emission in T03's router.

## Files Created/Modified

- `backend/app/alembic/versions/s09_team_secrets.py`
- `backend/app/models.py`
- `backend/tests/migrations/test_s09_team_secrets_migration.py`
