---
id: S03
parent: M005
milestone: M005
provides:
  - ["Workflow CRUD API (POST/GET/PUT/DELETE) with admin/member role gates", "Workflow cancellation endpoint (POST /workflow_runs/{run_id}/cancel)", "workflow_dispatch service with user/team/round_robin scope routing", "Frontend workflow list, editor, custom dashboard buttons, and run cancel button"]
requires:
  - slice: S02
    provides: workflow_runs + step_runs schema and run engine
affects:
  - ["S04 — adds webhook/scheduled triggers and team_mirror executor on top of this CRUD surface"]
key_files:
  - ["backend/app/api/routes/workflows_crud.py", "backend/app/services/workflow_dispatch.py", "backend/app/alembic/versions/s13_workflow_crud_extensions.py", "backend/tests/api/test_workflow_crud_routes.py", "backend/tests/api/test_workflow_cancel_route.py", "backend/tests/api/test_workflow_dispatch_service.py", "backend/tests/migrations/test_s13_workflow_crud_extensions_migration.py", "frontend/src/routes/_layout/workflows.tsx", "frontend/src/routes/_layout/workflows_.$workflowId.tsx", "frontend/src/components/workflows/WorkflowEditor.tsx", "frontend/src/components/dashboard/CustomWorkflowButtons.tsx", "frontend/src/routes/_layout/runs_.$runId.tsx"]
key_decisions:
  - ["WorkflowRun cancellation uses terminal 'cancelled' directly — DB CHECK constraint only allows 5 values; no intermediate 'cancelling' state needed", "round_robin cursor uses raw UPDATE...RETURNING for atomicity under concurrent dispatch", "resolve_target_user accepts both string and WorkflowScope enum — guards against mixed comparison after SQLModel deserialization", "System_owned check precedes team-admin gate on PUT/DELETE so non-admins get structured 403 not auth confusion", "form_schema validation returns 400 with structured {detail, reason} not 422 — callers can distinguish schema errors from type errors", "TargetUserNoMembershipError carries workflow_id+target_user_id for structured 409 at API boundary"]
patterns_established:
  - ["CRUD routes with system_owned guard pattern: check system_owned before admin gate to give structured error", "Atomic step replacement: DELETE steps WHERE workflow_id + session.flush() + INSERT new steps in single transaction", "Dispatch service fallback chain: scope routing → membership validation → live-workspace filter → triggering user fallback with reason"]
observability_surfaces:
  - none
drill_down_paths:
  []
duration: ""
verification_result: passed
completed_at: 2026-04-29T07:53:32.592Z
blocker_discovered: false
---

# S03: Workflow CRUD API + Dispatch Service + Frontend Editor

**Full workflow CRUD API with role gates, scope-routing dispatch service, cancellation endpoint, and frontend editor/dashboard/cancel UI — all verified with 23 pytest + 20 Playwright tests.**

## What Happened

S03 delivered the complete workflow management surface on top of S02's run engine and schema foundation. Five tasks executed cleanly, with all output files present from prior session work and verified passing.

**T01 — s13 Alembic migration:** Added form_schema (JSONB NOT NULL DEFAULT '{}'::jsonb), target_user_id (UUID FK→user SET NULL), round_robin_cursor (BIGINT DEFAULT 0) to workflows; target_container (VARCHAR CHECK constraint) to workflow_steps; cancelled_by_user_id + cancelled_at to workflow_runs. 9 migration tests pass covering defaults, JSONB storage, FK SET NULL behavior, CHECK constraint, BIGINT range, and downgrade. JSONB server_default uses sa.text("'{}'::jsonb") per MEM448/453 to avoid PostgreSQL double-quoting. Migration files live at backend/app/alembic/versions/ and tests at backend/tests/migrations/.

**T02 — Model + DTO extensions:** All required fields and Pydantic DTOs (WorkflowCreate, WorkflowUpdate, WorkflowWithStepsPublic, WorkflowFormFieldKind, WorkflowFormField, WorkflowFormSchema, WorkflowScope) confirmed in backend/app/models.py. No separate schemas.py exists in this project — models and DTOs are co-located (MEM452). Import verification passed for all 6 field attributes.

