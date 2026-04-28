# M005-sqm8et: AI Integrations + Workflows — Context

**Gathered:** 2026-04-27
**Status:** Ready for planning

## Project Description

M005-sqm8et is the AI + workflows milestone of Perpetuity. It turns the team-collaboration loop M004 delivered (per-team GitHub App, projects materialized in user containers via two-hop clone, push-back rules stored but inert) into actual automation. Two threads land together because they unlock each other:

1. **Per-team AI credentials.** Each team stores its Claude API key and OpenAI API key encrypted at rest (Fernet-on-existing-`system_settings`-pattern from M004/S01, but team-scoped not system-scoped — new `team_secrets` table). Workflows and dashboard actions execute `claude` and `codex` CLIs inside the user's team workspace container with the TTY workaround (`script -q /dev/null …`) and `--dangerously-skip-permissions` for automated use. Buttons land first in the dashboard for direct invocation; workflow step types reference the same executor.
2. **The workflow trigger/step engine.** Celery-backed workflow runs with full step-level history, three trigger sources (dashboard button + optional form, GitHub webhook events, admin manual), three target scopes (user, team round-robin, team specific user), and run+step records that store config snapshot, status, stdout/stderr, duration, and exit code per step. The webhook → workflow path replaces M004's `dispatch_github_event` no-op stub. The push-back rules with `mode='rule'` and `mode='manual_workflow'` (stored inert by M004) become live.

The team-mirror container (M004/S03) doubles as the workflow execution host for steps that don't need the user's workspace — D022's "dual role" call cashes out here.

## Why This Milestone

R013–R020 have been on the roadmap since M001 discussion and were carried as "active, unmapped" through every prior milestone. The platform now has the infrastructure (M002 terminals, M003 verified, M004 GitHub + projects + mirror + auto-push) but no automation surface — users can shell into a workspace and `git pull`, but the dashboard's promised "click to run Claude on this branch" button and "PR opened → run lint workflow" auto-loop don't exist yet.

It needs to land **now** (before M006 PWA/notifications/voice) because:

- M004's webhook receiver landed `dispatch_github_event` as a no-op hook (R052) — every webhook delivery is currently persisted and dropped on the floor; M005 fills the body
- M004's `project_push_rules` stored `rule` and `manual_workflow` modes as inert rows (R051, D024) — schema-now / executors-deferred was the explicit phase boundary, M005 lights up the executors
- M006's notification center (R023, R024) is configured **per workflow + per event type** (success / failure / step completion) — the workflow event source must exist before the routing config is meaningful
- M006's voice (R025) and PWA (R021) are UX-layer features — they sit on top of forms and dashboards that M005's workflow trigger buttons populate

## User-Visible Outcome

### When this milestone is complete, the user can:

- **Team admin:** paste a Claude API key (`sk-ant-…`) and an OpenAI API key (`sk-…`) once into team settings; subsequent GETs show `has_value: true` with no value flowing back; rotation is paste-once-replace
- **Any team user:** click the dashboard's "Run Claude" button, fill the prompt form, and watch `claude -p '<prompt>'` execute inside their `(user, team)` workspace container with output streaming to a terminal panel — same for "Run Codex" against the OpenAI CLI
- **Any team user:** create a workflow definition with a trigger (dashboard button + optional form / GitHub event filter / admin manual) and an ordered list of steps (shell command / Claude prompt / Codex prompt / git command / future custom types); save it; see it appear in the dashboard's button row if it has a button trigger
- **Any team user:** click a configured workflow button, fill the form (if any), watch the run page show step-by-step progress in real time with stdout/stderr panels, exit codes, and durations; drill into a finished run and see the same record forever
- **Team admin:** configure a project's push-back rule to `mode='rule'` with a `branch_pattern` (e.g. `feature/*`), see auto-push fire only for matching branches; configure another project to `mode='manual_workflow'` with `workflow_id=<id>`, see push trigger that workflow instead of pushing to GitHub
- **External GitHub event:** push to a connected repo → webhook delivered → HMAC verified (M004) → `dispatch_github_event` resolves any workflow whose trigger filter matches (event_type, repo, branch) → Celery enqueues a run → dashboard shows it appear in real time
- **Team admin (operational):** view a workflow's run history with filters (status, trigger type, time range), drill into any failed step, see its stdout/stderr/exit code without re-running anything

### Entry point / environment

- Entry point: web UI for trigger buttons + workflow editor + run history; `POST /api/v1/workflows/{id}/run` for programmatic dashboard invocation; M004's `POST /api/v1/github/webhooks` for inbound event-driven triggers; `POST /api/v1/admin/workflows/{id}/run` for admin manual trigger
- Environment: full compose stack (Postgres + Redis + orchestrator + Docker daemon + new Celery worker service) + real Anthropic API + real OpenAI API for AI step execution (acceptance only; respx-mocked in fast tests)
- Live dependencies involved: real Anthropic API (`api.anthropic.com`), real OpenAI API (`api.openai.com`), real Docker daemon (workspace + mirror containers), real Postgres (new tables), real Redis (Celery broker + result backend)

## Completion Class

- **Contract complete means:** unit + migration tests for `team_secrets` (encrypted key storage + `has_value` semantics), workflow definition schema (workflows / workflow_steps / workflow_runs / step_runs), trigger filter matching, dispatch resolution, Celery task definitions, push-rule executor branch matching, AI CLI wrapper TTY-shaping; respx-mocked Anthropic + OpenAI APIs; in-process Celery eager mode for fast tests
- **Integration complete means:** dashboard button click → POST run → Celery task picks up → orchestrator acquires container (user workspace OR team-mirror per step config) → `claude` / `codex` / shell command executes inside container → step record persisted with stdout/stderr/exit/duration → run record updated → frontend stream shows real-time progression. Webhook → trigger-resolution → run dispatch path proven against a fixture event payload. Push rule executors (`rule` matches branch pattern, `manual_workflow` enqueues run instead of pushing) proven via fake mirror push.
- **Operational complete means:** run history survives Celery worker restart (state in Postgres, not worker memory); failed steps with non-zero exit codes mark the run as `failed` and never poison the queue; Anthropic / OpenAI API failures (401, 429, 5xx) surface as step `failed` with the API error class in stderr — never as a worker-level exception that requeues forever; long-running steps (e.g. `claude` thinking for 90s) don't time out the run beyond a configurable `step_timeout_seconds` per step type; round-robin team scope correctly cycles across members and falls back to triggering user if the chosen member has no live workspace; webhook → workflow dispatch is idempotent under GitHub's 24h delivery retry (delivery_id-keyed at the dispatch boundary, not just the receiver)
- **UAT complete means:** the four scenarios in "Final Integrated Acceptance" pass end-to-end against real Anthropic + OpenAI APIs and a real GitHub test org

