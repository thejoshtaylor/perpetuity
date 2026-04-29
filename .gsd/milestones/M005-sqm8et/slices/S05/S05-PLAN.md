# S05: Run history UI + admin manual trigger + worker crash recovery + operational caps

**Goal:** Add run history list endpoint + admin manual trigger endpoint + operational caps (max_concurrent_runs / max_runs_per_hour with 429 enforcement + audit row) + worker crash recovery Beat task + celery-beat compose service + frontend run history list page with filters. The run detail drilldown page already exists from S03; this slice adds the list view and the operational safety layer around it.
**Demo:** Team user opens /runs, sees list of all team runs with filters (status, trigger type, time range), clicks a finished run, drills in to see full per-step stdout/stderr/exit/duration. Drill-down works for runs whose workflow definitions have since been edited or deleted (snapshot semantics confirmed). System admin opens /admin/workflows/{id}/run, manually triggers a run with synthetic trigger payload. Operational caps in action: `max_concurrent_runs=2` set; trigger 3 simultaneous runs; 2 succeed, the 3rd returns 429 with audit row. Restart `celery-worker` mid-run; `recover_orphan_runs` Beat task (every 10min) marks the orphan failed with `error_class='worker_crash'`.

## Must-Haves

- GET /api/v1/teams/{team_id}/runs returns paginated run list filterable by status, trigger_type, and time range; snapshot semantics preserved (deleted/edited workflow definitions do not break history)
- POST /api/v1/admin/workflows/{id}/trigger fires a synthetic admin_manual run with user-supplied payload; system-admin only; run appears in history
- max_concurrent_runs and max_runs_per_hour enforced at dispatch time; 3rd run when cap=2 returns HTTP 429 with {detail: 'workflow_cap_exceeded', cap_type: 'concurrent'} and writes an audit log row
- recover_orphan_runs Celery Beat task (every 10min) marks any WorkflowRun stuck in running for >15min as failed with error_class='worker_crash'; celery-beat service present in docker-compose.yml
- Frontend /runs page lists team runs with status/trigger_type/time-range filters and links to existing run detail drilldown
- test_m005_s05_run_history_admin_e2e.py: 5+ tests covering history list, admin trigger, cap enforcement (429 + audit row), orphan recovery

## Proof Level

- This slice proves: integration — all features verified by test_m005_s05_run_history_admin_e2e.py against live compose stack (skipped without stack, exit 0)

## Integration Closure

T01 list endpoint wired into T04 frontend. T01 cap fields wired into T02 enforcement logic. T03 Beat task registered in Celery app beat_schedule and exposed via celery-beat compose service. All new endpoints registered in backend/app/api/main.py router includes.

## Verification

- New structured log discriminators: workflow_cap_exceeded (INFO, fires before 429 with workflow_id, cap_type, current_count, limit), recover_orphan_runs_sweep (INFO, fires per Beat execution with orphan_count), workflow_run_orphan_recovered (INFO, fires per recovered run with run_id, stuck_since). Admin manual trigger writes trigger_type='admin_manual' to WorkflowRun for filter discrimination.

## Tasks

- [x] **T01: s15 migration + run history list endpoint + admin manual trigger endpoint** `est:90m`
  Add max_concurrent_runs and max_runs_per_hour columns to the workflows table (s15 Alembic migration), update Workflow SQLModel and Pydantic DTOs, then add two new endpoints: GET /api/v1/teams/{team_id}/runs (paginated, filtered by status/trigger_type/time range, accessible to team members) and POST /api/v1/admin/workflows/{id}/trigger (system-admin only, accepts synthetic trigger_payload JSON, enqueues an admin_manual WorkflowRun via existing workflow_dispatch.dispatch_workflow_run). Register both routes in backend/app/api/main.py.

Snapshot semantics for the history list: WorkflowRun.workflow_snapshot (set at run creation in S03) must be readable even when the parent workflow row is deleted. The list query must LEFT JOIN workflow on workflow_id so deleted workflows don't drop the run from history — or rely on the snapshot field directly.

