---
id: T04
parent: S02
milestone: M005-sqm8et
key_files:
  - backend/app/api/routes/workflows.py
  - backend/app/api/main.py
  - backend/tests/api/test_workflow_run_routes.py
  - docker-compose.yml
key_decisions:
  - Persist workflow_runs + step_runs rows BEFORE Celery .delay() so a broker failure cannot leave a run in 'pending' with no task on the queue — failed dispatch stamps `error_class='dispatch_failed'` on the row before surfacing 503 (MEM432)
  - Translate `assert_caller_is_team_member`'s free-text 403/404 into the slice-plan's `{detail: '<discriminator>'}` shape at each route call site rather than forking the membership helper — keeps the security boundary single-sourced while routes own response shape
  - Lazy-import `run_workflow` inside the POST handler so the route module stays importable in environments without celery on the path
  - Tests reuse the auto-seeded `_direct_claude` row instead of inserting their own — the UNIQUE (team_id, name) constraint trips on duplicates because `create_team_with_admin` already seeds (MEM433)
  - Direct-AI `prompt` validation lives at the API boundary as `missing_required_field` 400 rather than letting the executor surface it as a step `error_class='cli_nonzero'` — malformed dispatch is a client error, not a CLI failure
duration: 
verification_result: passed
completed_at: 2026-04-29T03:03:42.007Z
blocker_discovered: false
---

# T04: Added workflow trigger + run-detail API router, wired it into the FastAPI app, and added a celery-worker compose service so the dashboard can dispatch workflow runs end-to-end

**Added workflow trigger + run-detail API router, wired it into the FastAPI app, and added a celery-worker compose service so the dashboard can dispatch workflow runs end-to-end**

## What Happened

Shipped the HTTP boundary and the worker compose service M005/S02 needs to drive a real `_direct_claude` / `_direct_codex` run from the dashboard.

`backend/app/api/routes/workflows.py` exposes three routes mounted under `/api/v1`:

- `POST /workflows/{workflow_id}/run` — looks up the Workflow, gates on team membership, validates the trigger payload (for `_direct_*` workflows the boundary requires a non-empty `prompt` string), inserts a `workflow_runs` row in `pending` plus one `step_runs` row per `WorkflowStep` (snapshot frozen at dispatch time per R018), commits, then calls `run_workflow.delay(str(run_id))`. Returns `{run_id, status: 'pending'}`. If `.delay()` raises (broker down) the run is marked `failed` with `error_class='dispatch_failed'` BEFORE the 503 surfaces — a row inspector always sees the breadcrumb and the run never sits in `pending` with no task on the queue.
- `GET /workflow_runs/{run_id}` — returns `WorkflowRunPublic` with ordered `step_runs`. Membership gate is on the run's team_id (it lives directly on the row, no join needed). 404 on missing.
- `GET /teams/{team_id}/workflows` — registry listing for the dashboard. Returns `WorkflowsPublic` (data + count) ordered by name so `_direct_claude` < `_direct_codex` < user workflows. Membership gate on the URL team_id.

Error shape is the locked discriminator pattern (`{detail: "<discriminator>", ...}`): `workflow_not_found`, `workflow_run_not_found`, `not_team_member`, `team_not_found`, `missing_required_field` (with `field`), `task_dispatch_failed`. The membership helper from `app.api.team_access` raises a free-text 403/404 that I translate at each call site so the dashboard can branch on a stable key without parsing prose.

Lazy import of `app.workflows.tasks.run_workflow` inside the dispatch handler keeps the route module importable in environments without the celery package on import path (e.g. lint-only contexts) — the worker process always has it.

Wired the router into `backend/app/api/main.py` alongside the rest.

`docker-compose.yml` gains a `celery-worker` service: same `${DOCKER_IMAGE_BACKEND}` image as `backend` so source is shared without bundling, command `celery -A app.workflows.tasks worker --loglevel=info --concurrency=4`, env mirrors the backend's runtime contract (POSTGRES_*, REDIS_*, ORCHESTRATOR_*, SYSTEM_SETTINGS_ENCRYPTION_KEY per D016 two-key shared-secret discipline). depends_on db (healthy) + redis (healthy) + prestart (completed). Per D005 the worker does NOT mount the docker socket — it talks to the orchestrator over HTTP, the orchestrator owns docker.

