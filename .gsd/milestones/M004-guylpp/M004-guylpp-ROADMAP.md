# M004-guylpp: Projects & GitHub

**Vision:** Team admins install our GitHub App against an org or personal account → create per-team projects linked to real GitHub repos → users open a project and the repo materializes in their container via a two-hop clone (GitHub → team mirror container → user workspace) → users push back through the mirror, which auto-pushes to GitHub on configured projects → webhooks land verified and logged, ready for workflow dispatch in M005. The team-mirror container introduced here is the same image as user workspaces and will double as the workflow execution host in M005.

## Success Criteria

- Team admin installs the GitHub App against a test org through the actual GitHub install URL and the resulting installation is visible in team settings
- Team admin can paste the GitHub App private key once into the system_settings UI; subsequent reads return has_value:true with no value
- Team admin can generate a new webhook secret via a confirmed UI modal; the value appears exactly once in the response and is write-only thereafter; re-generate is destructive (old webhooks 401 until the upstream secret is updated)
- Team admin can create a project linked to a real GitHub repo and configure its push-back rule (auto / rule-matched / manual+workflows); only auto is wired in M004
- Team admin can disable auto-reap on the team-mirror container (always-on for that team)
- Any team user can click 'Open project' and watch the repo materialize at /workspaces/<u>/<t>/<project_name> with no credentials in .git/config
- Any team user's git push lands in the team mirror; if the project rule is auto, the change appears on github.com within seconds
- External GitHub push delivers a webhook to /api/v1/github/webhooks; HMAC verifies; row lands in github_webhook_events; no-op dispatch_github_event hook fires
- Bad-HMAC webhook is rejected with 401 and audit-logged in webhook_rejections without body persistence
- Fernet decrypt failure surfaces as 503 with system_settings_decrypt_failed ERROR log naming the key — no silent fallback
- All git-op boundaries emit structured logs tagged with team_id and project_id; redaction sweep finds zero token-prefix or PEM-header matches across backend + orchestrator logs
- Final integrated acceptance against a real GitHub test org passes: install → project → open → commit → push → mirror → auto-push → github.com round-trip

## Slices

- [x] **S01: S01** `risk:high` `depends:[]`
  > After this: Admin pastes GitHub App private key (PEM) once; GET shows has_value:true, value:null. Admin clicks Generate webhook secret → response shows the secret exactly once → subsequent GET shows has_value:true with no value. Orchestrator-side decrypt_setting() round-trips the private key to plaintext at the call site only. Fernet decrypt failure on a corrupted ciphertext returns 503 with system_settings_decrypt_failed ERROR log.

- [x] **S02: S02** `risk:high` `depends:[]`
  > After this: Team admin clicks Install GitHub App → redirected to https://github.com/apps/<our-app>/installations/new with a signed state token → GitHub install round-trips back to /api/v1/github/install-callback → state validates → row in github_app_installations with installation_id + account_login + account_type → team settings UI shows the installation. Orchestrator mint_installation_token on first call hits GitHub; second call within 50 min hits Redis cache; cache miss after 50 min re-mints.

- [x] **S03: S03** `risk:high` `depends:[]`
  > After this: POST /v1/teams/{id}/mirror/ensure returns {network_addr: 'team-mirror-<id>:9418'}; idempotent on second call. A sibling test container can git clone git://team-mirror-<id>:9418/test.git after a fixture bare repo is dropped into /repos/test.git. Reaper kills the container after mirror_idle_timeout_seconds of inactivity (verified by log line team_mirror_reaped reason=idle). Team admin PATCH /api/v1/teams/{id}/mirror with always_on=true suppresses reap on the next reaper tick.

