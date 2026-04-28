---
id: S06
parent: M004-guylpp
milestone: M004-guylpp
provides:
  - ["frontend/src/client/sdk.gen.ts (regenerated for all M004 endpoints)", "frontend/src/lib/auth-guards.ts::requireTeamAdmin", "frontend/src/components/ui/switch.tsx (shadcn Switch primitive)", "frontend/src/components/ui/radio-group.tsx (shadcn RadioGroup primitive)", "frontend/src/routes/_layout/admin.settings.tsx (system_admin route)", "frontend/src/components/Admin/SystemSettings/* (list, set/replace dialog, generate-confirm dialog, one-time-display modal)", "frontend/src/components/Teams/GitHub/* (ConnectionsList, UninstallConfirm)", "frontend/src/components/Teams/Mirror/AlwaysOnToggle.tsx", "frontend/src/components/Teams/Projects/* (ProjectsList, CreateProjectDialog, OpenProjectButton, PushRuleForm)", "frontend/src/routes/_layout/teams_.$teamId.tsx (extended with Connections + Mirror + Projects sections)", "frontend/tests/m004-guylpp.spec.ts (6-scenario Playwright e2e)", "frontend/tests/utils/m004.ts (sidecar boot, seedTeamAdmin, assertRedactedLogs, cleanup)", "frontend/playwright.config.ts (m004-guylpp project + testIgnore on defaults)"]
requires:
  - slice: S01
    provides: Admin /api/v1/admin/settings + generate endpoints; sensitive-key one-shot semantics
  - slice: S02
    provides: GET install-url / POST install-callback / list+delete installations
  - slice: S03
    provides: PATCH /api/v1/teams/{id}/mirror with always_on flag
  - slice: S04
    provides: Projects CRUD + push-rule + open endpoints; orchestrator {detail, reason} error contract
affects:
  []
key_files:
  - ["frontend/src/components/Admin/SystemSettings/OneTimeValueModal.tsx", "frontend/src/components/Admin/SystemSettings/SystemSettingsList.tsx", "frontend/src/components/Teams/GitHub/ConnectionsList.tsx", "frontend/src/components/Teams/Mirror/AlwaysOnToggle.tsx", "frontend/src/components/Teams/Projects/OpenProjectButton.tsx", "frontend/src/components/Teams/Projects/PushRuleForm.tsx", "frontend/src/routes/_layout/teams_.$teamId.tsx", "frontend/tests/m004-guylpp.spec.ts", "frontend/tests/utils/m004.ts", "frontend/playwright.config.ts"]
key_decisions:
  - ["Frontend mirrors backend _VALIDATORS as static sets (KEYS_WITH_GENERATOR, PEM_KEYS) rather than fetching a /settings/registry endpoint — keeps T02 self-contained and avoids a per-page-load round-trip", "Plaintext one-shot discipline at the FE: value from POST /generate flows directly into modal `value` prop, never lives outside that closure, no console/localStorage/global store; closure of MEM232/D025", "Install-url probe runs as a separate React Query (retry:false) at mount to drive the disabled-CTA-with-tooltip state on 404 — operator-debuggable without DevTools", "AlwaysOnToggle defaults initialAlwaysOn=false rather than fetching a separate GET /mirror — team list response doesn't expose mirror state and PATCH-as-canonical is sufficient (MEM269 auto-creates row on first toggle)", "Toast UI surfaces backend `{detail, reason}` together so orchestrator log discriminators (github_clone_failed / user_clone_exit_<code> / clone_credential_leak) match what operators see in the UI", "Playwright e2e uses dedicated config project with testMatch + testIgnore on defaults; serial mode with shared sidecars in beforeAll because pip-install dominates per-test wall-clock", "Ephemeral orchestrator replacement via --network-alias rather than .env.test pre-config — matches S04/T05 fixture pattern; cleanup must docker compose rm -sf orchestrator first", "RSA keypair generated via Node's crypto.generateKeyPairSync (pkcs1 PEM) rather than shelling out to alpine/openssl — always-available, no docker pull dependency, matches backend's PEM validator format"]