## Final Integrated Acceptance

To call this milestone complete, we must prove the following end-to-end (cannot be simulated):

1. **Dashboard AI button:** team admin pastes Claude key in team settings → user clicks "Run Claude" on the dashboard → fills prompt form ("List the files in this repo") → Celery picks up → orchestrator execs `script -q /dev/null claude -p '...' --dangerously-skip-permissions` inside the user's workspace container (project from M004 already materialized) → real Anthropic response streams back → step record stores stdout, exit 0, duration; same flow for Codex against the OpenAI CLI
2. **Dashboard-button workflow with form input:** team user creates a workflow "lint and report", trigger=button, form field `branch:string`, steps = `[git checkout {branch}, npm install, npm run lint, claude -p "summarize lint output: {prev.stdout}"]` → user clicks the button → fills branch → run page shows each step turn green/red in real time → final Claude step gets the lint output piped from the prior step's `prev.stdout` variable → run record retains the chain forever
3. **GitHub event triggers workflow:** team admin sets project push rule to `manual_workflow` with workflow "ci-on-pr" → external collaborator opens a PR on the connected repo → GitHub webhook delivered → HMAC verifies → `dispatch_github_event` resolves the matching workflow → Celery enqueues run → workflow runs `[claude -p "review this diff: {event.pull_request.diff_url}"]` against the team-mirror container (no user workspace needed) → step record + run record show in dashboard within seconds
4. **Round-robin team scope + run history:** team workflow scoped to "round-robin" with two members → trigger 4 times → assert distribution lands ≥1 on each member → trigger again with one member offline (no live workspace) → assert fallback to triggering user → drill into any of the runs from history a day later, see full stdout/stderr/exit/duration

## Architectural Decisions

### Per-team AI credentials live in a new `team_secrets` table, not in `system_settings`

**Decision:** New `team_secrets` table: `team_id FK PK, key VARCHAR(64) PK, value_encrypted BYTEA NOT NULL, has_value BOOLEAN NOT NULL DEFAULT TRUE, sensitive BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ`. Composite PK on `(team_id, key)`. Encryption discipline mirrors M004/S01: same Fernet, same `SYSTEM_SETTINGS_ENCRYPTION_KEY`, same decrypt-only-at-call-site rule, same loud failure on `InvalidToken` (R053 contract reused). API: team-admin-only `PUT /api/v1/teams/{id}/secrets/{key}` (paste once, replaces), `GET /api/v1/teams/{id}/secrets/{key}` (returns `has_value` only — never the value), `DELETE /api/v1/teams/{id}/secrets/{key}`. Registered keys for M005: `claude_api_key`, `openai_api_key`. Validator registry per-key (sk-ant-/sk- prefix shape check at PUT time; bool/int/etc. extension postponed until needed).

**Rationale:** `system_settings` is system-scoped — one row per key for the whole installation. Team-scoped secrets need composite PK and team-FK + cascade-on-team-delete. Reusing the encryption discipline (Fernet, env-keyed, decrypt-at-call-site, 503-on-decrypt-failure) keeps one mental model across both tables. `team_secrets` is intentionally minimal — no `value JSONB` column for non-sensitive team data, because team-level non-sensitive config (workflow definitions, push rules) lives in proper typed tables, not key/value soup. M002/S03's "key/value with per-key validators" pattern was right for **system runtime knobs**; team data deserves real schemas.

**Alternatives Considered:**
- Add `team_id` column to `system_settings` — rejected: changes the whole admin surface, leaks team-scoped rows into the system-admin GET, breaks M002/S03's API contract
- New `secrets` service module separate from settings — rejected: doubles the encryption wiring (two encrypt/decrypt sites, two ERROR log paths, two redaction sweeps); same trust boundary, no isolation gain
- Store keys in env vars per team — rejected: requires redeploy on rotation; admins can't manage credentials without ops involvement; back-compat-broken for the multi-tenant aspiration

---

### Workflow definition is a normalized two-table schema, not a JSONB blob

**Decision:** New tables — `workflows(id UUID PK, team_id FK, name, description, trigger_type ENUM(button|webhook|manual), trigger_config JSONB, target_scope ENUM(user|team_round_robin|team_specific) NOT NULL, target_user_id FK NULL, form_schema JSONB NULL, created_by FK, created_at, updated_at)` and `workflow_steps(id UUID PK, workflow_id FK, ordinal INT, step_type ENUM(shell|claude|codex|git|http_request|future...), config JSONB, target_container ENUM(user_workspace|team_mirror) NOT NULL, step_timeout_seconds INT NOT NULL DEFAULT 300, created_at)` — UNIQUE (workflow_id, ordinal). The trigger filter (event_type, repo pattern, branch pattern) lives in `workflows.trigger_config` JSONB because filters are heterogeneous; the step list is properly relational because step ordering, target_container, and timeout are queried independently and have indexes/constraints worth honoring.

**Rationale:** Hybrid model — relational where the query patterns demand it (step ordering, per-step timeout config, "list all workflows on team X with shell steps"), JSONB where the shape is heterogeneous and read-as-a-whole (trigger filter config, form schema, per-step `config` like `{prompt: "..."}` for Claude vs `{command: ["git", "checkout"]}` for shell). Pure JSONB for the whole workflow rejected — every step list query becomes a JSON-path operation; ordinal uniqueness becomes app-level instead of DB-level. Pure relational for trigger filter rejected — would require a `workflow_trigger_filters` table with conditional columns per filter type (event_type, branch_pattern, etc.), schema becomes brittle as new trigger types land. Hybrid matches the actual access pattern.

