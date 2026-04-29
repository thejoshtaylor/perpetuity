---
estimated_steps: 11
estimated_files: 4
skills_used: []
---

# T01: s14 migration + dispatch_github_event implementation

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

## Inputs

- `backend/app/models.py`
- `backend/app/services/dispatch.py`
- `backend/app/api/routes/github_webhooks.py`
- `backend/app/services/workflow_dispatch.py`
- `backend/app/workflows/tasks.py`
- `backend/app/core/config.py`

## Expected Output

- `backend/app/alembic/versions/s14_webhook_delivery_id.py`
- `backend/app/services/dispatch.py`
- `backend/app/api/routes/github_webhooks.py`
- `backend/app/models.py`
- `backend/tests/unit/test_dispatch_github_event.py`
- `backend/tests/unit/test_s14_migration.py`

## Verification

Run: cd backend && uv run alembic upgrade head && uv run pytest tests/unit/test_s14_migration.py -v (migration applies without error). Run: uv run pytest tests/unit/test_dispatch_github_event.py -v (unit tests: mode=rule match, mode=rule no-match, mode=manual_workflow enqueue, duplicate delivery_id, missing installation). All pass.

## Observability Impact

New log discriminators: webhook_dispatched dispatch_status=dispatched (replaces noop), webhook_dispatch_no_installation (WARN), webhook_dispatch_push_rule_evaluated (INFO, fires per rule), auto_push_skipped reason=branch_pattern_no_match (INFO), webhook_run_enqueued (INFO), webhook_dispatch_delivery_id_duplicate (INFO). dispatch_status column on github_webhook_events updated from 'noop' to 'dispatched'/'no_match' after real dispatch.
