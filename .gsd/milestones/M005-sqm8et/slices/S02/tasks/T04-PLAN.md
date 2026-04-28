---
estimated_steps: 13
estimated_files: 4
skills_used: []
---

# T04: Workflow trigger + run-detail API + compose celery-worker service

Backend HTTP boundary the dashboard calls plus the compose service that runs the Celery worker.

(1) `backend/app/api/routes/workflows.py`: new router. `POST /api/v1/workflows/{workflow_id}/run` — body `{trigger_payload: dict}` where for `_direct_claude` / `_direct_codex` the payload is `{prompt: str}`. Caller must be a member of the workflow's team (use existing `assert_caller_is_team_member`). Inserts `workflow_runs` row with status='pending', trigger_type='button', triggered_by_user_id=current_user.id, target_user_id=current_user.id (S02 scope='user'), trigger_payload as-is. Inserts `step_runs` rows from `workflow_steps` snapshot — each with status='pending', snapshot=row.dict() at dispatch time. Commits. Then dispatches `run_workflow.delay(run_id)`. Returns `{run_id: UUID, status: 'pending'}`. Failure modes: workflow_id not found → 404 `workflow_not_found`; non-member → 403 `not_team_member`; missing prompt for AI workflow → 400 `missing_required_field`. Logs `workflow_run_dispatched run_id workflow_id trigger_type=button`.

(2) Same router: `GET /api/v1/workflow_runs/{run_id}` — returns `WorkflowRunPublic` with ordered `step_runs: list[StepRunPublic]`. Caller must be a member of the run's team (joins through workflow → team). 404 if not found.

(3) Same router: `GET /api/v1/teams/{team_id}/workflows` — list query for the dashboard (filtered by team membership; T05's frontend uses this to find the `_direct_claude` / `_direct_codex` workflow ids).

(4) Wire router into `backend/app/api/main.py`.

(5) `docker-compose.yml`: new service `celery-worker` using the same backend image, `command: celery -A app.workflows.tasks worker --loglevel=info --concurrency=4`, env mirrors backend (Postgres, Redis, ORCHESTRATOR_BASE_URL, ORCHESTRATOR_API_KEY, SYSTEM_SETTINGS_ENCRYPTION_KEY) per D016 two-key shared-secret discipline; depends_on db + redis + prestart. Does NOT mount Docker socket (D005).

**Failure Modes:**
| Dependency | On error | On timeout | On malformed response |
|------------|----------|-----------|----------------------|
| Postgres (insert workflow_runs/step_runs) | 500 — let SQLAlchemy bubble; transactional rollback | N/A | N/A |
| Celery `.delay()` (Redis broker) | 503 `task_dispatch_failed`; mark run as failed with `error_class='dispatch_failed'`; client surfaces error inline | Default kombu timeout; same as error | N/A |

**Load Profile:** Postgres pool (one transaction per trigger), Redis broker (one publish per trigger). 2 DB writes + 1 publish per op. 10x breakpoint: Redis broker queue depth — S05 caps via `max_concurrent_runs`; S02 unguarded.

**Negative Tests:** empty trigger_payload, prompt absent, workflow_id not UUID, run_id not UUID, workflow_id valid but for a different team than caller, run_id of cascaded-deleted workflow.

## Inputs

- ``backend/app/workflows/tasks.py``
- ``backend/app/api/team_secrets.py``
- ``backend/app/api/routes/team_secrets.py``
- ``backend/app/api/main.py``
- ``backend/app/api/deps.py``
- ``docker-compose.yml``
- ``backend/app/models.py``

## Expected Output

- ``backend/app/api/routes/workflows.py``
- ``backend/app/api/main.py``
- ``docker-compose.yml``
- ``backend/tests/api/test_workflow_run_routes.py``

## Verification

cd backend && POSTGRES_DB=perpetuity_app uv run pytest tests/api/test_workflow_run_routes.py -v && docker compose config --services | grep -q celery-worker

## Observability Impact

New backend INFO log: `workflow_run_dispatched run_id=<uuid> workflow_id=<uuid> trigger_type=button triggered_by_user_id=<uuid>`. Failure responses use `{detail: <discriminator>}` shape: `workflow_not_found`, `not_team_member`, `missing_required_field`. Future agents triaging a missing dashboard run can grep `docker compose logs backend | grep workflow_run_dispatched` to confirm dispatch happened, then `docker compose logs celery-worker | grep <run_id>` to see if pickup happened.
