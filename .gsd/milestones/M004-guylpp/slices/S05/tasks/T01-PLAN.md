---
estimated_steps: 5
estimated_files: 3
skills_used:
  - tdd
---

# T01: Add github_webhook_events + webhook_rejections schema and SQLModel/Pydantic shapes

**Slice:** S05 — Webhook receiver (HMAC verify, persist, dispatch hook)
**Milestone:** M004-guylpp

## Description

Create the Alembic migration `s06e_github_webhook_events` that adds two new tables:

- `github_webhook_events` (id UUID PK, installation_id BIGINT NULL FK→github_app_installations(installation_id) ON DELETE SET NULL, event_type VARCHAR(64) NOT NULL, delivery_id VARCHAR(64) NOT NULL UNIQUE, payload JSONB NOT NULL, received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), dispatch_status VARCHAR(32) NOT NULL DEFAULT 'noop', dispatch_error TEXT NULL). UNIQUE on `delivery_id` is the storage-layer enforcement of GitHub's 24h retry idempotency contract (D025 / MEM229) — the route layer relies on the DB to enforce it via INSERT ... ON CONFLICT DO NOTHING.
- `webhook_rejections` (id UUID PK, delivery_id VARCHAR(64) NULL — header may be absent on a malformed request, signature_present BOOLEAN NOT NULL, signature_valid BOOLEAN NOT NULL, source_ip VARCHAR(64) NOT NULL, received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()).

Add SQLModel `GitHubWebhookEvent` and `WebhookRejection` table classes to `backend/app/models.py` mirroring the schema, plus public projection shapes: `GitHubWebhookEventPublic` (admin-side projection — no payload, never expose body in admin UIs), `WebhookRejectionPublic`. The migration's `down_revision` is `s06d_projects_and_push_rules` (the current head). The FK on `installation_id` uses ON DELETE SET NULL because losing an installation should not destroy the audit trail of webhooks GitHub already sent. Logging in the migration mirrors the `s06d` pattern: one INFO line per CREATE TABLE.

The unit test file is the slice's first verification target — it must catch (a) UNIQUE-violation IntegrityError on duplicate `delivery_id`, (b) FK SET-NULL behavior on parent `github_app_installations` deletion, and (c) `alembic upgrade head` followed by `downgrade -1` and back leaves no schema drift (catches divergence between the SQLModel and the migration).

## Steps

