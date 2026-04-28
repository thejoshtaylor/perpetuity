# Requirements

This file is the explicit capability and coverage contract for the project.

## Active

### R009 — Projects live at the team level. Each project links to a GitHub repository. Team members can see all team projects.
- Class: primary-user-loop
- Status: active
- Description: Projects live at the team level. Each project links to a GitHub repository. Team members can see all team projects.
- Why it matters: Projects are the shared unit of work; GitHub is the source of truth.
- Source: user
- Primary owning slice: M003/S01
- Supporting slices: none
- Validation: unmapped
- Notes: none

### R010 — When a user starts working on a project, the GitHub repo is cloned/copied into their team workspace container under a project-named folder. The copy is independent — changes in one user's workspace don't affect others.
- Class: primary-user-loop
- Status: active
- Description: When a user starts working on a project, the GitHub repo is cloned/copied into their team workspace container under a project-named folder. The copy is independent — changes in one user's workspace don't affect others.
- Why it matters: Isolation with shared project visibility.
- Source: user
- Primary owning slice: M003/S02
- Supporting slices: none
- Validation: unmapped
- Notes: Uses `git clone` or `rsync` inside the container.

### R011 — The platform receives GitHub webhooks for push events, pull request events, and tag creation. These events can trigger configured workflows.
- Class: integration
- Status: active
- Description: The platform receives GitHub webhooks for push events, pull request events, and tag creation. These events can trigger configured workflows.
- Why it matters: Enables automated workflows on code events without manual triggering.
- Source: user
- Primary owning slice: M003/S03
- Supporting slices: M005/S01
- Validation: unmapped
- Notes: none

### R012 — Team admins can configure multiple GitHub connections for their team (organization-level or personal OAuth/PAT). Personal teams use personal GitHub connections.
- Class: integration
- Status: active
- Description: Team admins can configure multiple GitHub connections for their team (organization-level or personal OAuth/PAT). Personal teams use personal GitHub connections.
- Why it matters: Teams use different GitHub orgs; multiple repos from different orgs need independent auth.
- Source: user
- Primary owning slice: M003/S01
- Supporting slices: none
- Validation: unmapped
- Notes: System admin can configure system-level defaults.

### R013 — Each user-team pair stores Claude API key encrypted in the database. Workflows and dashboard actions execute `claude` CLI inside the user's team container using the stored key (TTY workaround via `script -q /dev/null`).
- Class: integration
- Status: active
- Description: Each user-team pair stores Claude API key encrypted in the database. Workflows and dashboard actions execute `claude` CLI inside the user's team container using the stored key (TTY workaround via `script -q /dev/null`).
- Why it matters: AI coding assistance is a primary product feature; per-team isolation prevents credential leakage.
- Source: user
- Primary owning slice: M004/S01
- Supporting slices: M004/S02
- Validation: unmapped
- Notes: TTY workaround required; `--dangerously-skip-permissions` flag needed for automated use.

### R014 — Same model as R013 but for OpenAI Codex CLI. Per-team API key, TTY workaround, executed inside user's team container.
- Class: integration
- Status: active
- Description: Same model as R013 but for OpenAI Codex CLI. Per-team API key, TTY workaround, executed inside user's team container.
- Why it matters: Codex is an alternative AI coding tool users may prefer.
- Source: user
- Primary owning slice: M004/S01
- Supporting slices: M004/S02
- Validation: unmapped
- Notes: OpenAI API key stored encrypted per team.

### R015 — Dashboard has prominent Claude Code and Codex action buttons. Both are available as action step types in workflow configuration.
- Class: primary-user-loop
- Status: active
- Description: Dashboard has prominent Claude Code and Codex action buttons. Both are available as action step types in workflow configuration.
- Why it matters: These are primary AI interactions; they need to be first-class UI elements, not buried in settings.
- Source: user
- Primary owning slice: M004/S02
- Supporting slices: M005/S02
- Validation: unmapped
- Notes: Button click → prompt input form → CLI executed in user's current team container → output streamed to terminal.

### R016 — Workflows can be triggered by: dashboard button click (with optional form), GitHub webhook events (push, PR, tag), or admin manual trigger.
- Class: primary-user-loop
- Status: active
- Description: Workflows can be triggered by: dashboard button click (with optional form), GitHub webhook events (push, PR, tag), or admin manual trigger.
- Why it matters: Flexible automation for different team workflows.
- Source: user
- Primary owning slice: M005/S01
- Supporting slices: M003/S03
- Validation: unmapped
- Notes: none

### R017 — Workflow steps execute as Celery tasks. If a step requires a terminal space, the orchestrator acquires (or spins up) a container for the target user-team. Steps without terminal needs run without acquiring a container.
- Class: core-capability
- Status: active
- Description: Workflow steps execute as Celery tasks. If a step requires a terminal space, the orchestrator acquires (or spins up) a container for the target user-team. Steps without terminal needs run without acquiring a container.
- Why it matters: Decouples workflow execution from container availability; enables async, retryable execution.
- Source: user
- Primary owning slice: M005/S02
- Supporting slices: M002/S02
- Validation: unmapped
- Notes: Retry 3x with exponential backoff on container acquisition failure.

### R018 — Every workflow run produces a record with: run ID, trigger type, trigger data, status, timestamps, and an ordered list of step execution records. Each step record stores: config snapshot, status, stdout, stderr, duration, exit code. UI shows run history with drilldown.
- Class: failure-visibility
- Status: active
- Description: Every workflow run produces a record with: run ID, trigger type, trigger data, status, timestamps, and an ordered list of step execution records. Each step record stores: config snapshot, status, stdout, stderr, duration, exit code. UI shows run history with drilldown.
- Why it matters: Debugging failed runs requires full step-level detail, not just a pass/fail.
- Source: user
- Primary owning slice: M005/S02
- Supporting slices: none
- Validation: unmapped
- Notes: none

