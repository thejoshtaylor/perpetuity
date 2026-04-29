---
id: S04
parent: M005-sqm8et
milestone: M005-sqm8et
provides:
  - ["dispatch_github_event live — M004 no-op stub replaced with full webhook→workflow dispatch", "webhook_delivery_id idempotency column on workflow_runs (s14 migration)", "mode=rule branch fnmatch dispatch in orchestrator auto_push.py", "mode=manual_workflow WorkflowRun enqueue with Celery", "7-test e2e suite for webhook dispatch paths (skip-clean without live stack)"]
requires:
  - slice: S03
    provides: WorkflowRun + StepRun schema + run_workflow Celery task
  - slice: M004
    provides: github_webhook_events table + HMAC verification + dispatch_github_event stub
affects:
  []
key_files:
  - (none)
key_decisions:
  - ["Full UNIQUE constraint on webhook_delivery_id (not partial index) — PostgreSQL NULL semantics handle non-webhook rows naturally", "uuid.UUID(int=0) as sentinel triggering_user_id for webhook-triggered runs — preserves NOT NULL FK constraint without making column nullable", "Synchronous httpx.Client for orchestrator mode=rule callback — single blocking call in async FastAPI is acceptable, avoids anyio thread bridging", "Custom _LogCollector fixture resets logger.disabled=False after alembic fileConfig's disable_existing_loggers=True reset", "mode='manual_workflow' is first-class dispatch result in orchestrator (skipped_rule_manual_workflow) — not a fallthrough to skipped_rule_changed", "webhook_secret_fixture is function-scoped (not module-scoped) in e2e tests — conftest backend_url is function-scoped, module-scoped dependent causes ScopeMismatch", "Installation + project rows seeded via raw psql in e2e tests — API enforces real GitHub App handshake which cannot be satisfied in e2e isolation"]
patterns_established:
  - ["Webhook idempotency via DB UNIQUE constraint + IntegrityError catch — cleaner than application-level pre-check which has a TOCTOU race", "Discriminator-first e2e assertions for orchestrator HTTP calls — assert on log discriminators rather than orchestrator-side behavior when the HTTP call may fail in test environments", "AutoPushCallbackBody optional body pattern — new callers send JSON body; legacy no-body callers unaffected by defaulting body to AutoPushCallbackBody()"]
observability_surfaces:
  - ["webhook_dispatched delivery_id=X event_type=Y dispatch_status=dispatched|no_match", "webhook_dispatch_no_installation (WARN) — delivery_id has no matching installation", "webhook_dispatch_push_rule_evaluated (INFO) — fires per rule: project_id, mode, outcome", "auto_push_skipped project_id=X reason=branch_pattern_no_match ref=Y pattern=Z (INFO)", "webhook_run_enqueued workflow_id=X run_id=Y delivery_id=Z (INFO)", "webhook_dispatch_delivery_id_duplicate delivery_id=X (INFO) — duplicate skipped"]
drill_down_paths:
  []
duration: ""
verification_result: passed
completed_at: 2026-04-29T09:59:54.608Z
blocker_discovered: false
---

# S04: Webhook → workflow dispatch + push rule executors

**Replaced M004's dispatch_github_event no-op stub with full webhook→workflow dispatch: mode='rule' fnmatch branch gating, mode='manual_workflow' Celery enqueue with delivery_id idempotency, and 7-test e2e suite.**

## What Happened

S04 wired M004's inert webhook pipeline into live automation across three tasks.

**T01 — s14 migration + dispatch_github_event implementation**

