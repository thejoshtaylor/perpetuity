---
id: T03
parent: S05
milestone: M005-sqm8et
key_files:
  - backend/app/workflows/tasks.py
  - backend/app/core/celery_app.py
  - docker-compose.yml
  - backend/tests/unit/test_recover_orphan_runs.py
key_decisions:
  - Beat task body separated into _recover_orphan_runs_body(session) for testability — same pattern as _drive_run in the same file
  - Orphan threshold set to 15 minutes (ORPHAN_HEARTBEAT_THRESHOLD constant) matching the task plan spec
  - Uses col().is_(None) / col().isnot(None) for nullable column checks per codebase convention established in T02
  - celery-beat compose service uses /tmp/celerybeat-schedule so no persistent volume is needed and Beat restarts cleanly
duration: 
verification_result: passed
completed_at: 2026-04-29T10:21:06.436Z
blocker_discovered: false
---

# T03: recover_orphan_runs Beat task added to tasks.py + beat_schedule wired into celery_app.py + celery-beat compose service added; 5 unit tests all pass

**recover_orphan_runs Beat task added to tasks.py + beat_schedule wired into celery_app.py + celery-beat compose service added; 5 unit tests all pass**

## What Happened

Added `_recover_orphan_runs_body(session)` and `recover_orphan_runs` Celery task to `backend/app/workflows/tasks.py`. The body function queries WorkflowRun rows with status='running' where last_heartbeat_at (or created_at when heartbeat is NULL) is older than 15 minutes, marks each run failed with error_class='worker_crash' and finished_at=now(), then marks all associated step_runs in 'running' or 'pending' state as failed with the same discriminator. Emits `workflow_run_orphan_recovered` (INFO) per run and `recover_orphan_runs_sweep` (INFO) summary with orphan_count. Uses `col().is_()` and `col().isnot()` per codebase convention (consistent with T02's use of `col().in_()`).

Wired the `beat_schedule` entry (`recover-orphan-runs` task at 600s interval) into `celery_app.conf.update` in `backend/app/core/celery_app.py`.

Added `celery-beat` service to `docker-compose.yml` mirroring the `celery-worker` environment block exactly (same POSTGRES_*, REDIS_*, ORCHESTRATOR_*, SYSTEM_SETTINGS_ENCRYPTION_KEY, SENTRY_DSN vars) with command `celery -A app.core.celery_app beat --loglevel=info --schedule=/tmp/celerybeat-schedule`.

Created `backend/tests/unit/test_recover_orphan_runs.py` with 5 pure-unit tests using mocked Session (no Postgres required): no-orphans sweep, two-orphans-recovered, NULL-heartbeat uses created_at, succeeded step_runs not touched, finished_at stamped on both run and steps.

## Verification

cd backend && uv run pytest tests/unit/test_recover_orphan_runs.py -v — 5 passed in 0.26s. All three slice-level log discriminators confirmed present: workflow_cap_exceeded (from T02's workflow_dispatch.py), recover_orphan_runs_sweep, and workflow_run_orphan_recovered (both from tasks.py).

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && uv run pytest tests/unit/test_recover_orphan_runs.py -v` | 0 | ✅ pass | 260ms |

## Deviations

none

## Known Issues

none

## Files Created/Modified

- `backend/app/workflows/tasks.py`
- `backend/app/core/celery_app.py`
- `docker-compose.yml`
- `backend/tests/unit/test_recover_orphan_runs.py`