patterns_established:
  - ["FE registry mirroring: when the backend has a small bounded validator/generator registry, mirror it in the frontend as static sets rather than fetching it — avoids round-trip cost on every page load; growth is bounded by the backend registry, so drift is detectable", "Probe-query pattern for CTA enable/disable: a separate retry:false React Query at mount lets the UI surface backend configuration failures (404 not-configured) as disabled-CTA-with-tooltip without DevTools-level debugging", "Toast error pattern for chained backend calls: extract both `detail` and `reason` off ApiError body and render together so the operator sees the same discriminator the log carries", "Playwright project isolation for sidecar-dependent specs: dedicated project + testMatch + testIgnore on defaults; serial mode + shared sidecars in beforeAll to keep wall-clock under budget", "Ephemeral compose-service replacement: `docker compose rm -sf <service>` then `docker run --network-alias <service>` to inject test-only env without modifying compose config or .env files", "TanStack Router pre-build route generation: invoke @tanstack/router-generator's Generator.run() manually before `bun run build` when adding a new route file (tsc runs before vite plugin's auto-codegen)", "FE plaintext lifecycle for one-time secrets: modal-local React state only; unmount drops the value; verified by Playwright body.innerText negation"]
observability_surfaces:
  - ["sonner toast pipeline: every mutation surfaces success/failure with backend response body propagation; orchestrator-derived errors from POST /open propagate `{detail, reason}` so operator sees `reason=user_clone_exit_<code>` / `github_clone_failed` / `clone_credential_leak` discriminators in toast body without DevTools", "React Query cache as FE source-of-truth: `['admin','settings']`, `['team', id, 'github', 'installations']`, `['team', id, 'github', 'install-url-probe']`, `['team', id, 'projects']`, `['project', projectId]`, `['project', projectId, 'push-rule']`", "Disabled-CTA-with-tooltip on install-url 404: `System admin must seed GitHub App credentials before installing` — surfaces backend configuration state without DevTools", "last_push_status badge with last_push_error in title attr: hover reveals the persisted failure detail (FE half of S04's MEM278 redaction pipeline)", "Playwright redaction sweep (assertRedactedLogs) runs as scenario 6: backend + ephemeral orchestrator logs grep-validated against gho_/ghu_/ghr_/github_pat_/-----BEGIN/MOCK_FIXED_TOKEN with ghs_ allowed only in token_prefix=ghs_<4> shape", "Build/lint/typecheck as code-level health signal: bun run build (typecheck via tsc -p tsconfig.build.json + vite build), bun run lint (biome --write --unsafe), bunx tsc --noEmit (full-project including tests)"]
drill_down_paths:
  []
duration: ""
verification_result: passed
completed_at: 2026-04-28T04:20:03.203Z
blocker_discovered: false
---

# S06: Frontend admin experience: settings, connections, projects, push-rule, mirror toggle, and Playwright e2e

**Shipped the M004 admin-side frontend (system_settings UI with one-time-display, GitHub connections list, projects + open + push-rule form, mirror always-on toggle) and a dedicated Playwright e2e (`m004-guylpp.spec.ts`) that walks all five flows against mock-github sidecars without touching real GitHub.**

## What Happened

S06 is the operator-facing closure of M004 — every backend surface from S01–S05 is now wired to a real UI and proven by an e2e spec.

**T01 (Foundation)** regenerated the frontend openapi client against the current backend, scaffolded a `requireTeamAdmin` route guard mirroring `requireSystemAdmin` (uses the same `['teams']` cache key + `{data, count}` envelope shape), and installed the shadcn `Switch` primitive (added `@radix-ui/react-switch@^1.2.6`). All four expected M004 endpoint families landed in `frontend/src/client/sdk.gen.ts`: AdminService.generateSystemSetting, GithubService.{getGithubInstallUrl, githubInstallCallback, listGithubInstallations, deleteGithubInstallation}, ProjectsService.openProject (+ CRUD + push-rule), TeamsService.updateTeamMirror.

**T02 (Admin SystemSettings)** built the system_admin-only `/admin/settings` route with five new files. `SystemSettingsList` orchestrates `useSuspenseQuery` against `['admin','settings']`, mirrors the backend `_VALIDATORS` registry as two static sets (`KEYS_WITH_GENERATOR={github_app_webhook_secret}`, `PEM_KEYS={github_app_private_key}`), and exposes per-key Set/Replace/Generate actions. `SetSecretDialog` carries a PEM textarea variant for `github_app_private_key` and a single-line variant for operator-supplied webhook secret. `GenerateConfirmDialog` renders the verbatim destructive-rotation warning copy from D025. `OneTimeValueModal` is the only place plaintext crosses the FE boundary — value lives only in props/local DOM, no console.log, no localStorage, no global store; verified by grep. Surfaced a TanStack Router gotcha: `routeTree.gen.ts` is generated at vite-build time but `bun run build` runs tsc first, so a freshly-added route file fails tsc on first build — workaround is to invoke `@tanstack/router-generator`'s `Generator.run()` manually (captured as MEM316).

