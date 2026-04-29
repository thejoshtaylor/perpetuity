# S04: Webhook → workflow dispatch + push rule executors — UAT

**Milestone:** M005-sqm8et
**Written:** 2026-04-29T09:59:54.608Z

# S04 UAT Script — Webhook → Workflow Dispatch + Push Rule Executors

## Preconditions

- Compose stack running with backend, orchestrator, celery-worker, postgres, redis
- Team admin user created and authenticated
- A workflow named 'ci-on-pr' with at least one step (`claude -p 'review diff: {event.pull_request.diff_url}'`, `target_container='team_mirror'`) exists and is linked to the team
- A second project with push rule `mode='rule'`, `branch_pattern='feature/*'` configured
- GitHub webhook secret configured; test webhook deliveries can be POSTed to `/api/v1/github/webhooks`

---

## Test Case 1: PR webhook → manual_workflow push rule → WorkflowRun created

**Preconditions:** Project has push rule `mode='manual_workflow'`, `workflow_id=<ci-on-pr UUID>`

**Steps:**
1. POST a synthetic `pull_request` event (action=opened) to `/api/v1/github/webhooks` with:
   - `X-GitHub-Event: pull_request`
   - `X-GitHub-Delivery: test-delivery-001`
   - `X-Hub-Signature-256: <valid HMAC>`
   - payload body containing `installation.id` matching the project's installation, plus a realistic PR object
2. Query `workflow_runs` where `webhook_delivery_id = 'test-delivery-001'`
3. Check the `/runs` dashboard

**Expected:**
- Route returns HTTP 200
- Exactly one `WorkflowRun` row exists with `trigger_type='webhook'`, `webhook_delivery_id='test-delivery-001'`, `trigger_payload` containing the PR payload
- Run appears in the `/runs` dashboard within a few seconds
- Log contains `webhook_run_enqueued workflow_id=... run_id=... delivery_id=test-delivery-001`
- Log contains `webhook_dispatched delivery_id=test-delivery-001 event_type=pull_request dispatch_status=dispatched`

---

## Test Case 2: Duplicate delivery_id does NOT double-trigger

**Preconditions:** Test Case 1 completed; delivery-id `test-delivery-001` already in DB

**Steps:**
1. POST the identical webhook payload again with the same `X-GitHub-Delivery: test-delivery-001` header

**Expected:**
- Route returns HTTP 200
- No second `WorkflowRun` row created — `SELECT COUNT(*) FROM workflow_runs WHERE webhook_delivery_id='test-delivery-001'` returns 1
- Log contains `webhook_dispatch_delivery_id_duplicate delivery_id=test-delivery-001`

---

## Test Case 3: mode='rule' with matching branch → auto-push triggered

**Preconditions:** Project push rule `mode='rule'`, `branch_pattern='feature/*'`

**Steps:**
1. POST a synthetic `push` event to `/api/v1/github/webhooks` with:
   - `X-GitHub-Event: push`
   - `X-GitHub-Delivery: test-delivery-rule-match`
   - payload containing `ref: 'refs/heads/feature/test-branch'` and matching `installation.id`

**Expected:**
- Log contains `webhook_dispatch_push_rule_evaluated project_id=... mode=rule outcome=auto_push_triggered`
- No `WorkflowRun` row created (mode=rule is handled at orchestrator layer, not workflow layer)
- Orchestrator auto-push-callback was called with `{"ref": "refs/heads/feature/test-branch"}`

---

## Test Case 4: mode='rule' with non-matching branch → skipped, no run

**Preconditions:** Same project as Test Case 3

**Steps:**
1. POST a synthetic `push` event with `ref: 'refs/heads/main'`

**Expected:**
- No `WorkflowRun` created
- Log contains `auto_push_skipped project_id=... reason=branch_pattern_no_match ref=refs/heads/main pattern=feature/*`
- Log contains `webhook_dispatch_push_rule_evaluated project_id=... mode=rule outcome=branch_pattern_no_match`

---

## Test Case 5: Webhook with no installation key → graceful skip, 200

**Steps:**
1. POST any webhook payload that omits the `installation` key entirely

**Expected:**
- Route returns HTTP 200 (no 500)
- No `WorkflowRun` created
- Log contains `webhook_dispatch_no_installation`

---

## Test Case 6: manual_workflow run targets team_mirror container

**Preconditions:** Workflow step has `target_container='team_mirror'`; manual_workflow push rule linked to this workflow

**Steps:**
1. POST a PR webhook (same as Test Case 1) for this project
2. Wait for Celery to process the run
3. Query `step_runs` for the resulting run, or `workflow_steps.config` for the workflow definition

**Expected:**
- `workflow_steps.config['target_container']` equals `'team_mirror'`
- `step_runs.snapshot['target_container']` equals `'team_mirror'` once Celery has processed
- Run record visible in `/runs` drill-down

---

## Test Case 7: Discriminator sweep — no secret leakage

**Steps:**
1. Trigger both a manual_workflow webhook (Test Case 1) and a mode=rule push webhook (Test Case 3)
2. Collect all application logs from the session

**Expected:**
- `webhook_dispatched dispatch_status=dispatched` appears for both events
- `webhook_run_enqueued` appears for the manual_workflow event
- `webhook_dispatch_push_rule_evaluated` appears for the push/rule event
- No `sk-ant-` or `sk-` substrings appear anywhere in the collected logs

---

## Edge Cases

- **mode='auto' push rule**: `dispatch_github_event` skips it entirely (handled by post-receive hook); no WorkflowRun created, no orchestrator call
- **workflow_id in push rule references deleted workflow**: dispatch logs WARN and skips — no crash, no orphan run
- **Celery worker restarted mid-dispatch**: run record created in DB before Celery enqueue; orphan recovery (S05) will handle it; delivery_id dedup prevents re-trigger on retry
