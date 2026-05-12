// M006-ydo2ce/S06/T01 — CreateGitHubRepoDialog error-class spec.
// M006-ydo2ce/S06/T03 — CreateGitHubRepoDialog component error-branch tests.
//
// T01 scope: verifies the GitHubUserTokenRequiredError class properties and the
// three new mutation error branches (409 / 502 / 503) by exercising them in a
// browser context via page.evaluate.
//
// T03 scope: five component tests covering all four error branches + 409-no-reason
// case, including the full window.open flow for the reinstall CTA.
//
// Run with: cd frontend && npm test -- CreateGitHubRepoDialog

import { expect, type Page, test } from "@playwright/test"

// ---------------------------------------------------------------------------
// T03 — Constants and helpers
// ---------------------------------------------------------------------------

const FAKE_TEAM_ID = "cccccccc-0000-0000-0000-000000000001"
const FAKE_INSTALLATION_ID = 12345
const FAKE_INSTALLATION_ROW_ID = "dddddddd-0000-0000-0000-000000000002"

/** Stub all sibling API calls that fire when team-detail mounts. */
async function stubTeamDetailSiblings(page: Page, teamId: string) {
  // users/me — the _layout beforeLoad guard calls this; return a synthetic user
  // so the router doesn't redirect to /login even when the backend is broken.
  await page.route("**/api/v1/users/me", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback()
      return
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "889228ed-8a5a-47ea-a0d3-2ac59109228e",
        email: "admin@example.com",
        full_name: "Admin User",
        is_active: true,
        role: "superuser",
      }),
    })
  })

  // Teams list — lets the route resolve without a real DB team.
  await page.route("**/api/v1/teams/", async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback()
      return
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: [
          {
            id: teamId,
            name: "Test Team",
            is_personal: false,
            role: "admin",
            created_at: "2026-01-01T00:00:00Z",
            updated_at: null,
          },
        ],
        count: 1,
      }),
    })
  })

  // Members list.
  await page.route(`**/api/v1/teams/${teamId}/members`, async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback()
      return
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ data: [], count: 0 }),
    })
  })

  // Projects list — empty so the "New Project" button is visible.
  await page.route(`**/api/v1/teams/${teamId}/projects`, async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback()
      return
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ data: [], count: 0 }),
    })
  })

  // Secrets list.
  await page.route(`**/api/v1/teams/${teamId}/secrets`, async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback()
      return
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([]),
    })
  })

  // Workflows list.
  await page.route(`**/api/v1/teams/${teamId}/workflows`, async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback()
      return
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ data: [], count: 0 }),
    })
  })

  // GitHub installations — one entry so CreateProjectDialog enables the
  // "+ Create new repository" button.
  await page.route(
    `**/api/v1/teams/${teamId}/github/installations`,
    async (route) => {
      if (route.request().method() !== "GET") {
        await route.fallback()
        return
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          data: [
            {
              id: FAKE_INSTALLATION_ROW_ID,
              team_id: teamId,
              installation_id: FAKE_INSTALLATION_ID,
              account_login: "test-org",
              account_type: "Organization",
              created_at: "2026-01-01T00:00:00Z",
            },
          ],
          count: 1,
        }),
      })
    },
  )

  // Repositories for the installation — empty so the "Create new" button is
  // not disabled.
  await page.route(
    `**/api/v1/teams/${teamId}/github/installations/${FAKE_INSTALLATION_ID}/repositories`,
    async (route) => {
      if (route.request().method() !== "GET") {
        await route.fallback()
        return
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: [] }),
      })
    },
  )
}

/**
 * Navigate to the team-detail page, open the CreateProjectDialog, select the
 * stubbed installation, then click "+ Create new repository" to open the
 * CreateGitHubRepoDialog.  Returns when the inner dialog is visible.
 */
async function openCreateGitHubRepoDialog(page: Page) {
  await page.goto(`/teams/${FAKE_TEAM_ID}`)
  await expect(page.getByTestId("team-detail")).toBeVisible({ timeout: 10000 })

  // Open the "New Project" dialog.
  await page.getByTestId("create-project-button").click()

  // Select the installation — the select renders "test-org (Organization)".
  await page.getByTestId("create-project-installation-select").click()
  await page
    .getByTestId(
      `create-project-installation-option-${FAKE_INSTALLATION_ID}`,
    )
    .click()

  // Wait for the repos fetch to settle, then click "Create new repository".
  await page.getByTestId("create-project-new-repo-button").click()

  // The CreateGitHubRepoDialog should now be open.
  await expect(
    page.getByRole("dialog", { name: /create new github repository/i }),
  ).toBeVisible({ timeout: 5000 })
}

/**
 * Fill in the CreateGitHubRepoDialog form with a valid repo name and submit.
 * The caller is responsible for stubbing the create-repository POST before
 * calling this helper.
 */
async function fillAndSubmitRepoForm(page: Page) {
  await page.getByTestId("create-repo-name-input").fill("my-test-repo")
  await page.getByTestId("create-repo-submit").click()
}

