# M004-guylpp: Projects & GitHub — Context

**Gathered:** 2026-04-25
**Status:** Ready for planning

## Project Description

M004-guylpp is the Projects + GitHub milestone of Perpetuity. It delivers the team-collaboration loop that the platform was designed around: a team admin installs **our** GitHub App against an org or personal account, creates per-team projects linked to real GitHub repos, and team users open those projects in their per-(user, team) workspace containers — with the repo materialized via a **two-hop clone** (GitHub → team-mirror container → user workspace) that keeps GitHub credentials out of every user-facing container. Push-back from a user lands in the team mirror first; if the project rule is `auto`, the mirror pushes onward to GitHub. Inbound webhooks are HMAC-verified, persisted, and routed to a `dispatch_github_event` no-op hook that M005's workflow engine will fill in.

The team-mirror container is a new container class with the same lifecycle shape as user-team workspaces (warm/spin-up, idle reaper, team admin can disable auto-reap). It runs the same `workspace-image` because its second job — landing in M005 — is to be the team's workflow execution host. One container per team, multi-purpose by design.

## Why This Milestone

R009–R012 have been on the roadmap since M001 discussion and were carried as "active, unmapped" through M001/M002/M003 without delivery (M003 ended up being verification-only over M002's terminal infra). This milestone is the long-promised "make GitHub real" step. Without it, the platform has containers and a terminal but nothing for users to actually work on.

It needs to land *now* (before M005 workflows) because:
- Workflows in M005 trigger on GitHub events (R016) — there is no event source until M004 lands the webhook receiver
- Workflows execute in a container the orchestrator can dispatch to (R017) — the team-mirror container introduced here is that execution host
- The push-back rule schema (auto/rule/manual+workflows) is needed before M005 can wire executors to it
- The `system_settings` extension for sensitive secrets (R034) is foundational to M005's per-team Claude/Codex API key storage (R013/R014)

## User-Visible Outcome

### When this milestone is complete, the user can:

- **Team admin:** install our GitHub App against an org or personal account, see the resulting connection in team settings, and configure per-project push-back rules (auto / rule / manual+workflows) — only `auto` is wired to actually push in M004
- **Team admin:** generate a new GitHub webhook secret via a confirmed UI modal, see the new value displayed exactly once so they can paste it into GitHub's webhook config, and never see it again
- **Team admin:** paste a GitHub App private key (PEM) once into the system_settings UI; subsequent GETs show `has_value: true` only — never the PEM
- **Team admin:** configure the team-mirror container to be always-on (disable auto-reap) for that team
- **Any team user:** see the team's projects list in the dashboard, click "Open project", and watch the repo materialize in their workspace container at `/workspaces/<team_id>/<project_name>` over the compose network with no credentials touching their `.git/config`
- **Any team user:** make a commit in their workspace, `git push`, and see the change land in the team mirror; if the project's rule is `auto`, see it appear on github.com within seconds
- **External GitHub repo:** receive a webhook for push/PR/tag events delivered by GitHub, see the row in `github_webhook_events` table, see the no-op `dispatch_github_event` hook fire (M005 fills its body)

### Entry point / environment

- Entry point: web UI for admin flows (install GitHub App, generate webhook secret, create project, configure push-back rule); workspace terminal for `git` ops; `POST /api/v1/github/webhooks` for inbound webhook deliveries
- Environment: full compose stack (Postgres + Redis + orchestrator + Docker daemon) plus a real GitHub test org (acceptance only)
- Live dependencies involved: real GitHub API (App registration, installation tokens, webhook delivery), real Docker daemon (team-mirror container lifecycle), real Postgres (new tables), real Redis (installation token cache)

## Completion Class

- **Contract complete means:** unit + migration tests for system_settings extension, GitHub App credential validators, installation token caching, HMAC verification, push rule storage, webhook persistence; respx-mocked GitHub API in fast tests; local bare git repo as fixture upstream for two-hop clone tests
- **Integration complete means:** team-mirror container starts on demand and is reapable on idle; sibling user container can `git clone git://team-mirror-<id>:9418/...` over compose network; orchestrator clones GitHub→mirror with installation token (env-on-exec only); user push to mirror triggers auto-push to fixture upstream; webhook receiver verifies HMAC against the rotated secret and persists the event
- **Operational complete means:** team-mirror container survives orchestrator restart (cold-start on next demand is fine); reaper kills idle mirror after `mirror_idle_timeout_seconds`; always-on team flag suppresses reap; Fernet decrypt failure surfaces as 503 + named-key ERROR log; webhook secret rotation invalidates old signatures
- **UAT complete means:** the four scenarios in "Final Integrated Acceptance" pass against a real GitHub test org

## Final Integrated Acceptance

To call this milestone complete, we must prove the following against a real GitHub test org (cannot be simulated):

1. **End-to-end happy path:** team admin installs the GitHub App against a test org via the actual GitHub install URL → admin sees the connection in team settings → admin creates a project linked to a real GitHub repo → user clicks "open project" → repo materializes in user's container at `/workspaces/<u>/<t>/<project_name>` with no credentials in `.git/config` → user makes a commit + pushes → mirror receives → auto-push pushes to GitHub → GitHub shows the commit
2. **Webhook round-trip:** push to the GitHub repo from outside our system → GitHub delivers a webhook to our endpoint → HMAC verifies cleanly → row lands in `github_webhook_events` → no-op `dispatch_github_event` invoked (assertable via log line)
3. **Generate-secret-then-rotate:** admin clicks "Generate webhook secret" → response shows the secret once → admin pastes into GitHub → subsequent webhook from GitHub verifies cleanly. Admin clicks "Generate" again → old webhooks return 401 + audit row until GitHub side is updated
4. **Mirror lifecycle:** mirror is idle → reaper kills it → user clicks "open project" → mirror cold-starts → clone proceeds → mirror reachable via compose-network DNS

## Architectural Decisions

### GitHub App, not OAuth App, not OAuth-app-style user tokens

**Decision:** A single GitHub App we own (not Anthropic-affiliated, not OAuth App). Each team's "connection" is one App installation against an org or personal account. Multiple installations per team allowed.

**Rationale:** GitHub App matches "team-level connection" semantics natively (installation = connection), gives short-lived installation tokens (1h GitHub TTL — small encryption-at-rest blast radius), supports first-class per-installation webhook delivery, and avoids the user-scoped token problem of OAuth Apps. Owning the App ourselves means we control rotation cadence and the webhook secret without depending on Anthropic's identity.

**Alternatives Considered:**
- OAuth App — rejected: user-scoped tokens collide with team-level connections; long-lived tokens; deprecated path for new use cases
- Personal Access Tokens only — rejected: no per-installation webhook delivery; no installation token short-life property; team-admin UX of "paste a token" is worse than "install the app"
- Anthropic-affiliated GitHub App — rejected: explicit user requirement that the app be ours, not Anthropic's

---

### Sensitive `system_settings` extension over a separate secrets table

**Decision:** Extend the existing M002 `system_settings` table with `value_encrypted BYTEA NULL`, `sensitive BOOLEAN NOT NULL DEFAULT FALSE`, `has_value BOOLEAN NOT NULL DEFAULT FALSE`. Existing `value JSONB` column stays for non-sensitive keys (back-compat, no rewrite). The `_VALIDATORS` registry extends to `{validator, sensitive: bool, generator: Callable | None}`.

**Rationale:** M002's registered-key + per-key validator pattern already does most of the work; a parallel `system_settings_secret` table would double the wiring (two admin endpoints, two registries, two migration paths). One admin UI surface keeps the operator mental model simple. Encryption is Fernet (`cryptography` library), key from env `SYSTEM_SETTINGS_ENCRYPTION_KEY`, fail-fast at boot if any sensitive key is registered without the env var. Decrypt happens **only at the call site that needs the plaintext** — never in the API layer that returns to the UI.

**Alternatives Considered:**
- Separate `system_settings_secret` table — rejected: doubles wiring; splits the admin UI; adds a second migration path
- Env vars only — rejected: no rotation UI; redeploy to swap; admin can't generate webhook secrets without operator involvement
- KMS / external secrets manager — rejected: overkill for v1; same trust boundary as Postgres password

---

### Generate-and-display-once flow for webhook secret

**Decision:** `POST /admin/settings/{key}/generate` validates the key supports generation, runs the per-key generator (e.g. `secrets.token_urlsafe(32)` for the webhook secret), encrypts and stores the result, and returns it **once** in the response body so the admin can paste it into GitHub. Subsequent GETs and reads from the UI never see plaintext or ciphertext. Re-generate is intentionally destructive — old webhooks return 401 until the upstream service is updated.

**Rationale:** The frontend must never seed secret values — that puts them in browser memory and request logs. Backend-seeded generation keeps the seed inside the trust boundary. The one-time-display window is the only moment plaintext crosses backend→UI, and only for the second the admin needs it. The destructive nature of re-generate is honest — masking it would let admins shoot themselves in the foot quietly.

**Alternatives Considered:**
- Frontend-seeded secrets — rejected: secrets in browser memory and HTTP request logs
- Generate-and-never-display (admin reads secret from GitHub's webhook UI) — rejected: GitHub doesn't display the secret after creation either; admin would have nothing to paste

---

### Installation tokens cached in Redis with 50-minute TTL

**Decision:** Mint installation tokens on demand (JWT signed with App private key → POST `/app/installations/{id}/access_tokens`), cache in Redis keyed by installation_id with 50-min TTL (10-min safety margin under GitHub's 1h TTL), never persist to Postgres. Lazy population: cache miss → mint → store.

**Rationale:** ~150ms per token mint stacks fast across clone/push/webhook hot paths. Redis is already wired (`orchestrator/app/redis_client.py`); short-lived tokens limit encryption-at-rest blast radius (we don't have to encrypt them — they expire faster than they'd survive a leak). Race on cache miss accepted: two concurrent ops both mint, both store the same key, last-write-wins — cost is one extra GitHub API call, not correctness.

**Alternatives Considered:**
- Mint per call — rejected: 150ms per op compounds badly
- Postgres token cache — rejected: Redis is the right tool for short-TTL caches; Postgres would log/replicate every token write
- Redis lock on cache miss to serialize mints — rejected: single redundant mint is cheaper than the lock complexity

---

### Team-mirror container: same image as user workspaces, dual role (mirror + future workflow host)

**Decision:** One team-mirror container per team. Same `workspace-image` as user containers (already has git, claude CLI, codex CLI, full toolchain). Per-team Docker volume holds bare repos at `/repos/<project_id>.git`. Lifecycle reuses `volume_store.py` / `sessions.py` / `reaper.py` shapes from M002. Spin-up triggers: any user `git fetch`, any webhook, any "open project" call, any explicit ensure-mirror admin call. Per-team `mirror_idle_timeout_seconds` (separate from user-session reaper). Team admin can disable auto-reap → always-on for that team.

**Rationale:** The mirror's second job — landing in M005 — is to be the team's workflow execution host. Workflows need the full toolchain, so the slimmer dedicated mirror image was a false economy. One image to maintain, one container class with parametrized labels (`perpetuity.team_mirror=true` vs the user-workspace label). Same lifecycle shape means we're reusing battle-tested M002 code, not inventing a new lifecycle.

**Alternatives Considered:**
- Slim dedicated mirror image (just git + ssh + bash) — rejected: workflow role needs full toolchain; second image to maintain
- Bare repos on the orchestrator host (no mirror container) — rejected: user originally suggested this but converged on a container after the workflow-host role surfaced; no clean way to give workflow steps an execution context without a container
- One mirror container per project — rejected: container-per-project explodes lifecycle work; project granularity inside one container is just file paths

---

### `git daemon` over compose network for mirror→user transport

**Decision:** Inside the team-mirror container: `git daemon --base-path=/repos --export-all --reuseaddr --enable=receive-pack` on port 9418. User containers clone/push by `git://team-mirror-<team_id>:9418/<project_id>.git` over the compose network. No credentials in user-side `.git/config`.

**Rationale:** Honest two-hop boundary — `git push` from user to mirror is a real git op, not a bind-mount FS write. The compose network itself is the trust boundary. Future per-user auth can be added by swapping `git daemon` for `git http-backend` without rewriting the user-side. The bind-mount alternative had a nasty failure mode where a user container holding the FS lock would block the mirror.

**Alternatives Considered:**
- Shared bind-mount of `/repos` between mirror and user containers — rejected: FS-lock failure mode; breaks the two-hop abstraction; concurrent writers are unsafe
- `git http-backend` over HTTPS in M004 — rejected: no per-user auth needed yet; defer until use case appears

---

### Two-hop clone with env-on-exec credential discipline

**Decision:** GitHub→mirror clone uses installation token injected via env vars on the orchestrator's `docker exec` call into the mirror container — never written to `.git/config`. The cloned remote URL is sanitized to bare `https://github.com/<owner>/<repo>.git` after clone; subsequent ops re-inject the token on each call. Mirror→user clone is over `git daemon` with no credentials. Push-back follows the same two hops in reverse: user push → mirror; if rule = auto, mirror push to GitHub origin with a fresh installation token.

**Rationale:** Credentials never touch the user container. Atomic from the user's POV — GitHub→mirror failure aborts before user-side clone starts.

---

### Push-back rule schema lands in M004; only auto-push executor is wired

**Decision:** New `project_push_rules` table: `project_id FK, mode enum(auto|rule|manual_workflow), branch_pattern VARCHAR NULL, workflow_id UUID NULL, created_at`. M004-guylpp ships rule storage, rule CRUD UI, and the auto-push executor. Rules with `mode='rule'` or `mode='manual_workflow'` are stored but inert until M005 lights up the workflow engine.

**Rationale:** Schema is cheap; deferring to M005 would force a migration on top of M005's workflow tables. Storing all three modes now means the UI lands once, M005 only adds executors. Clean phase boundary.

---

### Webhook receive + persist + dispatch-hook (no-op in M004)

**Decision:** `POST /api/v1/github/webhooks` reads `X-Hub-Signature-256`, computes `hmac.compare_digest(...)` against the current `github_app_webhook_secret` plaintext (decrypted at the call site only). On HMAC pass: persist event to `github_webhook_events`, call `dispatch_github_event(event_type, payload)` (no-op in M004), 200. On HMAC fail: insert `webhook_rejections` audit row, 401, no event-body persistence. Duplicate `delivery_id` accepted but second insert is a no-op (UNIQUE constraint).

**Rationale:** Webhook hygiene — bad signatures must not consume DB row space (DoS protection); good signatures must persist for replay debugging. The dispatch hook landing as a real (no-op) function in M004 means M005's wiring is one-line and the failure path is testable now.

---

### Fernet decrypt failure: fail loud, never silently fall back

**Decision:** Any `cryptography.fernet.InvalidToken` raised at a decrypt call site emits a structured ERROR log with key name + call site, surfaces as 503 `{"detail": "system_settings_decrypt_failed", "key": "<name>"}`. Operator must rotate `SYSTEM_SETTINGS_ENCRYPTION_KEY` or restore the previous key.

**Rationale:** Silent fallback after key rotation would mean webhooks pass HMAC against a stale secret while the admin thinks the new one is in effect — a security-state-vs-operator-intent divergence bug. Loud failure is correct.

---

> See `.gsd/DECISIONS.md` for the full append-only register of all project decisions (D019–D025 added by M004-guylpp).

## Error Handling Strategy

Defaults applied in Layer 3 (no per-component deep dive):

- **GitHub API failures during clone:** surface to user as "clone failed, retry" with the GitHub error class included in the message; no auto-retry — clone is user-initiated, let them re-click. Log the failure with team_id + project_id + GitHub status code.
- **Webhook arriving when mirror is down:** persist the event row to Postgres regardless of mirror state. The mirror only spins up if the dispatch hook needs it. M004's hook is a no-op so this is moot until M005, but the event survives for replay.
- **HMAC failure:** 401, audit row in `webhook_rejections` (delivery_id, signature_present, signature_valid, source_ip), no body persistence. Standard webhook hygiene.
- **Two-hop clone partial failure:** GitHub→mirror failure aborts before the user-side clone starts (atomic from user's POV). Mirror→user failure leaves the mirror clean and surfaces to the user as a workspace error with the underlying git error class.
- **Push-back failure:** log, set `last_push_status` and `last_push_error` on the project row, surface in UI on the project list. No retry queue in M004 — manual retry button.
- **Fernet decrypt failure:** fail loud, structured log naming the key, 503 to API caller. Never silently fall back. Operator must rotate or restore the env key.

## Risks and Unknowns

- **Installation token race on cold cache** — Two concurrent ops both miss cache, both mint, both write last-write-wins. Accepted: cost is one redundant GitHub API call, not correctness. Note for monitoring: if `installation_token_minted` rate spikes, look here.
- **Mirror container OOM/crash mid-clone** — A clone that runs the mirror out of memory leaves a partial bare repo at `/repos/<project_id>.git`. Mitigation: clone to a temp path, atomic rename on success. If the mirror crashes mid-rename we get a partial repo on disk. Acceptable for v1 — the next clone attempt detects the partial state and retries from scratch.
- **GitHub webhook delivery retries** — GitHub retries failed webhooks for up to 24h with exponential backoff. We dedupe by `delivery_id` UNIQUE constraint. A second insert on the same delivery_id no-ops cleanly.
- **Concurrent pushes from two users in the same team mirror** — Standard git race; the mirror's `receive.denyNonFastForwards` defaults to false (allow), matching GitHub's default. Users coordinate via PRs (R048 out-of-scope: we don't detect or surface conflicts).
- **GitHub App install state token lost on org-admin approval callback** — When an org requires admin approval, the install URL state token may not survive the round-trip. Acceptable for v1 — admin re-confirms which team to attach the connection to.
- **`git daemon` security** — Read-write `git daemon` with `--enable=receive-pack` has no auth on the wire. Trust boundary is the compose network. Documented and accepted; future swap to `git http-backend` for per-user auth is straightforward.
- **`SYSTEM_SETTINGS_ENCRYPTION_KEY` rotation** — Rotating the env key invalidates every existing encrypted row. Operator runbook (S07 deliverable) documents the rotation procedure and the cost (re-paste private key, regenerate webhook secret).

## Existing Codebase / Prior Art

- `backend/app/api/routes/admin.py` — M002 admin settings router; `_VALIDATORS` registry pattern extends to support sensitive keys
- `backend/app/api/routes/__init__.py`, `backend/app/api/main.py` — API router structure; new `/api/v1/github/webhooks` endpoint plugs in here
- `backend/app/models.py` — SQLModel definitions; `SystemSetting` extends here, new `GitHubAppInstallation`, `Project`, `ProjectPushRule`, `GitHubWebhookEvent`, `WebhookRejection` land here
- `backend/app/alembic/versions/s05_system_settings.py` — M002's system_settings migration; M004 adds a new revision that ALTERs to add encrypted columns
- `backend/app/core/config.py` — env var loading; `SYSTEM_SETTINGS_ENCRYPTION_KEY` lands here with fail-fast-if-missing-and-sensitive-keys-registered logic
- `orchestrator/app/redis_client.py` — already wired; installation token cache uses this pool
- `orchestrator/orchestrator/sessions.py` — user-team session lifecycle; team-mirror container manager mirrors this pattern
- `orchestrator/orchestrator/volume_store.py` — per-(user, team) volume store; team-mirror per-team volume manager mirrors this pattern with simpler key (team_id only)
- `orchestrator/orchestrator/reaper.py` — idle reaper for user sessions; team-mirror reaper is a sibling task with its own per-team timeout setting
- `orchestrator/orchestrator/protocol.py`, `backend/app/api/ws_protocol.py` — WS frame protocol; M004 does NOT touch this (no new frame types — WS bridge is unchanged)
- `frontend/src/components/Admin/` — M001 admin UI structure; new connections settings + projects list + generate-secret modal land alongside
- `frontend/src/client/` — auto-generated API client; new endpoints regenerate this
- `backend/tests/integration/test_m002_*_e2e.py` — M002 integration test patterns (live-orchestrator-swap, e2e markers, redaction sweep); M004 acceptance tests follow the same shape

## Relevant Requirements

- **R009** — Projects live at the team level. M004-guylpp/S04 ships projects CRUD scoped to teams.
- **R010** — Repo materializes into user's container workspace under a project folder, independent across users. M004-guylpp/S04 ships the user-side clone over `git daemon`.
- **R011** — Webhook receiver for push/PR/tag events. M004-guylpp/S05 ships the receiver, persistence, and no-op dispatch hook.
- **R012** — Per-team GitHub connections, multiple per team allowed. M004-guylpp/S02 ships the GitHub App install flow and connection storage.
- **R034** — Sensitive `system_settings` encrypted at rest, never flow back to UI. M004-guylpp/S01 ships the schema extension and encryption.
- **R035** — Generate-only keys with one-time display. M004-guylpp/S01 ships the generate endpoint and modal.
- **R036** — Single GitHub App we own. M004-guylpp/S01 registers the four credential keys.
- **R037** — Installation tokens cached in Redis with 50-min TTL. M004-guylpp/S02 ships the mint + cache.
- **R038** — Team-mirror container per team, lifecycle-managed. M004-guylpp/S03 ships container + lifecycle.
- **R039** — `git daemon` over compose network. M004-guylpp/S03 ships the daemon config.
- **R050** — Two-hop clone with env-on-exec credential discipline. M004-guylpp/S04 ships both hops.
- **R051** — Push-back rule schema (auto/rule/manual+workflows); auto executor wired in M004. M004-guylpp/S04 ships schema + auto.
- **R052** — Webhook receiver: HMAC verify + persist + no-op dispatch hook. M004-guylpp/S05 ships receiver.
- **R053** — Fernet decrypt fail-loud. M004-guylpp/S01 ships the failure path.
- **R054** — Structured logs at every git-op boundary; tokens never logged. M004-guylpp cross-cutting; S07 final acceptance includes redaction grep.

## Scope

### In Scope

- New `system_settings` columns (`value_encrypted BYTEA`, `sensitive BOOLEAN`, `has_value BOOLEAN`); Fernet encryption key from env; per-key registration of `{validator, sensitive, generator}`
- Four GitHub App credential keys: `github_app_id`, `github_app_client_id` (public), `github_app_private_key` (sensitive paste-once), `github_app_webhook_secret` (sensitive generate-only with one-time display)
- `POST /admin/settings/{key}/generate` with one-time-display response for generate-only keys
- GitHub App install flow (redirect to install URL with state token, callback validation, persist `installation_id`)
- `github_app_installations` table (per-team, multiple allowed)
- Installation token mint + Redis cache (50-min TTL) in orchestrator
- `team_mirror_volumes` table + per-team team-mirror container lifecycle (orchestrator-side); same image as user containers; `git daemon --enable=receive-pack` on port 9418
- Per-team `mirror_idle_timeout_seconds` setting; team admin always-on toggle
- `projects` table + projects CRUD scoped to teams + UI for listing/creating
- "Open project" action: orchestrator-driven GitHub→mirror clone (env-on-exec credentials), then user-side clone over `git daemon`
- `project_push_rules` table + UI for all three modes; **auto-push executor only** wired in M004
- `POST /api/v1/github/webhooks` HMAC-verified receiver; `github_webhook_events` + `webhook_rejections` tables; no-op `dispatch_github_event` hook
- Frontend: connections settings (install button, list installations), generate-secret modal with confirm + one-time-display, projects list + create + open, push-back rule config
- Operator runbook: rotate `SYSTEM_SETTINGS_ENCRYPTION_KEY`; rotate webhook secret without breaking active deliveries
- Final integrated acceptance against a real GitHub test org (S07)

### Out of Scope / Non-Goals

- **Workflow execution** (M005)
- **Rule-matched and manual+workflows push executors** — schema lands in M004; executors lit up in M005
- **Branch/PR creation from the UI** (R046 out-of-scope)
- **Repo browser / file viewer in the frontend** (R047 out-of-scope)
- **Conflict detection across users' workspace copies** (R048 out-of-scope)
- **OAuth-app-style user-scoped GitHub tokens** (R049 out-of-scope)
- **System-level default GitHub connection** — every team installs its own
- **Per-user GitHub identity attribution** — installation-level only in M004
- **Webhook retry queue** — relying on GitHub's 24h retry; manual re-deliver via GitHub's UI
- **Mirror container per project** — one mirror per team, projects multiplex inside

## Technical Constraints

- Must extend M002's `system_settings` table — no parallel secrets table
- Must reuse M002's `volume_store.py` / `sessions.py` / `reaper.py` shapes for team-mirror lifecycle — no new lifecycle pattern
- Must not change the locked WS frame protocol (`orchestrator/orchestrator/protocol.py`, `backend/app/api/ws_protocol.py`)
- Must not log installation tokens (`gho_`, `ghs_`, `ghu_`, `ghr_`, `github_pat_` prefixes) or sensitive `system_settings` values; M002 redaction discipline extends to git-op logs
- Must use Fernet (`cryptography` library) for sensitive value encryption; key from env `SYSTEM_SETTINGS_ENCRYPTION_KEY` (32 url-safe base64 bytes); fail-fast at app boot if missing and any sensitive key is registered
- Must use `hmac.compare_digest` for webhook signature verification (constant-time)
- Must use `secrets.token_urlsafe(32)` for webhook secret generation (cryptographically random)
- Installation tokens must never be persisted to Postgres
- User containers must never have GitHub credentials in `.git/config` or on disk
- Team-mirror container must never run `privileged: true` (only the orchestrator runs privileged for loopback support)

## Integration Points

- **GitHub API** — App registration, installation token mint, webhook delivery; respx in fast tests, real GitHub test org for final acceptance
- **Postgres** — new tables (`github_app_installations`, `projects`, `project_push_rules`, `team_mirror_volumes`, `github_webhook_events`, `webhook_rejections`); ALTER on `system_settings`
- **Redis** — installation token cache (50-min TTL)
- **Orchestrator** — new HTTP endpoints: ensure-mirror, clone-to-mirror, materialize-project; team-mirror container lifecycle managed via Docker socket (already owned)
- **Backend** — new admin endpoints (generate, install-callback), new public endpoints (projects CRUD, project open, webhook receive); auth gates: team-admin for connection/project/rule management, system-admin for `system_settings`
- **Frontend** — new components for connections, projects, generate-secret modal; auto-generated API client regeneration

## Testing Requirements

Per Layer 4 quality bar:

- **`system_settings` extension:** unit tests for validator/encryptor wiring; migration test for the new columns; integration test that proves "PUT secret → GET returns `has_value:true, value:null` → orchestrator-side decrypt produces original plaintext"
- **GitHub App installation tokens:** unit tests with `respx` for the GitHub `/app/installations/{id}/access_tokens` call; integration test with real Redis that proves cache-hit + cache-miss + 50-min TTL boundary
- **Webhook receiver:** unit test for HMAC verification with known-good/bad signatures; integration test that POSTs a real GitHub-shaped payload, asserts the row lands in Postgres, asserts the no-op `dispatch_github_event` hook fires
- **Team-mirror container lifecycle:** integration test that hits orchestrator `POST /v1/teams/{id}/mirror/ensure`, asserts container starts, asserts `git daemon` reachable from a sibling container, asserts reaper kills it after configured idle, asserts always-on flag suppresses reap
- **Two-hop clone:** integration test that uses `respx` for GitHub API + a local bare repo as fixture upstream; runs orchestrator clone-to-mirror, then user-side clone-from-mirror; asserts both worktrees match and user container has no credentials in `.git/config`
- **Push-back auto-mode:** integration test with fixture upstream that asserts user push → mirror → auto-push to upstream → upstream sees the ref
- **Frontend:** Playwright e2e for install-app flow (mocked GitHub callback), generate-webhook-secret with one-time-display, project create + open from UI
- **Final acceptance:** four integrated scenarios against real GitHub test org (S07 deliverable, recorded in `S07-UAT.md`)

Test fixture choice for upstream git: **respx for GitHub API + local bare repo for the actual git protocol layer.** Faster than running gitea in CI; more honest than skipping live-upstream tests.

## Acceptance Criteria

Per-slice acceptance gathered during Layer 4 (the planner uses these directly when writing slice plans):

- **S01 (sensitive system_settings + GitHub App credentials):** PUT a sensitive key → row has `value_encrypted` populated, `value` null, `has_value` true; GET returns `value:null, has_value:true, sensitive:true`; orchestrator-side decrypt round-trips to plaintext; PUT to `github_app_private_key` accepts a PEM and rejects garbage; `POST /admin/settings/github_app_webhook_secret/generate` returns the secret once with `has_value:true`; subsequent GET returns no value; Fernet `InvalidToken` at decrypt call site → 503 + `system_settings_decrypt_failed` ERROR log; missing `SYSTEM_SETTINGS_ENCRYPTION_KEY` at boot with sensitive keys registered → app refuses to start
- **S02 (per-team connections + installation tokens):** Admin clicks "Install GitHub App" → redirected to GitHub install URL with state token → callback validates state and persists `github_app_installations` row; orchestrator mints an installation token via JWT-from-private-key → cache hit on second call within 50 min; cache miss after 50 min → re-mints
- **S03 (team-mirror container + lifecycle):** `POST /v1/teams/{id}/mirror/ensure` returns mirror network address (idempotent); sibling container can `git clone git://team-mirror-<id>:9418/test.git` (after a fixture bare repo is placed in `/repos/test.git`); reaper kills it after `mirror_idle_timeout_seconds` of inactivity; team admin always-on flag suppresses reap
- **S04 (projects + two-hop clone + push-back rules + auto-push executor):** Admin creates project linked to GitHub repo; user `POST /v1/projects/{id}/open` → repo materializes at `/workspaces/<u>/<t>/<project_name>` with no creds in `.git/config`; user `git push` → mirror → if rule=auto, auto-push to GitHub upstream visible on github.com; rule CRUD UI persists all three modes; rule=rule and rule=manual_workflow are stored but inert (no executor calls)
- **S05 (webhook receiver):** External `curl -H "X-Hub-Signature-256: sha256=<good>" ...` → row in `github_webhook_events`; bad signature → 401 + `webhook_rejections` row, no body persistence; no-op `dispatch_github_event` invoked (assertable via log); duplicate `delivery_id` second post is no-op
- **S06 (frontend):** Playwright e2e — admin walks install flow with mocked GitHub callback; generate-secret modal shows value once then write-only; project create + open from UI; push-back rule form persists
- **S07 (final integrated acceptance):** Four scenarios from "Final Integrated Acceptance" section pass against real GitHub test org; redaction sweep finds zero token-prefix or PEM-header matches across backend + orchestrator logs

Non-functional bar:
- **Performance:** clone-from-mirror to user (≤10MB repo) under 5s including mirror cold-start; webhook end-to-end (HMAC + persist + dispatch) under 200ms p95
- **Security:** no plaintext secrets in logs; installation tokens never logged; user containers never have GitHub creds on disk; sensitive `system_settings` rows never flow to GET responses
- **Observability:** structured logs at every git-op boundary (clone start/end, push start/end, mirror spawn/reap), tagged with team_id and project_id; webhook ingest logs include delivery_id; Fernet decrypt failures emit a distinct log class for grep

## Open Questions

- **Mirror container temp path for atomic clone** — Use `/repos/.tmp/<project_id>.git` then atomic rename to `/repos/<project_id>.git`? Plan-time decision in S04.
- **Push-back failure UI surfacing** — `last_push_status` + `last_push_error` columns on `projects`, surfaced in the project list row. Plan-time UI design in S06.
- **GitHub App install URL state token shape** — Signed JWT keyed by team_id + nonce + 10-min TTL feels right; lock at S02 plan time.
- **Webhook secret rotation operator runbook** — How long do existing webhook deliveries stay valid against the OLD secret after re-generate? Currently: zero. Acceptable, but the runbook (S07) should call out the operator coordination needed (re-paste new secret into GitHub before regenerating).
