---
id: S04
parent: M004-guylpp
milestone: M004-guylpp
provides:
  - ["backend/app/alembic/versions/s06d_projects_and_push_rules.py — projects + project_push_rules tables", "backend/app/models.py — Project, ProjectPublic, ProjectCreate, ProjectUpdate, ProjectPushRule, ProjectPushRulePublic, ProjectPushRulePut", "backend/app/api/routes/projects.py — full team-scoped CRUD + push-rule + POST /open", "orchestrator/orchestrator/clone.py — clone_to_mirror, clone_to_user_workspace, _install_post_receive_hook, _uninstall_post_receive_hook", "orchestrator/orchestrator/auto_push.py — run_auto_push (mirror→GitHub push-back executor with token in env, stderr scrub)", "orchestrator/orchestrator/routes_projects.py — POST /v1/projects/{id}/materialize-{mirror,user}, /install-push-hook, /uninstall-push-hook, /auto-push-callback", "orchestrator/orchestrator/sessions.py — NetworkMode=perpetuity_default on user containers (closes MEM264)", "orchestrator/orchestrator/team_mirror.py — PERPETUITY_ORCH_KEY baked into mirror container env", "orchestrator/orchestrator/config.py — github_clone_base_url setting (https→git:// branch, prod-default https://github.com)", "orchestrator/orchestrator/errors.py — CloneCredentialLeakDetected"]