**Alternatives Considered:**
- Single `workflows` table with `definition JSONB` — rejected: kills SQL-driven filters and constraints; harder admin tooling; per-step indexes impossible
- One table per step type (`shell_steps`, `claude_steps`, ...) — rejected: explosion of tables; common ops (run-execution, run-history) become N-table joins; new step types = new migrations
- Step list on `workflows.steps_json` plus normalized `workflow_runs` — rejected: coupling between "step config snapshot stored in run record" and "live step list" becomes confusing; storing the config snapshot at run time (see next decision) makes this moot

---

### Run records snapshot the workflow definition at trigger time

**Decision:** New tables — `workflow_runs(id UUID PK, workflow_id FK, team_id FK denormalized, trigger_type, trigger_data JSONB, target_user_id FK, status ENUM(pending|running|succeeded|failed|cancelled), workflow_snapshot JSONB NOT NULL, started_at, finished_at, duration_ms BIGINT, error_class VARCHAR(64) NULL, error_message TEXT NULL, created_at)` and `step_runs(id UUID PK, workflow_run_id FK, ordinal INT, step_id FK NULL — null if step deleted post-run, step_snapshot JSONB NOT NULL, status ENUM(pending|running|succeeded|failed|skipped), stdout TEXT NOT NULL DEFAULT '', stderr TEXT NOT NULL DEFAULT '', exit_code INT NULL, container_id VARCHAR(64) NULL — labels which container ran it, started_at, finished_at, duration_ms)`. UNIQUE (workflow_run_id, ordinal). `workflow_snapshot` and `step_snapshot` are deep copies of the workflow + step rows captured at run-trigger time; subsequent edits to the workflow definition never alter past run records.

**Rationale:** R018 requires "config snapshot" per step run for forever-debuggable history. Without snapshotting, editing a workflow re-interprets what past runs did — a debugging anti-pattern. JSONB snapshot is fine because it's read whole when drilling into a run. `step_id` retained as nullable FK so live workflow definition can backlink to its history while allowing definition edits. `target_user_id` recorded on the run (not just inferred from scope+round-robin state) so round-robin history is auditable.

**Alternatives Considered:**
- No snapshot, infer from current workflow definition — rejected: violates R018 forever-debuggable contract
- Snapshot only the specific fields used by the executor — rejected: half-measure that breaks down when the executor adds a field; full snapshot is cheap (workflows are small)
- Separate `workflow_versions` table with monotonic versioning — rejected: heavyweight; users don't think in versions; snapshot-per-run captures the same property at run granularity

---

### Workflow execution is Celery + Redis broker, not a custom asyncio dispatcher

**Decision:** New compose service `celery-worker` running `celery -A app.workflows.tasks worker --loglevel=info --concurrency=4`. Celery uses Redis as both broker and result backend (already wired for M002 session registry + M004 installation token cache — no new broker dep). One Celery task per workflow run (`run_workflow(run_id)` opens the Postgres run row, iterates steps sequentially, persists per-step records). Step-level retries handled inside the run task (no per-step Celery task — keeps the run a single unit of failure). Cancellation via `workflow_runs.status='cancelled'` checked between steps. `--concurrency=4` per worker; per-step container acquisition is the natural bottleneck so concurrency lives at the worker level, not the broker level.