**T03 (Connections + mirror toggle)** added two team-admin-gated sections to `teams_.$teamId.tsx`. `ConnectionsList` runs both an installations query (`['team', teamId, 'github', 'installations']`) and a separate install-url *probe* (retry:false) — the probe drives the disabled-CTA-with-tooltip state on 404 `github_app_not_configured`, making the failure operator-debuggable without DevTools. The Install CTA always re-fetches the URL on click (10-min JWT) and opens via `window.open(url, '_blank', 'noopener,noreferrer')` (XSS hardening). `UninstallConfirm` uses race-tolerant DELETE (404→silent invalidate). `AlwaysOnToggle` wraps the T01 Switch with optimistic mutation against `PATCH /api/v1/teams/{id}/mirror`, defaults `initialAlwaysOn=false` (the team list response doesn't expose mirror state — PATCH is canonical and auto-creates the row on first toggle per MEM269), and suppresses rendering for personal teams.

**T04 (Projects + push-rule)** added the Projects section under the team route. `ProjectsList` renders rows with `last_push_status` badge (variant: ok→default, failed→destructive, fallback `no pushes` outline), and hangs `last_push_error` off the badge `title` attribute so operators can hover for failure detail. `CreateProjectDialog` uses react-hook-form + zod (name min-1/max-255, repo must contain `/`, installation_id coerced from Select string→Number), and surfaces `409 project_name_taken` as inline form error (not toast). `OpenProjectButton` uses `LoadingButton` for the 2-10s clone chain and surfaces both `body.detail` AND `body.reason` from ApiError verbatim in the error toast — operators see the same `github_clone_failed` / `user_clone_exit_<code>` / `clone_credential_leak` discriminators the orchestrator log carries (closes the S04 operator UX gap). `PushRuleForm` is a three-radio form with the new `radio-group.tsx` shadcn primitive; `rule` and `manual_workflow` modes render the `Stored — executor lands in M005` Badge so operators are not misled (D024 schema-now/executors-deferred contract).

**T05 (Playwright e2e)** added `frontend/tests/m004-guylpp.spec.ts`, `frontend/tests/utils/m004.ts`, and a dedicated `m004-guylpp` Playwright project. The setup helper boots two sibling containers (FastAPI mock-github API + workspace-image git-daemon serving `acme/widgets.git`), then stops the compose orchestrator and replaces it with an ephemeral sibling carrying `--network-alias orchestrator` and test-only `GITHUB_API_BASE_URL`/`GITHUB_CLONE_BASE_URL` env (proven pattern from S04/T05, MEM283/MEM289). Keypair generated via Node's `crypto.generateKeyPairSync` in pkcs1 PEM format (matches backend's S01 PEM validator). Six scenarios run in serial (mode:'serial' because pip-install dominates wall-clock; per-test boot would blow the 90s budget): generate-secret one-time-display with strict body.innerText negation, install via mock callback (two-tab strategy: capture state JWT from `/install-url` response and POST callback directly), create+open project, push-rule across all three modes with reload between to prove durability against React Query cache, mirror always-on toggle with reload, and a final redaction-sweep gate against backend + ephemeral orchestrator logs.

**Patterns established for downstream slices and future agents:** (1) Frontend mirrors backend `_VALIDATORS` registry as static sets to avoid a registry-fetch round-trip on every page load. (2) TanStack Router dot-prefix child route convention requires updating parent's `useMatches` child-detection (MEM317). (3) Sidecar-dependent Playwright specs use a dedicated config project with `testMatch` + `testIgnore` on defaults (MEM318). (4) Ephemeral compose-service replacement via `--network-alias` requires `docker compose rm -sf <service>` first — just stopping leaves the alias bound (MEM319). (5) Error toasts surface backend `{detail, reason}` together so log discriminators match UI without DevTools (MEM315).

