# S06: Frontend admin experience: settings, connections, projects, push-rule, mirror toggle, and Playwright e2e — UAT

**Milestone:** M004-guylpp
**Written:** 2026-04-28T04:20:03.203Z

# S06 UAT — M004 Frontend Admin Experience

**Goal:** Verify the full M004 admin-side experience against the live compose stack with mock-github sidecars. The Playwright spec `frontend/tests/m004-guylpp.spec.ts` codifies these scenarios; this UAT documents the manual + automated verification path.

## Preconditions

1. Clean compose stack: `docker compose down -v && docker compose build backend orchestrator && docker compose up -d db redis backend orchestrator` exits 0.
2. Frontend dev server running: `cd frontend && VITE_API_URL=http://localhost:8001 bun run dev` reachable at `http://localhost:5173`.
3. Superuser credentials available (default seed: `superuser@example.com` / `changethis123`).
4. Required env: `SYSTEM_SETTINGS_ENCRYPTION_KEY` set in backend container (M004/S01 contract).
5. No existing system_settings rows for `github_app_*` keys (or test will exercise replace path instead of set).

## Scenario 1: Generate webhook secret with one-time-display modal

**Preconditions:** Logged in as superuser; on `/admin/settings`.

**Steps:**
1. Locate row `system-settings-row-github_app_webhook_secret`. Expected: lock icon visible, has_value badge shows `Empty`.
2. Click `system-settings-generate-button-github_app_webhook_secret`. Expected: GenerateConfirmDialog opens with the destructive-rotation warning copy: `"Re-generating breaks any existing GitHub webhook deliveries until you update the upstream secret on github.com — proceed?"`.
3. Click Cancel. Expected: dialog closes; no POST fires; has_value still `Empty`.
4. Re-open the dialog and click `system-settings-generate-confirm`. Expected: POST `/admin/settings/github_app_webhook_secret/generate` fires; OneTimeValueModal opens.
5. Read the value from `system-settings-one-time-value`. Expected: non-empty string of length ≥ 32; Copy button (`system-settings-one-time-copy`) is present.
6. Click `system-settings-one-time-copy`. Expected: clipboard contains the plaintext (browser permission allowing).
7. Click `system-settings-one-time-acknowledge`. Expected: modal unmounts; list re-renders.
8. Re-read `system-settings-row-github_app_webhook_secret`. Expected: has_value badge now shows `Set`; no plaintext anywhere on the page.
9. Negative test: `await page.locator('body').innerText()` MUST NOT contain the captured plaintext substring.

**Pass criteria:** Steps 1–9 all succeed; step 9 strictly excludes the captured value (one-shot plaintext discipline at the FE).

## Scenario 2: Install GitHub App via mock callback

**Preconditions:** Mock-github sidecar booted; `github_app_private_key`, `github_app_id`, `github_app_client_id` seeded via admin PUT; team-admin user logged in (fresh browser context per MEM064); on `/teams/<non-personal-team-id>`.

**Steps:**
1. Locate `connections-section`. Expected: visible (admin gating + non-personal team).
2. Click `install-github-cta`. Expected: GET `/api/v1/teams/<id>/github/install-url` fires; response carries `install_url` with shape `<base>/apps/<client_id>/installations/new?state=<jwt>`.
3. Capture the response body's `state` JWT (test process intercepts via `page.waitForResponse`).
4. Stub `window.open` to a no-op (test does not need to drive the popup).
5. From the test process, POST `/api/v1/github/install-callback` with `{state, installation_id: <fixed>}`. Expected: 200; response carries `account_login: "test-org"` (canonical `mock_github_app.py` fixture, MEM252).
6. Reload the team page. Expected: `installation-row-<installation_id>` renders with `account_login` and `account_type` from the mock.
7. Negative test: as a non-admin team member, navigate to the same team page. Expected: `install-github-cta` is not rendered; uninstall actions are not rendered.
8. Edge case: GET `/install-url` returns 404 `github_app_not_configured` when system_settings rows are missing. Expected: CTA disabled with tooltip "System admin must seed GitHub App credentials before installing".

**Pass criteria:** Steps 1–8 all succeed; the install-url JWT round-trips correctly and the row appears.

## Scenario 3: Create project + open project (full clone chain)

**Preconditions:** Scenario 2 completed (installation seeded); mock-github sidecar serving `acme/widgets.git`; team-admin on `/teams/<id>`.

**Steps:**
1. Locate `projects-section`. Expected: visible empty state with "Create your first project" CTA.
2. Click `create-project-button`. Expected: CreateProjectDialog opens.
3. Fill `create-project-name-input=widgets`, `create-project-repo-input=acme/widgets`, select the seeded installation in `create-project-installation-select`.
4. Click `create-project-submit`. Expected: POST `/api/v1/teams/<id>/projects` returns 201; `project-row-<id>` appears.
5. Click `project-open-button-<id>`. Expected: spinner state visible immediately; LoadingButton disabled.
6. Wait up to 60s for success toast `Project opened in your workspace`. Expected: orchestrator chain (`mirror/ensure → materialize-mirror → materialize-user`) completes against mock-github API + git-daemon.
7. Negative test: empty-name submit blocked with inline error. Repo without `/` separator blocked.
8. Edge case: 409 `project_name_taken` surfaces inline on `name` field (not toast).
9. Edge case: 502 with `{detail, reason}` body — toast description shows `${detail} (reason: ${reason})` with the orchestrator's discriminator (`github_clone_failed` / `user_clone_exit_<code>` / `clone_credential_leak`).
10. Edge case: 503 `orchestrator_unavailable` — toast `"Orchestrator is unreachable — please try again in a moment"`.

