---
phase: M005-sqm8et
phase_name: AI Integrations + Workflows
project: perpetuity
generated: "2026-04-29T11:30:00Z"
counts:
  decisions: 8
  lessons: 10
  patterns: 12
  surprises: 4
missing_artifacts: []
---

### Decisions

- **util-linux script(1) TTY wrapper chosen over BSD form.** `script -q -e -c '<cmd>' /dev/null` is the correct invocation on Ubuntu 24.04; the BSD form `script -q /dev/null sh -c '<body>'` errors with "unexpected number of arguments". The `-e`/`--return` flag is critical: without it script exits 0 regardless of child exit code, silently masking CLI failures. Image-build TTY smoke checks (`claude --version` and `codex --version` through the wrapper) assert this at build time so regressions fail the build, not the runtime.
  Source: S02-SUMMARY.md/Deviations

- **Env-only secret passing for AI executor.** cmd argv carries only `[claude|codex, -p, $PROMPT, --dangerously-skip-permissions]`; API keys and sensitive prompt content flow through the pydantic-validated env dict. `/bin/sh` invoked by `script -c` expands bare `"$NAME"` references against the env. Secrets never appear in cmd list, process table, or container exec inspect output.
  Source: S02-SUMMARY.md/Key decisions

- **Postgres as Celery source-of-truth (no result backend).** workflow_runs.status is the authoritative run state. Celery result backend was explicitly rejected to avoid split-brain between two systems. All state transitions are DB writes; the polling API reads the DB directly.
  Source: S02-SUMMARY.md/Key decisions

- **Pre-create-pending-then-update-in-place lifecycle for workflow step runs.** API trigger writes workflow_runs + step_runs rows in pending state before Celery dispatch so GET /workflow_runs/{id} returns the full step list immediately. Worker UPDATEs the existing pending row to running/succeeded/failed rather than INSERTing a new one. Fallback INSERT covers older runs.
  Source: S02-SUMMARY.md/Key decisions

- **Webhook idempotency via DB UNIQUE constraint + IntegrityError catch.** webhook_delivery_id UNIQUE NULLABLE (PostgreSQL NULL semantics handle non-webhook rows naturally — no partial index needed). Application-level pre-check rejected: it has a TOCTOU race. IntegrityError catch on INSERT is the correct idempotency boundary.
  Source: S04-SUMMARY.md/Key decisions

- **Substitution engine uses str.replace chains, not str.format.** User prompts containing literal `{` characters would trip KeyError with str.format. str.replace chains are safe for user-controlled template substitution at the AI executor and step substitution boundary.
  Source: S03-SUMMARY.md/Key decisions

- **Snapshots store fully resolved config post-substitution.** step_runs.snapshot captures the WorkflowStep contents AFTER all {form.field}/{prev.stdout}/{trigger.*} substitutions are resolved, not the template. This satisfies R018 forever-debuggable history: run drill-down shows exactly what was sent to the container, even if the workflow definition has since been edited.
  Source: S03-SUMMARY.md/Key decisions

- **S06 as dedicated acceptance slice with no product code.** Separating real-API acceptance (S06) from the last code-shipping slice (S05) gives the milestone a clean closure boundary: S01-S05 prove themselves with respx-mocked APIs and test-shim CLIs; S06 proves the assembled system against real Anthropic + OpenAI + GitHub. This matches the UAT-complete contract and avoids amplifying real-API cost N-fold across every slice's e2e.
  Source: M005-sqm8et-ROADMAP.md/D029

---

### Lessons

- **util-linux script(1) `-e` flag is mandatory for exit-code propagation.** Without `-e`/`--return`, `script` always exits 0 regardless of what the child process did. This causes CLI failures (non-zero claude/codex exit) to masquerade as success — the step record shows exit_code=0 even if the CLI errored. The integration test that asserts non-zero exit caught this; unit tests with mocked execs cannot catch it.
  Source: S02-SUMMARY.md/Deviations

- **UNIQUE(workflow_run_id, step_index) violation from pre-create + INSERT collision.** The original plan had the Celery worker INSERT a new running row. The API trigger was pre-creating pending rows for GET readiness. Both paths write to the same UNIQUE column pair. The collision only surfaces in e2e with a live Postgres — unit tests mock the DB and miss it. Fix: worker looks up the existing pending row and UPDATEs it in place (MEM436).
  Source: S02-SUMMARY.md/What Happened

- **JSONB server_default in Alembic requires sa.text() wrapping.** Bare Python string `'{}'` gets double-escaped by SQLAlchemy's literal processor. The migration applies successfully but the column gets the string literal `'{}'` instead of an actual JSONB value, causing type errors at insert time. Use `sa.text("'{}'::jsonb")` always (MEM461).
  Source: S03-SUMMARY.md/What Happened