**Rationale:** D009 picked Celery for workflow execution at M001 discussion — M005 cashes that in. Celery has retry, scheduling, dead-letter handling, and existing FastAPI integration. One-task-per-run rather than one-task-per-step because steps share state (intermediate stdout, target container, run-level cancellation), and step-level retry is a per-step config concern not a Celery concern (R017's "retry 3x with exponential backoff on container acquisition failure" is implementable in-task without a per-step task graph). Custom asyncio rejected — would reinvent broker, retry semantics, dead-letter handling. Argo / Temporal / Dagster rejected — heavyweight, separate runtime, separate UI.

**Alternatives Considered:**
- One Celery task per step — rejected: cancellation between steps becomes a multi-task coordination problem; intermediate state needs Redis pub/sub or DB polling; step-level retry duplicates Celery retry; net loss
- Pure asyncio dispatcher in the FastAPI process — rejected: backend restart kills in-flight runs; no retry semantics; no dead-letter; reinvents Celery
- External orchestrator (Argo/Temporal) — rejected: M005 scope is "make it work"; ops complexity beyond the team-of-one budget

---

### Per-step `target_container` is the executor's contract, not a runtime decision

**Decision:** Each `workflow_steps` row declares `target_container ENUM(user_workspace|team_mirror) NOT NULL` at definition time. The executor uses this column to decide where to run — never inferred from step type. `claude` and `codex` and `shell` and `git` steps can run in either container. The team-mirror container becomes a real workflow execution host (D022 cashed) — it has the same toolchain as user workspaces (`workspace-image`), and it's the right target for steps that operate on the team's bare repos (auto-push hooks, fetch-and-tag operations, lint-on-mirror) without needing a specific user's filesystem. User-workspace steps target the user determined by `workflows.target_scope` (the triggering user, the round-robin pick, or `target_user_id` for `team_specific`).

**Rationale:** Separating WHERE from WHAT decouples executor logic. A `claude` step running in user-workspace operates on user-edited files; a `claude` step running in team-mirror operates on the bare repo's branches via `git worktree` or the mirror's bare repo metadata. Both are valid; the workflow author picks. Inferring from step type rejected — too rigid; team admins want to choose for legitimate reasons (e.g. "lint runs on mirror, AI summary runs in user workspace so the user can edit the summary doc"). Dynamic-runtime-target rejected — non-deterministic runs; harder to debug; harder to test. Locking the choice at definition time makes runs reproducible.

**Alternatives Considered:**
- Step type implies container — rejected: rigid; conflates WHAT with WHERE
- Workflow-level `target_container` for all steps — rejected: real workflows mix targets (lint on mirror, summarize result in user workspace)
- Runtime decision based on resource availability — rejected: non-deterministic; failure modes diverge across runs of the "same" workflow

---

### Round-robin selection is monotonic per-workflow, not random or sticky

**Decision:** Per-workflow Postgres counter `workflows.round_robin_cursor INT NOT NULL DEFAULT 0`. Each round-robin trigger picks `team_members[cursor % len(active_team_members)]`, increments the cursor in the same transaction as the run insert. Active team members = members with at least one container provisioned in the last 24h; if zero qualify or the picked member has no live workspace at dispatch time, fall back to the triggering user. Cursor never resets; modular arithmetic handles team membership changes naturally.

**Rationale:** Random-pick rejected — UAT scenario 4 ("trigger 4 times, assert distribution lands ≥1 on each member") wants determinism. Sticky-per-workflow rejected — concentrates load. Monotonic-with-modular wraparound delivers fair distribution across membership changes. Cursor-in-transaction-with-run-insert avoids the read-then-write race where two concurrent triggers pick the same member. "Active member" gating prevents picking a member who hasn't logged in for months and would need a cold-start container provision (huge UX hit). Fallback to triggering user is the operational floor — better to run the workflow in a slightly-wrong place than fail to run it at all.

**Alternatives Considered:**
- Random with seed — rejected: indeterministic UAT, hard to debug
- Sticky-per-workflow (always same member) — rejected: load concentration
- Pick least-busy member — rejected: requires real-time load tracking; out of scope; round-robin is good enough

---

### Webhook → workflow dispatch is one resolver function, not a pluggable rule engine

**Decision:** Replace M004's `dispatch_github_event(event_type, payload)` no-op stub with a real implementation: `dispatch_github_event` queries `workflows` for rows with `trigger_type='webhook'` AND `trigger_config @> '{"event_types": ["{event_type}"]}'`, applies branch/repo regex filters in Python (not SQL — JSONB regex is awkward and the candidate set is small), enqueues one `run_workflow` Celery task per match, and inserts the `workflow_runs` rows transactionally. Idempotency: pass `delivery_id` from the receiver into the dispatcher; dispatcher INSERTs runs with `(workflow_id, trigger_data->>'delivery_id')` UNIQUE so duplicate deliveries don't double-run.

**Rationale:** M004 already shipped HMAC + persistence + 24h-retry idempotency at the webhook receiver layer (R052, D025). Re-doing idempotency at the dispatch layer is the right division — the receiver dedupes the *event*, the dispatcher dedupes the *run*. A pluggable rule engine (custom DSL, expression language, etc.) is dramatically over-scoped for M005 — three trigger types and a regex-on-branch filter cover the use case; if more flexibility is needed later, the resolver is a single function to extend.

**Alternatives Considered:**
- Pluggable rule engine with custom DSL — rejected: scope explosion; YAGNI
- SQL-based filter (move regex to Postgres) — rejected: regex-in-JSONB is awkward; small candidate set so Python is fine
- Pass dedup to Celery (`task_id=delivery_id`) — rejected: Celery task IDs are global, collisions possible across workflows; per-run UNIQUE in Postgres is the right dedup boundary

---

### AI CLI execution wraps the existing TTY workaround, doesn't replace with SDK

**Decision:** New module `app/workflows/executors/ai.py` shells out to `claude` and `codex` CLIs via the orchestrator's `docker exec` inside the target container. Wrapper command: `script -q /dev/null bash -c "ANTHROPIC_API_KEY=$KEY claude -p $(printf %q "$prompt") --dangerously-skip-permissions"` (and equivalent OPENAI_API_KEY for codex). Key injected via env on the exec, never written to `.bashrc` or any container file. Output captured from the `script` wrapper's stdout (which captures the TTY). Step success = exit code 0; AI-API-error vs CLI-error distinguished by stderr pattern matching (rough but sufficient — the alternative is parsing CLI internals which break across CLI updates).

**Rationale:** D007 picked the TTY workaround at M001 discussion as the pragmatic-and-known-working path. M005 productionizes it inside Celery without revisiting. SDK rejected — Claude Code and Codex CLIs are not just thin wrappers around the SDKs; they bundle agentic loop logic, tool use, file edit primitives that the SDK does NOT expose. Replacing with SDK would lose features and reinvent the agentic loop. CLI-via-script is the supported path. `script -q /dev/null` continues to be required because both CLIs gate features on `isatty(stdin)`.

**Alternatives Considered:**
- Use Anthropic / OpenAI Python SDKs — rejected: loses agentic-loop features; reinventing
- Run CLI without `script` (relying on `--no-tty` flags) — rejected: D007 documented that the flags don't fully replace TTY presence in current CLI versions
- Run CLI on the host via `docker exec` from Celery worker (no orchestrator hop) — rejected: violates D005 (orchestrator owns Docker socket); celery-worker would need socket access

---

### Push rule executors land minimally — `rule` and `manual_workflow` only

**Decision:** Light up M004's two inert push rule modes. `mode='rule'`: orchestrator's mirror `post-receive` hook (already installed at M004) checks `branch_pattern` against the incoming ref using `fnmatch.fnmatch`; if match, fall through to the existing auto-push executor (push to GitHub origin); if no match, log `auto_push_skipped reason=branch_pattern_no_match` and stop. `mode='manual_workflow'`: hook calls `dispatch_workflow_for_push(project_id, branch, workflow_id)` which inserts a `workflow_runs` row and enqueues `run_workflow` — same Celery path as the webhook dispatcher. No new modes added; `auto` (always-push) stays the M004 behavior unchanged.

**Rationale:** M004 promised exactly this split: schema-now / executors-deferred. M005 honors the boundary by lighting up the two missing executors and nothing more — no new rule modes, no new hook surfaces, no new mirror-side machinery. The `rule` executor reuses M004's auto-push path verbatim with a branch filter prefix; the `manual_workflow` executor reuses the run-dispatch path verbatim with a synthetic trigger payload. Minimal new code. Small failure surface.

**Alternatives Considered:**
- Add `mode='rule_then_workflow'` (push-and-also-trigger) — rejected: scope creep; current modes cover the use case
- Mirror-side glob library beyond `fnmatch` — rejected: `fnmatch` covers the documented pattern syntax; users who need regex can use webhook triggers
- Replace M004's auto-push hook entirely — rejected: would re-prove M004's auto-push end-to-end; the boundary is "executors only"

---

### Step output streaming is poll-the-DB, not WebSocket pub/sub

**Decision:** Run page polls `GET /api/v1/workflow_runs/{id}` every 1.5s while status is `running`; backend returns the run record with all step records nested. Frontend diff-renders. No WebSocket channel for run events in M005 — the M002 WS channel is per-terminal-session and doesn't generalize cleanly to per-run events without a new pub/sub layer. Polling is fine because run pages are short-lived (open during run, closed after) and the cardinality of concurrent open run pages is low (a handful per team, not thousands).

**Rationale:** WebSocket-for-run-events would require a Redis pub/sub channel, a backend WS endpoint that maps `run_id → channel`, frontend reconnect logic, and a stop-streaming-on-finish handshake. All buildable; none cheap. Polling at 1.5s is operationally indistinguishable for a UI that updates a few times per minute. If the run page becomes a "team dashboard with 50 live runs" later, this assumption may need revisiting — but M005 is not that. The polling endpoint is also the same endpoint used for run history drill-down (already needed) — one less thing.

**Alternatives Considered:**
- WebSocket pub/sub via Redis — rejected: scope creep; polling is fine for the cardinality
- Server-Sent Events — rejected: same complexity as WS without the cookie-auth-just-works property; M002 invested in WS-cookie-auth so adding SSE would split the real-time stack
- 5s polling — rejected: feels laggy for short steps (lint, format) that finish in <2s
- 500ms polling — rejected: unnecessary load; 1.5s is the right ergonomic floor

---

> See `.gsd/DECISIONS.md` for the full append-only register of all project decisions. M005-sqm8et will append D026–D034 covering the decisions above.

## Error Handling Strategy

Workflow runs are the principal new failure surface. The discipline:

- **Step-level failure is the run's failure mode.** A step that exits non-zero marks the step record `failed` with `exit_code` populated, marks the run `failed` with `error_class='step_failed'` and `error_message='step {ordinal} ({step_type}) exited {exit_code}'`, and stops further steps. No automatic retry at the step level (R017's "retry 3x with exponential backoff" applies only to **container acquisition failure** — distinct from step failure).
- **Container acquisition failure** retries 3x with exponential backoff (1s, 2s, 4s) inside the Celery task — this is per R017 and matches the "transient infrastructure error" class. After exhaustion, the step is `failed` with `error_class='container_acquisition_exhausted'`.
- **Anthropic / OpenAI API errors** propagate to step failure with the API error class in stderr (`anthropic.APIError`, `openai.APIError`, etc.). 401/403/429 errors get an inline hint in stderr ("API key invalid or rate limited; check team secret"). Never retry at the executor level — workflow authors who want retry on rate-limits write a retry step.
- **Celery worker crash mid-run** is contained by the Postgres-as-source-of-truth model. On worker restart, a separate scheduled Celery task `recover_orphan_runs` finds runs with status='running' and `started_at < now() - 30min`, marks them `failed` with `error_class='worker_crash'`. Step records partially-written are reconciled by the same task.
- **Webhook → dispatch failures** (Anthropic API down at the moment of dispatch, etc.) shouldn't block `dispatch_github_event` — the dispatcher's only job is to enqueue. Per-run failures are the run's concern.
- **Fernet decrypt failure on team secrets** mirrors M004/S01: structured ERROR log naming the team_id and key, 503 to the API caller (admin or workflow runner), never a silent fallback. Run executors check `team_secrets.has_value` first and fail with `error_class='missing_team_secret'` if the key isn't set — better failure mode than a 503 mid-run.
- **Cancellation** is cooperative. `POST /api/v1/workflow_runs/{id}/cancel` flips `status='cancelled'`. The Celery task checks status between steps and exits cleanly. In-flight `docker exec` calls are not killed — letting the running step finish naturally is the safe default; an aborted exec mid-`claude` would leave the AI conversation in an indeterminate state. M006 may add hard-cancel via container `kill -INT` if needed.

