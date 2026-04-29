---
estimated_steps: 9
estimated_files: 4
skills_used: []
---

# T03: recover_orphan_runs Beat task + celery-beat compose service

Add the recover_orphan_runs Celery Beat task to backend/app/workflows/tasks.py and wire it into the Celery beat_schedule in backend/app/core/celery_app.py. Add the celery-beat service to docker-compose.yml.

Orphan definition: WorkflowRun with status='running' and last_heartbeat_at < now()-15min (or last_heartbeat_at IS NULL and created_at < now()-15min). These are runs whose Celery worker died mid-execution without updating status. The task: SELECT all orphan runs, for each: set status='failed', error_class='worker_crash', completed_at=now(), then for any step_runs in status='running' or 'pending' belonging to the orphan run: set status='failed', error_class='worker_crash'. Emit workflow_run_orphan_recovered (INFO) per run and recover_orphan_runs_sweep (INFO) summary with count.

Celery beat_schedule entry: run every 10 minutes. Beat service in docker-compose.yml: same image as backend, command `celery -A app.core.celery_app beat --loglevel=info --schedule=/tmp/celerybeat-schedule`. Must share the same env vars as celery-worker (POSTGRES_*, REDIS_URL, ORCHESTRATOR_API_KEY, etc.).

Why/Files/Do/Verify/Done-when:
- Why: Without orphan recovery, a celery-worker container crash leaves runs stuck in 'running' forever — the history list shows phantom running runs, and cap enforcement double-counts them.
- Files: backend/app/workflows/tasks.py, backend/app/core/celery_app.py, docker-compose.yml
- Do: Add recover_orphan_runs task. Add beat_schedule to celery_app.py Celery configuration. Add celery-beat service to docker-compose.yml. Write unit tests with a mocked DB session: no orphans → sweep log count=0, two orphans → both marked failed + step_runs updated + two recovered logs emitted.
- Verify: cd backend && uv run pytest tests/unit/test_recover_orphan_runs.py -v
- Done when: Unit tests pass; docker-compose.yml has celery-beat service; beat_schedule has recover_orphan_runs entry with 600s interval.

## Inputs

- `backend/app/workflows/tasks.py`
- `backend/app/core/celery_app.py`
- `docker-compose.yml`
- `backend/app/models.py`

## Expected Output

- `backend/app/workflows/tasks.py`
- `backend/app/core/celery_app.py`
- `docker-compose.yml`
- `backend/tests/unit/test_recover_orphan_runs.py`

## Verification

cd backend && uv run pytest tests/unit/test_recover_orphan_runs.py -v

## Observability Impact

recover_orphan_runs_sweep (INFO) fires per Beat execution with orphan_count. workflow_run_orphan_recovered (INFO) fires per recovered run with run_id, workflow_id, stuck_since (last_heartbeat_at or created_at value).