- **PostgreSQL CHECK constraint changes require DROP + ADD CONSTRAINT.** ALTER TABLE ... CHECK ... cannot modify an existing constraint — it only adds a new one that the old values may not satisfy. Adding 'rejected' to ck_workflow_runs_status required a separate migration with drop + recreate in a single transaction (MEM479).
  Source: S05-SUMMARY.md/What Happened

- **Alembic fileConfig() disables all existing loggers.** Python logging's `disable_existing_loggers=True` default means any test using `caplog` that runs after a migration test in the same pytest session captures empty records. Fix: `logger.disabled = False` before `caplog.at_level()` in the affected test (MEM016/MEM476).
  Source: S05-SUMMARY.md/What Happened

- **GSD verification runner splits `&&`-chained commands naively.** `cd backend && pytest ...` is split into two separate process invocations; the `cd` is a no-op for the next process. Slice plan verify commands must use absolute paths or `bash -c "..."` invocation. This caused the auto-fix retry signal on S01, S02, and S03 slice closes.
  Source: S02-SUMMARY.md/Known Limitations

- **In-container test-shim CLI for slice e2e is both cheaper and more honest than HTTP mocking.** Dropping a deterministic shell script at `/usr/local/bin/{claude,codex}` via `docker exec` exercises the full Celery → orchestrator HTTP → docker exec → script(1) chain. The shim reads API keys from env and fails if empty, so the env-injection wiring is genuinely proven. HTTP-boundary mocking (respx) cannot catch TTY or env-injection failures.
  Source: S02-SUMMARY.md/Patterns established

- **Synthesized HMAC-signed webhook delivery for deterministic e2e.** Waiting for real GitHub App delivery introduces external timing unpredictability (GitHub may batch or delay). Synthesizing the delivery as a POST with a computed X-Hub-Signature-256 header makes the test deterministic and avoids external webhook registration. The acceptance criterion (run exists with trigger_type='webhook') is still fully met.
  Source: S06-SUMMARY.md/Key decisions

- **Round-robin scope assertion should target DB target_user_id values, not container isolation.** Workspace containers are team-scoped (one container per team, not per user), so distinct per-user containers cannot be asserted at the acceptance test level. The distribution logic is proven by inspecting target_user_id on each WorkflowRun row — which is the correct observable for round-robin scope dispatch.
  Source: S06-SUMMARY.md/Known Limitations

- **Ephemeral worker + ephemeral orchestrator conftest fixtures are mandatory for Celery e2e.** The compose celery-worker inherits `POSTGRES_DB=app` from .env; the CRM-contaminated 'app' DB breaks alembic prestart. A sibling backend:latest container running `celery worker` on the compose network with POSTGRES_DB=perpetuity_app + a sibling orchestrator:latest with --network-alias orchestrator override are required for M005 integration tests. Pattern reusable for any future milestone that adds Celery tasks (MEM437).
  Source: S02-SUMMARY.md/What Happened

---

### Patterns

- **Pre-create-pending-then-update-in-place for trigger→worker pipelines.** API trigger writes all expected rows in pending state in one transaction, then dispatches to Celery. Worker finds the existing pending row and UPDATEs it to running/succeeded/failed. Fallback INSERT covers re-queued older runs that lack the pre-create. Avoids UNIQUE constraint violations when a worker races the pre-create. Reusable for any future trigger→worker pattern with a known step list at trigger time.
  Source: S02-SUMMARY.md/Patterns established

- **Persist-then-dispatch ordering invariant.** DB writes MUST happen BEFORE any external dispatch (Celery .delay(), HTTP outbound, etc.). On dispatch failure, stamp the row with a discriminator error_class BEFORE surfacing the error. A row inspector always sees a breadcrumb even when the broker is down (MEM432). Reusable for any workflow dispatch, admin trigger, or webhook re-dispatch path.
  Source: S02-SUMMARY.md/Patterns established

- **Deterministic UUID5 session/container addressing.** `uuid5(NAMESPACE, f'{user_id}:{team_id}:{run_id}')` produces a stable identifier so Celery double-deliveries and dispatcher retries land on the same backing workspace container. provision_container is already idempotent on (user, team) labels (MEM429). Reusable for any future executor pattern that needs a stable workspace per dispatch unit.
  Source: S02-SUMMARY.md/Patterns established

- **Env-only secret + sensitive-payload passing to container exec.** cmd argv carries only the command name and public arguments; bare `"$NAME"` references in the command string are expanded by /bin/sh against the pydantic-validated env dict that carries the actual secrets. Secrets never appear in cmd list, process table, or container exec inspect. Applicable to any future executor that takes user-controlled or sensitive input.
  Source: S02-SUMMARY.md/Patterns established

- **Image-build TTY smoke check for CLI version assertions.** Dockerfile smoke checks (`script -q -e -c '<cli> --version' /dev/null`) assert TTY discipline and CLI version at build time so regressions fail the build, not the runtime. Reusable for any workspace-image change that pins a CLI tool.
  Source: S02-SUMMARY.md/Patterns established