### R019 — Workflows can be scoped to a user (runs in their terminal space) or a team (round-robin or specified user selected for terminal-requiring steps). User-scoped workflows always use the triggering user's space.
- Class: core-capability
- Status: active
- Description: Workflows can be scoped to a user (runs in their terminal space) or a team (round-robin or specified user selected for terminal-requiring steps). User-scoped workflows always use the triggering user's space.
- Why it matters: Enables both personal automation and team-wide automated tasks.
- Source: user
- Primary owning slice: M005/S03
- Supporting slices: none
- Validation: unmapped
- Notes: Round-robin selection is for team workflows only.

### R020 — Dashboard shows configurable workflow trigger buttons. Each button can optionally present a form to collect user input before the workflow executes. Form data is passed as variables to workflow steps.
- Class: primary-user-loop
- Status: active
- Description: Dashboard shows configurable workflow trigger buttons. Each button can optionally present a form to collect user input before the workflow executes. Form data is passed as variables to workflow steps.
- Why it matters: Users frequently need to provide parameters (branch name, PR number, message) before a workflow runs.
- Source: user
- Primary owning slice: M005/S01
- Supporting slices: none
- Validation: unmapped
- Notes: Buttons are configurable per user within their teams.

### R042 — Pty sessions outlive WebSocket connections via tmux-inside-container; reattach to a running session restores ≥100KB scrollback and survives orchestrator restart.
- Class: core-capability
- Status: active
- Description: Pty sessions outlive WebSocket connections via tmux-inside-container; reattach to a running session restores ≥100KB scrollback and survives orchestrator restart.
- Why it matters: A user closing a browser tab or losing the network must not interrupt long-running shell work (npm install, claude CLI runs, builds). Reconnecting later — even after an orchestrator restart — must restore the live shell with recent scrollback. This is the defining UX promise of the terminal.
- Source: M003 layer-2 architecture gate (tmux-inside-container model)
- Primary owning slice: M003/S04
- Supporting slices: M003/S05, M003/S06
- Validation: Integration test: connect WS, run echo hello, disconnect, docker compose restart orchestrator, reconnect same session_id, see prior scrollback in attach frame, run echo world in same shell.

### R043 — Orchestrator service runs as a separate compose container, holds sole Docker socket access, exposes shared-secret-authed HTTP+WS API. Backend never talks to the Docker daemon directly.
- Class: core-capability
- Status: active
- Description: Orchestrator service runs as a separate compose container, holds sole Docker socket access, exposes shared-secret-authed HTTP+WS API. Backend never talks to the Docker daemon directly.
- Why it matters: Per D005: limiting Docker socket access to one service contains the privilege blast radius. Backend and Celery workers route all container operations through the orchestrator's HTTP API authenticated by a shared secret.
- Source: M003 layer-2 architecture gate; D005 (orchestrator-as-service)
- Primary owning slice: M003/S01
- Supporting slices: M003/S02, M003/S03, M003/S04, M003/S05
- Validation: docker-compose.yml shows orchestrator as the only service mounting /var/run/docker.sock; backend integration tests fail closed if ORCHESTRATOR_API_KEY is wrong/missing; orchestrator rejects unauthorized HTTP and WS upgrade requests with 401/1008.

### R044 — Per-container resource limits (mem_limit=2g, cpus=2, pids_limit=512) plus per-volume hard size cap via loopback ext4. Volume cap value lives in the system_settings table, read per provision; grow-on-next-provision via resize2fs; shrink refused with a warning naming affected (user, team) pairs and current usage.
- Class: operability
- Status: active
- Description: Per-container resource limits (mem_limit=2g, cpus=2, pids_limit=512) plus per-volume hard size cap via loopback ext4. Volume cap value lives in the system_settings table, read per provision; grow-on-next-provision via resize2fs; shrink refused with a warning naming affected (user, team) pairs and current usage.
- Why it matters: A single user must not be able to fork-bomb the host, exhaust memory, or fill the host disk via runaway dd. Loopback-backed ext4 enforces volume caps at the kernel level — soft du-based enforcement is a postmortem, not a quota. Sysadmin-adjustable means hosting can grow per-tenant capacity without redeploy.
- Source: M003 layer-4 quality bar gate (resource limits + loopback volumes)
- Primary owning slice: M003/S02
- Supporting slices: M003/S01, M003/S03
- Validation: Integration test: container provisioned with HostConfig limits set; volume is a loopback ext4 file under /var/lib/perpetuity/vols/; writes past the cap return ENOSPC; admin PUT /api/v1/admin/settings raises the cap, next provision grows the volume via resize2fs; shrink request with overflow returns 4xx with affected pairs listed.

### R045 — system_settings Postgres table plus GET/PUT /api/v1/admin/settings API gated by role == system_admin. UI ships in a later frontend milestone; M003 ships only the API surface and the workspace_volume_size_gb key.
- Class: admin/support
- Status: active
- Description: system_settings Postgres table plus GET/PUT /api/v1/admin/settings API gated by role == system_admin. UI ships in a later frontend milestone; M003 ships only the API surface and the workspace_volume_size_gb key.
- Why it matters: Operator settings that affect runtime tenant capacity must be data-driven, not env-var-driven, so they can change without redeploy. Settings infrastructure ships now so the data model is right; the admin UI is a frontend concern that lands with the next UI milestone.
- Source: M003 layer-4 quality bar gate (sysadmin-adjustable volume cap, option ii)
- Primary owning slice: M003/S02
- Validation: Alembic migration creates system_settings table with seeded workspace_volume_size_gb default (10); GET /api/v1/admin/settings returns current values; PUT updates them; non-system-admin users get 403; orchestrator reads the latest value on each provision call.

## Validated