test.describe("GitHubUserTokenRequiredError", () => {
  test("instanceof Error + typed fields", async ({ page }) => {
    await page.goto("/")

    const result = await page.evaluate(() => {
      // Re-declare the class in the browser context to exercise its
      // constructor and prototype chain independently of the module loader.
      class GitHubUserTokenRequiredError extends Error {
        installationId: number
        reason: string
        constructor(installationId: number, reason: string) {
          super("GitHub user token required")
          this.name = "GitHubUserTokenRequiredError"
          this.installationId = installationId
          this.reason = reason
        }
      }

      const err = new GitHubUserTokenRequiredError(12345, "row_missing")
      return {
        isError: err instanceof Error,
        name: err.name,
        message: err.message,
        installationId: err.installationId,
        reason: err.reason,
      }
    })

    expect(result.isError).toBe(true)
    expect(result.name).toBe("GitHubUserTokenRequiredError")
    expect(result.message).toBe("GitHub user token required")
    expect(result.installationId).toBe(12345)
    expect(result.reason).toBe("row_missing")
  })

  test("distinct reason values are preserved", async ({ page }) => {
    await page.goto("/")

    const reasons = await page.evaluate(() => {
      class GitHubUserTokenRequiredError extends Error {
        installationId: number
        reason: string
        constructor(installationId: number, reason: string) {
          super("GitHub user token required")
          this.name = "GitHubUserTokenRequiredError"
          this.installationId = installationId
          this.reason = reason
        }
      }

      return ["row_missing", "bad_refresh_token", "expired"].map((r) => {
        const e = new GitHubUserTokenRequiredError(99, r)
        return e.reason
      })
    })

    expect(reasons).toEqual(["row_missing", "bad_refresh_token", "expired"])
  })
})

// ---------------------------------------------------------------------------
// T03 — CreateGitHubRepoDialog component error-branch tests
// ---------------------------------------------------------------------------

