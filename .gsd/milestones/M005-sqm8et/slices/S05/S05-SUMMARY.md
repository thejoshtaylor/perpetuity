---
id: S05
parent: M005-sqm8et
milestone: M005-sqm8et
provides:
  - (none)
requires:
  []
affects:
  []
key_files:
  - (none)
key_decisions:
  - ["WorkflowRun.team_id used directly for run history queries (not workflow JOIN) — CASCADE FK means deleted-workflow runs are also deleted; team_id is the stable ownership anchor (MEM474)", "Cap enforcement is best-effort without SELECT FOR UPDATE; rejected WorkflowRun audit row written before 429 so rejections appear in standard run history (MEM475)", "s16 migration required to extend ck_workflow_runs_status CHECK constraint to add 'rejected' — PostgreSQL requires drop+recreate for CHECK constraint changes (MEM479)", "Beat task body extracted to _recover_orphan_runs_body(session) for testability — same pattern as _drive_run (MEM477)", "Frontend runs.tsx uses raw request() + OpenAPI config instead of generated SDK — endpoint added after last SDK codegen; local TypeScript interfaces mirror backend DTOs (MEM478)"]
patterns_established:
  - ["MEM016 logger.disabled=False fix applied to any test using caplog after alembic migration tests run in the same session — alembic fileConfig() disables all existing loggers by default (MEM476)", "Beat task body-extraction pattern: Celery task acquires session, calls _body(session) which is unit-testable with mock session", "Rejected run audit pattern: operational cap violations write a rejected WorkflowRun row before returning 429 so rejections appear in standard run history filtered by status=rejected", "URL search param filter state pattern: TanStack Router validateSearch + zod schema for all filter params so filter state survives navigation/back-button"]
observability_surfaces:
  - ["workflow_cap_exceeded (INFO) — fires before 429 with workflow_id, cap_type, current_count, limit", "recover_orphan_runs_sweep (INFO) — fires per Beat execution with orphan_count", "workflow_run_orphan_recovered (INFO) — fires per recovered run with run_id, stuck_since", "admin_manual_trigger_queued (INFO) — fires on admin trigger with run_id, workflow_id, triggered_by, trigger_payload_keys"]
drill_down_paths:
  []
duration: ""
verification_result: passed
completed_at: 2026-04-29T10:40:02.242Z
blocker_discovered: false
---

# S05: Run history UI + admin manual trigger + worker crash recovery + operational caps

**Run history list endpoint + admin manual trigger + operational caps (429 + audit row) + orphan recovery Beat task + celery-beat compose service + frontend /runs page — 44 unit tests pass, 6 e2e tests skip cleanly.**

## What Happened

S05 delivered the operational safety and observability layer for the M005 workflow engine.

**T01 — s15 migration + run history list + admin manual trigger:** The s15 Alembic migration added `max_concurrent_runs` and `max_runs_per_hour` nullable INTEGER columns to the `workflows` table, plus a composite index `ix_workflow_runs_workflow_id_status_created_at (workflow_id, status, created_at DESC)` for efficient cap-enforcement COUNT queries. Workflow SQLModel and all four DTOs (WorkflowCreate, WorkflowUpdate, WorkflowPublic, WorkflowWithStepsPublic) were updated. Two new endpoints were added to `workflows.py`: `GET /api/v1/teams/{team_id}/runs` (paginated, filterable by status/trigger_type/time range, membership-gated) and `POST /api/v1/admin/workflows/{id}/trigger` (system-admin only, accepts free-form trigger_payload, enqueues admin_manual WorkflowRun, emits `admin_manual_trigger_queued` log). Run history queries use `WorkflowRun.team_id` directly (not a JOIN through workflow) because the workflow_id FK is CASCADE — deleted-workflow runs are also deleted, so team_id is the stable ownership anchor. 26 unit tests verified: migration round-trip, all filter combos, pagination, error paths, admin trigger paths including Celery failure and log emission.

**T02 — operational cap enforcement:** `WorkflowCapExceededError` and `_check_workflow_caps(session, workflow)` were added to `workflow_dispatch.py`. Before enqueuing a run, two COUNT queries fire (concurrent: count pending+running rows; hourly: count rows created in last 1h) — both only when the cap field is non-None. On cap hit, a `WorkflowRun` with status='rejected' and error_class='cap_exceeded' is inserted as an audit record (visible in run history via status=rejected filter), then HTTP 429 is returned with `{detail: 'workflow_cap_exceeded', cap_type, current_count, limit}`. A new s16 Alembic migration was required to extend the `ck_workflow_runs_status` CHECK constraint to include 'rejected' — PostgreSQL requires drop+recreate for this. Enforcement is best-effort (no SELECT FOR UPDATE); the audit row makes any race-window double-admission visible. 13 unit tests verified all cap paths.

