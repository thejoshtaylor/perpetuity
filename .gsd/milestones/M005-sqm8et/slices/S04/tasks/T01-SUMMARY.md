---
id: T01
parent: S04
milestone: M005-sqm8et
key_files:
  - backend/app/alembic/versions/s14_webhook_delivery_id.py
  - backend/app/models.py
  - backend/app/services/dispatch.py
  - backend/app/api/routes/github_webhooks.py
  - backend/tests/api/test_dispatch_github_event.py
  - backend/tests/migrations/test_s14_webhook_delivery_id_migration.py
  - backend/tests/api/routes/test_github_webhooks.py
key_decisions:
  - Used full UNIQUE constraint (not partial index) on webhook_delivery_id — PostgreSQL NULL semantics handle non-webhook runs naturally
  - Used uuid.UUID(int=0) as sentinel triggering_user_id for webhook-triggered manual_workflow runs (no authenticated user context)
  - Used synchronous httpx.Client for orchestrator callback (not AsyncClient) — single blocking call in async context is acceptable
  - Custom _LogCollector fixture explicitly resets logger.disabled=False to survive alembic fileConfig's disable_existing_loggers=True reset
  - Updated test spies to async coroutines since dispatch_github_event is now async and the route uses await
duration: 
verification_result: passed
completed_at: 2026-04-29T09:00:04.099Z
blocker_discovered: false
---

# T01: s14 migration added webhook_delivery_id + dispatch_github_event fully implemented with push-rule dispatch, WorkflowRun insert, and Celery enqueue

**s14 migration added webhook_delivery_id + dispatch_github_event fully implemented with push-rule dispatch, WorkflowRun insert, and Celery enqueue**

## What Happened

Implemented two tightly coupled pieces: (1) Alembic migration s14_webhook_delivery_id adds webhook_delivery_id VARCHAR(64) UNIQUE NULLABLE to workflow_runs, enabling idempotent webhook dispatch via IntegrityError deduplication. (2) Replaced the M004 no-op dispatch stub with full webhook→workflow dispatch logic: mode='rule' evaluates fnmatch branch patterns and POSTs to the orchestrator auto-push-callback; mode='manual_workflow' resolves target user, inserts WorkflowRun, and enqueues run_workflow Celery task. The function signature became async and gained a required session parameter; the call site in github_webhooks.py was updated with await and session=session. All slice S04 log discriminators are emitted. Unit tests (5) use asyncio.run() with a custom _LogCollector fixture that re-enables the logger after alembic's fileConfig resets disable_existing_loggers. Migration tests (6) test column presence, unique constraint, duplicate blocking, NULL coexistence, downgrade, and round-trip. Pre-existing test_github_webhooks.py spy mocks were updated to async coroutines accepting session kwarg; one stale dispatch_status=noop assertion was updated to match real dispatch behavior (no_match when no installation in payload).

## Verification

Ran tests/api/routes/test_github_webhooks.py (9 pass), tests/api/test_dispatch_github_event.py (5 pass), tests/migrations/test_s14_webhook_delivery_id_migration.py (6 pass). Full suite shows 647 pass; remaining 15 failures are pre-existing (test_sessions, test_voice, older migration ordering issues) confirmed by stash-and-rerun on base commit.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `uv run pytest tests/api/routes/test_github_webhooks.py tests/api/test_dispatch_github_event.py tests/migrations/test_s14_webhook_delivery_id_migration.py -q` | 0 | 20 passed, 3 warnings | 4200ms |

## Deviations

Pre-existing test_github_webhooks.py tests required two fixes beyond the task plan: (1) sync spy functions patching an async dispatch needed to become async coroutines, (2) one test asserting dispatch_status=noop needed updating to dispatch_status=no_match to reflect real dispatch behavior.

## Known Issues

None.

## Files Created/Modified

- `backend/app/alembic/versions/s14_webhook_delivery_id.py`
- `backend/app/models.py`
- `backend/app/services/dispatch.py`
- `backend/app/api/routes/github_webhooks.py`
- `backend/tests/api/test_dispatch_github_event.py`
- `backend/tests/migrations/test_s14_webhook_delivery_id_migration.py`
- `backend/tests/api/routes/test_github_webhooks.py`