### R001 — Users can sign up, log in, and log out using httpOnly cookie-based sessions. Sessions work for both REST API and WebSocket upgrade requests.
- Class: core-capability
- Status: validated
- Description: Users can sign up, log in, and log out using httpOnly cookie-based sessions. Sessions work for both REST API and WebSocket upgrade requests.
- Why it matters: Foundation for all auth-gated features; httpOnly prevents XSS token theft; cookie auth is required for WS upgrade compatibility.
- Source: user
- Primary owning slice: M001-6cqls8/S01
- Supporting slices: none
- Validation: S01 delivered cookie signup/login/logout + /users/me and cookie-authenticated WS /ws/ping. All 21 slice-level tests pass against real Postgres: backend/tests/api/routes/test_auth.py (13 cases: cookie issuance, happy-path, duplicate email→400, wrong password→400, unknown email→400 uniform, missing/tampered/expired cookie→401, deleted-user cookie→401, logout clears, logout idempotent, redaction) + backend/tests/api/routes/test_ws_auth.py (6 cases: missing_cookie, invalid_token [garbage+expired], user_not_found, user_inactive, happy-path pong with role) + backend/tests/migrations/test_s01_migration.py (upgrade+downgrade round trip). Full suite 76/76 passing in 3.81s.
- Notes: Replaces current JWT localStorage pattern in the template.

