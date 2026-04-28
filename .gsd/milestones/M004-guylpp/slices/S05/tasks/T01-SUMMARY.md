---
id: T01
parent: S05
milestone: M004-guylpp
key_files:
  - backend/app/alembic/versions/s06e_github_webhook_events.py
  - backend/app/models.py
  - backend/tests/api/routes/test_github_webhooks_schema.py
key_decisions:
  - Migration revision id `s06e_github_webhook_events` chained to `s06d_projects_and_push_rules`
  - FK on `github_webhook_events.installation_id` uses ON DELETE SET NULL (not CASCADE) to preserve the audit trail when an installation is removed
  - UNIQUE on `delivery_id` enforced at the DB layer so the route can rely on INSERT ... ON CONFLICT DO NOTHING for GitHub's 24h retry idempotency
  - GitHubWebhookEventPublic intentionally omits the `payload` field so admin UIs cannot expose request bodies
  - Schema test placed at `tests/api/routes/test_github_webhooks_schema.py` per the explicit plan path (every other migration test in this repo lives under `tests/migrations/`); used the MEM014/MEM016 autouse fixture pattern there to dodge AccessShareLock deadlocks
duration: 
verification_result: passed
completed_at: 2026-04-28T01:03:57.901Z
blocker_discovered: false
---

# T01: Add s06e migration + SQLModel shapes for github_webhook_events and webhook_rejections

**Add s06e migration + SQLModel shapes for github_webhook_events and webhook_rejections**

## What Happened

Created the persistence substrate for the M004/S05 webhook receiver. Wrote `backend/app/alembic/versions/s06e_github_webhook_events.py` (revision `s06e_github_webhook_events`, down_revision `s06d_projects_and_push_rules`) building two tables: `github_webhook_events` (UUID PK, BIGINT installation_id FK→github_app_installations.installation_id ON DELETE SET NULL, event_type VARCHAR(64), delivery_id VARCHAR(64) UNIQUE, payload JSONB, received_at TIMESTAMPTZ DEFAULT NOW(), dispatch_status VARCHAR(32) DEFAULT 'noop', dispatch_error TEXT NULL) and `webhook_rejections` (UUID PK, delivery_id VARCHAR(64) NULL, signature_present/signature_valid BOOL, source_ip VARCHAR(64), received_at TIMESTAMPTZ). The UNIQUE on `delivery_id` is the storage-layer enforcement of GitHub's 24h retry idempotency contract (D025 / MEM229) — the route in T02 will rely on `INSERT ... ON CONFLICT DO NOTHING`. The FK uses `ON DELETE SET NULL` so losing an installation does not destroy the audit trail. Each `CREATE TABLE` emits one INFO log line, mirroring the s06d pattern. Appended SQLModel `GitHubWebhookEvent` and `WebhookRejection` (with `BigInteger` + `ForeignKey` + `JSONB` columns matching the migration) plus `GitHubWebhookEventPublic` and `WebhookRejectionPublic` projection classes to `backend/app/models.py` after the `ProjectPushRulePut` block; the public projection of `GitHubWebhookEvent` deliberately omits `payload` so admin UIs cannot leak request bodies. Wrote `backend/tests/api/routes/test_github_webhooks_schema.py` with three tests using the MEM014/MEM016 autouse fixture pattern (`_release_autouse_db_session` + `_restore_head_after`) — the session-scoped `db` fixture would otherwise hold an AccessShareLock that deadlocks alembic DDL: (1) duplicate `delivery_id` insert raises `IntegrityError`, (2) deleting a parent installation NULLs the child `installation_id` (audit-trail preservation), (3) alembic upgrade → downgrade to s06d → re-upgrade leaves the schema byte-identical (catches SQLModel/migration drift). The plan put the test under `tests/api/routes/` (not `tests/migrations/` where every other migration test lives); I followed the plan's explicit path.

## Verification

Ran `POSTGRES_PORT=5432 uv run alembic upgrade head` from `backend/` — exit 0, both INFO log lines emitted. `POSTGRES_PORT=5432 uv run alembic heads` reports `s06e_github_webhook_events (head)`. `POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_github_webhooks_schema.py -v` — 3/3 passed in 0.28s. Verified all four SQLModel classes (`GitHubWebhookEvent`, `WebhookRejection`, `GitHubWebhookEventPublic`, `WebhookRejectionPublic`) import cleanly. Sanity-ran the neighboring s06d migration test (`tests/migrations/test_s06d_projects_migration.py`) — 12/12 still pass, confirming the new migration did not destabilize the existing chain. The two slice-level test files (`test_github_webhooks_schema.py` covered here, `test_github_webhooks.py` for T02 and `test_m004_s05_webhook_receiver_e2e.py` for T03) are the slice's stopping condition; this task delivers the first one green.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && POSTGRES_PORT=5432 uv run alembic upgrade head` | 0 | pass | 2000ms |
| 2 | `cd backend && POSTGRES_PORT=5432 uv run alembic heads` | 0 | pass | 1500ms |
| 3 | `cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_github_webhooks_schema.py -v` | 0 | pass | 280ms |
| 4 | `uv run python -c 'from app.models import GitHubWebhookEvent, WebhookRejection, GitHubWebhookEventPublic, WebhookRejectionPublic'` | 0 | pass | 800ms |
| 5 | `cd backend && POSTGRES_PORT=5432 uv run pytest tests/migrations/test_s06d_projects_migration.py -q` | 0 | pass | 540ms |

## Deviations

None — followed the inlined task plan exactly. Test path is `tests/api/routes/` per the plan's Expected Output, even though every other migration test in this repo lives under `tests/migrations/`.

## Known Issues

None.

## Files Created/Modified

- `backend/app/alembic/versions/s06e_github_webhook_events.py`
- `backend/app/models.py`
- `backend/tests/api/routes/test_github_webhooks_schema.py`