Why/Files/Do/Verify/Done-when:
- Why: Backend foundation for history UI, admin ops, and cap enforcement (cap fields needed by T02).
- Files: backend/app/alembic/versions/s15_workflow_operational_caps.py, backend/app/models.py, backend/app/api/routes/workflows.py, backend/app/api/routes/workflows_crud.py, backend/app/api/main.py
- Do: Write s15 migration with nullable Integer columns max_concurrent_runs and max_runs_per_hour on workflows table + composite index (workflow_id, status, created_at DESC) on workflow_runs for cap queries. Update Workflow SQLModel + WorkflowPublic/WorkflowCreate/WorkflowUpdate DTOs. Add GET runs list endpoint with query params: status (optional enum), trigger_type (optional enum), after (optional ISO datetime), before (optional ISO datetime), limit (default 50, max 200), offset. Add POST admin trigger endpoint guarded by is_system_admin dependency. Wire both into main.py.
- Verify: uv run pytest backend/tests/unit/test_s15_migration.py -v; uv run pytest backend/tests/unit/test_run_history_endpoint.py -v; uv run pytest backend/tests/unit/test_admin_trigger_endpoint.py -v
- Done when: Migration applies cleanly (alembic upgrade head), list endpoint returns correct shape with all filter combos, admin trigger returns 202 with run_id, non-admin gets 403.
  - Files: `backend/app/alembic/versions/s15_workflow_operational_caps.py`, `backend/app/models.py`, `backend/app/api/routes/workflows.py`, `backend/app/api/routes/workflows_crud.py`, `backend/app/api/main.py`
  - Verify: cd backend && uv run pytest tests/unit/test_s15_migration.py tests/unit/test_run_history_endpoint.py tests/unit/test_admin_trigger_endpoint.py -v

- [x] **T02: Operational cap enforcement in workflow_dispatch.py** `est:60m`
  Implement max_concurrent_runs and max_runs_per_hour enforcement in backend/app/services/workflow_dispatch.py. Before enqueuing a run, check: (1) count of WorkflowRun rows for this workflow_id with status IN ('pending','running') — if >= max_concurrent_runs, raise WorkflowCapExceededError('concurrent'); (2) count of WorkflowRun rows for this workflow_id with created_at >= now()-1h — if >= max_runs_per_hour, raise WorkflowCapExceededError('hourly'). Both checks only fire when the cap field is non-None. The caller (dispatch route in workflows.py) catches WorkflowCapExceededError and returns HTTP 429 with body {detail: 'workflow_cap_exceeded', cap_type: 'concurrent'|'hourly', current_count: N, limit: M} and writes a structured log line workflow_cap_exceeded. An audit WorkflowRun row with status='rejected' and error_class='cap_exceeded' is inserted before raising so the rejection appears in run history.

The two count queries must be efficient — use the composite index added in T01's migration: (workflow_id, status, created_at DESC).

Why/Files/Do/Verify/Done-when:
- Why: Prevents runaway automated triggers from saturating containers and Celery queues; requirement R050 (implicit from milestone context).
- Files: backend/app/services/workflow_dispatch.py, backend/app/api/routes/workflows.py
- Do: Add WorkflowCapExceededError to workflow_dispatch.py. Add _check_workflow_caps(session, workflow) helper that queries both caps atomically under a SELECT FOR UPDATE SKIP LOCKED on the workflow row. Wire _check_workflow_caps into dispatch_workflow_run before target resolution. In workflows.py dispatch route, catch WorkflowCapExceededError → 429 + audit log. Add unit tests covering: cap=None skips check, concurrent cap hit, hourly cap hit, both caps None (no-op), audit row written on cap hit.
- Verify: cd backend && uv run pytest tests/unit/test_workflow_cap_enforcement.py -v
- Done when: 3 concurrent runs against a workflow with max_concurrent_runs=2: third returns 429 with correct body + audit row in DB.
  - Files: `backend/app/services/workflow_dispatch.py`, `backend/app/api/routes/workflows.py`, `backend/tests/unit/test_workflow_cap_enforcement.py`
  - Verify: cd backend && uv run pytest tests/unit/test_workflow_cap_enforcement.py -v

