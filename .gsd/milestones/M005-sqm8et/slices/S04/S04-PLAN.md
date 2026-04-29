# S04: Webhook → workflow dispatch + push rule executors

**Goal:** Replace the M004 no-op `dispatch_github_event` stub with real webhook→workflow dispatch: evaluate per-project push rules (mode='rule' fnmatch + mode='manual_workflow' Celery enqueue), extend the orchestrator's auto_push.py for mode='rule' branch pattern matching, add a webhook_delivery_id idempotency column to workflow_runs, and cover the full dispatch path with an e2e integration test.
**Demo:** Team admin sets project push rule to `mode='manual_workflow'` with workflow 'ci-on-pr'. External collaborator opens a PR on the connected repo. Webhook delivered → HMAC verifies (M004) → `dispatch_github_event` resolves the matching workflow → Celery enqueues a run targeting team-mirror → workflow runs `[claude -p 'review this diff: {event.pull_request.diff_url}' (target=team_mirror)]` and the step record + run record show in the dashboard within seconds. Separately: team admin sets another project's push rule to `mode='rule'` with `branch_pattern='feature/*'`; pushing `feature/foo` triggers auto-push, pushing `main` does not (logged `auto_push_skipped reason=branch_pattern_no_match`). Duplicate webhook delivery (same delivery_id) does NOT double-trigger.

## Must-Haves

- 1. PR webhook delivered → HMAC verified (existing) → dispatch_github_event resolves installation → finds project push rule → mode='manual_workflow' enqueues WorkflowRun(trigger_type='webhook', trigger_payload=webhook body) idempotent on delivery_id; run appears in dashboard within seconds.
- 2. mode='rule' push rule with branch_pattern='feature/*': pushing feature/foo triggers auto-push callback to orchestrator; pushing main logs auto_push_skipped reason=branch_pattern_no_match.
- 3. Duplicate webhook delivery (same delivery_id) does NOT insert a second WorkflowRun (s14 unique index blocks it).
- 4. 7 e2e tests in test_m005_s04_webhook_dispatch_e2e.py collect and pass (or skip cleanly without live stack).

## Proof Level

- This slice proves: Integration: 7 e2e tests against a compose stack with mock-github sidecar (respx for GitHub API calls). All dispatch paths exercised: mode=auto passthrough (not in scope), mode=rule match, mode=rule no-match, mode=manual_workflow enqueue, duplicate delivery_id dedup, missing installation graceful skip.

## Integration Closure

dispatch_github_event now crosses three runtime boundaries: (1) backend DB read (installation → projects → push rules), (2) backend → orchestrator HTTP for mode=rule auto-push trigger, (3) backend DB write + Celery enqueue for mode=manual_workflow. The s14 migration's unique index is the only new schema surface. No frontend changes needed — run shows up via existing /runs UI from S03.

## Verification

- New discriminators added:
- webhook_dispatched delivery_id=X event_type=Y dispatch_status=dispatched (replaces noop)
- webhook_dispatch_no_installation (WARN) — delivery_id has no matching installation
- webhook_dispatch_push_rule_evaluated (INFO) — fires per push rule evaluated: project_id, mode, outcome
- auto_push_skipped project_id=X reason=branch_pattern_no_match (INFO) — new reason added in T02
- webhook_run_enqueued (INFO) — fires when WorkflowRun inserted + task enqueued: workflow_id, run_id, delivery_id

## Tasks

- [x] **T01: s14 migration + dispatch_github_event implementation** `est:3h`
  Two things in one task because they're tightly coupled — the migration adds the webhook_delivery_id idempotency column that the dispatch function needs to write.

**Migration (s14):** Add `webhook_delivery_id VARCHAR(64) UNIQUE NULLABLE` to `workflow_runs`. This column is set when `trigger_type='webhook'`; NULL for all other trigger types. The UNIQUE constraint (partial or full — full is simpler) ensures a given delivery_id can only produce one run, regardless of how many times dispatch is called.

**dispatch_github_event body:** Update the function signature to accept `session: Session` (non-optional), update the call site in `github_webhooks.py` to pass `session`. Then implement:
1. Extract `installation_id` from payload — for `push` events it's `payload['installation']['id']`; for `pull_request` events same path. Log and return if not present (`webhook_dispatch_no_installation` WARN).
2. Query all `Project` rows matching `installation_id`.
3. For each project, load its `ProjectPushRule` (if any). Skip projects with no push rule or `mode='auto'` (mode=auto is handled by the post-receive hook, not by webhook dispatch).
4. **mode='rule':** Extract `ref` from payload (e.g. `payload.get('ref', '')` for push events — format is `refs/heads/feature/foo`). Strip `refs/heads/` prefix to get bare branch name. Apply `fnmatch.fnmatch(branch, push_rule.branch_pattern)`. If match: POST to `{ORCHESTRATOR_BASE_URL}/v1/projects/{project_id}/auto-push-callback` with `X-Orchestrator-Key` header; log `webhook_dispatch_push_rule_evaluated mode=rule outcome=auto_push_triggered`. If no match: log `auto_push_skipped project_id=X reason=branch_pattern_no_match ref=Y pattern=Z`; log `webhook_dispatch_push_rule_evaluated mode=rule outcome=branch_pattern_no_match`.
5. **mode='manual_workflow':** Resolve `workflow_id` — the `ProjectPushRule.workflow_id` column stores the workflow UUID as a string (max 255). Parse it as UUID and load the `Workflow` row; skip with WARN if not found. Call `resolve_target_user` from `app.services.workflow_dispatch` to pick the target user (respects scope + round_robin_cursor). Insert `WorkflowRun(trigger_type='webhook', trigger_payload=payload, webhook_delivery_id=delivery_id)` via `INSERT ... ON CONFLICT (webhook_delivery_id) DO NOTHING` — use raw SQL or `try/except IntegrityError`. If a row was inserted (not a duplicate), enqueue `run_workflow.delay(str(run_id))`. Log `webhook_run_enqueued` on insert, `webhook_dispatch_delivery_id_duplicate` on skip.

**Update dispatch_status on github_webhook_events:** After dispatch completes, update the `dispatch_status` column on the `github_webhook_events` row from `'noop'` to `'dispatched'` (or `'no_match'` if no rules fired).

**Note:** `dispatch_github_event` is called synchronously in the webhook route. The DB session is available. The orchestrator call for mode=rule must be async or use `httpx` with `anyio.to_thread.run_sync` — check existing patterns. Looking at executors in S03, they use `httpx.AsyncClient`. Since the webhook route is async FastAPI, we can make `dispatch_github_event` async and `await` the orchestrator call.

**Assumption:** `ProjectPushRule.workflow_id` stores a UUID string (not a GitHub Actions workflow_id number) for `mode='manual_workflow'` rows created via the S03 CRUD UI. The field is VARCHAR(255) and the CRUD UI allows the user to pick any existing workflow — so it stores the Workflow.id UUID as a string.
  - Files: `backend/app/alembic/versions/s14_webhook_delivery_id.py`, `backend/app/models.py`, `backend/app/services/dispatch.py`, `backend/app/api/routes/github_webhooks.py`
  - Verify: Run: cd backend && uv run alembic upgrade head && uv run pytest tests/unit/test_s14_migration.py -v (migration applies without error). Run: uv run pytest tests/unit/test_dispatch_github_event.py -v (unit tests: mode=rule match, mode=rule no-match, mode=manual_workflow enqueue, duplicate delivery_id, missing installation). All pass.

- [ ] **T02: Orchestrator auto_push.py — mode='rule' branch fnmatch executor** `est:1.5h`
  Extend `run_auto_push` in `orchestrator/orchestrator/auto_push.py` to handle `mode='rule'` in addition to the existing `mode='auto'`.

Currently, line 320 checks `if mode != 'auto': return {'result': 'skipped_rule_changed'}`. This must change.

**New logic:**
1. Accept `ref: str | None = None` as an optional keyword argument to `run_auto_push`. When called for a webhook-triggered mode=rule dispatch, the backend passes the push ref from the webhook payload.
2. If `mode == 'auto'`: existing path, unchanged.
3. If `mode == 'rule'`:
   a. Require `branch_pattern` (loaded from DB via `_read_push_rule_mode_auto_push` or inline query). If no `branch_pattern` in DB, log `auto_push_skipped project_id=X reason=rule_no_branch_pattern` and return `{'result': 'skipped_rule_no_branch_pattern'}`.
   b. Extract branch name from `ref` by stripping `refs/heads/` prefix. If `ref` is None or doesn't start with `refs/heads/`, log `auto_push_skipped project_id=X reason=ref_not_branch` and return `{'result': 'skipped_ref_not_branch'}`.
   c. Apply `fnmatch.fnmatch(branch, branch_pattern)`. If no match: log `auto_push_skipped project_id=X reason=branch_pattern_no_match ref=Y pattern=Z` and return `{'result': 'skipped_branch_pattern_no_match'}`.
   d. If match: proceed with the existing auto-push flow (token mint → find mirror → git push). The execution path from 'auto_push_started' onward is identical — reuse it.
4. If `mode == 'manual_workflow'`: return `{'result': 'skipped_rule_manual_workflow'}` — these are handled at the backend layer, not here.
5. If mode is anything else: keep existing `{'result': 'skipped_rule_changed'}` return.

**Update the auto-push-callback route** in `orchestrator/orchestrator/routes_projects.py` to accept an optional JSON body with `{"ref": "refs/heads/feature/foo"}` and pass it through to `run_auto_push`. The post-receive hook doesn't send a body (existing callers), so the body must be optional. Add a small Pydantic model `AutoPushCallbackBody(ref: str | None = None)`.

**Backward compat:** `run_auto_push(docker, pool, project_id=...)` callers (post-receive hook via orchestrator route) pass no `ref` → defaults to None → existing mode=auto path is unaffected.

**New result values to add to the result dict union in the route response:** `skipped_rule_no_branch_pattern`, `skipped_ref_not_branch`, `skipped_branch_pattern_no_match`, `skipped_rule_manual_workflow`.
  - Files: `orchestrator/orchestrator/auto_push.py`, `orchestrator/orchestrator/routes_projects.py`
  - Verify: Run: cd orchestrator && python -m pytest tests/unit/test_auto_push_mode_rule.py -v (unit tests: mode=rule match executes push, mode=rule no-match returns skipped, mode=rule no branch_pattern returns skipped, mode=manual_workflow returns skipped, mode=auto unchanged). All pass.

- [ ] **T03: E2e integration test suite for webhook dispatch** `est:2h`
  Write `backend/tests/integration/test_m005_s04_webhook_dispatch_e2e.py` covering the full S04 surface against a live compose stack.

Follow the S02/S03 e2e test pattern: `pytest.mark.e2e`, `@pytest.fixture(scope='module')` for compose stack, respx for mocking external GitHub API calls, log accumulator + discriminator sweep.

**Test functions (7):**

1. `test_webhook_pr_manual_workflow_push_rule_dispatches_run` — Create a project with push rule `mode='manual_workflow'` + `workflow_id=<uuid of a test workflow with claude step>`. POST a synthetic `pull_request` webhook event (opened) with matching `installation.id`. Assert: (a) `WorkflowRun` row created with `trigger_type='webhook'`, `trigger_payload` contains the PR payload, `webhook_delivery_id` matches the delivery_id header. (b) Celery picks up and transitions run to running/succeeded. (c) `webhook_run_enqueued` discriminator in logs.

2. `test_webhook_duplicate_delivery_id_no_double_trigger` — POST the same webhook payload twice with the same `X-GitHub-Delivery` header value. Assert: only ONE `WorkflowRun` row exists with that `webhook_delivery_id`. Second POST returns 200 (route still returns ok) but no second run is created.

3. `test_webhook_push_rule_mode_rule_branch_match_triggers_auto_push` — Create a project with push rule `mode='rule'` + `branch_pattern='feature/*'`. POST a `push` webhook event with `ref=refs/heads/feature/test-branch`. Assert: orchestrator auto-push-callback was called for that project_id (use respx to mock orchestrator, or check the log discriminator `webhook_dispatch_push_rule_evaluated mode=rule outcome=auto_push_triggered`).

4. `test_webhook_push_rule_mode_rule_branch_no_match_skips` — Same setup but push `ref=refs/heads/main`. Assert: no `WorkflowRun` created, log contains `auto_push_skipped reason=branch_pattern_no_match`.

5. `test_webhook_no_installation_graceful_skip` — POST webhook event with no `installation` key in payload. Assert: no WorkflowRun created, log contains `webhook_dispatch_no_installation`, route returns 200.

6. `test_webhook_run_target_is_team_mirror` — For `manual_workflow` push rule where the workflow step has `target_container='team_mirror'`: verify the WorkflowRun's `trigger_payload` contains the PR payload and the step snapshot shows `target_container='team_mirror'` after execution.

7. `test_discriminator_sweep` — POST two webhook events (one manual_workflow, one push/rule-match). Collect all logs. Assert: (a) `webhook_dispatched dispatch_status=dispatched` appears for both; (b) `webhook_run_enqueued` appears for manual_workflow event; (c) `webhook_dispatch_push_rule_evaluated` appears for push event; (d) no `sk-ant-` or `sk-` substrings in any log line.

**Note on orchestrator mocking:** For tests 3 and 4, mock the orchestrator auto-push-callback HTTP call via respx (pointing at the test orchestrator service's URL). The orchestrator itself doesn't need to actually perform a git push in tests — just return `{"result": "ok"}`.

**Fixture requirements:** Requires `team`, `project`, `push_rule`, `workflow` (with at least one claude step), `team_mirror_container` (mocked). Follow the same `shim_inject` + `log_accumulator` pattern established in S02/S03.
  - Files: `backend/tests/integration/test_m005_s04_webhook_dispatch_e2e.py`
  - Verify: cd backend && POSTGRES_DB=perpetuity_app uv run pytest -m e2e tests/integration/test_m005_s04_webhook_dispatch_e2e.py -v → 7 tests collected; without live compose stack all 7 skip (exit 0); with live stack all 7 pass.

## Files Likely Touched

- backend/app/alembic/versions/s14_webhook_delivery_id.py
- backend/app/models.py
- backend/app/services/dispatch.py
- backend/app/api/routes/github_webhooks.py
- orchestrator/orchestrator/auto_push.py
- orchestrator/orchestrator/routes_projects.py
- backend/tests/integration/test_m005_s04_webhook_dispatch_e2e.py