## Risks and Unknowns

- **Anthropic / OpenAI API quota costs** — workflow runs that invoke `claude` or `codex` cost real money. The dashboard button surfaces this cost trivially; webhook-triggered runs can amplify (one PR opens → 3 workflows fire). Mitigation: M005 surfaces a per-team setting `max_concurrent_runs` (default 4) and `max_runs_per_hour` (default 60) — workflow dispatcher rejects new runs over those caps with 429 + audit row. Operational floor only; not perfect.
- **Long-running `claude` steps** (agent loops can take minutes) versus the polling UI — polling at 1.5s is fine for the user, but the Celery worker holding the task for 5+ minutes blocks one of `--concurrency=4` slots. Mitigation: `step_timeout_seconds` per step (default 300) caps it; team admins running long-form Claude sessions can raise per step. If team-wide blocking becomes a problem, raise worker concurrency or split workers by step type.
- **Round-robin "active member" definition is heuristic.** "Has provisioned a container in the last 24h" is approximate — a member who's logged in but never opened a terminal won't qualify. Mitigation: tune the window post-launch; 24h is a starting value, not a load-bearing constraint.
- **Workflow definition versioning vs run snapshot.** The snapshot model (above) handles edits cleanly, but if a user *deletes* a workflow, run history rows have `step_runs.step_id = NULL` and the workflow row itself goes away. Run history must continue to render from snapshot only — must verify the run history UI never re-fetches the live workflow definition.
- **Push rule `manual_workflow` executor needs the trigger user.** When a push triggers a workflow, "who is the user" is non-obvious — git push from inside a user container is attributable, but a webhook-event-triggered push from outside isn't. Decision: trigger user = the user whose container did the push (resolved by querying the orchestrator's session registry for who has a live workspace on the project at push time); fallback to project's creator. Worth re-examining in planning.
- **Celery worker security boundary.** The celery-worker container talks to Postgres + Redis + the orchestrator HTTP API. It does **not** mount the Docker socket (D005 — orchestrator owns it). Workflow steps reach Docker only via orchestrator HTTP calls. Confirm during planning that no leak path lets a workflow author execute arbitrary docker commands.
- **AI-CLI version drift.** Both `claude` and `codex` ship inside `workspace-image` and the team-mirror container. CLI updates that change `script` interaction or `--dangerously-skip-permissions` semantics will surface as silent step failures. Mitigation: pin CLI versions in the image build (already standard); add a smoke test in CI that runs `claude -p "echo test"` after image build.

## Existing Codebase / Prior Art