- [x] **T03: recover_orphan_runs Beat task + celery-beat compose service** `est:60m`
  Add the recover_orphan_runs Celery Beat task to backend/app/workflows/tasks.py and wire it into the Celery beat_schedule in backend/app/core/celery_app.py. Add the celery-beat service to docker-compose.yml.

Orphan definition: WorkflowRun with status='running' and last_heartbeat_at < now()-15min (or last_heartbeat_at IS NULL and created_at < now()-15min). These are runs whose Celery worker died mid-execution without updating status. The task: SELECT all orphan runs, for each: set status='failed', error_class='worker_crash', completed_at=now(), then for any step_runs in status='running' or 'pending' belonging to the orphan run: set status='failed', error_class='worker_crash'. Emit workflow_run_orphan_recovered (INFO) per run and recover_orphan_runs_sweep (INFO) summary with count.

Celery beat_schedule entry: run every 10 minutes. Beat service in docker-compose.yml: same image as backend, command `celery -A app.core.celery_app beat --loglevel=info --schedule=/tmp/celerybeat-schedule`. Must share the same env vars as celery-worker (POSTGRES_*, REDIS_URL, ORCHESTRATOR_API_KEY, etc.).

Why/Files/Do/Verify/Done-when:
- Why: Without orphan recovery, a celery-worker container crash leaves runs stuck in 'running' forever — the history list shows phantom running runs, and cap enforcement double-counts them.
- Files: backend/app/workflows/tasks.py, backend/app/core/celery_app.py, docker-compose.yml
- Do: Add recover_orphan_runs task. Add beat_schedule to celery_app.py Celery configuration. Add celery-beat service to docker-compose.yml. Write unit tests with a mocked DB session: no orphans → sweep log count=0, two orphans → both marked failed + step_runs updated + two recovered logs emitted.
- Verify: cd backend && uv run pytest tests/unit/test_recover_orphan_runs.py -v
- Done when: Unit tests pass; docker-compose.yml has celery-beat service; beat_schedule has recover_orphan_runs entry with 600s interval.
  - Files: `backend/app/workflows/tasks.py`, `backend/app/core/celery_app.py`, `docker-compose.yml`, `backend/tests/unit/test_recover_orphan_runs.py`
  - Verify: cd backend && uv run pytest tests/unit/test_recover_orphan_runs.py -v

- [ ] **T04: Frontend run history list page (/runs) with filters** `est:60m`
  Create frontend/src/routes/_layout/runs.tsx — the team run history list page. It fetches from GET /api/v1/teams/{teamId}/runs (added in T01), renders a table of runs with columns: run ID (truncated), workflow name (from snapshot, not live FK), trigger type badge, status badge, created_at relative timestamp, duration. Filter controls above the table: status multi-select (pending/running/succeeded/failed/cancelled/rejected), trigger_type multi-select, after/before date inputs. Each row links to the existing /runs/{runId} drilldown page. Pagination with limit=50 and offset-based 'Load more' button.

Use existing UI patterns from runs_.$runId.tsx and workflows.tsx for TanStack Query fetching, badge styling, and status color conventions. No new UI components needed — compose from existing ones.

Why/Files/Do/Verify/Done-when:
- Why: R018 mandates run history UI with drilldown. The drilldown exists; only the list view is missing.
- Files: frontend/src/routes/_layout/runs.tsx
- Do: Create the route file. Add a nav link to /runs in the sidebar/nav component (check existing nav for pattern). Add TanStack Router route registration if needed (check routes.ts or equivalent). Add API query function for the runs list endpoint. Implement filter state with URL search params (status, trigger_type, after, before) so filter state survives navigation. TypeScript build must be 0 errors.
- Verify: cd frontend && npm run build 2>&1 | tail -5 (0 errors, 0 type errors)
- Done when: TypeScript build passes; route is reachable; table renders with correct columns; filter params round-trip via URL.
  - Files: `frontend/src/routes/_layout/runs.tsx`
  - Verify: cd frontend && npm run build 2>&1 | tail -5

