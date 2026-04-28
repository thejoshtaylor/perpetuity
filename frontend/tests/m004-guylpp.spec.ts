/**
 * M004 admin-side experience e2e (S06/T05).
 *
 * Single Playwright spec walking the four success-criteria flows from the
 * slice plan against the live compose stack with mock-github sidecars
 * standing in for `api.github.com` and the upstream git remote:
 *
 *   1. Generate webhook secret with one-time-display modal (FE side of
 *      MEM232 — plaintext crosses the FE boundary exactly once).
 *   2. Install GitHub App via the mock callback.
 *   3. Create project + Open project (orchestrator chain hits the mock
 *      sidecars and the credential-free git-daemon target — same shape as
 *      S04/T05).
 *   4. Push-rule form persists across all three modes (auto / rule /
 *      manual_workflow), durably across React Query cache flushes.
 *   5. Mirror always-on toggle persists across reload.
 *
 * Bound to its own Playwright project `m004-guylpp` (see playwright.config.ts).
 * The default chromium project sets `testIgnore: 'm004-guylpp.spec.ts'` so
 * `bunx playwright test` runs everything but `bunx playwright test
 *  --project=chromium` does NOT trigger this spec — it requires the
 * mock-github sidecars + an orchestrator parameterized to talk to them.
 *
 * Wall-clock budget: under 90s on a warm compose stack. Sidecar boot +
 * pip install dominates first-run cost (~30s).
 */
import { expect, test } from "@playwright/test"

import {
  assertRedactedLogs,
  FIXED_INSTALLATION_ID,
  MOCK_FIXED_TOKEN,
  REPO_ROOT,
  type SetupMockGithubResult,
  seedTeamAdmin,
  setupMockGithub,
} from "./utils/m004"
import { randomEmail, randomPassword } from "./utils/random"
import "node:fs"
import { existsSync, readFileSync } from "node:fs"
import path from "node:path"

const API_BASE = process.env.VITE_API_URL ?? "http://localhost:8001"

function readDotenv(key: string, fallback: string): string {
  const envPath = path.join(REPO_ROOT, ".env")
  if (!existsSync(envPath)) return fallback
  for (const raw of readFileSync(envPath, "utf-8").split("\n")) {
    const line = raw.trim()
    if (!line || line.startsWith("#")) continue
    const eq = line.indexOf("=")
    if (eq === -1) continue
    const k = line.slice(0, eq).trim()
    if (k !== key) continue
    let v = line.slice(eq + 1).trim()
    if (
      (v.startsWith('"') && v.endsWith('"')) ||
      (v.startsWith("'") && v.endsWith("'"))
    ) {
      v = v.slice(1, -1)
    }
    return v
  }
  return fallback
}

const REDIS_PASSWORD =
  process.env.REDIS_PASSWORD ?? readDotenv("REDIS_PASSWORD", "changethis")
const PG_PASSWORD =
  process.env.POSTGRES_PASSWORD ?? readDotenv("POSTGRES_PASSWORD", "changethis")