The Alembic s14 migration added `webhook_delivery_id VARCHAR(64) UNIQUE NULLABLE` to `workflow_runs`. A full UNIQUE constraint (not a partial index) was chosen: PostgreSQL NULL semantics mean non-webhook rows (NULL delivery_id) never conflict, so no partial-index complexity was needed. The M004 no-op `dispatch_github_event` stub was replaced with a fully async function accepting a required `Session` parameter. The implementation: (1) extracts `installation_id` from webhook payload, logs `webhook_dispatch_no_installation` WARN and returns early if absent; (2) queries all `Project` rows matching the installation; (3) per project, loads `ProjectPushRule` — skipping projects with no rule or `mode='auto'`; (4) for `mode='rule'`, strips `refs/heads/` prefix, runs `fnmatch.fnmatch(branch, push_rule.branch_pattern)`, POSTs to the orchestrator auto-push-callback, and emits `webhook_dispatch_push_rule_evaluated`; (5) for `mode='manual_workflow'`, resolves target user via `workflow_dispatch.resolve_target_user`, inserts `WorkflowRun` with `trigger_type='webhook'` + `webhook_delivery_id`, catches `IntegrityError` for duplicate delivery dedup, enqueues `run_workflow.delay` on a fresh insert, and logs `webhook_run_enqueued` or `webhook_dispatch_delivery_id_duplicate`; (6) updates `dispatch_status` on `github_webhook_events` from `noop` to `dispatched` or `no_match`. The call site in `github_webhooks.py` was updated to `await dispatch_github_event(..., session=session)`. Pre-existing webhook route tests required two fixes: spy functions patching an async dispatch needed to become async coroutines, and one test asserting `dispatch_status=noop` needed updating to `dispatch_status=no_match`.

A notable design choice: `uuid.UUID(int=0)` is used as sentinel `triggering_user_id` for webhook-triggered runs — there is no authenticated user in the webhook dispatch context, and the NOT NULL FK constraint cannot be satisfied otherwise without making the column nullable.

Synchronous `httpx.Client` was used for the orchestrator callback (not `AsyncClient`) — the single blocking call in an async FastAPI route is acceptable given the low call frequency and eliminates the need for anyio thread bridging.

**T02 — Orchestrator mode='rule' fnmatch executor**

`run_auto_push` in `orchestrator/orchestrator/auto_push.py` gained a `ref: str | None = None` keyword argument and full mode='rule' dispatch. A new `_read_push_rule(pool, project_id)` helper returns both `mode` and `branch_pattern`; the old `_read_push_rule_mode` is kept as a thin alias preserving backward compat with the existing test harness (which seeds fake DB rows without `branch_pattern`). Mode dispatch was restructured: `mode='manual_workflow'` now returns `skipped_rule_manual_workflow` as a first-class result (previously fell through to `skipped_rule_changed`); `mode='rule'` evaluates branch_pattern presence, ref format, and fnmatch match before falling through to the shared mint→mirror→push path with `rule_mode_label='rule'`; `mode='auto'` is unchanged; anything else returns `skipped_rule_changed`. The auto-push-callback route in `routes_projects.py` was updated to accept `AutoPushCallbackBody(ref: str | None = None)` with a default of `AutoPushCallbackBody()` — legacy no-body POST callers (post-receive hook) are completely unaffected. The existing `test_rule_changed_skipped_no_exec` test was updated from `mode='manual_workflow'` to `mode='unknown_legacy_mode'` since `manual_workflow` is now first-class. Five new unit tests cover all new dispatch paths.

**T03 — E2e integration test suite**

`backend/tests/integration/test_m005_s04_webhook_dispatch_e2e.py` was created with 7 `pytest.mark.e2e` test functions following the S02/S03 pattern. Key design choices: (1) `webhook_secret_fixture` is function-scoped rather than module-scoped because conftest's `backend_url` is function-scoped — a module-scoped dependent fixture would cause ScopeMismatch at collect time; (2) GitHub app installation and project rows are seeded via raw psql rather than the API, which would require a real GitHub App install handshake; (3) for `mode='rule'` tests, the orchestrator HTTP call may fail with ConnectError in non-live environments, but `_handle_mode_rule` swallows HTTPError and the discriminator assertions (`webhook_dispatch_push_rule_evaluated`, `auto_push_skipped`) fire regardless; (4) the `team_mirror` target test asserts `target_container='team_mirror'` via `workflow_steps.config` (always available) with a fallback to `step_runs.snapshot`; (5) all 7 tests skip cleanly (exit 0) without a live compose stack, satisfying the slice requirement.