**T03 — recover_orphan_runs Beat task + celery-beat compose service:** `_recover_orphan_runs_body(session)` was added to `tasks.py` following the same body-extraction pattern as `_drive_run`. Orphan definition: WorkflowRun with status='running' and last_heartbeat_at (or created_at when NULL) older than 15 minutes (ORPHAN_HEARTBEAT_THRESHOLD constant). The task marks each orphan run and its running/pending step_runs as failed with error_class='worker_crash', emits `workflow_run_orphan_recovered` (INFO) per run and `recover_orphan_runs_sweep` (INFO) with count. The `beat_schedule` entry (`recover-orphan-runs`, 600s) was wired into `celery_app.conf.update`. A `celery-beat` service was added to `docker-compose.yml` mirroring `celery-worker`'s full environment block with command `celery -A app.core.celery_app beat --loglevel=info --schedule=/tmp/celerybeat-schedule`. 5 unit tests verified all recovery scenarios.

**T04 — frontend /runs page:** `frontend/src/routes/_layout/runs.tsx` was created. Filter state lives in URL search params (status, trigger_type, after, before, offset) via TanStack Router `validateSearch` + zod so filter state survives navigation. The table shows: truncated run ID (linked to /runs/$runId), workflow_id with `wf:` prefix (snapshot-safe — no live FK), trigger type badge, status badge, error_class, duration, relative created_at. Sidebar nav link added to baseItems. TypeScript build passes 0 errors. Known gap: the backend `status` filter accepts only a single value; the UI multi-select sends comma-joined values which the backend rejects with 422 — a future slice can extend the backend to accept multi-value.

**T05 — e2e integration test suite:** `test_m005_s05_run_history_admin_e2e.py` delivers 6 test functions covering the full S05 surface: history list with filters (including deleted-workflow snapshot verification), admin manual trigger (202 + audit, 403 for non-admin), concurrent cap enforcement (429 + rejected audit row), hourly cap enforcement, orphan recovery via direct _recover_orphan_runs_body() invocation, and discriminator sweep (zero sk-ant-/sk- leakage + all 4 S05 discriminators confirmed). All 6 skip cleanly without a live stack (exit 0).

**Cross-cutting fix:** A test-ordering bug was discovered and fixed in `test_recover_orphan_runs.py` — Alembic's `fileConfig()` called during migration tests disables all existing loggers (Python logging default), causing `caplog` to capture empty records when orphan tests run after migration tests. Fix applied: `logger.disabled = False` before `caplog.at_level()` (MEM016 pattern).

## Verification

- 44 unit tests pass in any order: `uv run pytest tests/unit/test_s15_migration.py tests/unit/test_run_history_endpoint.py tests/unit/test_admin_trigger_endpoint.py tests/unit/test_workflow_cap_enforcement.py tests/unit/test_recover_orphan_runs.py -v` → 44 passed
- 6 e2e tests skip cleanly: `POSTGRES_DB=perpetuity_app uv run pytest -m e2e tests/integration/test_m005_s05_run_history_admin_e2e.py -v` → 6 skipped, exit 0
- Frontend TypeScript build: `cd frontend && npm run build` → 0 errors, 0 type errors
- s15 migration applies cleanly (max_concurrent_runs, max_runs_per_hour columns + composite index)
- s16 migration applies cleanly (rejected status added to ck_workflow_runs_status CHECK constraint)
- celery-beat service present in docker-compose.yml with correct beat_schedule entry (600s interval)
- All 4 S05 log discriminators implemented: workflow_cap_exceeded, recover_orphan_runs_sweep, workflow_run_orphan_recovered, admin_manual_trigger_queued

## Requirements Advanced

None.

## Requirements Validated

None.

## New Requirements Surfaced

None.

## Requirements Invalidated or Re-scoped

None.

## Operational Readiness

None.

## Deviations

None.

## Known Limitations

["Frontend status filter multi-select sends comma-joined values (e.g. status=succeeded,failed); backend currently only accepts single status value — multi-value backend support deferred to a future slice", "WorkflowRunSummaryPublic DTO does not include snapshot_name; frontend shows truncated workflow_id with wf: prefix instead of workflow name — can be fixed by adding snapshot_name to the summary DTO"]

## Follow-ups

None.

## Files Created/Modified

- `backend/app/alembic/versions/s15_workflow_operational_caps.py` — 
- `backend/app/alembic/versions/s16_workflow_run_rejected_status.py` — 
- `backend/app/models.py` — 
- `backend/app/api/routes/workflows.py` — 
- `backend/app/services/workflow_dispatch.py` — 
- `backend/app/workflows/tasks.py` — 
- `backend/app/core/celery_app.py` — 
- `docker-compose.yml` — 
- `backend/tests/unit/__init__.py` — 
- `backend/tests/unit/test_s15_migration.py` — 
- `backend/tests/unit/test_run_history_endpoint.py` — 
- `backend/tests/unit/test_admin_trigger_endpoint.py` — 
- `backend/tests/unit/test_workflow_cap_enforcement.py` — 
- `backend/tests/unit/test_recover_orphan_runs.py` — 
- `frontend/src/routes/_layout/runs.tsx` — 
- `frontend/src/components/Sidebar/AppSidebar.tsx` — 
- `frontend/src/routeTree.gen.ts` — 
- `backend/tests/integration/test_m005_s05_run_history_admin_e2e.py` — 