`backend/tests/api/test_workflow_run_routes.py` ships 21 integration tests against the real FastAPI app + Postgres via the existing `client` / `db` fixtures: auth gating (3), POST happy path with run/step_run row assertions + Celery dispatch assertion + observability INFO log assertion (1), POST user-workflow-no-prompt path (1), POST failure modes — workflow_not_found, not_team_member, missing_required_field for empty/whitespace prompt, bad-uuid 422, dispatch-503 with row marked failed (6), GET happy path with ordered step_runs (1), GET failure modes — unknown id, non-member, bad uuid, cascaded-delete (4), GET-list happy path with system-seed coexistence, cross-team isolation, unknown team, non-member, bad uuid (5).

Captured two memories: MEM432 documents the dispatch ordering invariant (DB writes before Celery .delay) and MEM433 documents the test gotcha that tests cannot insert a duplicate `_direct_claude` row because `create_team_with_admin` already seeds it.

One adaptation from the plan: the inlined plan said to use `assert_caller_is_team_member` directly. The shared helper raises `HTTPException(detail="...")` with free-text strings, but the slice plan locks structured `{detail: "<discriminator>"}` shapes. I catch and re-raise at each call site rather than fork a second helper — keeps the membership boundary single-sourced while the route owns the response shape.

## Verification

Ran the slice-plan verification command from `backend/`:

```
POSTGRES_DB=perpetuity_app uv run pytest tests/api/test_workflow_run_routes.py -v
  → 21 passed, 60 warnings in 1.09s

cd /Users/josh/code/perpetuity && docker compose config --services | grep -q celery-worker
  → exit 0 ("OK: celery-worker service registered")
```

Also ran a regression sweep across the slice's unit tests to confirm T03's contract still holds:

```
POSTGRES_DB=perpetuity_app uv run pytest tests/api/test_workflow_executor_ai.py tests/api/test_workflow_runner.py tests/api/test_workflows_seed.py tests/api/test_workflow_run_routes.py
  → 47 passed in 1.69s
```

Slice-level verification (S02-PLAN.md) — partial pass since T05 (frontend) and T06 (e2e) still have to land:

- ✅ Structured INFO log `workflow_run_dispatched run_id=<uuid> workflow_id=<uuid> trigger_type=button triggered_by_user_id=<uuid>` emitted with the contract-specified shape — verified by caplog assertion in `test_post_run_happy_path_inserts_pending_run_and_dispatches`.
- ✅ Prompt body NEVER appears in the dispatch log line — verified by `assert "List the files" not in log_text`.
- ✅ `error_class` discriminators usable on this surface: `dispatch_failed` (broker-down path, verified in `test_post_run_dispatch_failure_marks_run_failed_and_returns_503`). The slice's other discriminators (`missing_team_secret`, `orchestrator_exec_failed`, `cli_nonzero`) belong to T03's executor and are already proven there.
- ✅ `GET /api/v1/workflow_runs/{run_id}` returns the run with ordered `step_runs` (snapshot, status, exit_code, error_class, started_at, finished_at, duration_ms) — verified by `test_get_run_returns_run_with_ordered_step_runs`.
- ✅ Failure visibility: `step_runs.error_class` and `workflow_runs.error_class` discriminators round-trip through the API.
- ⏳ Dashboard 'Run Claude' / 'Run Codex' buttons — T05.
- ⏳ Slice e2e against live compose — T06.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && POSTGRES_DB=perpetuity_app uv run pytest tests/api/test_workflow_run_routes.py -v` | 0 | ✅ pass | 1090ms |
| 2 | `docker compose config --services | grep -q celery-worker` | 0 | ✅ pass | 350ms |
| 3 | `POSTGRES_DB=perpetuity_app uv run pytest tests/api/test_workflow_executor_ai.py tests/api/test_workflow_runner.py tests/api/test_workflows_seed.py tests/api/test_workflow_run_routes.py` | 0 | ✅ pass | 1690ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `backend/app/api/routes/workflows.py`
- `backend/app/api/main.py`
- `backend/tests/api/test_workflow_run_routes.py`
- `docker-compose.yml`