## Verification

All slice verification checks passed:

1. **T01 unit + migration tests**: `uv run pytest tests/api/routes/test_github_webhooks.py tests/api/test_dispatch_github_event.py tests/migrations/test_s14_webhook_delivery_id_migration.py -q` → **20 passed, 3 warnings** (exit 0)

2. **T02 orchestrator unit tests**: `cd orchestrator && uv run pytest tests/unit/test_auto_push_mode_rule.py tests/unit/test_auto_push.py -q` → **19 passed** (exit 0)

3. **T03 e2e collection**: `POSTGRES_DB=perpetuity_app uv run pytest -m e2e tests/integration/test_m005_s04_webhook_dispatch_e2e.py --collect-only` → **7 tests collected** (exit 0)

4. **T03 e2e skip**: `POSTGRES_DB=perpetuity_app uv run pytest -m e2e tests/integration/test_m005_s04_webhook_dispatch_e2e.py -v` → **7 skipped, exit 0** (no live stack)

All key files confirmed present: `s14_webhook_delivery_id.py`, `dispatch.py`, `auto_push.py`, `routes_projects.py`, `test_m005_s04_webhook_dispatch_e2e.py`. All S04 log discriminators verified present in dispatch.py: `webhook_dispatched`, `webhook_dispatch_no_installation`, `webhook_dispatch_push_rule_evaluated`, `auto_push_skipped reason=branch_pattern_no_match`, `webhook_run_enqueued`, `webhook_dispatch_delivery_id_duplicate`.

## Requirements Advanced

- R011 — dispatch_github_event now live: PR and push webhook events trigger configured workflows via mode=manual_workflow and mode=rule push rules

## Requirements Validated

None.

## New Requirements Surfaced

None.

## Requirements Invalidated or Re-scoped

None.

## Operational Readiness

None.

## Deviations

["Pre-existing test_github_webhooks.py tests required two fixes: (1) sync spy functions patching an async dispatch needed to become async coroutines; (2) one test asserting dispatch_status=noop updated to dispatch_status=no_match reflecting real dispatch behavior", "test_rule_changed_skipped_no_exec in test_auto_push.py updated from mode='manual_workflow' to mode='unknown_legacy_mode' — manual_workflow is now a first-class dispatch result, not a fallthrough", "webhook_secret_fixture is function-scoped rather than the plan's implicit module-scoped pattern — required to avoid ScopeMismatch with conftest's function-scoped backend_url"]

## Known Limitations

["mode=rule orchestrator HTTP call uses synchronous httpx.Client — acceptable for current load but should be revisited if dispatch latency becomes a concern under high webhook volume", "uuid.UUID(int=0) sentinel triggering_user_id for webhook runs is not user-visible but could confuse audit queries — S05 or S06 should document this convention", "e2e tests 3+4 (mode=rule) assert on log discriminators rather than actual orchestrator behavior — full orchestrator-side verification requires a live stack (S06 acceptance)"]

## Follow-ups

None.

## Files Created/Modified

- `backend/app/alembic/versions/s14_webhook_delivery_id.py` — 
- `backend/app/models.py` — 
- `backend/app/services/dispatch.py` — 
- `backend/app/api/routes/github_webhooks.py` — 
- `backend/tests/api/test_dispatch_github_event.py` — 
- `backend/tests/migrations/test_s14_webhook_delivery_id_migration.py` — 
- `backend/tests/api/routes/test_github_webhooks.py` — 
- `orchestrator/orchestrator/auto_push.py` — 
- `orchestrator/orchestrator/routes_projects.py` — 
- `orchestrator/tests/unit/test_auto_push_mode_rule.py` — 
- `orchestrator/tests/unit/test_auto_push.py` — 
- `backend/tests/integration/test_m005_s04_webhook_dispatch_e2e.py` — 
