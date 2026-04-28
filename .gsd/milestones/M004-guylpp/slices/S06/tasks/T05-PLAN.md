---
estimated_steps: 14
estimated_files: 3
skills_used: []
---

# T05: Playwright e2e: full M004 admin-side experience with mock-github sidecars

Single Playwright spec `frontend/tests/m004-guylpp.spec.ts` that walks the four flows from the slice's success criteria against the live compose stack. Reuse the `backend/tests/integration/fixtures/mock_github_app.py` pattern (MEM261/MEM281/MEM289). Sidecars: (a) FastAPI mock-github for token mint and install lookup via GITHUB_API_BASE_URL — extends mock_github_app.py with the install-callback redirect target so the headless browser can be redirected from the install-CTA new-tab to a sibling URL and intercepted; (b) workspace-image+git-daemon clone target via github_clone_base_url (MEM281); (c) seed step for backend system_settings rows (admin-PUT via `bunx tsx` script invoking httpx).

**Test setup helper** `frontend/tests/utils/m004.ts`: `setupMockGithub(adminCookies)` — uses `child_process.execSync('docker run --rm -d ...')` to boot the two sidecars on perpetuity_default with `--network-alias mock-github-<uuid>` and `--network-alias mock-git-daemon-<uuid>`. Seeds backend system_settings via direct API calls (PUT github_app_id, github_app_client_id, github_app_private_key) using axios from the test process. Configures the orchestrator to point at the sidecars by writing `GITHUB_API_BASE_URL` and `GITHUB_CLONE_BASE_URL` env into the running orchestrator container via `docker compose exec` — OR more reliably, requires the orchestrator to already be configured via .env.test for these. Returns `{cleanup, mockTokenValue, fakeInstallationId, mockApiBase}`.

`seedTeamAdmin(page)` → signup via UI + create non-personal team + extract teamId.

**Scenarios** (each inside `test.describe('M004 frontend', () => { ... })` and skipped on `mobile-chrome-no-auth`):

1. **`generate webhook secret one-time-display`** — superuser navigates to /admin/settings, clicks Generate on github_app_webhook_secret, confirms warning modal, asserts the one-time-display modal renders a non-empty string of length ≥ 32, asserts a Copy button is present, captures the value, clicks acknowledge, asserts subsequent list render shows has_value:true with the value redacted. Then assert `await page.locator('body').innerText()` does NOT contain the captured plaintext substring after the modal is closed (one-shot discipline at the FE).

2. **`install GitHub App via mock callback`** — admin seeds github_app_private_key + github_app_id + github_app_client_id (helper). Team-admin clicks Install GitHub App on /teams/<id>. Two-tab strategy: capture the `install_url` from the GET response by intercepting `route('**/github/install-url')` and reading the response body; then in the test directly POST to /api/v1/github/install-callback with the installation_id + the captured state (cleaner than driving the headless browser through a redirect chain). Assert the original team page's connections list re-fetches and shows the installation row with account_login `mock-org`.

3. **`create project + open project`** — using the installation seeded in (2), team-admin clicks Create Project, fills `acme/widgets` + selects the installation, asserts row appears. Click Open, assert spinner state, wait for success toast within 30s (orchestrator chain hits the mock-github sidecars and git-daemon target — same shape as S04/T05). Assert the project row's last_push_status badge updates if the orchestrator reports back.

4. **`push-rule form persists all three modes`** — team-admin opens push-rule on the project from (3), selects `rule`, asserts `Stored — executor lands in M005` badge is visible, fills branch_pattern `main`, submits, asserts toast + persisted state on reload. Repeat for `manual_workflow` (workflow_id `deploy.yml`). Switch to `auto`, assert badge disappears, submit.

5. **`mirror always-on toggle`** — team-admin toggles the switch, asserts the toast `Mirror always-on enabled`, reloads page, asserts switch is still on (state persisted).

Teardown: `cleanup()` removes the two sidecars and the seeded system_settings rows. Wall-clock budget: under 90s on a warm compose stack.

This test runs separately from the existing chromium project (it needs the mock-github sidecars + special orchestrator config). Add a new project `m004-guylpp` to `playwright.config.ts` that points only at this spec via `testMatch`, inherits the chromium auth state via `dependencies: ['setup']`. Add `testIgnore: 'm004-guylpp.spec.ts'` on the existing chromium project — so `bunx playwright test` runs everything; `bunx playwright test --project=m004-guylpp` runs only this spec.

**Failure modes (Q5):** sidecar `docker run` fails → cleanup must still run (afterAll guard). Backend doesn't see the seeded settings if the test runs faster than docker-network DNS resolves → boot+ping check inside setupMockGithub. The test process's call to /api/v1/github/install-callback must use the same SECRET_KEY as the backend; rather than minting state ourselves, use the `state` returned by GET /install-url verbatim — that's what the backend will accept.

**Load profile (Q6):** Each scenario is sequential within the spec; no parallel browser contexts; single chromium worker. Wall-clock target 90s — if the orchestrator chain in scenario 3 takes >30s, that's S04 regression territory, fail the test.

**Negative tests (Q7):** Scenario 1 ends with a strict `body.innerText` substring search for the plaintext — the only place a regression in the OneTimeValueModal's lifecycle would surface. Scenario 4 reloads the page between mode switches to prove durability against the React Query cache rather than UI-only state.

## Inputs

- `frontend/playwright.config.ts`
- `frontend/tests/teams.spec.ts`
- `frontend/tests/admin-teams.spec.ts`
- `frontend/tests/utils/teams.ts`
- `backend/tests/integration/fixtures/mock_github_app.py`
- `backend/tests/integration/test_m004_s02_github_install_e2e.py`
- `backend/tests/integration/test_m004_s04_two_hop_clone_e2e.py`

## Expected Output

- `frontend/tests/m004-guylpp.spec.ts`
- `frontend/tests/utils/m004.ts`
- `frontend/playwright.config.ts`

## Verification

1) `docker compose build backend orchestrator && docker compose up -d db redis backend orchestrator` exits 0 (compose stack ready). 2) `cd frontend && bun run dev` started in background; baseURL http://localhost:5173 reachable. 3) `cd frontend && VITE_API_URL=http://localhost:8001 bunx playwright test --project=m004-guylpp` exits 0 with all 5 scenarios passing in under 90s wall-clock. 4) `cd frontend && bunx playwright test --project=chromium` does NOT include m004-guylpp.spec.ts (testIgnore proven by zero matches in default-project run). 5) `bun run lint` exits 0 over the new files. 6) Final assertion: `docker compose logs backend orchestrator | grep -E 'gho_|ghu_|ghr_|github_pat_|-----BEGIN'` returns zero unexpected matches (extends the S04/T05 redaction contract to the FE-driven flow). The single permitted match shape is `token_prefix=ghs_<4chars>` per MEM262.

## Observability Impact

Spec includes a final redaction sweep step (grep over backend + orchestrator container logs for all five GitHub token-prefix families and PEM headers) that would fail if any FE-driven flow accidentally piped plaintext through the backend log path. This extends the S02/T04 + S04/T05 redaction contracts to cover FE-initiated flows (closes the milestone-wide redaction invariant from M004's success criteria). Toast surfaces are exercised in scenarios 3 (open-project orchestrator-error path) and 5 (mirror toggle success path) — proves the operator-visible failure pipeline is live.