- `backend/app/api/routes/admin.py` — system-admin settings router; `team_secrets` admin endpoints will mirror the registered-key + per-key validator + sensitive-key + redaction discipline (MEM089: append to existing routers rather than create new modules)
- `backend/app/core/encryption.py` — Fernet helpers from M004/S01; reused verbatim by `team_secrets`
- `backend/app/api/team_access.py` — `_assert_caller_is_team_admin` / `_assert_caller_is_team_member` — gates the new team-secrets and workflow endpoints (R051 lifted these; M005 extends without rewriting)
- `backend/app/api/routes/projects.py` — push rule storage from M004; M005 lights up the `rule` and `manual_workflow` executors at the orchestrator post-receive hook, not in this file
- `orchestrator/orchestrator/auto_push.py` — M004's auto-push executor; the `rule` mode wraps this behind a branch filter
- `orchestrator/orchestrator/team_mirror.py` — M004's mirror lifecycle; M005's workflow runs target this container for `target_container='team_mirror'` steps (lifecycle reused unchanged — ensure_team_mirror called from the executor)
- `orchestrator/orchestrator/sessions.py` — workspace container provisioning; `target_container='user_workspace'` reuses `provision_container` (no new path)
- `orchestrator/orchestrator/clone.py` — two-hop clone helpers; workflows that operate on cloned-into-user-workspace projects assume the project is already materialized (separate concern from M005; if not materialized, the workflow step using it fails with `git: pathspec` and that's the right failure mode)
- `backend/app/api/routes/github.py` — webhook receiver from M004; `dispatch_github_event` lives here as a no-op; M005 replaces the body
- M004's `project_push_rules` table stores `mode` and `branch_pattern` and `workflow_id` — schema is ready, only executors are missing
- M002's `system_settings` table provides the **non-team-scoped** runtime knobs (`workspace_volume_size_gb`, `idle_timeout_seconds`); M005 adds `max_concurrent_runs` and `max_runs_per_hour` (both system-scoped, not team-scoped — operator-tunable defaults)

## Relevant Requirements

- R013 — Per-team Claude API key, encrypted, used by workflows + dashboard actions inside user containers (TTY workaround) — **directly delivered by S01 + S02**
- R014 — Per-team OpenAI API key + Codex CLI, same shape as R013 — **directly delivered by S01 + S02**
- R015 — Dashboard Claude + Codex action buttons + workflow step types — **directly delivered by S02 (dashboard buttons) + S03 (workflow step types)**
- R016 — Workflow triggers: dashboard button + form, GitHub webhook, admin manual — **directly delivered by S03 (button + form), S04 (webhook), S05 (admin manual)**
- R017 — Celery-backed step execution; container acquired on demand for terminal-needing steps; retry 3x exponential backoff on acquisition failure — **directly delivered by S03**
- R018 — Run record + step records with config snapshot, status, stdout, stderr, duration, exit code; UI run history with drilldown — **directly delivered by S03 (record schema) + S05 (UI history)**
- R019 — Workflow scope: user / team round-robin / team specific — **directly delivered by S03 (scope schema) + S05 (round-robin executor)**
- R020 — Dashboard configurable trigger buttons + optional form for input variables — **directly delivered by S02 (Claude/Codex direct buttons) + S05 (custom workflow buttons)**
- R011 — GitHub webhooks → trigger workflows — **lit up by S04 (replaces M004's no-op `dispatch_github_event`)**
- R051 — Per-project push rule executors (`rule`, `manual_workflow`) — **lit up by S04 (mirror post-receive hook extension)**
- R052 — Webhook receiver dispatches to real handler — **lit up by S04 (replaces no-op stub)**

## Scope

### In Scope

- **Per-team secrets:** new `team_secrets` table + Fernet-encrypted at-rest + admin API (PUT one-shot replace, GET has_value-only, DELETE) + `claude_api_key` + `openai_api_key` registered keys
- **AI CLI executors:** `claude` and `codex` step types using TTY workaround; env-on-exec credentials; stdout/stderr capture
- **Dashboard direct-action buttons:** "Run Claude" + "Run Codex" prominently in the dashboard; same modal-prompt flow for both; runs record as workflows under the hood (uses a system-defined `_direct_claude` and `_direct_codex` workflow per team so history is uniform)
- **Workflow definition CRUD:** team-admin can create/edit/delete workflows; `workflows` + `workflow_steps` tables; trigger types button/webhook/manual; target scope user/team_round_robin/team_specific; `target_container` per step; `step_timeout_seconds` per step
- **Workflow trigger UI:** configurable dashboard buttons per workflow (button trigger + optional form schema); admin manual trigger endpoint with role gate
- **Workflow run engine:** new `celery-worker` compose service; `run_workflow(run_id)` task; sequential step execution; container acquisition with 3x exponential retry; cancellation between steps
- **Run history:** `workflow_runs` + `step_runs` tables with snapshot semantics; full stdout/stderr/exit/duration retained; UI list page with filters + drill-down
- **Webhook → workflow:** replace M004's `dispatch_github_event` stub; resolve workflows by event_type + repo + branch filter; enqueue runs idempotently keyed on delivery_id
- **Push rule executors:** light up `mode='rule'` (branch pattern → auto-push) and `mode='manual_workflow'` (push → workflow run)
- **Round-robin scope:** monotonic per-workflow cursor; active-member gating; fallback to triggering user
- **Operational caps:** system-scoped `max_concurrent_runs` + `max_runs_per_hour` settings; dispatcher 429 + audit on overage
- **Observability:** required INFO/WARNING/ERROR log keys for the workflow + AI executor surfaces extending the M002+M004 taxonomy; redaction sweep grep covers AI API key prefixes (`sk-ant-`, `sk-`)
- **Migration tests:** alembic upgrade/downgrade round trips for every new table
- **Fast tests:** respx-mocked Anthropic + OpenAI APIs; Celery eager mode; in-process orchestrator stub
- **Integration tests:** real compose stack including new celery-worker; one slice per major surface
- **Acceptance e2e:** the four scenarios in "Final Integrated Acceptance" against real Anthropic + OpenAI + GitHub test org

### Out of Scope / Non-Goals

- **Conditional/branching workflows.** Steps run linearly. No `if step3.exit_code == 0 then step4 else step5` — the workflow author writes a shell step that does the conditional.
- **Parallel steps within a single workflow.** Steps execute sequentially. Concurrent runs of the *same* workflow are allowed (subject to `max_concurrent_runs`); concurrent steps within one run are not.
- **Step-level retry policy.** R017's "retry 3x exponential backoff" is for container acquisition only. Step exit-code retries are out of scope; workflow authors who need retry write `bash -c "for i in 1 2 3; do command && break; sleep $((i*2)); done"`.
- **Workflow templates / library.** Each team's workflows are local; no shared marketplace.
- **Step output filtering / transformation.** `{prev.stdout}` substitution is supported (one-step lookback); jq-style transforms or full templating engines are not.
- **Workflow versioning UI.** Snapshots provide the same forensic property without versioning UX. If users want named versions later, that's its own milestone.
- **Triggering workflows from inside a workflow** ("workflow A's step calls workflow B"). Possible via `curl POST /workflows/{id}/run` from a shell step; no first-class support.
- **Slack / webhook output sinks.** M005 records run history; broadcasting to external systems is M006+ scope (R023/R024 are about user-facing notifications, not external sinks).
- **Cost tracking / per-team API spend dashboards.** Anthropic + OpenAI bills are not introspected; future M-something for billing UI.
- **Workflow editor UX polish.** A functional editor (form for trigger config + ordered list of steps + per-step config form) is in scope; visual graph editors / drag-and-drop palettes are not.
- **AI tool-use beyond CLI defaults.** Workflows pass a prompt to `claude -p`; the agentic loop happens inside the CLI. M005 doesn't shape the system prompt or inject custom tools. Custom system prompts per workflow can land in M006+ if demand surfaces.

## Technical Constraints

- **Celery worker doesn't get the Docker socket.** Per D005, the orchestrator owns it. Workflow execution reaches containers via the orchestrator HTTP API only (`POST /v1/exec`, `POST /v1/sessions/{id}/data`). New orchestrator endpoints may be needed; route additions only, no breaking changes to M002+M004 routes.
- **Reuse the existing Fernet wiring.** M004/S01 set up `SYSTEM_SETTINGS_ENCRYPTION_KEY` env, encrypt/decrypt helpers, and the 503-on-decrypt-failure pattern. `team_secrets` uses the same key, helpers, and failure mode — no new key management.
- **Reuse the existing Redis instance.** Celery's broker + result backend share the M002 Redis (different DBs / key prefixes); no new Redis service.
- **Image build pins CLI versions.** `workspace-image` must continue to ship pinned `claude` and `codex` CLI versions; image build scripts updated as needed but the M002+M004 image-build flow is not rearchitected.
- **No frontend WebSocket changes.** Run-page polling is fine; the M002 WS frame protocol is locked.
- **No changes to M004 push-rule schema.** The two new executors read existing columns (`mode`, `branch_pattern`, `workflow_id`); no migration needed for push rules.
- **Migrations follow M004's append-only convention.** Slot the new alembic revisions under `backend/app/alembic/versions/s07_*` through `s12_*` (or whatever sequence the planning phase locks).
- **Backend test cwd discipline.** Per the existing project memory: backend pytest must run from `backend/` because `Settings()` reads `backend/.env`. Verification gate scripts continue to prefix `cd backend &&`.
- **Mock-github sidecar pattern (MEM261)** is reused for any S04 webhook tests that need a fake GitHub.
- **Two-key shared-secret auth (D016)** continues to gate backend↔orchestrator and now backend↔celery-worker (celery-worker is a backend-tier consumer of the orchestrator HTTP API; gets `ORCHESTRATOR_API_KEY` env).
- **Observability discipline (MEM134, R054)** extends to new log lines. AI API keys (`sk-ant-`, `sk-`, `ghs_`) are added to the redaction sweep grep at the milestone-wide level. Step stdout/stderr are stored in DB but redacted from logs (key + first/last 200 chars only — full content available via run-history endpoint).

## Integration Points

- **Anthropic API** (`api.anthropic.com`) — invoked by `claude` CLI inside containers; respx-mocked in fast tests; real call in acceptance only
- **OpenAI API** (`api.openai.com`) — same shape as Anthropic
- **GitHub API** — installation token mint + webhook delivery already wired in M004; M005 doesn't add new GitHub endpoints, only consumes the webhook stream via the dispatcher
- **Postgres** — new tables: `team_secrets`, `workflows`, `workflow_steps`, `workflow_runs`, `step_runs`. Alembic migrations s07_team_secrets through s11_step_runs. Migration tests for every revision.
- **Redis** — new key prefix `celery:*` (Celery broker + result backend); existing key prefixes (`session:*`, `user_sessions:*`, `gh:installtok:*`) untouched
- **Docker daemon** (via orchestrator HTTP only) — workflow steps acquire user-workspace OR team-mirror containers via existing `provision_container` + `ensure_team_mirror`; new orchestrator endpoint `POST /v1/exec` (or extension to existing `POST /v1/sessions/{id}/exec`) for one-shot command execution with TTY discipline
- **Celery worker container** (new compose service) — connects to Postgres + Redis + orchestrator HTTP; same image as backend (FastAPI + SQLModel + asyncpg + httpx) plus Celery; entry point `celery -A app.workflows.tasks worker`
- **Frontend** (React + TanStack Router) — new routes `/workflows`, `/workflows/{id}/edit`, `/runs`, `/runs/{id}`; new dashboard panel for direct AI buttons + configurable workflow buttons; new team-settings panel for AI keys; new admin-team panel extension for max_concurrent_runs / max_runs_per_hour visibility (system-scoped — admins see, team admins don't)
- **System admin panel** — new operational settings for `max_concurrent_runs` + `max_runs_per_hour` (system_settings extension, no UI rewrite needed beyond a key registration)
- **Mirror post-receive hook** (M004 territory) — extended for `mode='rule'` branch matching and `mode='manual_workflow'` workflow dispatch; orchestrator-side change only
- **Redaction sweep** — milestone-wide grep adds `sk-ant-` and `sk-` patterns to the existing M002+M004 redaction sweep

## Testing Requirements

**Unit tests** (per-module, fast, no compose stack):

- `team_secrets` encrypt/decrypt round trip, has_value semantics, validator registry
- Workflow definition validators (trigger config shape, form schema shape, step config per step type)
- Round-robin cursor: monotonic, active-member gating, fallback path
- Webhook → workflow resolver: event_type filter, repo regex, branch regex, idempotency by delivery_id
- AI CLI command shaping: TTY wrapper, env injection, output capture, error class detection from stderr
- Push rule executors: `rule` branch pattern matching, `manual_workflow` dispatch with synthetic trigger payload
- Celery task: step iteration, container acquisition retry (3x exponential), cancellation between steps, snapshot capture at trigger time

**Migration tests** (per-alembic-revision, hits real Postgres):

- s07_team_secrets through s11_step_runs upgrade/downgrade round trips
- M001's `_release_autouse_db_session` autouse fixture pattern reused (per the existing memory note about session-scoped autouse `db` fixture holding AccessShareLock — same fix applies)

**Integration tests** (per-slice, real compose stack including celery-worker):

- S01: team-admin paste API key → encrypt + persist; GET returns has_value only; PUT replaces; DELETE clears; non-admin gets 403
- S02: dashboard "Run Claude" button → workflow run created → Celery picks up → orchestrator exec into user workspace → respx-mocked Anthropic responds → step record stores stdout/exit; same for Codex; missing API key → step fails with `error_class='missing_team_secret'`
- S03: workflow CRUD (POST workflow with steps + trigger config; GET list; PUT edit; DELETE); workflow with button trigger appears in dashboard for team users; round-robin scope honored; user-scope honored; team_specific scope honored; step `target_container` honored
- S04: GitHub webhook → HMAC verifies → `dispatch_github_event` resolves workflow → run enqueued; idempotent under duplicate delivery_id; push rule `mode='rule'` matches branch pattern and auto-pushes; `mode='manual_workflow'` triggers workflow instead; `mode='auto'` (M004 unchanged) continues to push
- S05: admin manual trigger endpoint (system_admin only); run history endpoint with filter (status, time range); run drill-down endpoint returns full step records with snapshots; recover_orphan_runs scheduled task marks worker-crashed runs failed
- S06 (acceptance): the four "Final Integrated Acceptance" scenarios end-to-end against real Anthropic + OpenAI + a GitHub test org

**E2E backend image alembic skip-guard** (MEM162) extends to S01–S05 e2es: each test ships an autouse fixture probing for new alembic revisions in `backend:latest` and skips with the `docker compose build backend` instruction on miss.

**Redaction sweep** (R054 extension): every M005 e2e ends with `docker compose logs` grep that fails on `sk-ant-` or `sk-` (and continues to fail on `gho_/ghu_/ghr_/github_pat_/-----BEGIN`).

**Verification gate**: `cd backend && uv run pytest tests/integration/test_m005_*.py -v` runs all integration e2es; per-slice run-time budget ≤30s; full milestone budget ≤180s.

## Acceptance Criteria

Per-slice acceptance criteria are gathered during the planning phase. Roadmap-level criteria (one per planned slice) are surfaced here as anchors:

- **S01 — Per-team AI credentials at rest:** team admin pastes Claude + OpenAI keys; PUT one-shot replaces; GET shows has_value only; non-admin gets 403; encrypted at rest; decrypt-failure → 503 + ERROR log naming team_id + key
- **S02 — Dashboard direct AI buttons:** "Run Claude" and "Run Codex" buttons in dashboard; modal prompt → run record → step exec → stdout streams to UI; missing API key → step fails with named class; respx-mocked in fast tests, real Anthropic in acceptance
- **S03 — Workflow definition CRUD + Celery run engine:** team-admin creates workflow with steps + trigger; button-trigger workflow appears in dashboard; click → run; Celery worker executes steps sequentially; step records persist with snapshot, stdout, stderr, exit, duration; run record updates status; cancellation between steps works; container acquisition retries 3x exponential
- **S04 — Webhook → workflow + push rule executors:** webhook delivery → HMAC verify (M004) → dispatch_github_event resolves matching workflow → run enqueued; idempotent on delivery_id; push rule `mode='rule'` matches branch pattern and auto-pushes (delegates to M004 auto-push); `mode='manual_workflow'` enqueues workflow run instead
- **S05 — Run history UI + admin manual trigger + worker crash recovery:** workflow runs list with filters; drill-down with full step output; admin manual trigger endpoint (system_admin gate); recover_orphan_runs marks worker-crashed runs failed; round-robin scope distributes across active members with fallback to triggering user
- **S06 — Acceptance e2e against real APIs:** four "Final Integrated Acceptance" scenarios pass; redaction sweep clean; per-team operational caps (`max_concurrent_runs`, `max_runs_per_hour`) enforced

## Open Questions

- **Step output piping syntax.** `{prev.stdout}` covers single-step lookback. Should multi-step lookback (`{step3.stdout}`) land in M005, or only consecutive piping? Current thinking: only `{prev.stdout}` in M005 — multi-step adds template parsing complexity without strong demand. Worth a 5-minute discussion in S03 planning.
- **AI step model selection.** Should `claude` and `codex` step config expose `--model` flags to the workflow author? Default: yes, optional, defaults to whatever the CLI defaults to. Confirm in S03 planning.
- **Webhook trigger filter syntax for branch regex vs glob.** R052 + M005 dispatcher need a filter syntax. Glob (`refs/heads/feature/*`) is operator-friendly but limited; regex is powerful but error-prone. Current thinking: `fnmatch`-style globs (consistent with push rule branch_pattern). Confirm in S04 planning.
- **Round-robin "active member" window.** 24h is a starting value. Should this be a `system_settings` key (operator-tunable) or a per-workflow override? Current thinking: system-scoped `round_robin_active_window_hours` default 24, no per-workflow override in M005. Worth a 5-minute discussion.
- **Workflow editor UX in M005.** Form-with-step-list is functional but ugly. How polished does it need to be in M005? Current thinking: functional + accessible (mobile-usable per R022) but not pretty; M006/Polish milestone can re-skin. Confirm in S03 planning.
- **Mirror-side workflow execution security.** A workflow author with team-admin role can write a `shell` step that executes inside the team-mirror container, which has access to the team's bare repos and (transitively) GitHub installation tokens via the post-receive hook. Is the team-admin role enough authority for that, or do we need a separate `workflow_admin` role? Current thinking: team-admin is enough — they already have the GitHub App connection power. Worth a 5-minute discussion in S03 planning to confirm threat model.
- **Run history retention.** Forever (per R018) is the current contract. Should there be a per-team retention setting? Current thinking: forever in M005; an ops milestone can add retention if disk grows. No action in M005.
- **Worker crash recovery scheduling.** `recover_orphan_runs` runs every… how often? Every 5 minutes is excessive; every hour is too lax. Current thinking: every 10 minutes via Celery Beat (new compose service or in-process scheduler). Confirm in S05 planning.