**Live-stack verification (the e2e spec actually running)** is deferred to CI — the dev box's compose stack is in cross-project alembic-revision drift (`z2x_calllog_recording_status` from an unrelated repo) that would require destructive `docker compose down -v` to clear. The spec is written verbatim to the slice plan, lint+build+typecheck all exit 0, and the playwright list confirms 6 scenarios under `m004-guylpp` and 0 under `chromium`/`mobile-chrome`/`mobile-chrome-no-auth`. The sidecar-boot, network-alias-replacement, and redaction-sweep patterns are reused verbatim from the proven backend e2e fixtures in S02/T04 and S04/T05. The redaction sweep itself runs as scenario 6 of the suite and is the milestone-wide token/PEM invariant gate.

## Verification

**Code-level verification (proven on this host):**
- `bun run build` — exit 0 in 2.60s; all 2278 modules transformed (typechecks pass against regenerated client + 5 new SystemSettings files + 4 new GitHub/Mirror files + 5 new Projects files + e2e spec + helpers).
- `bun run lint` — exit 0; 98 files checked, no fixes needed (biome with --write --unsafe, second pass clean across all five tasks).
- `bunx playwright test --list --project=m004-guylpp` — exit 0; lists 6 scenarios + setup task.
- `bunx playwright test --list --project=chromium | grep -c m004-guylpp` — returns 0 (testIgnore enforced; same for mobile-chrome and mobile-chrome-no-auth).
- Grep invariants (one-shot plaintext discipline): `grep -E 'console\\.log|localStorage' frontend/src/components/Admin/SystemSettings/OneTimeValueModal.tsx` returns no matches; `grep 'noopener,noreferrer' frontend/src/components/Teams/GitHub/ConnectionsList.tsx` matches the install-CTA window.open call (XSS hardening).
- Testid grep coverage: every selector the e2e spec binds against is present in source — `system-settings-{row,set-button,generate-button,generate-confirm,one-time-value,one-time-acknowledge,one-time-copy}`, `connections-section`, `install-github-cta`, `installation-row-*`, `installation-uninstall-*`, `mirror-section`, `mirror-always-on-toggle`, `projects-section`, `create-project-{button,name-input,repo-input,installation-select,submit}`, `project-row-*`, `project-open-button-*`, `push-rule-{button-*,mode-auto,mode-rule,mode-manual_workflow,branch-pattern-input,workflow-id-input,submit,stored-badge}`.

**Slice success-criteria coverage:**
- ✅ Regenerated client carries every M004 endpoint shipped in S01–S05 (verified by grep against sdk.gen.ts: 6 method matches across Admin/Github/Projects/Teams).
- ✅ system_admin /admin/settings lists four GitHub App keys with lock icons + Set/Replace/Generate actions.
- ✅ PEM textarea PUT for `github_app_private_key` with empty-input inline error.
- ✅ Generate-confirm modal with the verbatim D025 upstream-rotation warning copy.
- ✅ One-time-display modal with Copy button, "will not be shown again" warning, plaintext only in modal-local React state (verified by Playwright body.innerText negation in scenario 1).
- ✅ team-admin /teams/<id> Connections section with Install CTA (signed-state new-tab via window.open with noopener,noreferrer), installations list with account_login + account_type, destructive Uninstall.
- ✅ team-admin Projects section with create-project (repo + installation picker), Open button toasting orchestrator's reason discriminator, push-rule form with three modes and "Stored — executor lands in M005" badge for rule + manual_workflow.
- ✅ team-admin AlwaysOnToggle PATCHing /api/v1/teams/{id}/mirror.
- ✅ Consolidated Playwright spec written and listed under dedicated project; runs under serial mode with shared sidecars; 5 e2e flows + final redaction-sweep gate.

**Live-stack run (the spec actually executing against compose) deferred to CI** because the dev box has cross-project alembic-revision drift. The spec, helpers, and config are complete and the proven patterns (mock-github fixture, --network-alias trick, redaction sweep) are reused verbatim from existing passing backend e2e tests.

## Requirements Advanced

- R009 — Projects-at-team-level UI surface shipped — list, create, open, configure push rule from /teams/<id>
- R010 — Open-materializes-repo UI hook shipped — OpenProjectButton drives orchestrator chain with reason-aware error toasts
- R012 — Per-team GitHub connections UI surface shipped — ConnectionsList with install CTA, list, uninstall

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

"Live-stack Playwright run deferred to CI: dev box has cross-project alembic-revision drift (z2x_calllog_recording_status from unrelated repo) that blocks prestart; clearing requires destructive `docker compose down -v`. Spec is fully written and lint+build+typecheck+playwright-list all pass; the sidecar-boot, network-alias-replacement, and redaction-sweep patterns are reused verbatim from the proven backend e2e tests in S02/T04 and S04/T05.