- [ ] **T05: E2e integration test suite (test_m005_s05_run_history_admin_e2e.py)** `est:60m`
  Write backend/tests/integration/test_m005_s05_run_history_admin_e2e.py covering the full S05 surface against a live compose stack. Follow the exact pattern established in test_m005_s03_workflow_run_engine_e2e.py and test_m005_s04_webhook_dispatch_e2e.py: pytestmark = pytest.mark.e2e, skip if PERPETUITY_E2E_STACK not set, use the shared conftest compose fixtures.

Required test functions (5 minimum):
1. test_run_history_list_with_filters — create 3 runs with different trigger_types + statuses, hit GET /teams/{id}/runs with each filter combination, verify correct subset returned, verify snapshot field present even for a workflow that was deleted after run creation.
2. test_admin_manual_trigger — system admin POSTs to /api/v1/admin/workflows/{id}/trigger with {"trigger_payload": {"note": "manual test"}}, verify 202 + run_id, verify run appears in history with trigger_type='admin_manual', verify non-admin gets 403.
3. test_concurrent_cap_enforcement — set max_concurrent_runs=2 on a workflow, fire 3 simultaneous dispatch requests, verify exactly 2 succeed (202) and 1 returns 429 with {detail: 'workflow_cap_exceeded', cap_type: 'concurrent'}, verify audit row with status='rejected' in run history.
4. test_hourly_cap_enforcement — set max_runs_per_hour=2 on a workflow, fire 3 sequential dispatch requests, verify 3rd returns 429 with cap_type='hourly'.
5. test_orphan_run_recovery — create a WorkflowRun row directly in DB with status='running' and last_heartbeat_at=now()-20min, call recover_orphan_runs() task directly (not via Beat), verify run transitions to status='failed' with error_class='worker_crash', verify step_runs in running/pending also marked failed.
6. test_discriminator_sweep — run all S05 discriminators (workflow_cap_exceeded, recover_orphan_runs_sweep, workflow_run_orphan_recovered, admin_manual_trigger_queued) through a combined log sweep; verify no sk-ant- or sk- prefix leakage.

Why/Files/Do/Verify/Done-when:
- Why: S05 has no value without exercisable proof. Mocked unit tests prove logic; e2e tests prove the wiring.
- Files: backend/tests/integration/test_m005_s05_run_history_admin_e2e.py
- Verify: cd backend && POSTGRES_DB=perpetuity_app uv run pytest -m e2e tests/integration/test_m005_s05_run_history_admin_e2e.py -v (6 skipped without live stack, exit 0)
- Done when: pytest collects 6 test functions, all skip cleanly without live stack, exit 0.
  - Files: `backend/tests/integration/test_m005_s05_run_history_admin_e2e.py`
  - Verify: cd backend && POSTGRES_DB=perpetuity_app uv run pytest -m e2e tests/integration/test_m005_s05_run_history_admin_e2e.py -v 2>&1 | tail -10

## Files Likely Touched

- backend/app/alembic/versions/s15_workflow_operational_caps.py
- backend/app/models.py
- backend/app/api/routes/workflows.py
- backend/app/api/routes/workflows_crud.py
- backend/app/api/main.py
- backend/app/services/workflow_dispatch.py
- backend/tests/unit/test_workflow_cap_enforcement.py
- backend/app/workflows/tasks.py
- backend/app/core/celery_app.py
- docker-compose.yml
- backend/tests/unit/test_recover_orphan_runs.py
- frontend/src/routes/_layout/runs.tsx
- backend/tests/integration/test_m005_s05_run_history_admin_e2e.py