### R002 — `UserRole` enum on User (`user`, `system_admin`). `TeamRole` enum on TeamMember (`member`, `admin`). Roles enforced at API layer. A user can be admin of one team and member of another.
- Class: core-capability
- Status: validated
- Description: `UserRole` enum on User (`user`, `system_admin`). `TeamRole` enum on TeamMember (`member`, `admin`). Roles enforced at API layer. A user can be admin of one team and member of another.
- Why it matters: Access control for team management, workflow configuration, system-level settings.
- Source: user
- Primary owning slice: M001-6cqls8/S01
- Supporting slices: M001-6cqls8/S05
- Validation: UserRole enum (user, system_admin) and TeamRole enum (member, admin) exist on User and TeamMember models — proven by the S01 migration test. is_superuser fully replaced in the API layer: get_current_active_superuser checks role==system_admin (S01). TeamRole enforced at the API by S03 invite/role/remove endpoints (PATCH/DELETE on /teams/{id}/members and team-admin-only invite). System-admin route gate now exercised end-to-end by S05: backend test_admin_teams.py (15 integration tests proving 200 for system_admin, 403 for normal user, 401 unauth, pagination, idempotency, 404s, cross-team bypass) and Playwright admin-teams.spec.ts (happy path: superuser sees all teams, drills into members, promotes a user via confirm dialog; non-admin redirected from /admin/teams off the /admin/* namespace by requireSystemAdmin). All endpoints emit structured INFO logs (admin_teams_listed, admin_team_members_listed, system_admin_promoted with already_admin flag).
- Notes: Replaces `is_superuser` bool. System admin promotes other system admins with confirm step.

### R003 — Every new user gets a personal team created automatically at signup. Personal teams are never shared or invitable. They serve as the user's private workspace.
- Class: primary-user-loop
- Status: validated
- Description: Every new user gets a personal team created automatically at signup. Personal teams are never shared or invitable. They serve as the user's private workspace.
- Why it matters: Ensures every user has a workspace immediately; no onboarding friction.
- Source: user
- Primary owning slice: M001-6cqls8/S02
- Supporting slices: none
- Validation: S02 integration tests prove every signup creates exactly one TeamMember(role=admin) on a Team(is_personal=True): `test_signup_creates_personal_team` (happy path, slug+suffix shape), `test_signup_rolls_back_on_mid_transaction_failure` (atomicity — monkeypatched helper failure leaves no user/team row), `test_superuser_bootstrap_has_personal_team` (init_db wiring for FIRST_SUPERUSER), `test_get_teams_after_signup_returns_only_personal_team` (caller sees exactly 1 personal team), `test_invite_on_personal_team_returns_403` (invite endpoints reject personal teams with "Cannot invite to personal teams"). All 93/93 backend tests pass against real Postgres.
- Notes: Personal team is identified by a flag; invite endpoints reject it.

### R004 — Any user can create a team and becomes its admin automatically. Team admins can invite users, promote members to team admin, and remove members. Users can belong to multiple teams with different roles in each.
- Class: primary-user-loop
- Status: validated
- Description: Any user can create a team and becomes its admin automatically. Team admins can invite users, promote members to team admin, and remove members. Users can belong to multiple teams with different roles in each.
- Why it matters: Core collaboration model.
- Source: user
- Primary owning slice: M001-6cqls8/S02
- Supporting slices: none
- Validation: S03 closes the collaboration loop with full coverage against real Postgres. Invite issuance: `test_invite_returns_code_url_expires_at` (200 with code/url/expires_at), `test_invite_personal_team_returns_403`, `test_invite_as_non_admin_returns_403`, `test_invite_as_member_not_admin_returns_403`. Invite acceptance: `test_join_valid_code_adds_member_and_marks_used`, `test_join_unknown_code_returns_404`, `test_join_expired_code_returns_410`, `test_join_used_code_returns_410`, `test_join_duplicate_member_returns_409`, `test_join_atomicity_on_membership_insert_failure` (rollback leaves invite.used_at NULL). Role management: `test_patch_role_promotes_member_to_admin`, `test_patch_role_demotes_admin_to_member`, `test_patch_role_as_non_admin_returns_403`, `test_patch_role_demoting_last_admin_returns_400`, `test_patch_role_unknown_target_returns_404`, `test_patch_role_invalid_body_returns_422`. Member removal: `test_delete_member_removes_row_returns_204`, `test_delete_last_admin_returns_400`, `test_delete_on_personal_team_returns_400`. Multi-team membership with distinct roles is end-to-end demonstrated by joiners holding `member` role on the joined team while keeping `admin` on their personal team. Full backend suite at 125/125 passing.
- Notes: Validated by S03 — closes M001-6cqls8 collaboration loop. S04 frontend wiring and S05 system-admin panel build on these endpoints but do not change R004 status.

### R005 — Each user-team pair gets its own Docker container with a dedicated mounted volume at `/workspaces/<user_id>/<team_id>/`. Containers are isolated; no shared filesystem between users.
- Class: core-capability
- Status: validated
- Description: Each user-team pair gets its own Docker container with a dedicated mounted volume at `/workspaces/<user_id>/<team_id>/`. Containers are isolated; no shared filesystem between users.
- Why it matters: Isolation and independence are the core product promise.
- Source: user
- Primary owning slice: M002/S01
- Supporting slices: M002/S02
- Validation: Validated end-to-end across M002 slices. S01 (`test_m002_s01_e2e.py`) provisions per-(user, team) container with labels `user_id=`/`team_id=`/`perpetuity.managed=true`, name `perpetuity-ws-<first8-team>`, mounted at `/workspaces/<u>/<t>/`. S02 (`test_m002_s02_volume_cap_e2e.py`, ~17.87s) replaces plain bind-mount with kernel-enforced loopback-ext4 `.img` per (user, team) — alice's 1 GiB cap returns ENOSPC at the kernel boundary while bob's separate volume is untouched (neighbor isolation proven). S04 (`test_m002_s04_e2e.py`) proves the workspace_volume row + .img persist across container reap. S05 T01 (`test_m002_s05_full_acceptance_e2e.py`, ~46s combined) re-validates per-(user, team) container + dedicated volume across the full lifecycle: signup → provision → durability across orchestrator restart → reaper-reap → workspace_volume row persists in Postgres → re-provision implicitly remounts the existing volume. All slice e2es run against real Postgres + Redis + Docker daemon, no mocks. MEM134 redaction sweep finds zero email/full_name leaks.
- Notes: Dedicated orchestrator service manages container lifecycle.

### R006 — Containers spin up on demand (when a task or user session needs one). Idle containers shut down automatically after a configurable timeout. Volumes persist across shutdowns. New containers for same user-team remount the existing volume.
- Class: operability
- Status: validated
- Description: Containers spin up on demand (when a task or user session needs one). Idle containers shut down automatically after a configurable timeout. Volumes persist across shutdowns. New containers for same user-team remount the existing volume.
- Why it matters: Operational cost control without losing user state.
- Source: user
- Primary owning slice: M002/S02
- Supporting slices: M002/S02, M002/S04
- Validation: Validated end-to-end by `backend/tests/integration/test_m002_s04_e2e.py::test_m002_s04_full_demo` (e2e marker, ~19s wall-clock against real Postgres + Redis + orchestrator + Docker daemon — no mocks). The test drives the full R006 contract: alice POSTs two sessions which both attach to distinct tmux sessions inside the SAME (user, team) container (on-demand spin-up, multi-session reuse); after the admin PUTs idle_timeout_seconds=3 and ~6s passes with no I/O and no live attach, the orchestrator's two-phase reaper kills the surviving tmux session and reaps the container (idempotent timeout-driven shutdown); the workspace_volume row + underlying loopback .img persist across the reap; alice's third POST re-provisions a fresh container and remounts the EXISTING volume, with `cat /workspaces/<team_id>/marker.txt` returning the bytes written before the reap (D015 invariant — volumes outlive containers). The reaper's idle timeout is now admin-tunable via `system_settings.idle_timeout_seconds` (1..86400 int, default 1800s), proven by the dynamic PUT in the e2e. Asserted log lines: `reaper_killed_session reason=idle_no_attach`, `reaper_reaped_container reason=last_session_killed`, `idle_timeout_seconds_resolved`. MEM134 redaction sweep over backend + orchestrator logs finds zero email/full_name leaks.
- Notes: Celery workers request containers from orchestrator. Orchestrator tracks idle time in Postgres.

### R007 — FastAPI exposes a `/ws/terminal/{session_id}` endpoint that relays I/O between the browser and a pty process running inside the user's container via `docker exec`.
- Class: primary-user-loop
- Status: validated
- Description: FastAPI exposes a `/ws/terminal/{session_id}` endpoint that relays I/O between the browser and a pty process running inside the user's container via `docker exec`.
- Why it matters: Real-time terminal access is the core interaction surface for developers.
- Source: user
- Primary owning slice: M002/S03
- Supporting slices: M002/S04
- Validation: Validated by S01's `test_m002_s01_full_e2e` (echo round-trip + tmux durability across `docker compose restart orchestrator`) and by S04's `test_m002_s04_full_demo` (two distinct WS sessions per `/api/v1/ws/terminal/{session_id}` attach to distinct tmux sessions inside one container; data frames carry stdout bytes from `docker exec` of `tmux attach-session`). Backend bridge proxies frames verbatim per the S01-locked WS frame protocol. Existence-enumeration prevention: 1008 `session_not_owned` close shape is identical for missing-vs-not-owned (S01); GET /api/v1/sessions/{sid}/scrollback (S04/T03) extends the same no-enumeration rule to the public scrollback proxy.
- Notes: Uses `aiodocker` for async container interaction.

### R008 — A user can open multiple terminal windows/tabs for the same team workspace. Each instance is a separate pty process but all operate on the same container filesystem (`/workspaces/<user_id>/<team_id>/`).
- Class: primary-user-loop
- Status: validated
- Description: A user can open multiple terminal windows/tabs for the same team workspace. Each instance is a separate pty process but all operate on the same container filesystem (`/workspaces/<user_id>/<team_id>/`).
- Why it matters: Standard developer workflow — multiple terminal panes for the same project.
- Source: user
- Primary owning slice: M002/S01
- Supporting slices: M002/S04
- Validation: Validated end-to-end by `backend/tests/integration/test_m002_s04_e2e.py::test_m002_s04_full_demo`: alice opens two distinct WS sessions for her personal team — both POST /api/v1/sessions calls hit the SAME (user, team) container (orchestrator response.created==True for the first, False for the second per MEM120) but each session is a distinct tmux session attached via `docker exec` and `tmux attach-session -t <session_id>`. The test writes a marker through sid_a (`echo 'a' > /workspaces/<team_id>/marker.txt`) and reads it back through sid_b (`cat /workspaces/<team_id>/marker.txt` returns 'a' in the data-frame stream) — proving distinct tmux sessions but shared container filesystem. GET /api/v1/sessions returns set {sid_a, sid_b}. DELETE one leaves the sibling AND the container alive. The orchestrator-side AttachMap (S04/T01) tracks per-session live-attach counts via `register`/`unregister` calls in `routes_ws.py::session_stream`, observable through `attach_registered`/`attach_unregistered` log lines (UUIDs only).
- Notes: Multiple containers share a single mounted volume per user-team.

### R021 — Frontend ships with a valid Web App Manifest and service worker. Users can install the app on their phone or desktop home screen.
- Class: launchability
- Status: validated
- Description: Frontend ships with a valid Web App Manifest and service worker. Users can install the app on their phone or desktop home screen.
- Why it matters: Mobile-first product; installability is a hard requirement.
- Source: user
- Primary owning slice: M005-oaptsz/S01
- Supporting slices: none
- Validation: S01 delivered vite-plugin-pwa injectManifest with route-classified service worker (NetworkOnly /api/* and /ws/*, CacheFirst hashed assets, precache app shell), Web App Manifest + 192/512/maskable/180 icons, InstallBanner (Android beforeinstallprompt + iOS one-time toast) and OfflineBanner mounted in _layout. SW NetworkOnly contract proven by m005-oaptsz-sw-bypass.spec.ts (1/1 pass) using context.route() at BrowserContext level. Lighthouse install criteria satisfied; production preview at :4173 launches standalone.
- Notes: Vite PWA plugin (vite-plugin-pwa) is the standard approach.

### R022 — Every feature is accessible and usable on a phone screen. Touch targets meet mobile standards, no desktop-only flows, navigation works on small screens.
- Class: quality-attribute
- Status: validated
- Description: Every feature is accessible and usable on a phone screen. Touch targets meet mobile standards, no desktop-only flows, navigation works on small screens.
- Why it matters: User explicitly requires mobile as a primary interface, not a degraded experience.
- Source: user
- Primary owning slice: M005-oaptsz/S01
- Supporting slices: M005-oaptsz/S02, M005-oaptsz/S03, M005-oaptsz/S04
- Validation: S01 four-project Playwright matrix (chromium, mobile-chrome Pixel-5, iphone-13-mobile-safari, desktop-firefox) walks 7 routes × assertNoHorizontalScroll + assertTouchTargets + 1% visual-diff. Design-system-primitive-floor (min-h-11/min-w-11) on Button, Input, PasswordInput, Tabs, SidebarTrigger inherits ≥44×44 to all consumers. S02 bell, S03 push prompt, S04 mic button all pass the same gate. 30/30 mobile-chrome+iphone-13 audit on S01; 16/16 with bell on S02; 15/17 on S04 (2 pre-existing /admin/teams chevron at 32×44px documented as MEM369).
- Notes: Mobile layout verification starts in M1 and is enforced throughout.

### R023 — Bell icon notification center shows all workflow and system notifications. PWA push notifications delivered to device/browser when app is backgrounded.
- Class: primary-user-loop
- Status: validated
- Description: Bell icon notification center shows all workflow and system notifications. PWA push notifications delivered to device/browser when app is backgrounded.
- Why it matters: Users need to know when workflows complete or fail without watching the screen.
- Source: user
- Primary owning slice: M005-oaptsz/S02
- Supporting slices: M005-oaptsz/S03
- Validation: S02 shipped notifications + notification_preferences tables, notify() helper with payload redaction and preference resolution, REST routes (list, mark-read, mark-all-read, preferences upsert), NotificationBell + Panel mounted in _layout with 5s polling refetchInterval. Cross-device sync proven by Playwright Scenario A (two BrowserContexts as same user, badge clears in second within 6s). 24/24 backend + 4/4 Playwright pass. S03 added Web Push: push_subscriptions table (s08), pywebpush dispatcher with VAPID signing, HTTP 410 immediate prune, 5xx after-5-consecutive prune, SW push/notificationclick handlers, PushPermissionPrompt. 41 push tests pass; real-device round-trip deferred to S05 operator UAT (S05-CHECKLIST.md).
- Notes: Web Push API for push; notification records in Postgres for in-app center.

### R024 — Users can configure which events trigger which notification types (in-app, push, or none) per workflow and per event type (success, failure, step completion).
- Class: quality-attribute
- Status: validated
- Description: Users can configure which events trigger which notification types (in-app, push, or none) per workflow and per event type (success, failure, step completion).
- Why it matters: Notification fatigue is real; users need control over what alerts them.
- Source: user
- Primary owning slice: M005-oaptsz/S02
- Supporting slices: M005-oaptsz/S03
- Validation: S02 shipped notification_preferences with COALESCE-based uniqueness on (user_id, COALESCE(workflow_id, '00..0'), event_type) — schema supports team-default (workflow_id NULL) plus per-workflow override (workflow_id = UUID); UI ships team-default toggles in NotificationPreferences settings tab. Defaults: failure→push+in-app, success→in-app, step_completed→none. S03 made the push column live: toggling push=true now gates pywebpush dispatcher fan-out. Per-workflow override UI deferred until workflow detail page ships (workflow engine slice in a future milestone) — schema is ready and notify() preference resolution can be widened with one lookup.
- Notes: none

### R025 — Every text input in the app shows a microphone icon. Clicking it starts Grok speech-to-text recording (waveform shown during recording). On stop, transcription is inserted into the text field. System-level API key, no per-user config needed.
- Class: differentiator
- Status: validated
- Description: Every text input in the app shows a microphone icon. Clicking it starts Grok speech-to-text recording (waveform shown during recording). On stop, transcription is inserted into the text field. System-level API key, no per-user config needed.
- Why it matters: Hands-free input for prompt entry is a first-class UX feature in an AI coding tool.
- Source: user
- Primary owning slice: S04
- Supporting slices: none
- Validation: S04 delivered: grok_stt_api_key registered as sensitive in system_settings (Fernet-encrypted, never round-tripped); POST /api/v1/voice/transcribe rate-limited 30/min/user with 429+Retry-After; VoiceInput/VoiceTextarea/Waveform/useVoiceRecorder primitives with mic button, live waveform, codec fallback, inline errors, and onChange injection; password/OTP/sensitive fields opted out at primitive level; 70/70 backend tests pass; 6/6 Playwright voice tests pass on mobile-chrome; redaction grep clean.
- Notes: Grok STT is a REST API (`FormData` audio upload). Mic → waveform display → transcription injected into field.

## Deferred

### R030 — Send workflow event notifications via email in addition to in-app/push.
- Class: operability
- Status: deferred
- Description: Send workflow event notifications via email in addition to in-app/push.
- Why it matters: Some users prefer email for async notification.
- Source: user
- Primary owning slice: none
- Supporting slices: none
- Validation: unmapped
- Notes: Deferred post-M006. SMTP infra exists in template but notification system not wired to it.

### R031 — Send workflow event notifications to a configured Slack channel.
- Class: operability
- Status: deferred
- Description: Send workflow event notifications to a configured Slack channel.
- Why it matters: Team notification in existing communication tools.
- Source: user
- Primary owning slice: none
- Supporting slices: none
- Validation: unmapped
- Notes: Deferred post-M006.

## Out of Scope

### R040 — Project folders are copied into each user's workspace independently. There is no live-synced shared filesystem between users working on the same project.
- Class: anti-feature
- Status: out-of-scope
- Description: Project folders are copied into each user's workspace independently. There is no live-synced shared filesystem between users working on the same project.
- Why it matters: Prevents scope confusion with collaborative file sync (not what this product does).
- Source: user
- Primary owning slice: none
- Supporting slices: none
- Validation: n/a
- Notes: By design. Each user's copy is independent.

### R041 — Personal teams cannot have other users invited to them. They are strictly single-user workspaces.
- Class: anti-feature
- Status: out-of-scope
- Description: Personal teams cannot have other users invited to them. They are strictly single-user workspaces.
- Why it matters: Prevents misuse of personal teams as ad-hoc shared spaces.
- Source: user
- Primary owning slice: none
- Supporting slices: none
- Validation: n/a
- Notes: API enforces this at the invite endpoint.

## Traceability

| ID | Class | Status | Primary owner | Supporting | Proof |
|---|---|---|---|---|---|
| R001 | core-capability | validated | M001-6cqls8/S01 | none | S01 delivered cookie signup/login/logout + /users/me and cookie-authenticated WS /ws/ping. All 21 slice-level tests pass against real Postgres: backend/tests/api/routes/test_auth.py (13 cases: cookie issuance, happy-path, duplicate email→400, wrong password→400, unknown email→400 uniform, missing/tampered/expired cookie→401, deleted-user cookie→401, logout clears, logout idempotent, redaction) + backend/tests/api/routes/test_ws_auth.py (6 cases: missing_cookie, invalid_token [garbage+expired], user_not_found, user_inactive, happy-path pong with role) + backend/tests/migrations/test_s01_migration.py (upgrade+downgrade round trip). Full suite 76/76 passing in 3.81s. |
| R002 | core-capability | validated | M001-6cqls8/S01 | M001-6cqls8/S05 | UserRole enum (user, system_admin) and TeamRole enum (member, admin) exist on User and TeamMember models — proven by the S01 migration test. is_superuser fully replaced in the API layer: get_current_active_superuser checks role==system_admin (S01). TeamRole enforced at the API by S03 invite/role/remove endpoints (PATCH/DELETE on /teams/{id}/members and team-admin-only invite). System-admin route gate now exercised end-to-end by S05: backend test_admin_teams.py (15 integration tests proving 200 for system_admin, 403 for normal user, 401 unauth, pagination, idempotency, 404s, cross-team bypass) and Playwright admin-teams.spec.ts (happy path: superuser sees all teams, drills into members, promotes a user via confirm dialog; non-admin redirected from /admin/teams off the /admin/* namespace by requireSystemAdmin). All endpoints emit structured INFO logs (admin_teams_listed, admin_team_members_listed, system_admin_promoted with already_admin flag). |
| R003 | primary-user-loop | validated | M001-6cqls8/S02 | none | S02 integration tests prove every signup creates exactly one TeamMember(role=admin) on a Team(is_personal=True): `test_signup_creates_personal_team` (happy path, slug+suffix shape), `test_signup_rolls_back_on_mid_transaction_failure` (atomicity — monkeypatched helper failure leaves no user/team row), `test_superuser_bootstrap_has_personal_team` (init_db wiring for FIRST_SUPERUSER), `test_get_teams_after_signup_returns_only_personal_team` (caller sees exactly 1 personal team), `test_invite_on_personal_team_returns_403` (invite endpoints reject personal teams with "Cannot invite to personal teams"). All 93/93 backend tests pass against real Postgres. |
| R004 | primary-user-loop | validated | M001-6cqls8/S02 | none | S03 closes the collaboration loop with full coverage against real Postgres. Invite issuance: `test_invite_returns_code_url_expires_at` (200 with code/url/expires_at), `test_invite_personal_team_returns_403`, `test_invite_as_non_admin_returns_403`, `test_invite_as_member_not_admin_returns_403`. Invite acceptance: `test_join_valid_code_adds_member_and_marks_used`, `test_join_unknown_code_returns_404`, `test_join_expired_code_returns_410`, `test_join_used_code_returns_410`, `test_join_duplicate_member_returns_409`, `test_join_atomicity_on_membership_insert_failure` (rollback leaves invite.used_at NULL). Role management: `test_patch_role_promotes_member_to_admin`, `test_patch_role_demotes_admin_to_member`, `test_patch_role_as_non_admin_returns_403`, `test_patch_role_demoting_last_admin_returns_400`, `test_patch_role_unknown_target_returns_404`, `test_patch_role_invalid_body_returns_422`. Member removal: `test_delete_member_removes_row_returns_204`, `test_delete_last_admin_returns_400`, `test_delete_on_personal_team_returns_400`. Multi-team membership with distinct roles is end-to-end demonstrated by joiners holding `member` role on the joined team while keeping `admin` on their personal team. Full backend suite at 125/125 passing. |
| R005 | core-capability | validated | M002/S01 | M002/S02 | Validated end-to-end across M002 slices. S01 (`test_m002_s01_e2e.py`) provisions per-(user, team) container with labels `user_id=`/`team_id=`/`perpetuity.managed=true`, name `perpetuity-ws-<first8-team>`, mounted at `/workspaces/<u>/<t>/`. S02 (`test_m002_s02_volume_cap_e2e.py`, ~17.87s) replaces plain bind-mount with kernel-enforced loopback-ext4 `.img` per (user, team) — alice's 1 GiB cap returns ENOSPC at the kernel boundary while bob's separate volume is untouched (neighbor isolation proven). S04 (`test_m002_s04_e2e.py`) proves the workspace_volume row + .img persist across container reap. S05 T01 (`test_m002_s05_full_acceptance_e2e.py`, ~46s combined) re-validates per-(user, team) container + dedicated volume across the full lifecycle: signup → provision → durability across orchestrator restart → reaper-reap → workspace_volume row persists in Postgres → re-provision implicitly remounts the existing volume. All slice e2es run against real Postgres + Redis + Docker daemon, no mocks. MEM134 redaction sweep finds zero email/full_name leaks. |
| R006 | operability | validated | M002/S02 | M002/S02, M002/S04 | Validated end-to-end by `backend/tests/integration/test_m002_s04_e2e.py::test_m002_s04_full_demo` (e2e marker, ~19s wall-clock against real Postgres + Redis + orchestrator + Docker daemon — no mocks). The test drives the full R006 contract: alice POSTs two sessions which both attach to distinct tmux sessions inside the SAME (user, team) container (on-demand spin-up, multi-session reuse); after the admin PUTs idle_timeout_seconds=3 and ~6s passes with no I/O and no live attach, the orchestrator's two-phase reaper kills the surviving tmux session and reaps the container (idempotent timeout-driven shutdown); the workspace_volume row + underlying loopback .img persist across the reap; alice's third POST re-provisions a fresh container and remounts the EXISTING volume, with `cat /workspaces/<team_id>/marker.txt` returning the bytes written before the reap (D015 invariant — volumes outlive containers). The reaper's idle timeout is now admin-tunable via `system_settings.idle_timeout_seconds` (1..86400 int, default 1800s), proven by the dynamic PUT in the e2e. Asserted log lines: `reaper_killed_session reason=idle_no_attach`, `reaper_reaped_container reason=last_session_killed`, `idle_timeout_seconds_resolved`. MEM134 redaction sweep over backend + orchestrator logs finds zero email/full_name leaks. |
| R007 | primary-user-loop | validated | M002/S03 | M002/S04 | Validated by S01's `test_m002_s01_full_e2e` (echo round-trip + tmux durability across `docker compose restart orchestrator`) and by S04's `test_m002_s04_full_demo` (two distinct WS sessions per `/api/v1/ws/terminal/{session_id}` attach to distinct tmux sessions inside one container; data frames carry stdout bytes from `docker exec` of `tmux attach-session`). Backend bridge proxies frames verbatim per the S01-locked WS frame protocol. Existence-enumeration prevention: 1008 `session_not_owned` close shape is identical for missing-vs-not-owned (S01); GET /api/v1/sessions/{sid}/scrollback (S04/T03) extends the same no-enumeration rule to the public scrollback proxy. |
| R008 | primary-user-loop | validated | M002/S01 | M002/S04 | Validated end-to-end by `backend/tests/integration/test_m002_s04_e2e.py::test_m002_s04_full_demo`: alice opens two distinct WS sessions for her personal team — both POST /api/v1/sessions calls hit the SAME (user, team) container (orchestrator response.created==True for the first, False for the second per MEM120) but each session is a distinct tmux session attached via `docker exec` and `tmux attach-session -t <session_id>`. The test writes a marker through sid_a (`echo 'a' > /workspaces/<team_id>/marker.txt`) and reads it back through sid_b (`cat /workspaces/<team_id>/marker.txt` returns 'a' in the data-frame stream) — proving distinct tmux sessions but shared container filesystem. GET /api/v1/sessions returns set {sid_a, sid_b}. DELETE one leaves the sibling AND the container alive. The orchestrator-side AttachMap (S04/T01) tracks per-session live-attach counts via `register`/`unregister` calls in `routes_ws.py::session_stream`, observable through `attach_registered`/`attach_unregistered` log lines (UUIDs only). |
| R009 | primary-user-loop | active | M003/S01 | none | unmapped |
| R010 | primary-user-loop | active | M003/S02 | none | unmapped |
| R011 | integration | active | M003/S03 | M005/S01 | unmapped |
| R012 | integration | active | M003/S01 | none | unmapped |
| R013 | integration | active | M004/S01 | M004/S02 | unmapped |
| R014 | integration | active | M004/S01 | M004/S02 | unmapped |
| R015 | primary-user-loop | active | M004/S02 | M005/S02 | unmapped |
| R016 | primary-user-loop | active | M005/S01 | M003/S03 | unmapped |
| R017 | core-capability | active | M005/S02 | M002/S02 | unmapped |
| R018 | failure-visibility | active | M005/S02 | none | unmapped |
| R019 | core-capability | active | M005/S03 | none | unmapped |
| R020 | primary-user-loop | active | M005/S01 | none | unmapped |
| R021 | launchability | validated | M005-oaptsz/S01 | none | S01 delivered vite-plugin-pwa injectManifest with route-classified service worker (NetworkOnly /api/* and /ws/*, CacheFirst hashed assets, precache app shell), Web App Manifest + 192/512/maskable/180 icons, InstallBanner (Android beforeinstallprompt + iOS one-time toast) and OfflineBanner mounted in _layout. SW NetworkOnly contract proven by m005-oaptsz-sw-bypass.spec.ts (1/1 pass) using context.route() at BrowserContext level. Lighthouse install criteria satisfied; production preview at :4173 launches standalone. |
| R022 | quality-attribute | validated | M005-oaptsz/S01 | M005-oaptsz/S02, M005-oaptsz/S03, M005-oaptsz/S04 | S01 four-project Playwright matrix (chromium, mobile-chrome Pixel-5, iphone-13-mobile-safari, desktop-firefox) walks 7 routes × assertNoHorizontalScroll + assertTouchTargets + 1% visual-diff. Design-system-primitive-floor (min-h-11/min-w-11) on Button, Input, PasswordInput, Tabs, SidebarTrigger inherits ≥44×44 to all consumers. S02 bell, S03 push prompt, S04 mic button all pass the same gate. 30/30 mobile-chrome+iphone-13 audit on S01; 16/16 with bell on S02; 15/17 on S04 (2 pre-existing /admin/teams chevron at 32×44px documented as MEM369). |
| R023 | primary-user-loop | validated | M005-oaptsz/S02 | M005-oaptsz/S03 | S02 shipped notifications + notification_preferences tables, notify() helper with payload redaction and preference resolution, REST routes (list, mark-read, mark-all-read, preferences upsert), NotificationBell + Panel mounted in _layout with 5s polling refetchInterval. Cross-device sync proven by Playwright Scenario A (two BrowserContexts as same user, badge clears in second within 6s). 24/24 backend + 4/4 Playwright pass. S03 added Web Push: push_subscriptions table (s08), pywebpush dispatcher with VAPID signing, HTTP 410 immediate prune, 5xx after-5-consecutive prune, SW push/notificationclick handlers, PushPermissionPrompt. 41 push tests pass; real-device round-trip deferred to S05 operator UAT (S05-CHECKLIST.md). |
| R024 | quality-attribute | validated | M005-oaptsz/S02 | M005-oaptsz/S03 | S02 shipped notification_preferences with COALESCE-based uniqueness on (user_id, COALESCE(workflow_id, '00..0'), event_type) — schema supports team-default (workflow_id NULL) plus per-workflow override (workflow_id = UUID); UI ships team-default toggles in NotificationPreferences settings tab. Defaults: failure→push+in-app, success→in-app, step_completed→none. S03 made the push column live: toggling push=true now gates pywebpush dispatcher fan-out. Per-workflow override UI deferred until workflow detail page ships (workflow engine slice in a future milestone) — schema is ready and notify() preference resolution can be widened with one lookup. |
| R025 | differentiator | validated | S04 | none | S04 delivered: grok_stt_api_key registered as sensitive in system_settings (Fernet-encrypted, never round-tripped); POST /api/v1/voice/transcribe rate-limited 30/min/user with 429+Retry-After; VoiceInput/VoiceTextarea/Waveform/useVoiceRecorder primitives with mic button, live waveform, codec fallback, inline errors, and onChange injection; password/OTP/sensitive fields opted out at primitive level; 70/70 backend tests pass; 6/6 Playwright voice tests pass on mobile-chrome; redaction grep clean. |
| R030 | operability | deferred | none | none | unmapped |
| R031 | operability | deferred | none | none | unmapped |
| R040 | anti-feature | out-of-scope | none | none | n/a |
| R041 | anti-feature | out-of-scope | none | none | n/a |
| R042 | core-capability | active | M003/S04 | M003/S05, M003/S06 | Integration test: connect WS, run echo hello, disconnect, docker compose restart orchestrator, reconnect same session_id, see prior scrollback in attach frame, run echo world in same shell. |
| R043 | core-capability | active | M003/S01 | M003/S02, M003/S03, M003/S04, M003/S05 | docker-compose.yml shows orchestrator as the only service mounting /var/run/docker.sock; backend integration tests fail closed if ORCHESTRATOR_API_KEY is wrong/missing; orchestrator rejects unauthorized HTTP and WS upgrade requests with 401/1008. |
| R044 | operability | active | M003/S02 | M003/S01, M003/S03 | Integration test: container provisioned with HostConfig limits set; volume is a loopback ext4 file under /var/lib/perpetuity/vols/; writes past the cap return ENOSPC; admin PUT /api/v1/admin/settings raises the cap, next provision grows the volume via resize2fs; shrink request with overflow returns 4xx with affected pairs listed. |
| R045 | admin/support | active | M003/S02 | none | Alembic migration creates system_settings table with seeded workspace_volume_size_gb default (10); GET /api/v1/admin/settings returns current values; PUT updates them; non-system-admin users get 403; orchestrator reads the latest value on each provision call. |

## Coverage Summary

- Active requirements: 16
- Mapped to slices: 16
- Validated: 13 (R001, R002, R003, R004, R005, R006, R007, R008, R021, R022, R023, R024, R025)
- Unmapped active requirements: 0