**T03 — workflow_dispatch service:** resolve_target_user(session, workflow, triggering_user_id) → (target_user_id, fallback_reason|None) handles three scopes: user (always triggering user), team_specific (validates target_user_id is non-null and still a member, raises TargetUserNoMembershipError otherwise), round_robin (atomic UPDATE...RETURNING cursor increment, walks member list filtered by workspace_volume within 7-day window, falls back to triggering user with reason="no_live_workspace"). Accepts both string and WorkflowScope enum forms. 11 pytest tests cover all paths including cursor wrap, all-offline fallback, and partial-offline skip.

**T04 — CRUD API routes + cancellation:** workflows_crud.py implements 5 routes: POST /teams/{team_id}/workflows (admin-gated, validates form_schema, rejects _direct_ prefix with 403), GET /workflows/{id} (member-gated, returns WorkflowWithStepsPublic), PUT /workflows/{id} (admin-gated, rejects system_owned=True, atomic step replacement via DELETE-then-INSERT with flush), DELETE /workflows/{id} (admin-gated, rejects system_owned=True, CASCADE to steps/runs/step_runs), POST /workflow_runs/{run_id}/cancel (member-gated, transitions pending/running→cancelled, stamps audit fields, returns 202). Router mounted in backend/app/api/main.py. 23 pytest tests pass covering all role gates, system_owned rejection, form_schema validation, 404/409 error paths.

**T05 — Frontend CRUD UI:** TypeScript client regenerated from updated OpenAPI spec. /workflows list route with admin CRUD gates and member read view. /workflows/$workflowId editor route. WorkflowEditor, FormSchemaEditor, StepsEditor components. CustomWorkflowButtons on team dashboard (form-schema workflows open WorkflowFormDialog; others dispatch directly). Optimistic cancel button on runs/$runId detail page. routeTree.gen.ts regenerated. 20 Playwright tests pass across WorkflowsList, WorkflowEditor, CustomWorkflowButtons, RunCancelButton specs.

**Verification note:** The auto-fix trigger was caused by the gate runner invoking `python -m pytest tests/api/...` from the project root instead of `cd backend && python -m pytest tests/api/...`. The files exist and pass — this was a working-directory path issue (MEM449).

## Verification

- `cd backend && python -m pytest tests/migrations/test_s13_workflow_crud_extensions_migration.py -x -q` → 9 passed (T01)
- `cd backend && python -c 'from app.models import Workflow, WorkflowStep, WorkflowRun, WorkflowCreate, WorkflowWithStepsPublic, WorkflowFormFieldKind; ...'` → all imports OK, all 6 hasattr checks True (T02)
- `cd backend && python -m pytest tests/api/test_workflow_dispatch_service.py -x -q` → 11 passed (T03)
- `cd backend && python -m pytest tests/api/test_workflow_crud_routes.py tests/api/test_workflow_cancel_route.py -x -q` → 23 passed in 1.49s (T04)
- `cd frontend && bunx playwright test --project=chromium tests/routes/WorkflowsList.spec.ts tests/routes/WorkflowEditor.spec.ts tests/components/CustomWorkflowButtons.spec.ts tests/components/RunCancelButton.spec.ts` → 20 passed (T05)

## Requirements Advanced

- R019 — WorkflowScope enum (user/team/round_robin) implemented in models.py; resolve_target_user service handles all three scope variants with correct routing and fallback logic

## Requirements Validated

- R019 — 11 pytest tests in test_workflow_dispatch_service.py verify all scope variants including round_robin cursor, team membership validation, and offline fallback

## New Requirements Surfaced

None.

## Requirements Invalidated or Re-scoped

None.

## Operational Readiness

None.

## Deviations

["Task plans referenced backend/app/schemas.py which does not exist — all models and DTOs are co-located in backend/app/models.py", "Migration test files live in backend/tests/migrations/ not backend/tests/api/ — consistent with all other migration tests", "Auto-fix verification failure was a working-directory path issue (pytest run from project root instead of backend/) — no code changes were required"]

## Known Limitations

["Live end-to-end cancellation (container actually stopping) not tested — requires live worker runtime, deferred to integration testing", "round_robin atomicity under concurrent load proven by code pattern only — no concurrent load test", "Webhook/scheduled trigger dispatch is S04 scope"]

## Follow-ups

None.

## Files Created/Modified

None.
