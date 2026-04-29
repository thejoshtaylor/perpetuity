# S02: Dashboard direct AI buttons (Claude + Codex) — proves the AI executor end-to-end — UAT

**Milestone:** M005-sqm8et
**Written:** 2026-04-29T03:51:05.261Z

# M005-sqm8et / S02 — UAT

**Slice:** Dashboard direct AI buttons (Claude + Codex) — proves the AI executor end-to-end
**Demo:** Team user clicks 'Run Claude' button in the dashboard, fills 'List the files in this repo' into the modal prompt form, clicks Submit. Run page opens and shows step status flip pending → running → succeeded with full stdout from a real `claude -p '...'` call inside their `(user, team)` workspace container. Same flow for 'Run Codex'. Missing API key → step fails with `error_class='missing_team_secret'` and an inline error in the run UI.

## Preconditions

1. Compose stack is up: `docker compose up -d db redis orchestrator backend celery-worker` and all show healthy/Up.
2. Backend image carries alembic head ≥ `s12_seed_direct_workflows`. Verify with `docker compose run --rm backend alembic current` (must include the s12 revision id).
3. The workspace image is built with claude/codex CLIs pinned. Verify with `docker run --rm perpetuity/workspace:latest sh -c "script -q -e -c 'claude --version' /dev/null"` (prints `1.0.30 (Claude Code)`).
4. Two test users exist: `admin@example.com` (team admin) and `member@example.com` (team member).
5. The team `<TEAM_ID>` has both `claude_api_key` and `openai_api_key` secrets set via `PUT /api/v1/teams/{team_id}/secrets/{key}` (S01 surface). Confirm with `GET /api/v1/teams/{team_id}/secrets/claude_api_key` returning `has_value: true`.
6. The member user has a workspace session for this team. Provision via `POST /api/v1/sessions` with `{team_id: '<TEAM_ID>'}` if absent.

## Test Cases

### TC1 — Dashboard renders both AI buttons (member sees them)

1. Sign in as `member@example.com`.
2. Navigate to `/teams/<TEAM_ID>`.
3. **Expect:** Above the existing TeamSecretsPanel, a `DirectAIButtons` strip is visible with two buttons: 'Run Claude' and 'Run Codex'.
4. **Expect:** Both buttons are enabled (workflow list resolved). Inspecting the DOM shows `data-workflow-id` attributes carrying valid UUIDs.

### TC2 — Auto-seeded workflows visible