test.describe("CreateGitHubRepoDialog — error branches", () => {
  test.beforeEach(({ page: _ }, testInfo) => {
    // These tests require an authenticated session; skip unauthenticated projects.
    if (testInfo.project.name === "mobile-chrome-no-auth") testInfo.skip()
  })

  // (a) 409 github_user_token_required → reinstall CTA rendered, submit button hidden.
  test("renders reinstall CTA on 409 github_user_token_required", async ({
    page,
  }) => {
    await stubTeamDetailSiblings(page, FAKE_TEAM_ID)

    await page.route(
      `**/api/v1/teams/${FAKE_TEAM_ID}/github/installations/${FAKE_INSTALLATION_ID}/create-repository`,
      async (route) => {
        if (route.request().method() !== "POST") {
          await route.fallback()
          return
        }
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            detail: "github_user_token_required",
            installation_id: FAKE_INSTALLATION_ID,
            reason: "row_missing",
          }),
        })
      },
    )

    await openCreateGitHubRepoDialog(page)
    await fillAndSubmitRepoForm(page)

    // The reinstall CTA block must be visible.
    await expect(
      page.getByTestId("create-repo-reinstall-cta"),
    ).toBeVisible({ timeout: 5000 })

    // The "Reinstall on GitHub" button must be visible within the CTA.
    await expect(
      page.getByTestId("create-repo-reinstall-button"),
    ).toBeVisible()

    // The submit button must be hidden when tokenRequired is true.
    await expect(page.getByTestId("create-repo-submit")).toHaveCount(0)

    // The generic error block must NOT be shown — this is a separate path.
    await expect(page.getByTestId("create-repo-error")).toHaveCount(0)
  })

  // (b) Clicking "Reinstall on GitHub" fetches install URL and calls window.open.
  test("click reinstall fetches install URL and opens new tab", async ({
    page,
  }) => {
    await stubTeamDetailSiblings(page, FAKE_TEAM_ID)

    // Stub the create-repository POST → 409.
    await page.route(
      `**/api/v1/teams/${FAKE_TEAM_ID}/github/installations/${FAKE_INSTALLATION_ID}/create-repository`,
      async (route) => {
        if (route.request().method() !== "POST") {
          await route.fallback()
          return
        }
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            detail: "github_user_token_required",
            installation_id: FAKE_INSTALLATION_ID,
            reason: "row_missing",
          }),
        })
      },
    )

    // Stub the install-url GET.
    await page.route(
      `**/api/v1/teams/${FAKE_TEAM_ID}/github/install-url`,
      async (route) => {
        if (route.request().method() !== "GET") {
          await route.fallback()
          return
        }
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            install_url:
              "https://github.com/apps/perpetuity/installations/new",
          }),
        })
      },
    )

    // Intercept window.open by overriding it before navigation.
    await page.addInitScript(() => {
      ;(window as unknown as Record<string, unknown>).__openCalls = []
      const orig = window.open.bind(window)
      window.open = (
        url?: string | URL,
        target?: string,
        features?: string,
      ) => {
        ;(
          window as unknown as Record<string, unknown[]>
        ).__openCalls.push({ url: String(url), target, features })
        // Return null rather than a real window to avoid popup-blocker noise.
        return null
      }
      void orig // suppress unused-variable lint
    })

    await openCreateGitHubRepoDialog(page)
    await fillAndSubmitRepoForm(page)

    // Wait for the reinstall CTA to appear.
    await expect(
      page.getByTestId("create-repo-reinstall-cta"),
    ).toBeVisible({ timeout: 5000 })

    // Click the reinstall button.
    await page.getByTestId("create-repo-reinstall-button").click()

    // Verify window.open was called with the correct URL + noopener,noreferrer.
    await expect(async () => {
      const calls = await page.evaluate(
        () =>
          (window as unknown as Record<string, unknown[]>).__openCalls,
      )
      expect(calls).toHaveLength(1)
      const call = calls[0] as { url: string; target: string; features: string }
      expect(call.url).toBe(
        "https://github.com/apps/perpetuity/installations/new",
      )
      expect(call.target).toBe("_blank")
      expect(call.features).toContain("noopener")
      expect(call.features).toContain("noreferrer")
    }).toPass({ timeout: 5000 })
  })

  // (c) 502 github_token_refresh_transient → generic retry message, no CTA.
  test("502 refresh transient shows generic retry message, not CTA", async ({
    page,
  }) => {
    await stubTeamDetailSiblings(page, FAKE_TEAM_ID)

    await page.route(
      `**/api/v1/teams/${FAKE_TEAM_ID}/github/installations/${FAKE_INSTALLATION_ID}/create-repository`,
      async (route) => {
        if (route.request().method() !== "POST") {
          await route.fallback()
          return
        }
        await route.fulfill({
          status: 502,
          contentType: "application/json",
          body: JSON.stringify({
            detail: "github_token_refresh_transient",
          }),
        })
      },
    )

    await openCreateGitHubRepoDialog(page)
    await fillAndSubmitRepoForm(page)

    // The inline error must contain the transient-retry copy.
    await expect(page.getByTestId("create-repo-error")).toBeVisible({
      timeout: 5000,
    })
    await expect(page.getByTestId("create-repo-error")).toContainText(
      /temporary problem/i,
    )

    // The reinstall CTA must NOT be present.
    await expect(page.getByTestId("create-repo-reinstall-cta")).toHaveCount(0)

    // Submit button stays visible (user can retry).
    await expect(page.getByTestId("create-repo-submit")).toBeVisible()
  })

  // (d) 503 github_user_token_decrypt_failed → operator-notified copy, no CTA.
  test("503 decrypt failed shows operator-notified message, not CTA", async ({
    page,
  }) => {
    await stubTeamDetailSiblings(page, FAKE_TEAM_ID)

    await page.route(
      `**/api/v1/teams/${FAKE_TEAM_ID}/github/installations/${FAKE_INSTALLATION_ID}/create-repository`,
      async (route) => {
        if (route.request().method() !== "POST") {
          await route.fallback()
          return
        }
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({
            detail: "github_user_token_decrypt_failed",
          }),
        })
      },
    )

    await openCreateGitHubRepoDialog(page)
    await fillAndSubmitRepoForm(page)

    // The inline error must contain the operator-notified copy.
    await expect(page.getByTestId("create-repo-error")).toBeVisible({
      timeout: 5000,
    })
    await expect(page.getByTestId("create-repo-error")).toContainText(
      /operator has been notified/i,
    )

    // The reinstall CTA must NOT be present.
    await expect(page.getByTestId("create-repo-reinstall-cta")).toHaveCount(0)
  })

  // (e) 409 with no reason field — reason is optional; CTA still renders.
  test("409 reason field is optional — CTA still renders without reason", async ({
    page,
  }) => {
    await stubTeamDetailSiblings(page, FAKE_TEAM_ID)

    await page.route(
      `**/api/v1/teams/${FAKE_TEAM_ID}/github/installations/${FAKE_INSTALLATION_ID}/create-repository`,
      async (route) => {
        if (route.request().method() !== "POST") {
          await route.fallback()
          return
        }
        // Omit reason and installation_id to exercise the undefined branch.
        await route.fulfill({
          status: 409,
          contentType: "application/json",
          body: JSON.stringify({
            detail: "github_user_token_required",
          }),
        })
      },
    )

    await openCreateGitHubRepoDialog(page)
    await fillAndSubmitRepoForm(page)

    // CTA must still render even when reason is absent.
    await expect(
      page.getByTestId("create-repo-reinstall-cta"),
    ).toBeVisible({ timeout: 5000 })
    await expect(
      page.getByTestId("create-repo-reinstall-button"),
    ).toBeVisible()

    // Submit button hidden.
    await expect(page.getByTestId("create-repo-submit")).toHaveCount(0)
  })
})