**Pass criteria:** Steps 1–10 all succeed; the open-project chain completes within 60s wall-clock (anything materially slower is S04 regression territory).

## Scenario 4: Push-rule form persists all three modes across reload

**Preconditions:** Scenario 3 completed (`acme/widgets` project exists); team-admin on `/teams/<id>`.

**Steps for each mode in order:** `rule` → `manual_workflow` → `auto`.

1. Click `push-rule-button-<id>`. Expected: PushRuleForm opens; current persisted mode pre-selected.
2. For mode=`rule`: select `push-rule-mode-rule`, assert `push-rule-stored-badge` is visible, fill `push-rule-branch-pattern-input=main`, click `push-rule-submit`. Expected: PUT `/api/v1/projects/<id>/push-rule` returns 200; success toast.
3. For mode=`manual_workflow`: select `push-rule-mode-manual_workflow`, assert `push-rule-stored-badge` is visible, fill `push-rule-workflow-id-input=deploy.yml`, submit. Expected: 200; toast.
4. For mode=`auto`: select `push-rule-mode-auto`, assert `push-rule-stored-badge` is NOT visible, submit. Expected: 200; toast.
5. **Reload the page** between every mode switch. Expected: the form re-opens with the persisted mode pre-selected (proves durability against React Query cache flushes — Q7 negative test).
6. Negative test: workflow_id with only whitespace blocks submit on mode=manual_workflow.
7. Negative test: branch_pattern with only whitespace blocks submit on mode=rule.

**Pass criteria:** All three modes persist and survive reload; `Stored — executor lands in M005` badge logic is correct (visible for rule + manual_workflow, not visible for auto).

## Scenario 5: Mirror always-on toggle persists across reload

**Preconditions:** Non-personal team (mirror-section visible); team-admin on `/teams/<id>`.

**Steps:**
1. Locate `mirror-section`. Expected: visible (non-personal team + admin role).
2. Capture `mirror-always-on-toggle` initial `aria-checked` value (default `false`).
3. Click the toggle. Expected: optimistic flip; PATCH `/api/v1/teams/<id>/mirror` fires with `always_on=!initial`; success toast `Mirror always-on enabled` (or `disabled`).
4. Reload page. Expected: toggle state is the negation of the captured initial (state survived round-trip).
5. Negative test: navigate to a personal team. Expected: `mirror-section` is NOT rendered.
6. Negative test: as a non-admin team member, navigate to the same team. Expected: `mirror-section` is NOT rendered.
7. Edge case: PATCH 503 (orchestrator unavailable) — optimistic state rolls back; toast surfaces backend detail.

**Pass criteria:** Toggle round-trips correctly through the backend; personal teams and non-admins do not see the section.

## Scenario 6: Redaction sweep (final milestone gate)

**Preconditions:** Scenarios 1–5 all completed.

**Steps:**
1. Capture backend logs: `docker compose logs perpetuity-backend-1 --since 10m`.
2. Capture ephemeral orchestrator logs: `docker logs <ephemeral_orch_name> --since 10m`.
3. Run grep checks on the combined output:
   - `grep -E 'gho_|ghu_|ghr_|github_pat_'` — MUST return zero matches.
   - `grep -E '-----BEGIN'` — MUST return zero matches.
   - `grep 'MOCK_FIXED_TOKEN'` plaintext — MUST return zero matches (the constant should only appear redacted).
   - `grep ghs_` — must ONLY match the `token_prefix=ghs_<4chars>` log shape (MEM262); any longer match is a regression.
   - `grep <captured_webhook_secret_from_scenario_1>` — MUST return zero matches (one-shot plaintext discipline at the backend log surface).

**Pass criteria:** All grep assertions return the expected counts. Failure of any single check fails the milestone-wide redaction invariant from M004's success criteria.

## Test Execution

**Automated path:** `cd frontend && VITE_API_URL=http://localhost:8001 bunx playwright test --project=m004-guylpp` runs all 6 scenarios in serial mode in under 240s (90s warm-cache target; cold-cache + image pull adds ~60s).

**Manual fallback:** Each scenario can be exercised manually in the dev server UI; testid selectors are stable and discoverable in DevTools.

## Known Limitations

- The e2e spec assumes the compose stack starts clean. Cross-project alembic-revision drift (e.g. unrelated `z2x_*` migrations in the dev DB) blocks prestart; recover with `docker compose down -v` (destructive of unrelated work) or run on a fresh CI worker.
- The cleanup() helper is best-effort. SIGKILL of the test process leaves the ephemeral orchestrator + mock-github sidecars + child team-mirror containers alive; manual recovery: `docker rm -f mock-github-api-* mock-gh-git-* orch-s06-m004-* team-mirror-* perpetuity-ws-* && docker compose up -d orchestrator`.
- The `account_login` value in scenario 2 is `test-org` (canonical `mock_github_app.py` fixture per MEM252) rather than the slice plan's approximate `mock-org` — diverging would require a parallel fixture, which violates the plan's "reuse the mock_github_app.py pattern" instruction.

## Sign-Off

- [ ] Scenario 1 passes (one-time-display + body.innerText negation)
- [ ] Scenario 2 passes (install via mock callback)
- [ ] Scenario 3 passes (create + open within 60s)
- [ ] Scenario 4 passes (all three modes + reload durability)
- [ ] Scenario 5 passes (toggle + reload + personal-team gating)
- [ ] Scenario 6 passes (redaction sweep returns zero forbidden matches)
- [ ] Lint + build + typecheck all exit 0
- [ ] Playwright list confirms 6 scenarios under m004-guylpp project and 0 under defaults