1. Sign in as `member@example.com`.
2. `GET /api/v1/teams/<TEAM_ID>/workflows` (via the dashboard's network panel or curl with cookie auth).
3. **Expect:** Response includes both `_direct_claude` and `_direct_codex` workflows; both have `system_owned: true`, `scope: 'user'`. Workflows are ordered alphabetically.

### TC3 — Run Claude happy path (end-to-end)

1. Continuing TC1: click 'Run Claude'.
2. **Expect:** A `PromptDialog` modal opens with a textarea, Submit, and Cancel buttons. Submit is disabled while the textarea is empty.
3. Type `List the files in this repo` into the textarea.
4. **Expect:** Submit becomes enabled.
5. Click Submit.
6. **Expect:** Browser navigates to `/runs/<run_id>` (a UUID). The dialog closes.
7. **Expect:** Within ~2 seconds, the run-detail page shows status pill flip from `pending` → `running` → `succeeded`. (The flip is best-effort — the test shim is fast; the authoritative observability contract is the `workflow_run_started` log discriminator, asserted in the e2e.)
8. **Expect:** One step row is visible with `action='claude'`, `exit_code=0`, `duration_ms > 0`. Expanding the collapsed `<details>` block reveals stdout containing `stub-claude-output for prompt: List the files in this repo` (test-shim signature; real Anthropic call reserved for S06).

### TC4 — Run Codex happy path (end-to-end)

1. Same as TC3 but click 'Run Codex' instead.
2. Type `summarize the README` into the modal textarea.
3. **Expect:** Run terminates `succeeded`; one step row with `action='codex'`, `exit_code=0`. Stdout contains `stub-codex-output for prompt: summarize the README`.

### TC5 — Missing API key surfaces as inline run failure

1. As team admin (sign in as `admin@example.com`), navigate to `/teams/<TEAM_ID>` and DELETE the `claude_api_key` secret via the TeamSecretsPanel (or `DELETE /api/v1/teams/<TEAM_ID>/secrets/claude_api_key`).
2. Sign in as `member@example.com`. Navigate to `/teams/<TEAM_ID>`. Click 'Run Claude'. Submit any non-empty prompt.
3. **Expect:** Browser navigates to `/runs/<run_id>`.
4. **Expect:** Run terminates with status `failed`. The run-level `error_class` badge shows `missing_team_secret` prominently. The single step row shows `error_class='missing_team_secret'`, `status='failed'`, and an `AlertCircle` icon.
5. **Cleanup:** Restore the `claude_api_key` secret for subsequent tests.

### TC6 — Empty prompt is rejected at the dashboard boundary

1. As `member@example.com` on `/teams/<TEAM_ID>`: click 'Run Claude'. Leave the textarea empty (or whitespace-only).
2. **Expect:** Submit stays disabled — modal cannot be submitted.
3. As an alternative path (curl/tooling): `POST /api/v1/workflows/<_direct_claude_workflow_id>/run` with `{trigger_payload: {prompt: ''}}`.
4. **Expect:** 400 `{detail: 'missing_required_field', field: 'prompt'}`. No `workflow_runs` row created.

### TC7 — Cross-team isolation enforced

1. Sign in as `member@example.com`. Pick a team `<OTHER_TEAM_ID>` they are NOT a member of.
2. `POST /api/v1/workflows/<workflow_id_belonging_to_other_team>/run` with a valid prompt.
3. **Expect:** 403 `{detail: 'not_team_member'}`. No row created.
4. `GET /api/v1/workflow_runs/<some_run_id_in_other_team>`.
5. **Expect:** 403 `{detail: 'not_team_member'}`. No row exposure.

### TC8 — Run-detail polling stops on terminal status

1. Trigger a Claude run as in TC3. Open browser devtools Network tab.
2. Observe that while the run is `pending` or `running`, `GET /api/v1/workflow_runs/<run_id>` is hit every ~1500ms.
3. **Expect:** Once the run flips to `succeeded` or `failed`, polling stops (no further requests to the run endpoint).

### TC9 — Run-detail 4xx lands on the error card without retry storm

1. As `member@example.com`: navigate directly to `/runs/00000000-0000-0000-0000-000000000000` (a non-existent UUID).
2. **Expect:** A 'Run not found' card is visible within one poll (~500ms after request resolves).
3. **Expect:** Devtools shows exactly one (1) `GET /api/v1/workflow_runs/...` request — no retry storm. (The `retry: false` override on 4xx is the contract; MEM435.)

### TC10 — Stdout is persisted (R018: forever-debuggable history)

1. Trigger a Claude run as in TC3. After it terminates `succeeded`:
2. Inspect the database: `psql perpetuity_app -c "SELECT id, status, exit_code, stdout, error_class FROM step_runs WHERE workflow_run_id = '<run_id>';"`.
3. **Expect:** One row with `stdout` populated with the test-shim output. `stderr` empty. `exit_code = 0`. `error_class` NULL.

### TC11 — Container deterministic across re-runs (idempotency)

1. As `member@example.com`: trigger 'Run Claude' twice in quick succession with different prompts.
2. **Expect:** Both runs land on the same workspace container (no per-run container churn). Verify with `docker ps --filter label=perpetuity.workspace.team_id=<TEAM_ID> --filter label=perpetuity.workspace.user_id=<MEMBER_USER_ID>` — exactly one workspace container.
3. **Expect:** Both runs succeed independently with each carrying its own prompt-echoing stdout.

### TC12 — Observability log discriminators emitted

1. Trigger a Claude run end-to-end as in TC3.
2. Inspect logs: `docker compose logs backend celery-worker orchestrator --since 1m`.
3. **Expect:** All 9 INFO discriminators present, in order: `workflow_run_dispatched`, `workflow_run_started`, `step_run_started`, `oneshot_exec_started`, `oneshot_exec_completed`, `step_run_succeeded`, `workflow_run_succeeded`. Plus structured key-value pairs (run_id, workflow_id, step_index, action, exit, duration_ms).
4. **Expect:** Zero occurrences of `sk-ant-` or `sk-` prefixes anywhere in the captured log stream. Zero occurrences of the prompt body verbatim.

### TC13 — Redaction sweep clean

1. Run `bash /Users/josh/code/perpetuity/scripts/redaction-sweep.sh`.
2. **Expect:** Exit code 0. All 7 PASS lines printed (no FAIL).

### TC14 — Cancel/Cleanup discipline

1. After all UAT cases: as team admin, restore the `claude_api_key` if previously deleted (TC5). Verify `GET /api/v1/teams/<TEAM_ID>/secrets/claude_api_key` returns `has_value: true`.
2. Optional: cleanup test runs with `psql perpetuity_app -c "DELETE FROM workflow_runs WHERE workflow_id IN (SELECT id FROM workflows WHERE team_id = '<TEAM_ID>' AND system_owned = true);"`. Cascading deletes the `step_runs`. The auto-seeded `_direct_claude`/`_direct_codex` workflow rows themselves are NOT deleted.

## Edge Cases / Negative Coverage

- **Decrypt failure during run:** if the Fernet key rotates between secret-set and run-trigger, the run terminates with `error_class='team_secret_decrypt_failed'` (S01 boundary). Verify by corrupting `value_encrypted` via psql then triggering a run.
- **Orchestrator down mid-run:** with backend + celery-worker up but `docker compose stop orchestrator`, trigger a run. Run terminates `failed` with `error_class='orchestrator_exec_failed'`. Restart orchestrator before continuing.
- **Celery broker down:** with `docker compose stop redis` momentarily, attempt a trigger. The route returns 503 `{detail: 'task_dispatch_failed'}`. The corresponding `workflow_runs` row is marked `failed` with `error_class='dispatch_failed'` BEFORE the 503 surfaces (MEM432) — verify via psql. Restart redis before continuing.
- **Concurrent triggers:** trigger 5 Claude runs in rapid succession. All 5 succeed; each gets its own `workflow_runs` row + `step_runs` row; same workspace container reused (TC11). Operational caps (`max_concurrent_runs`) are deferred to S05 — S02 has no cap, so this case is observational only.

## Out of Scope (deferred)

- Real Anthropic / real OpenAI API round-trip — reserved for S06 per D029. S02 uses the deterministic test-shim CLI exclusively.
- Multi-step workflows + `{prev.stdout}` substitution — S03.
- Round-robin scope dispatch + team_specific scope — S03.
- Webhook-triggered runs — S04.
- Run history list page + filters — S05.
- Admin manual trigger — S05.
- Worker crash recovery (`recover_orphan_runs` Beat task) — S05.
- Operational caps (`max_concurrent_runs`, `max_runs_per_hour`) — S05.