- **Free-text VARCHAR error_class discriminator (not CHECK/enum).** error_class stored as VARCHAR(64) lets future slices add new discriminators without an ALTER TABLE. Constraint: documentation in slice plan + test assertions are the only source of truth for valid values. Observability dashboards must accept new values gracefully without hardcoded allowlists.
  Source: S02-SUMMARY.md/Patterns established

- **Snapshot-at-dispatch JSONB column (R018 forever-debuggable history).** step_runs.snapshot freezes the WorkflowStep contents at trigger time post-substitution so historical runs survive workflow CRUD. UI drill-down works for runs whose workflow definitions have since been edited or deleted. Pattern: whenever a run depends on a definition that can mutate, snapshot at dispatch time.
  Source: S02-SUMMARY.md/Patterns established

- **System workflow auto-seed in both alembic backfill and runtime team-create.** Seed payload duplicated between the alembic data migration (covers existing teams) and the runtime team-create helper (covers new teams). Alembic should not import from the app package. UNIQUE(team_id, name) makes the runtime seed idempotent on re-run (MEM428).
  Source: S02-SUMMARY.md/Patterns established

- **Ephemeral sibling worker + orchestrator conftest fixtures for Celery e2e.** Boot a sibling backend:latest container as a celery worker on the compose network with the correct DATABASE_URL; mask the compose orchestrator with a sibling carrying the e2e DATABASE_URL via --network-alias override. Mirrors M002/S05 ephemeral-orchestrator pattern (MEM193). Reusable for any future milestone that adds Celery tasks or orchestrator-dependent e2e.
  Source: S02-SUMMARY.md/Patterns established

- **Two-fixture autouse skip-guard for real-API acceptance tests.** Fixture 1: probe backend:latest for the expected alembic revision (skip with `docker compose build backend` instruction on miss). Fixture 2: check required env vars (ANTHROPIC_API_KEY_M005_ACCEPTANCE, OPENAI_API_KEY_M005_ACCEPTANCE, GITHUB_TEST_ORG_PAT) present. Each test shows individually SKIPPED with a clear reason. Standard CI behavior: acceptance suite runs only when a human supplies real credentials.
  Source: S06-SUMMARY.md/Key decisions

- **Atomic round-robin cursor increment via UPDATE...RETURNING.** Round-robin target selection uses `UPDATE workflows SET round_robin_cursor = round_robin_cursor + 1 RETURNING round_robin_cursor` to avoid read-modify-write race conditions under concurrent dispatch. Cursor is BIGINT not INT to survive long-lived teams with many dispatches (MEM466).
  Source: S03-SUMMARY.md/Key decisions

- **Rejected-run audit row before 429 for operational cap violations.** When max_concurrent_runs or max_runs_per_hour is hit, write a WorkflowRun with status='rejected' and error_class='cap_exceeded' as an audit record before returning 429. Cap violations appear in standard run history via status=rejected filter. Enforcement is best-effort (no SELECT FOR UPDATE); the audit row makes any race-window double-admission visible (MEM475).
  Source: S05-SUMMARY.md/Patterns established

---

### Surprises

- **util-linux script(1) and BSD script have incompatible CLI forms.** The M005 vision and original plan documents said wrap as BSD `script -q /dev/null sh -c '<body>'`. Ubuntu 24.04 ships util-linux script, which uses `script [opts] [file]` — the BSD form errors with "script: unexpected number of arguments". The `-e` flag for exit-code propagation is also a util-linux addition not present in BSD. This was only caught when the orchestrator integration test asserted on non-zero CLI exit codes.
  Source: S02-SUMMARY.md/Deviations

- **UNIQUE(workflow_run_id, step_index) violation from pre-create + worker INSERT collision.** The design had the worker INSERT a new row without knowing the API was pre-creating them. The constraint violation caused the worker to crash with error_class='worker_crash' rather than a structured step failure. Discovered only in the live e2e run — unit tests mocked the DB and could not catch the constraint.
  Source: S02-SUMMARY.md/What Happened

- **PostgreSQL requires DROP + ADD CONSTRAINT to add 'rejected' to an existing CHECK constraint.** The assumption was that ALTER TABLE ... ADD CHECK could extend an existing constraint. PostgreSQL's CHECK constraint semantics require the old constraint to be dropped and a new one created. This requires a separate migration (s16) and was not anticipated in the S05 plan.
  Source: S05-SUMMARY.md/What Happened

- **Round-robin acceptance cannot assert per-user container isolation because containers are team-scoped.** The M005 vision described round-robin scope distributing work across team members' workspaces. In the current architecture, workspace containers are one-per-team (not one-per-user-per-team), so "distributing to member workspaces" means tracking target_user_id on the run record, not container-level isolation. The acceptance test was relaxed to assert target_user_id-set on all 4 runs rather than distinct containers — which is the correct observable for the current architecture.
  Source: S06-SUMMARY.md/Known Limitations