- [x] **S04: S04** `risk:high` `depends:[]`
  > After this: Team admin POST /api/v1/teams/{team_id}/projects creates a project linked to a real GitHub repo via installation_id. User POST /api/v1/projects/{id}/open materializes the repo: orchestrator clones GitHub→mirror with installation token (env-on-exec only — verified by inspecting the mirror's /repos/<project_id>.git/config showing bare github.com URL with no token), then user container runs git clone git://team-mirror-<team_id>:9418/<project_id>.git → repo lands at /workspaces/<u>/<t>/<project_name> with no credentials in user-side .git/config. User commits + git push → mirror receives → if rule.mode=auto, mirror auto-pushes to GitHub origin → fixture upstream sees the new ref. PUT /api/v1/projects/{id}/push-rule persists all three modes (auto, rule, manual_workflow); rule and manual_workflow are stored but inert.

- [x] **S05: S05** `risk:medium` `depends:[]`
  > After this: External curl with a valid X-Hub-Signature-256 header against POST /api/v1/github/webhooks → 200 → row in github_webhook_events table → no-op dispatch_github_event invoked (verified via webhook_dispatched log line). Same payload with a bad signature → 401 → row in webhook_rejections table → no row in github_webhook_events. Duplicate delivery_id second post → 200 idempotent → no second row inserted (UNIQUE constraint).

- [x] **S06: S06** `risk:medium` `depends:[]`
  > After this: Playwright e2e walks the full admin-side experience: install GitHub App (mocked GitHub callback returns installation_id), create a project, open it (mocked clone path), configure a push rule, generate the webhook secret and confirm one-time-display modal, toggle mirror always-on. All four flows complete without hitting the real GitHub API.

- [x] **S07: S07** `risk:medium` `depends:[]`
  > After this: Manual UAT against a real GitHub test org records the four scenarios from CONTEXT.md Final Integrated Acceptance: (1) full install→project→open→commit→push→auto-push→github.com round-trip; (2) external GitHub push delivers a webhook that verifies, persists, and fires the no-op dispatch hook; (3) generate-then-rotate webhook secret breaks old deliveries with 401 until GitHub-side is updated; (4) mirror reap → user click 'Open project' → cold-start → clone proceeds. Each scenario has a recorded timestamp, observed log lines, and screenshots in S07-UAT.md. Final redaction grep over backend + orchestrator logs returns zero matches for token prefixes (gho_, ghs_, ghu_, ghr_, github_pat_) and PEM headers.

## Boundary Map

## Boundary Map

### S01 → S02

Produces:
- `backend/app/models.py` → `SystemSetting` extended with `value_encrypted: bytes | None`, `sensitive: bool`, `has_value: bool` (SQLModel)
- `backend/app/alembic/versions/s06_*.py` → ALTER `system_settings` to add the three columns; idempotent upgrade + reversible downgrade
- `backend/app/core/encryption.py` → `encrypt_setting(plaintext: str) -> bytes`, `decrypt_setting(ciphertext: bytes) -> str` (Fernet); fail-fast `_load_key()` at module import if `SYSTEM_SETTINGS_ENCRYPTION_KEY` env missing and any sensitive key registered
- `backend/app/api/routes/admin.py` → `_VALIDATORS` registry now `dict[str, {validator, sensitive: bool, generator: Callable | None}]`; new `POST /admin/settings/{key}/generate` endpoint with one-time-display response
- Four registered keys: `github_app_id` (public, int validator), `github_app_client_id` (public, str validator), `github_app_private_key` (sensitive, PEM validator, no generator), `github_app_webhook_secret` (sensitive, no public validator, generator = `secrets.token_urlsafe(32)`)
- `backend/app/api/routes/admin.py` → `GET /admin/settings/{key}` returns `{value: null, has_value: true, sensitive: true}` for sensitive keys
- `decrypt_setting` callable surface for orchestrator to import

Consumes: nothing (foundation slice; M002 system_settings table is the substrate but not an in-milestone produce)

### S02 → S03

Produces:
- `backend/app/models.py` → `GitHubAppInstallation` (id, team_id FK, installation_id, account_login, account_type, created_at)
- `backend/app/alembic/versions/s06b_*.py` → CREATE TABLE github_app_installations
- `backend/app/api/routes/github.py` → `GET /api/v1/teams/{team_id}/github/install-url` (returns redirect URL with signed state token); `POST /api/v1/github/install-callback` (validates state, persists installation row); `GET /api/v1/teams/{team_id}/github/installations` (list); `DELETE /api/v1/teams/{team_id}/github/installations/{id}` (uninstall record)
- `orchestrator/orchestrator/github_tokens.py` → `mint_installation_token(installation_id: int) -> str` (JWT-from-private-key → POST /app/installations/{id}/access_tokens); Redis-cached with 50-min TTL keyed by `gh:installtok:{installation_id}`
- `orchestrator/orchestrator/github_tokens.py` → `get_installation_token(installation_id: int) -> str` (cache-first; mint on miss)
- HTTP endpoint on orchestrator: `GET /v1/installations/{id}/token` (backend-callable, returns plaintext token for backend-side ops; gated by orchestrator shared-secret auth)

Consumes from S01:
- `decrypt_setting()` to pull `github_app_private_key` plaintext at JWT signing call site only
- Registered keys `github_app_id`, `github_app_client_id`, `github_app_private_key`

### S03 → S04

Produces:
- `backend/app/models.py` → `TeamMirrorVolume` (team_id PK FK, volume_path, container_id NULLABLE, last_started_at, last_idle_at, always_on BOOLEAN DEFAULT false)
- `backend/app/alembic/versions/s06c_*.py` → CREATE TABLE team_mirror_volumes
- New registered system_settings key: `mirror_idle_timeout_seconds` (validator [60, 86400] int, default 1800)
- `orchestrator/orchestrator/team_mirror.py` → `ensure_team_mirror(team_id: UUID) -> {container_id, network_addr}` (idempotent: spin up if absent, return network address if present); `reap_team_mirror(team_id: UUID)` (stops container, leaves volume)
- `orchestrator/orchestrator/team_mirror_reaper.py` → background asyncio task that scans team_mirror_volumes for `last_idle_at + mirror_idle_timeout_seconds < now() AND NOT always_on`; calls reap_team_mirror
- HTTP endpoint on orchestrator: `POST /v1/teams/{team_id}/mirror/ensure` (returns network_addr); `POST /v1/teams/{team_id}/mirror/reap` (admin force-reap)
- Backend endpoint: `PATCH /api/v1/teams/{team_id}/mirror` (team-admin: toggle always_on flag)
- Compose: team-mirror containers run `git daemon --base-path=/repos --export-all --reuseaddr --enable=receive-pack` on port 9418; sibling-container DNS `team-mirror-<team_id>` resolves
- Required INFO log keys: `team_mirror_started`, `team_mirror_reaped`, `mirror_idle_timeout_seconds_resolved`

Consumes from S02:
- Backend-side: nothing direct yet (S04 calls into both)
- Reuses M002's `volume_store.py`, `sessions.py`, `reaper.py` shapes (reference, not in-milestone produce)

### S04 → S05

Produces:
- `backend/app/models.py` → `Project` (id, team_id FK, installation_id FK, github_repo_full_name, name, last_push_status NULLABLE, last_push_error TEXT NULLABLE, created_at); `ProjectPushRule` (project_id FK, mode enum, branch_pattern NULLABLE, workflow_id NULLABLE, created_at)
- `backend/app/alembic/versions/s06d_*.py` → CREATE TABLEs projects + project_push_rules
- `backend/app/api/routes/projects.py` → `GET/POST /api/v1/teams/{team_id}/projects`, `GET/PATCH/DELETE /api/v1/projects/{id}`, `POST /api/v1/projects/{id}/open` (calls orchestrator to materialize), `GET/PUT /api/v1/projects/{id}/push-rule`
- `orchestrator/orchestrator/clone.py` → `clone_to_mirror(team_id, project_id, repo_full_name, installation_id)` (env-on-exec installation token; clones to `/repos/.tmp/<project_id>.git` then atomic rename to `/repos/<project_id>.git`); `clone_to_user_workspace(user_id, team_id, project_id, project_name)` (user-container `git clone git://team-mirror-<team_id>:9418/<project_id>.git`)
- `orchestrator/orchestrator/auto_push.py` → mirror post-receive hook handler that, on rule mode=auto, runs `git push origin --all --tags` from inside the mirror container with a fresh installation token in env vars; on success updates `projects.last_push_status='ok'`; on failure logs and updates `last_push_status='failed' + last_push_error`
- HTTP endpoints on orchestrator: `POST /v1/projects/{id}/materialize-mirror`, `POST /v1/projects/{id}/materialize-user`
- Required INFO log keys: `team_mirror_clone_started`, `team_mirror_clone_completed`, `user_clone_started`, `user_clone_completed`, `mirror_push_started`, `mirror_push_completed`, `auto_push_started`, `auto_push_completed`. WARNING: `auto_push_rejected_by_remote`. Update on `last_push_*` columns is the failure-visibility surface.

Consumes from S02 + S03:
- `get_installation_token()` from S02 for both clone-to-mirror and auto-push
- `ensure_team_mirror()` from S03 to spin up the mirror before clone
- Compose-network DNS `team-mirror-<team_id>:9418` from S03

### S05 → S06

Produces:
- `backend/app/models.py` → `GitHubWebhookEvent` (id, installation_id FK NULLABLE, event_type, delivery_id UNIQUE, payload JSONB, received_at, dispatch_status, dispatch_error TEXT NULLABLE); `WebhookRejection` (id, delivery_id, signature_present BOOLEAN, signature_valid BOOLEAN, source_ip, received_at)
- `backend/app/alembic/versions/s06e_*.py` → CREATE TABLEs github_webhook_events + webhook_rejections; UNIQUE on github_webhook_events.delivery_id
- `backend/app/api/routes/github_webhooks.py` → `POST /api/v1/github/webhooks` (HMAC verify against decrypted github_app_webhook_secret using hmac.compare_digest; on pass: persist event, call dispatch_github_event, 200; on fail: persist rejection, 401, no body persistence; on duplicate delivery_id: 200 idempotent no-op)
- `backend/app/services/dispatch.py` → `dispatch_github_event(event_type: str, payload: dict) -> None` (no-op stub function with NotImplementedError-marker comment for M005; emits `webhook_dispatched` log line)
- Required INFO log keys: `webhook_received`, `webhook_verified`, `webhook_dispatched`. WARNING: `webhook_signature_invalid` (with delivery_id, source_ip).

Consumes from S01:
- `decrypt_setting('github_app_webhook_secret')` at HMAC verification call site only

### S06 → S07

Produces:
- `frontend/src/components/Admin/SystemSettings/` → settings list with sensitive-key visualization (lock icon, "Set" / "Generate" / "Replace" actions); generate-confirm modal; one-time-display modal
- `frontend/src/components/Teams/GitHub/ConnectionsList.tsx` → list installations for a team; "Install GitHub App" CTA → opens GitHub install URL in new tab; "Uninstall" action
- `frontend/src/components/Teams/Projects/ProjectsList.tsx` → list, create, open project from team page
- `frontend/src/components/Teams/Projects/PushRuleForm.tsx` → all three rule modes; mode=rule and mode=manual_workflow show "stored — executor lands in M005" badge
- `frontend/src/components/Teams/Mirror/AlwaysOnToggle.tsx` → team-admin toggle for mirror always-on
- `frontend/src/client/` → regenerated from new openapi spec
- Playwright e2e: `frontend/tests/m004-guylpp.spec.ts` covering install flow (mocked GitHub), generate-secret modal, project create + open, push rule persistence

Consumes from S01–S05:
- All backend endpoints from prior slices (auto-generated client)

### S07 → (terminal slice)

Produces:
- `backend/tests/integration/test_m004_guylpp_s07_full_acceptance_e2e.py` → bundled scenarios from CONTEXT.md "Final Integrated Acceptance" against a real GitHub test org (manual mode — uses real org credentials from `.env.test-org`, skips in CI)
- `S07-UAT.md` → recorded results of the four scenarios with timestamps and observed log lines
- Operator runbook (location decided in slice — likely `deployment.md` extension or new `docs/runbooks/m004-secrets-rotation.md`): SYSTEM_SETTINGS_ENCRYPTION_KEY rotation procedure; webhook secret rotation procedure with operator coordination steps
- Final redaction grep over backend + orchestrator logs (extends M002 redaction sweep): fails on `gho_`, `ghs_`, `ghu_`, `ghr_`, `github_pat_`, `-----BEGIN`

Consumes from S01–S06:
- Everything (this is the integration-closure slice)