requires:
  - slice: S01
    provides: decrypt_setting() at call sites for github_app_private_key (used by S02's mint_installation_token, called transitively here)
  - slice: S02
    provides: get_installation_token(installation_id) — cache-first, used by both clone_to_mirror and run_auto_push
  - slice: S02
    provides: github_app_installations table — installation_id FK target on projects.installation_id
  - slice: S03
    provides: ensure_team_mirror(team_id) — idempotent mirror spin-up before clone-to-mirror
  - slice: S03
    provides: _team_mirror_container_name + _network_addr — DNS alias the user-side clone dials
  - slice: S03
    provides: Mirror container runs git daemon with --enable=receive-pack on port 9418
affects:
  - ["backend (new routes + migration)", "orchestrator (new clone, auto_push, routes_projects modules; sessions.py + team_mirror.py modifications)", "user-session container provisioning (NetworkMode change)", "mirror container env (PERPETUITY_ORCH_KEY)"]
key_files:
  - (none)
key_decisions:
  - ["Token discipline: pass installation tokens via container.exec environment dict, reference as $TOKEN inside sh -c wrapper. Shell expands at exec time so the token never lands in docker exec inspect record. Logs only ever carry the 4-char _token_prefix (MEM262/MEM274/MEM292).", "User-container NetworkMode=perpetuity_default at create time closes MEM264 (DNS resolution of team-mirror-<first8>). Module-level constant mirrors the team_mirror module's same-name constant.", "Single shared post-receive hook script with PROJECT_ID derived from $(basename $(pwd) .git) — works across all auto-rule projects on a team mirror. $GIT_DIR is unreliable under git-daemon (MEM279).", "Defense-in-depth credential leak detection on user-side clone: read remote.origin.url after clone and reject anything not bare git:// — reuses CloneCredentialLeakDetected for 500 mapping (MEM276).", "Stderr from failed auto-push scrubbed of all 5 GitHub token-prefix families before persistence into projects.last_push_error AND before WARNING log line (MEM278).", "PUT push-rule hook install/uninstall failure is non-fatal — the rule is the source of truth, the hook is derived state, the next clone-to-mirror reconverges.", "github_clone_base_url setting (default https://github.com, never overridden in production) lets the e2e target a credential-free git-daemon mock without TLS termination — clone.py and auto_push.py branch on URL scheme."]
patterns_established:
  - ["Two-hop clone with credential discipline: env-on-exec for tokens (sh -c expansion at exec time), .git/config sanitize + verification, credential-free user transport with defense-in-depth leak guard", "Single shared post-receive hook script per mirror (PROJECT_ID derived from $(pwd) at runtime), best-effort callback to orchestrator, X-Orchestrator-Key from env baked at mirror create time", "Mock-GitHub for live e2e: two sibling containers (FastAPI for token mint via GITHUB_API_BASE_URL, workspace-image+git-daemon for clone+push via github_clone_base_url) — clone.py and auto_push.py branch on URL scheme to keep production code path unchanged", "Hook is derived state, rule is source of truth — PUT push-rule's hook install/uninstall failure is non-fatal, next clone-to-mirror reconverges"]
observability_surfaces:
  - ["INFO team_mirror_clone_started/completed/failed (token_prefix only, never plaintext)", "INFO user_clone_started/completed (no token-related fields — credential-free transport)", "INFO mirror_push_started/completed for orchestrator-driven pushes", "INFO auto_push_started/completed/skipped (project_id, rule_mode, trigger, result)", "WARNING auto_push_rejected_by_remote (project_id, exit_code, stderr_short — scrubbed of all token prefixes)", "INFO project_push_rule_updated (project_id, mode, actor_id) on every successful PUT", "INFO project_opened (project_id, user_id, duration_ms) on backend /open success", "INFO network_mode_attached_to_user_container (container_id, network) — MEM264 verification fingerprint", "INFO post_receive_hook_installed/uninstalled (project_id, mirror_container_id)", "DB column projects.last_push_status + last_push_error — durable failure surface for auto-push (SELECT failing projects without grepping logs)"]
drill_down_paths:
  []
duration: ""
verification_result: passed
completed_at: 2026-04-28T00:35:54.074Z
blocker_discovered: false
---

# S04: Projects CRUD + two-hop clone + push-back rule storage + auto-push executor

**Stood up the projects domain end-to-end: per-team projects linked to GitHub repos, two-hop materialize (GitHub→mirror with env-on-exec installation token, then mirror→user via credential-free git://), push-rule schema for all three modes, and an auto-push executor that round-trips user pushes back to GitHub for mode=auto projects — proven by a live-stack e2e through both happy and failure paths with a clean redaction sweep.**

## What Happened

## What this slice delivered

S04 is the slice that makes M004's primary user loop (R009 + R010) actually work: a team admin links a real GitHub repo as a project, a team member clicks "open" and the repo materializes inside their workspace container, and when they push, the change can reach github.com — with zero credentials ever landing on user disk and zero token plaintext appearing in any log.

### Persistence (T01)
- Alembic revision `s06d_projects_and_push_rules` adds two tables. `projects` is keyed by UUID, FK→teams CASCADE, FK→github_app_installations.installation_id RESTRICT, with `last_push_status`/`last_push_error` as the persistent failure surface so the next agent can `SELECT` failing projects without grepping logs. `project_push_rules` is keyed by project_id (1:1), with a CHECK constraint enforcing mode ∈ {auto, rule, manual_workflow}.
- `backend/app/api/routes/projects.py` exposes the full CRUD: GET/POST `/api/v1/teams/{id}/projects`, GET/PATCH/DELETE `/api/v1/projects/{id}`, GET/PUT `/api/v1/projects/{id}/push-rule`. Team-admin gating on writes, team-member gating on reads. Cross-team requests collapse to 404 to block enumeration. Default push_rule on create is `mode=manual_workflow`.

### Mirror hop with credential discipline (T02)
- `orchestrator/orchestrator/clone.py::clone_to_mirror` is the credential-discipline boundary for the GitHub→mirror hop. The structural invariant: the GitHub installation token is passed via the `environment` dict on `container.exec(...)` and referenced as `$TOKEN` inside an `sh -c "git clone ... https://x-access-token:$TOKEN@github.com/<repo>.git ..."` — the shell expands at exec time, so the token never lands in docker's exec-inspect record (MEM274 pattern, MEM292).
- After clone, `git remote set-url origin https://github.com/<repo>.git` scrubs the token from `.git/config`, then `cat /repos/.tmp/<id>.git/config` is verified against `x-access-token` and the full GitHub token-prefix family (gho_/ghs_/ghu_/ghr_/github_pat_) — fail-closed via `CloneCredentialLeakDetected` mapped to 500. Atomic `mv` to `/repos/<id>.git`.
- Idempotency keyed on `/repos/<project_id>.git/HEAD` existence — re-materialize returns `{result:'reused', duration_ms:0}` and short-circuits BEFORE minting a token.
- `POST /v1/projects/{id}/materialize-mirror` (SharedSecretMiddleware) maps `InstallationTokenMintFailed` → 502 `github_clone_failed`, `_CloneExecFailed` → 502 with reason=`git_clone_exit_<code>`, `CloneCredentialLeakDetected` → 500, `DockerUnavailable` → 503.

### User hop + backend orchestration (T03)
- `orchestrator/orchestrator/sessions.py::_build_container_config` now sets `NetworkMode=perpetuity_default` so user-session containers can DNS-resolve `team-mirror-<first8>:9418`. This closes MEM264 — without it the user-side `git clone git://...` fails with "Could not resolve host". The first-create path emits `network_mode_attached_to_user_container` for verification (MEM285).
- `clone_to_user_workspace` docker-execs `git clone git://team-mirror-<first8>:9418/<id>.git /workspaces/<u>/<t>/<name>` inside the user container with NO env dict — the transport is credential-free per D023. Defense-in-depth post-clone: `remote.origin.url` is read and rejected if it doesn't start with `git://` or contains `x-access-token`/`https://github.com` (MEM276/MEM291). Reuses `CloneCredentialLeakDetected`. Idempotency keyed on `<workspace>/.git/HEAD`.
- `POST /v1/projects/{id}/materialize-user` (SharedSecretMiddleware) returns `{result, duration_ms, workspace_path}`; `_CloneExecFailed` → 502 `user_clone_failed` with `reason=user_clone_exit_<code>`.
- Backend `POST /api/v1/projects/{id}/open` (member-gated) chains `mirror/ensure` → `materialize-mirror` → `materialize-user` inside ONE httpx.AsyncClient. Orchestrator 502 detail payloads are forwarded verbatim so the FE can branch on `reason` (e.g. `user_clone_exit_128` vs `github_clone_failed`).

### Auto-push executor (T04)
- A single shared post-receive hook script is written into `/repos/<id>.git/hooks/post-receive` (mode 0755) by `clone_to_mirror` ONLY when the project's push_rule.mode=auto at clone time. The hook derives PROJECT_ID from `basename "$(pwd)" .git` and POSTs to `http://orchestrator:8001/v1/projects/$PROJECT_ID/auto-push-callback` with `X-Orchestrator-Key: $PERPETUITY_ORCH_KEY`, the env var being baked into the mirror container at create time by `team_mirror._build_team_mirror_container_config`. Hook is best-effort (`|| true`) per D024 — auto-push failure surfaces only in `projects.last_push_status`.
- `orchestrator/orchestrator/auto_push.py::run_auto_push` defensively re-checks `project_push_rules.mode == 'auto'` (rule may have flipped between hook install and callback), mints a fresh installation token, then docker-execs `git --git-dir=/repos/<id>.git push --all --prune <authed>` followed by `git push --tags <authed>` with the token in the env dict only. On non-zero exit: persist `last_push_status='failed'` with stderr scrubbed of all GitHub token-prefix substrings (capped 500 chars in DB, 200 in log) — log WARNING `auto_push_rejected_by_remote` (MEM278).
- `POST /v1/projects/{id}/install-push-hook` and `POST /v1/projects/{id}/uninstall-push-hook` are no-op `mirror_missing` when the team mirror isn't currently running — the next clone-to-mirror reconverges the hook from the persisted rule.
- Backend `PUT /api/v1/projects/{id}/push-rule` (now async) calls install/uninstall on transitions to/from `auto`. Orchestrator failures are logged WARNING and SWALLOWED — the rule write is the source of truth, the hook is derived state.

### Live-stack proof (T05)
- `backend/tests/integration/test_m004_s04_two_hop_clone_e2e.py` walks scenarios A–H against an ephemeral orchestrator + sibling backend + two mock-github sidecars (FastAPI for token mint via GITHUB_API_BASE_URL, ubuntu+git-daemon for clone+push via the new `github_clone_base_url` setting — MEM281).
- Surfaced and surgically fixed two real defects in T04's shipped code: (1) the post-receive hook used `wget` which is not installed in the workspace image (MEM280) — switched to `curl -fsS`; (2) `$GIT_DIR=.` under git-daemon's receive-pack (MEM279) — switched PROJECT_ID derivation to `basename "$(pwd)" .git`. Both fell straight through unit tests because the byte-for-byte hook checks don't validate runtime semantics.
- Added orchestrator setting `github_clone_base_url` (default `https://github.com`, never overridden in production). `clone.py` and `auto_push.py` branch on the URL scheme — https keeps the `x-access-token` userinfo form, git:// drops it (still exercises env-on-exec discipline because the unused token still flows through the env dict).
- All 8 e2e scenarios green in 25.88s (under the 90s wall-clock target). Redaction sweep over orchestrator + backend logs returns ZERO matches for full token plaintext, gho_, ghu_, ghr_, github_pat_, or `-----BEGIN`. `ghs_` is permitted only inside the canonical `token_prefix=ghs_…` 4-char-prefix log shape (MEM262).

## What the next slice should know
- **S05 (webhook receiver)** consumes `decrypt_setting('github_app_webhook_secret')` from S01 — exactly the same call-site-only decrypt pattern used by `mint_installation_token` for the App private key. The github_webhook_events table will FK to `github_app_installations.installation_id` (NULLABLE because uninstall events arrive after the row is gone). The `dispatch_github_event` no-op stub doesn't need to know about projects yet (project-resolution is M005's job).
- **S06 (frontend)** can now generate the openapi client against the projects routes; the `mode=rule` and `mode=manual_workflow` storage paths are wired but their executors land in M005, so the FE shows the "stored — executor lands in M005" badge on those modes.
- **The auto-push hook is derived state.** PUT /push-rule's hook install/uninstall failure is intentionally non-fatal — the rule is the source of truth, the next clone-to-mirror reconverges. Future agents debugging "rule says auto but no hook present" should check the orchestrator unreachable-warning log, not assume the rule is wrong.
- **MEM264 is closed and verified by the e2e.** The new HostConfig.NetworkMode=perpetuity_default key on user-session containers is a backwards-compatible addition (Docker accepts it on a network that already exists, which the test harness always uses). Adjacent tests `test_m002_s01_e2e.py` and `test_m002_s05_full_acceptance_e2e.py` still pass against the changed sessions.py.

## Gates closure
- **Q3 (boundary contracts)**: produces/consumes lists in the slice plan are honored 1:1 — see `provides`/`requires` arrays.
- **Q4 (integration closure)**: T05 e2e proves the produced surfaces actually work together against the live stack, not just hermetic unit tests.
- **Q5 (failure visibility)**: `projects.last_push_status` + `last_push_error` is the durable failure column; `auto_push_rejected_by_remote` WARNING is the log fingerprint; `_token_prefix` is the only sanctioned token-in-log shape.
- **Q6 (security/credential discipline)**: env-on-exec for tokens; .git/config sanitize verification; user-side credential-free transport with defense-in-depth leak guard; stderr scrubbing of all 5 GitHub token prefixes; redaction sweep across orchestrator + backend logs returns zero matches.
- **Q7 (idempotency)**: both materialize hops idempotency-short-circuit on existing `.git/HEAD`; backend /open is safe to retry; install/uninstall-push-hook are mirror_missing no-ops.
- **Q8 (operational readiness)**: see the dedicated section below.

## Operational Readiness
- **Health signals**: required INFO log markers all observed in a clean run — `team_mirror_clone_started`/`completed`, `user_clone_started`/`completed`, `mirror_push_started`/`completed`, `auto_push_started`/`completed`, `project_push_rule_updated`, `project_opened`, `network_mode_attached_to_user_container`. Hook installation logs `post_receive_hook_installed` / `_uninstalled`.
- **Failure signals**: 502 from `/api/v1/projects/{id}/open` carries the orchestrator's `{detail, reason}` so an operator can branch on `github_clone_failed` (token mint or git auth) vs `user_clone_exit_<code>` (mirror DNS regressed — MEM264 fingerprint) vs `clone_credential_leak` (sanitize-step regression — should be unreachable). WARNING `auto_push_rejected_by_remote` carries `project_id`, `exit_code`, scrubbed stderr — `projects.last_push_status='failed'` is the durable surface.
- **Recovery**: `clone_to_mirror` and `clone_to_user_workspace` are idempotent — re-running `/open` after a transient failure is safe. If the mirror container is reaped between hook-install and a user push, the next `/open` re-materializes and re-installs the hook from the persisted rule. PUT push-rule failures on the orchestrator side are non-fatal; the rule is authoritative.
- **Monitoring gaps**: T04's unit tests for the post-receive hook check byte-for-byte content — they don't validate that the named executable exists in the image OR that runtime env vars resolve correctly under git-daemon. T05 surfaced both defects (wget vs curl, $GIT_DIR=.). A future S07 follow-up could harden this by exec'ing the hook against a real git-daemon-backed bare repo in unit tests. Also: e2e cleanup does not auto-reconcile leaked loopback devices from prior crashed sessions; symptom is `volume_provision_failed` 502, fix is operational (MEM282).

## Verification

## Slice-level verification (all green)

**Unit suites (orchestrator + backend) — 145 tests across the slice's modules:**
- `cd orchestrator && uv run pytest tests/unit/test_clone_to_mirror.py tests/unit/test_clone_to_user_workspace.py tests/unit/test_routes_projects_materialize_mirror.py tests/unit/test_routes_projects_materialize_user.py tests/unit/test_auto_push.py tests/unit/test_post_receive_hook_install.py tests/unit/test_routes_projects_auto_push_callback.py tests/unit/test_team_mirror.py tests/unit/test_sessions.py` → 103/103 passed in 0.48s
- `cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_projects.py tests/api/routes/test_projects_open.py` → 42/42 passed in 2.77s

**Live-stack e2e (T05) — the slice's authoritative integration proof:**
- `cd /Users/josh/code/perpetuity && docker compose build backend orchestrator && docker compose up -d db redis && cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m004_s04_two_hop_clone_e2e.py -v` → PASSED in 25.88s (8 scenarios A–H against db + redis + ephemeral orch + sibling backend + two mock-github sidecars).

**Structural assertions verified:**
- Token-discipline: tests assert `environment` dict on `container.exec` carries the token while cmd contains `$TOKEN` (shell variable name) and never the plaintext.
- `.git/config` sanitize: post-clone `cat config` verified against `x-access-token` AND the full GitHub token-prefix family (gho_/ghs_/ghu_/ghr_/github_pat_); leak-detection raises `CloneCredentialLeakDetected`.
- User-side credential-free transport: `clone_to_user_workspace` exec carries NO `environment` dict; `remote.origin.url` rejected if not `git://`-prefixed.
- NetworkMode regression guard: `_build_container_config` HostConfig contains `NetworkMode: 'perpetuity_default'`.
- Hook script (byte-for-byte) embeds `$PERPETUITY_ORCH_KEY` + the orchestrator URL; no `wget`, uses `curl -fsS`.
- Stderr scrubbing covers all 5 GitHub token-prefix families.
- Redaction sweep over the e2e's orchestrator + backend container logs returns ZERO matches for full token plaintext, gho_, ghu_, ghr_, github_pat_, or `-----BEGIN`.

**Inspection surfaces (verified by e2e on the live stack):**
- `psql -c "SELECT id, team_id, github_repo_full_name, name, last_push_status, last_push_error FROM projects"` returns the seeded project with `last_push_status='ok'` after happy-path push, `'failed'` after rejected-by-remote.
- `docker exec <mirror> cat /repos/<project_id>.git/config` shows `https://github.com/acme/widgets.git` with no token, no `x-access-token`.
- `docker exec <mirror> ls /repos/<id>.git/hooks/post-receive` returns 0 (executable) on auto-rule projects, hook absent on manual_workflow.
- `docker exec <user_ws> cat /workspaces/<u>/<t>/widgets/.git/config` shows `git://team-mirror-<first8>:9418/<id>.git` with no credentials.
- `docker inspect <user_ws> --format '{{.HostConfig.NetworkMode}}'` returns `perpetuity_default` (MEM264 closed, verified).

No deviations from the slice plan's verification gates. Two surgical fixes in T05 (hook wget→curl, $GIT_DIR→pwd derivation) are documented in T04/T05 summaries and covered by the e2e's hook-firing assertions.

## Requirements Advanced

None.

## Requirements Validated

None.

## New Requirements Surfaced

None.

## Requirements Invalidated or Re-scoped

None.

## Operational Readiness

None.

## Deviations

None.

## Known Limitations

- T04's unit tests for the post-receive hook check byte-for-byte content + env-var placeholders + the orchestrator URL substring — they DO NOT validate that (a) the executable named in the hook is installed in the workspace image, or (b) `$GIT_DIR` resolves correctly under git-daemon's receive-pack. Both defects (wget→curl, $GIT_DIR=. vs $(pwd)) surfaced only at e2e in T05. A future S07 follow-up should harden the hook unit tests by exec'ing the hook against a real git-daemon-backed bare repo.

- Auto-push to a private remote that requires SSH key auth is unsupported — the slice exclusively uses `https://x-access-token:<TOKEN>@github.com/<repo>.git` form. This is intentional and matches the GitHub App installation-token auth model.

- The e2e cleanup does not auto-reconcile leaked loopback devices and ext4 mounts under /var/lib/perpetuity/workspaces/* from PRIOR crashed sessions. When the kernel's loopback pool is exhausted, the next `volume_provision` returns 502 `volume_provision_failed`. Recovery is operational only (MEM282) — `docker run --rm --privileged --pid=host alpine:3 nsenter -t 1 -m -- sh -c '<unmount + losetup -d + rm -f .img>'`.

- S05 has not yet wired the GitHub webhook receiver, so external pushes from github.com do not yet round-trip into the system — the milestone primary loop (R009 + R010) is closed in this slice for the user→GitHub direction; the GitHub→user direction remains for S05.

- Frontend for the new endpoints lands in S06; the projects/push-rule UI is not yet present in the FE so all UAT is via curl/HTTPie or the autogenerated client.

## Follow-ups

- S05: webhook receiver consumes `decrypt_setting('github_app_webhook_secret')` from S01 with hmac.compare_digest; persist verified events to github_webhook_events (UNIQUE on delivery_id), reject bad-HMAC into webhook_rejections with 401, fire no-op dispatch_github_event stub.

- S06: regenerate openapi client, build ProjectsList + PushRuleForm + Open button + always-on toggle for mirror; mode=rule and mode=manual_workflow show a "stored — executor lands in M005" badge.

- S07: real-GitHub UAT against a live test org covering the four scenarios from CONTEXT.md "Final Integrated Acceptance"; final redaction grep over backend + orchestrator logs across the whole milestone; SYSTEM_SETTINGS_ENCRYPTION_KEY + webhook-secret rotation runbook.

- Future hardening: harden post-receive hook unit tests by actually exec'ing the hook against a real git-daemon-backed bare repo (would have caught both wget and $GIT_DIR defects pre-e2e).

- Future hardening: e2e cleanup should reconcile leaked loopback devices from prior sessions automatically (MEM282).

## Files Created/Modified

- `backend/app/alembic/versions/s06d_projects_and_push_rules.py` — Migration creating projects + project_push_rules tables with FK CASCADE/RESTRICT and CHECK on mode
- `backend/app/models.py` — Project, ProjectPublic, ProjectCreate, ProjectUpdate, ProjectPushRule, ProjectPushRulePublic, ProjectPushRulePut SQLModels
- `backend/app/api/routes/projects.py` — Team-scoped CRUD + push-rule + POST /open chaining mirror/ensure → materialize-mirror → materialize-user; PUT push-rule fires install/uninstall-push-hook on transitions
- `backend/app/api/main.py` — Router registration
- `orchestrator/orchestrator/clone.py` — clone_to_mirror with env-on-exec + sanitize + leak detection + idempotency; clone_to_user_workspace with credential-free transport + defense-in-depth leak guard; _install/_uninstall_post_receive_hook with curl-based script
- `orchestrator/orchestrator/auto_push.py` — run_auto_push: load project, defensive rule re-check, mint token, docker-exec push --all + push --tags, scrub stderr, update last_push_status
- `orchestrator/orchestrator/routes_projects.py` — /v1/projects/{id}/materialize-{mirror,user}, /install-push-hook, /uninstall-push-hook, /auto-push-callback
- `orchestrator/orchestrator/sessions.py` — _build_container_config sets NetworkMode=perpetuity_default; provision_container logs network_mode_attached_to_user_container
- `orchestrator/orchestrator/team_mirror.py` — _build_team_mirror_container_config bakes PERPETUITY_ORCH_KEY into mirror container env
- `orchestrator/orchestrator/config.py` — github_clone_base_url setting (default https://github.com)
- `orchestrator/orchestrator/errors.py` — CloneCredentialLeakDetected
- `orchestrator/orchestrator/main.py` — Wire routes_projects router into lifespan
- `backend/tests/integration/test_m004_s04_two_hop_clone_e2e.py` — Live-stack e2e: 8 scenarios A-H with two mock-github sidecars, redaction sweep
- `backend/tests/api/routes/test_projects.py` — 32 tests including push-rule transition install/uninstall hook checks
- `backend/tests/api/routes/test_projects_open.py` — 10 tests for POST /open chaining + error propagation
- `backend/tests/migrations/test_s06d_projects_migration.py` — Migration round-trip + FK + UNIQUE + CHECK
- `orchestrator/tests/unit/test_clone_to_mirror.py` — 16 tests including env-on-exec discipline + leak detection
- `orchestrator/tests/unit/test_clone_to_user_workspace.py` — 11 tests for credential-free transport + defense-in-depth leak guard
- `orchestrator/tests/unit/test_auto_push.py` — 14 tests including stderr scrub over all 5 token-prefix families
- `orchestrator/tests/unit/test_post_receive_hook_install.py` — 10 tests for hook content + install/uninstall paths
- `orchestrator/tests/unit/test_routes_projects_materialize_mirror.py` — 11 tests for response shapes + error mappings + auth
- `orchestrator/tests/unit/test_routes_projects_materialize_user.py` — 9 tests for materialize-user route
- `orchestrator/tests/unit/test_routes_projects_auto_push_callback.py` — 14 tests for callback route + shared-secret enforcement
- `orchestrator/tests/unit/test_sessions.py` — NetworkMode regression guard test