test.describe("M004 frontend (live compose + mock-github sidecars)", () => {
  // The fixture must outlive every scenario in this describe so the install
  // seeded in scenario 2 is reusable by 3+. Set a generous suite timeout —
  // sidecar boot + pip install dominates wall-clock on cold caches.
  test.describe.configure({ mode: "serial", timeout: 240_000 })

  let mock: SetupMockGithubResult | null = null
  let teamId = ""
  let projectId: string | null = null

  // The team-admin's credentials. Seeded once, reused across scenarios.
  const adminEmail = randomEmail()
  const adminPassword = randomPassword()

  // Captured plaintext from scenario 1 — the redaction sweep at the end
  // grep-validates this never appears in backend/orchestrator logs.
  let capturedSecret: string | null = null

  test.beforeAll(async ({ browser }) => {
    // Boot sidecars + ephemeral orchestrator + seed system_settings as the
    // seeded superuser. setupMockGithub is the ONLY place a docker shell-out
    // can fail before the user-facing scenarios begin.
    mock = await setupMockGithub({
      apiBase: API_BASE,
      redisPassword: REDIS_PASSWORD,
      pgPassword: PG_PASSWORD,
    })

    // Sign up the team-admin user in a fresh, unauthenticated context so the
    // signup's Set-Cookie does NOT clobber the chromium project's seeded
    // superuser session (MEM064 / detached-cookie-jar pattern).
    const signupCtx = await browser.newContext({
      storageState: { cookies: [], origins: [] },
    })
    try {
      const signupPage = await signupCtx.newPage()
      const teamName = `m004-${Math.random().toString(36).slice(2, 8)}`
      const result = await seedTeamAdmin(signupPage, {
        fullName: `M004 Admin ${Math.random().toString(36).slice(2, 6)}`,
        email: adminEmail,
        password: adminPassword,
        teamName,
      })
      teamId = result.teamId
    } finally {
      await signupCtx.close()
    }
  })

  test.afterAll(async () => {
    if (mock) {
      await mock.cleanup()
    }
  })

  test("01 generate webhook secret one-time-display", async ({ page }) => {
    // Authenticated chromium context starts as the seeded superuser. /admin/
    // settings is system-admin-gated.
    await page.goto("/admin/settings")
    await expect(page.getByTestId("system-settings-page")).toBeVisible()

    const generateBtn = page.getByTestId(
      "system-settings-generate-button-github_app_webhook_secret",
    )
    await expect(generateBtn).toBeVisible()
    await generateBtn.click()

    // Confirm dialog must mention upstream-rotation breakage (MEM232 / D025).
    const confirmDialog = page.getByText(/Re-generating breaks/i)
    await expect(confirmDialog).toBeVisible()
    await page.getByTestId("system-settings-generate-confirm").click()

    // One-time-display modal renders the plaintext exactly once.
    const oneTimeValue = page.getByTestId("system-settings-one-time-value")
    await expect(oneTimeValue).toBeVisible()
    const valueText = (await oneTimeValue.textContent())?.trim() ?? ""
    expect(valueText.length).toBeGreaterThanOrEqual(32)
    capturedSecret = valueText

    // Copy button is present.
    await expect(
      page.getByTestId("system-settings-one-time-copy"),
    ).toBeVisible()

    // Acknowledge → modal unmounts → React drops the value prop.
    await page.getByTestId("system-settings-one-time-acknowledge").click()
    await expect(
      page.getByTestId("system-settings-one-time-modal"),
    ).not.toBeVisible()

    // FE one-shot discipline: after the modal closes, the plaintext must
    // not appear anywhere in the rendered DOM. This is the strict negative
    // test in the slice plan's Q7 — a regression in OneTimeValueModal's
    // lifecycle (e.g. a stray useState hoist into SystemSettingsList) would
    // surface here and only here.
    const bodyText = await page.locator("body").innerText()
    expect(bodyText).not.toContain(valueText)

    // The list re-renders has_value:true for the regenerated key. The badge
    // testid is constant; the data-has-value attribute toggles to true.
    const row = page.getByTestId(
      "system-settings-row-github_app_webhook_secret",
    )
    await expect(row).toBeVisible()
    await expect(row).toHaveAttribute("data-has-value", "true")
  })

  test("02 install GitHub App via mock callback", async ({ page, request }) => {
    expect(mock).not.toBeNull()
    if (!mock) throw new Error("mock not initialized")

    // Log out the chromium-superuser session, then log in as the seeded
    // team-admin from beforeAll. The chromium project's storageState
    // belongs to the superuser; we need the team-admin's session for the
    // /teams/<id>/github/install-url + install-callback round-trip.
    await page.goto("/login")
    await page.getByTestId("email-input").fill(adminEmail)
    await page.getByTestId("password-input").fill(adminPassword)
    await page.getByRole("button", { name: "Log In" }).click()
    await page.waitForURL("/teams")

    // Capture the install_url's `state` JWT off the network response. The
    // backend mints a fresh state on every GET — we listen for the response,
    // then click the CTA so the request actually fires.
    await page.goto(`/teams/${teamId}`)

    const installUrlPromise = page.waitForResponse(
      (resp) =>
        resp.url().includes(`/api/v1/teams/${teamId}/github/install-url`) &&
        resp.request().method() === "GET" &&
        resp.status() === 200,
    )

    // Two-tab guard: window.open is invoked with `_blank`; we don't actually
    // need the popup, only the GET-install-url response we just hooked.
    await page.evaluate(() => {
      ;(window as unknown as { open: (...a: unknown[]) => unknown }).open =
        () => null
    })
    const installCta = page.getByTestId("install-github-cta")
    await expect(installCta).toBeEnabled()
    await installCta.click()
    const installResp = await installUrlPromise
    const installBody = (await installResp.json()) as {
      install_url: string
      state: string
      expires_at: string
    }
    expect(installBody.state.length).toBeGreaterThan(0)
    expect(installBody.install_url).toContain("perpetuity-m004-s06")

    // Now POST to the public install-callback with the captured state — this
    // is the cleaner-than-redirect-chain shape the slice plan describes.
    const cbResp = await request.post(
      `${API_BASE}/api/v1/github/install-callback`,
      {
        data: {
          installation_id: FIXED_INSTALLATION_ID,
          setup_action: "install",
          state: installBody.state,
        },
      },
    )
    expect(cbResp.status()).toBe(200)
    const cbBody = (await cbResp.json()) as {
      installation_id: number
      account_login: string
      account_type: string
      team_id: string
    }
    expect(cbBody.installation_id).toBe(FIXED_INSTALLATION_ID)
    // The fixture's lookup endpoint hardcodes `test-org`; the slice plan's
    // mention of `mock-org` is approximate — assert against the canonical
    // fixture value (MEM252).
    expect(cbBody.account_login).toBe("test-org")
    expect(cbBody.team_id).toBe(teamId)

    // Reload the team page so the connections list re-fetches and shows
    // the new installation row.
    await page.reload()
    await expect(
      page.getByTestId(`installation-row-${FIXED_INSTALLATION_ID}`),
    ).toBeVisible()
  })

  test("03 create project + open project", async ({ page }) => {
    expect(mock).not.toBeNull()
    if (!mock) throw new Error("mock not initialized")

    await page.goto(`/teams/${teamId}`)
    await page.getByTestId("create-project-button").first().click()

    await page.getByTestId("create-project-name-input").fill("widgets")
    await page.getByTestId("create-project-repo-input").fill("acme/widgets")

    // Open the installation Select then click the option matching the
    // fixed installation_id. The Select uses Radix → click the trigger,
    // then click the option by its testid.
    await page.getByTestId("create-project-installation-select").click()
    await page
      .getByTestId(
        `create-project-installation-option-${FIXED_INSTALLATION_ID}`,
      )
      .click()

    // Race-aware response watcher: capture the create response so we can
    // recover the canonical projectId without parsing the rendered list.
    const createRespPromise = page.waitForResponse(
      (resp) =>
        resp.url().includes(`/api/v1/teams/${teamId}/projects`) &&
        resp.request().method() === "POST" &&
        resp.status() === 200,
    )
    await page.getByTestId("create-project-submit").click()
    const createResp = await createRespPromise
    const createBody = (await createResp.json()) as { id: string }
    projectId = createBody.id

    const projectRow = page.getByTestId(`project-row-${projectId}`)
    await expect(projectRow).toBeVisible()

    // Open. The orchestrator chain (ensure_team_mirror → clone-to-mirror via
    // the git-daemon sidecar → user-session container) takes 5-30s; the
    // success toast is the assertion.
    const openButton = projectRow.getByTestId(
      `project-open-button-${projectId}`,
    )
    await expect(openButton).toBeVisible()
    await openButton.click()

    // Toast text is set in OpenProjectButton: `Project opened in your workspace`.
    await expect(
      page.getByText(/Project opened in your workspace/i),
    ).toBeVisible({ timeout: 60_000 })

    // S04 regression bar: orchestrator chain budget is ~30s on a warm
    // stack. Test budget allows up to 60s for the toast to surface, but
    // anything materially slower means S04 has regressed and we want a
    // failing test before the milestone closes.
  })

  test("04 push-rule form persists all three modes", async ({ page }) => {
    expect(projectId).not.toBeNull()
    if (!projectId) throw new Error("projectId missing — scenario 3 skipped?")

    await page.goto(`/teams/${teamId}`)

    const projectRow = page.getByTestId(`project-row-${projectId}`)
    await expect(projectRow).toBeVisible()

    // ----- mode=rule ---------------------------------------------------
    await projectRow.getByTestId(`push-rule-button-${projectId}`).click()
    await page.getByTestId("push-rule-mode-rule").click()
    await expect(page.getByTestId("push-rule-stored-badge")).toBeVisible()
    await page.getByTestId("push-rule-branch-pattern-input").fill("main")
    await page.getByTestId("push-rule-submit").click()
    await expect(page.getByText(/Push rule saved/i)).toBeVisible()

    // Reload to prove durability against React Query cache (Q7 negative
    // test in the slice plan).
    await page.reload()
    const projectRow2 = page.getByTestId(`project-row-${projectId}`)
    await projectRow2.getByTestId(`push-rule-button-${projectId}`).click()
    await expect(
      page.getByTestId("push-rule-branch-pattern-input"),
    ).toHaveValue("main")
    await expect(page.getByTestId("push-rule-stored-badge")).toBeVisible()

    // ----- mode=manual_workflow ---------------------------------------
    await page.getByTestId("push-rule-mode-manual_workflow").click()
    await expect(page.getByTestId("push-rule-stored-badge")).toBeVisible()
    await page.getByTestId("push-rule-workflow-id-input").fill("deploy.yml")
    await page.getByTestId("push-rule-submit").click()
    await expect(page.getByText(/Push rule saved/i).first()).toBeVisible()

    await page.reload()
    const projectRow3 = page.getByTestId(`project-row-${projectId}`)
    await projectRow3.getByTestId(`push-rule-button-${projectId}`).click()
    await expect(page.getByTestId("push-rule-workflow-id-input")).toHaveValue(
      "deploy.yml",
    )

    // ----- mode=auto (badge MUST disappear) ----------------------------
    await page.getByTestId("push-rule-mode-auto").click()
    await expect(page.getByTestId("push-rule-stored-badge")).not.toBeVisible()
    await page.getByTestId("push-rule-submit").click()
    await expect(page.getByText(/Push rule saved/i).first()).toBeVisible()

    await page.reload()
    const projectRow4 = page.getByTestId(`project-row-${projectId}`)
    await projectRow4.getByTestId(`push-rule-button-${projectId}`).click()
    await expect(page.getByTestId("push-rule-stored-badge")).not.toBeVisible()
  })

  test("05 mirror always-on toggle persists across reload", async ({
    page,
  }) => {
    await page.goto(`/teams/${teamId}`)
    const toggle = page.getByTestId("mirror-always-on-toggle")
    await expect(toggle).toBeVisible()

    // Initial state may be off (default; AlwaysOnToggle starts at false
    // when the team object doesn't carry mirror.always_on). Capture it.
    const initialChecked =
      (await toggle.getAttribute("aria-checked")) === "true"
    await toggle.click()
    await expect(
      page.getByText(
        initialChecked
          ? /Mirror always-on disabled/i
          : /Mirror always-on enabled/i,
      ),
    ).toBeVisible()

    // Reload and verify the state persisted at the backend (PATCH
    // round-trips through /api/v1/teams/<id>/mirror, AlwaysOnToggle
    // re-anchors to the team-list mirror.always_on on remount).
    await page.reload()
    const toggle2 = page.getByTestId("mirror-always-on-toggle")
    const after = (await toggle2.getAttribute("aria-checked")) === "true"
    expect(after).toBe(!initialChecked)
  })

  test("99 redaction sweep — backend + orchestrator logs carry no token leaks", async () => {
    expect(mock).not.toBeNull()
    if (!mock) throw new Error("mock not initialized")
    // Closes the milestone-wide redaction invariant: gho_/ghu_/ghr_/
    // github_pat_/-----BEGIN appearing in backend OR orchestrator logs is
    // a regression. ghs_ is permitted only inside `token_prefix=ghs_<4>`
    // log lines (MEM262). The captured webhook secret from scenario 1 is
    // also grepped.
    assertRedactedLogs({
      ephName: mock.ephName,
      capturedSecretValue: capturedSecret,
    })
    // Sanity: the mock-github fixed token must NEVER appear in logs.
    // assertRedactedLogs checks this directly.
    expect(MOCK_FIXED_TOKEN.startsWith("ghs_")).toBe(true)
  })
})