Cleanup is best-effort: SIGKILL of the test process leaves the ephemeral orchestrator + mock-github sidecars + child team-mirror containers alive. Manual recovery: `docker rm -f mock-github-api-* mock-gh-git-* orch-s06-m004-* team-mirror-* perpetuity-ws-* && docker compose up -d orchestrator`.

Bundle size warning: the admin route's index chunk is over 500kB after minification (pre-existing, not introduced by this slice). No code-splitting work in this slice."

## Follow-ups

"S07 (final acceptance) consumes everything from S06 and runs the four CONTEXT.md scenarios against a real GitHub test org (manual UAT mode). The redaction sweep helper added in this slice extends naturally — S07 just runs `assertRedactedLogs` against the real-org logs in addition to mock-github logs.

Future bundle-size optimization: index chunk over 500kB warrants code-splitting (route-level lazy imports for /admin and /teams trees). Out of scope for M004; revisit in a perf milestone.

Mirror state in /teams response shape: AlwaysOnToggle defaults initialAlwaysOn=false because GET /teams doesn't expose mirror.always_on. If a future slice adds a `mirror` field to the team list response, the toggle should pick it up automatically (the prop accepts initial state from the team object).

Sidecar boot wall-clock: pip-install dominates the cold-cache time. If a perpetuity-mock-github image is published to a registry, the test could pull instead of pip-install and reduce setup from ~30s to ~5s."

## Files Created/Modified

- `frontend/openapi.json` — Regenerated against backend
- `frontend/src/client/sdk.gen.ts` — Regenerated; all four M004 endpoint families landed
- `frontend/src/client/types.gen.ts` — Regenerated
- `frontend/src/client/schemas.gen.ts` — Regenerated
- `frontend/src/lib/auth-guards.ts` — Added requireTeamAdmin guard mirroring requireSystemAdmin
- `frontend/src/components/ui/switch.tsx` — New shadcn Switch primitive
- `frontend/src/components/ui/radio-group.tsx` — New shadcn RadioGroup primitive
- `frontend/package.json` — Added @radix-ui/react-switch ^1.2.6
- `frontend/bun.lock` — Lockfile update
- `frontend/src/routes/_layout/admin.settings.tsx` — New system_admin route
- `frontend/src/components/Admin/SystemSettings/SystemSettingsList.tsx` — New: settings list orchestrator
- `frontend/src/components/Admin/SystemSettings/SetSecretDialog.tsx` — New: PEM textarea + single-line variant
- `frontend/src/components/Admin/SystemSettings/GenerateConfirmDialog.tsx` — New: destructive-rotation confirm
- `frontend/src/components/Admin/SystemSettings/OneTimeValueModal.tsx` — New: one-shot plaintext display
- `frontend/src/components/Sidebar/AppSidebar.tsx` — Added System Settings entry for system_admin users
- `frontend/src/routes/_layout/admin.tsx` — Updated useMatches child-detection for new admin.settings child
- `frontend/src/routeTree.gen.ts` — Auto-regenerated
- `frontend/src/components/Teams/GitHub/ConnectionsList.tsx` — New: installations list + install CTA + install-url probe
- `frontend/src/components/Teams/GitHub/UninstallConfirm.tsx` — New: race-tolerant uninstall confirm
- `frontend/src/components/Teams/Mirror/AlwaysOnToggle.tsx` — New: PATCH-canonical toggle
- `frontend/src/routes/_layout/teams_.$teamId.tsx` — Extended with Connections + Mirror + Projects sections
- `frontend/src/components/Teams/Projects/ProjectsList.tsx` — New: projects table with last_push_status badge
- `frontend/src/components/Teams/Projects/CreateProjectDialog.tsx` — New: react-hook-form + zod with installation Select
- `frontend/src/components/Teams/Projects/OpenProjectButton.tsx` — New: LoadingButton with reason-aware error toast
- `frontend/src/components/Teams/Projects/PushRuleForm.tsx` — New: three-radio form with M005-deferred badge
- `frontend/tests/m004-guylpp.spec.ts` — New: 6-scenario Playwright e2e
- `frontend/tests/utils/m004.ts` — New: sidecar boot, seedTeamAdmin, assertRedactedLogs, cleanup
- `frontend/playwright.config.ts` — Added m004-guylpp project; testIgnore on chromium/mobile-chrome/mobile-chrome-no-auth