1. Read `backend/app/alembic/versions/s06d_projects_and_push_rules.py` end-to-end to mirror its docstring shape, `sa.Column` typing patterns, logger usage, and downgrade ordering.
2. Write `backend/app/alembic/versions/s06e_github_webhook_events.py` with `revision='s06e_github_webhook_events'`, `down_revision='s06d_projects_and_push_rules'`, both `CREATE TABLE` statements, the UNIQUE constraint on `github_webhook_events.delivery_id` named `uq_github_webhook_events_delivery_id`, and the FK on `installation_id` with `ondelete='SET NULL'`. Downgrade drops `webhook_rejections` first then `github_webhook_events` (no FK between them, but mirror the s06d ordering for consistency).
3. Append SQLModel `GitHubWebhookEvent` and `WebhookRejection` table classes to `backend/app/models.py` after the existing `ProjectPushRulePut` block. Reuse the file's existing `UniqueConstraint`, `ForeignKey`, `BigInteger`, `Column(JSONB, nullable=False)`, and `DateTime(timezone=True)` patterns. Add `GitHubWebhookEventPublic` and `WebhookRejectionPublic` projection classes immediately after.
4. Write `backend/tests/api/routes/test_github_webhooks_schema.py` with three tests: (a) duplicate-`delivery_id` insert raises `sqlalchemy.exc.IntegrityError`; (b) deleting the parent `github_app_installations` row sets `github_webhook_events.installation_id` to NULL (assert via `session.refresh`); (c) `alembic upgrade head` then `downgrade s06d_projects_and_push_rules` then `upgrade head` succeeds (use the existing `alembic` python API or subprocess; mirror whatever pattern the existing schema-level tests use — check `tests/api/routes/test_admin_settings.py` for an example).
5. Run `cd backend && POSTGRES_PORT=5432 uv run alembic upgrade head` to apply the migration locally; run the new test file; commit nothing (the slice instruction says don't commit).

## Must-Haves

- [ ] `s06e_github_webhook_events.py` migration file exists with `down_revision='s06d_projects_and_push_rules'`.
- [ ] `alembic heads` reports `s06e_github_webhook_events (head)` after upgrade.
- [ ] `github_webhook_events.delivery_id` has a real UNIQUE constraint (not just a non-unique index) — verified by the duplicate-insert test raising `IntegrityError`.
- [ ] `github_webhook_events.installation_id` FK uses `ON DELETE SET NULL` — verified by deleting a parent installation and re-reading the child.
- [ ] `models.py` exports `GitHubWebhookEvent`, `WebhookRejection`, `GitHubWebhookEventPublic`, `WebhookRejectionPublic` and they are importable.
- [ ] Migration round-trip (upgrade → downgrade → upgrade) is tested and passes.

## Failure Modes

| Dependency | On error | On timeout | On malformed response |
|------------|----------|------------|-----------------------|
| Postgres (compose `db` service) | Migration aborts with non-zero exit; alembic logs the SQL that failed; no partial schema (alembic runs each revision in a transaction) | Alembic returns non-zero after Postgres connect timeout (~30s); test fixture skips with a clear message | N/A — Postgres protocol is binary and well-defined; `IntegrityError` is the expected "malformed" path under test |
| `github_app_installations` table (S02) | If the parent table is missing, the FK creation fails at upgrade time with a clear "relation does not exist" — proves migration ordering is wrong; abort early | N/A | N/A |

## Negative Tests

- **Malformed inputs**: insert a `github_webhook_events` row with `delivery_id=NULL` → must raise NOT-NULL constraint violation. Insert with a 65-char `delivery_id` → must raise length-violation.
- **Error paths**: insert two rows with the same `delivery_id` → second raises `IntegrityError` (the slice's idempotency-correctness invariant).
- **Boundary conditions**: deleting the parent `github_app_installations` row when child events exist must NOT cascade the child events — only the FK column NULLs out (audit-trail preservation invariant).

## Verification

- `cd backend && POSTGRES_PORT=5432 uv run alembic upgrade head` exits 0.
- `cd backend && POSTGRES_PORT=5432 uv run alembic heads` output contains `s06e_github_webhook_events (head)`.
- `cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_github_webhooks_schema.py -v` passes (3 tests).

## Observability Impact

- Signals added/changed: migration emits one INFO line per CREATE TABLE (mirror of s06d's pattern: `S06e migration: created github_webhook_events`, `S06e migration: created webhook_rejections`). No runtime signals from this task itself — the route in T02 emits the contract log keys.
- How a future agent inspects this: `cd backend && POSTGRES_PORT=5432 uv run alembic heads` to see the current head; `\d+ github_webhook_events` and `\d+ webhook_rejections` from psql to inspect column shapes and constraints.
- Failure state exposed: alembic upgrade failure surfaces at app boot via the existing alembic-on-startup check; downgrade failure is caught by the round-trip test.

## Inputs

- `backend/app/alembic/versions/s06d_projects_and_push_rules.py` — pattern to follow for the new migration (docstring shape, `sa.Column` types, logger import, downgrade ordering)
- `backend/app/models.py` — append new SQLModel classes after the existing `ProjectPushRulePut` block; reuse the `UniqueConstraint` / `ForeignKey` / `DateTime(timezone=True)` patterns already in the file
- `backend/app/api/routes/admin.py` — for cross-reference of the `GITHUB_APP_WEBHOOK_SECRET_KEY` constant in the migration docstring (no import needed)

## Expected Output

- `backend/app/alembic/versions/s06e_github_webhook_events.py` — new alembic revision (revision='s06e_github_webhook_events', down_revision='s06d_projects_and_push_rules') creating both tables with the UNIQUE on `github_webhook_events.delivery_id` and the FK on `installation_id` with `ON DELETE SET NULL`
- `backend/app/models.py` — appended `GitHubWebhookEvent` and `WebhookRejection` SQLModel table classes plus `GitHubWebhookEventPublic` and `WebhookRejectionPublic` projection shapes
- `backend/tests/api/routes/test_github_webhooks_schema.py` — new unit test file asserting (a) UNIQUE on `delivery_id` raises IntegrityError on duplicate insert, (b) FK on `installation_id` with `ON DELETE SET NULL` behaves correctly, (c) alembic upgrade/downgrade round-trip leaves no schema drift
