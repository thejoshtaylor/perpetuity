# S05: Run history UI + admin manual trigger + worker crash recovery + operational caps — UAT

**Milestone:** M005-sqm8et
**Written:** 2026-04-29T10:40:02.243Z

# S05 UAT Script: Run History UI + Admin Manual Trigger + Worker Crash Recovery + Operational Caps

## Preconditions

- M005 backend running with all migrations applied through s16
- At least one team with at least one workflow created
- Two user accounts: team_admin (is_team_admin=True) and system_admin (is_superuser=True)
- celery-worker and celery-beat services running

---

## TC-S05-01: Run history list with filters

**Goal:** Verify GET /api/v1/teams/{team_id}/runs returns correct paginated, filtered results.

**Steps:**
1. Dispatch 3 workflow runs: 2 with trigger_type='button', 1 with trigger_type='admin_manual'
2. GET /api/v1/teams/{team_id}/runs (no filters) → expect all 3 in results, `total` >= 3
3. GET /api/v1/teams/{team_id}/runs?trigger_type=button → expect exactly the 2 button runs
4. GET /api/v1/teams/{team_id}/runs?trigger_type=admin_manual → expect exactly the 1 admin run
5. GET /api/v1/teams/{team_id}/runs?status=pending → expect only pending runs
6. GET /api/v1/teams/{team_id}/runs?after=<ISO datetime before runs> → all 3 appear
7. GET /api/v1/teams/{team_id}/runs?before=<ISO datetime before runs> → 0 results
8. GET /api/v1/teams/{team_id}/runs?limit=2&offset=0 → 2 results; GET with offset=2 → 1 result
9. Delete the workflow; GET /api/v1/teams/{team_id}/runs → runs still appear (team_id ownership)
10. Non-team-member GET → 403 not_team_member
11. GET for unknown team_id → 404 team_not_found
12. GET ?status=invalid_value → 422 validation error

**Expected outcomes:** Filters work correctly; snapshot semantics preserved after workflow deletion; auth gates enforced.

---

## TC-S05-02: Admin manual trigger

**Goal:** Verify POST /api/v1/admin/workflows/{id}/trigger fires a synthetic run visible in history.

**Steps:**
1. System admin POST /api/v1/admin/workflows/{id}/trigger with body `{"trigger_payload": {"note": "uat test"}}` → expect 202 with `{run_id, status: "pending"}`
2. GET /api/v1/teams/{team_id}/runs?trigger_type=admin_manual → run with returned run_id appears
3. Verify run has trigger_type='admin_manual' and trigger_payload contains "note"
4. Log sweep → `admin_manual_trigger_queued` discriminator present with run_id + workflow_id
5. Non-admin user POST same endpoint → 403
6. System admin POST with unknown workflow_id → 404

**Expected outcomes:** Admin trigger creates auditable run visible in history; non-admin blocked.

---

## TC-S05-03: Concurrent cap enforcement (429 + audit row)

**Goal:** Verify max_concurrent_runs=2 blocks a 3rd simultaneous dispatch.

**Steps:**
1. PATCH workflow to set max_concurrent_runs=2
2. Seed 2 WorkflowRun rows in DB with status='running' for this workflow
3. POST /api/v1/workflows/{id}/run (3rd dispatch) → expect 429 with body `{detail: "workflow_cap_exceeded", cap_type: "concurrent", current_count: 2, limit: 2}`
4. GET /api/v1/teams/{team_id}/runs?status=rejected → audit row appears with error_class='cap_exceeded'
5. Log sweep → `workflow_cap_exceeded` discriminator with workflow_id + cap_type=concurrent
6. Remove the 2 seeded running rows; POST again → 202 (cap no longer exceeded)
7. PATCH workflow to set max_concurrent_runs=null; POST 3 simultaneous dispatches → all 202 (no cap)

**Expected outcomes:** 3rd run blocked with correct error shape; audit row visible; cap=null is no-op.

---

## TC-S05-04: Hourly cap enforcement

**Goal:** Verify max_runs_per_hour=2 blocks a 3rd dispatch within the rolling hour.

**Steps:**
1. PATCH workflow to set max_runs_per_hour=2
2. Seed 2 WorkflowRun rows with created_at=now()-30min (within the hour window)
3. POST /api/v1/workflows/{id}/run → expect 429 with cap_type='hourly'
4. Seed an additional row with created_at=now()-90min (outside window); POST again → still 429 (old run ignored)
5. Remove the 2 recent seeded rows; POST → 202

**Expected outcomes:** Hourly window is rolling 1h; old runs outside window are ignored; cap enforced correctly.

---

## TC-S05-05: Worker crash recovery (orphan runs)

**Goal:** Verify recover_orphan_runs marks stuck runs failed with error_class='worker_crash'.

**Steps:**
1. Insert WorkflowRun directly in DB: status='running', last_heartbeat_at=now()-20min, team_id=<valid team>
2. Insert 2 StepRun rows for that run: one status='running', one status='pending'
3. Insert 1 StepRun with status='succeeded' for the same run
4. Call _recover_orphan_runs_body() (or trigger via celery-beat after 10min wait in a live stack)
5. SELECT WorkflowRun → status='failed', error_class='worker_crash', finished_at is set
6. SELECT running/pending step_runs → both status='failed', error_class='worker_crash'
7. SELECT succeeded step_run → status unchanged ('succeeded')
8. Log sweep → `workflow_run_orphan_recovered` with run_id; `recover_orphan_runs_sweep` with orphan_count=1
9. Run with last_heartbeat_at=now()-5min (recent) → NOT recovered (not an orphan)
10. Run with last_heartbeat_at=NULL and created_at=now()-20min → IS recovered (uses created_at)

**Expected outcomes:** Orphans (stuck >15min) recovered; recent runs untouched; succeeded steps preserved.

---

## TC-S05-06: Frontend /runs page

**Goal:** Verify the /runs page renders correctly with filters and links.

**Steps:**
1. Navigate to /runs in the browser → run history table loads
2. Verify columns: run ID (truncated, linked), workflow_id (wf: prefix), trigger type badge, status badge, duration, relative timestamp
3. Sidebar nav shows "Run history" link pointing to /runs
4. Apply status filter (e.g. status=succeeded) → URL updates with ?status=succeeded; table filters
5. Back-button → filter state preserved in URL
6. Apply after/before date filters → results narrow correctly
7. Click "Load more" → offset increments, additional rows appear
8. Click a run row link → navigates to /runs/{runId} detail drilldown page
9. Navigate to /runs?status=succeeded&trigger_type=button directly → filter panel shows active filters even if toggle was off

**Expected outcomes:** Filters survive navigation via URL params; drilldown links work; sidebar nav present.

---

## TC-S05-07: celery-beat service present and wired

**Goal:** Verify docker-compose.yml has celery-beat and the beat_schedule is registered.

**Steps:**
1. `grep celery-beat docker-compose.yml` → service definition present
2. `grep ORCHESTRATOR_API_KEY docker-compose.yml` (in celery-beat section) → env var present
3. In running stack: `docker logs celery-beat` → shows beat scheduler started, `recover-orphan-runs` task scheduled every 600s
4. Wait 10+ minutes in a running stack → `recover_orphan_runs_sweep` log line appears with orphan_count=0

**Expected outcomes:** celery-beat service starts; recover_orphan_runs fires every 10 minutes.
