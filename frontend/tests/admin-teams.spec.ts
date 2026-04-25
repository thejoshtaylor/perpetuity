import { expect, test } from "@playwright/test"

import { randomEmail, randomPassword } from "./utils/random"
import { signupViaUI } from "./utils/teams"

test.describe("admin teams panel (authenticated superuser)", () => {
  // Chromium + mobile-chrome carry the seeded-superuser storageState; the
  // mobile-chrome-no-auth project intentionally ships no storage and is
  // reserved for the 403-redirect spec below.
  test.beforeEach(({ page: _ }, testInfo) => {
    if (testInfo.project.name === "mobile-chrome-no-auth") {
      testInfo.skip()
    }
  })

  test("system admin sees all teams and promotes a user", async ({
    page,
    browser,
    baseURL,
  }) => {
    // Seed two fresh users via UI in isolated contexts so their signups don't
    // stomp on the superuser session this test runs under.
    const aEmail = randomEmail()
    const aPassword = randomPassword()
    const aFullName = `Admin Teams A ${Math.random().toString(36).slice(2, 8)}`
    const bEmail = randomEmail()
    const bPassword = randomPassword()
    const bFullName = `Admin Teams B ${Math.random().toString(36).slice(2, 8)}`

    for (const creds of [
      { email: aEmail, password: aPassword, name: aFullName },
      { email: bEmail, password: bPassword, name: bFullName },
    ]) {
      const ctx = await browser.newContext({
        storageState: { cookies: [], origins: [] },
        baseURL,
      })
      try {
        const p = await ctx.newPage()
        await signupViaUI(p, creds.name, creds.email, creds.password)
      } finally {
        await ctx.close()
      }
    }

    // Back on the superuser page: navigate to /admin/teams.
    await page.goto("/admin/teams")
    await expect(page.getByRole("heading", { name: "All Teams" })).toBeVisible()

    // At least three team rows (two new signups' personal teams + the seeded
    // superuser's personal team).
    const rows = page.getByTestId("admin-teams-row")
    await expect(rows.first()).toBeVisible()
    expect(await rows.count()).toBeGreaterThanOrEqual(3)

    // Personal team name = user's full_name (crud.create_personal_team), so
    // filter the row by the signup full name and click "View members".
    const aRow = page.getByRole("row").filter({ hasText: aFullName })
    await expect(aRow).toBeVisible()
    await aRow.getByTestId("view-members-link").click()

    await page.waitForURL(/\/admin\/teams\/[^/]+$/)
    await expect(page.getByTestId("admin-team-detail")).toBeVisible()
    await expect(
      page.getByTestId("admin-members-list").getByText(aEmail),
    ).toBeVisible()

    // Now promote user B via the /admin users table.
    await page.goto("/admin")
    await expect(page.getByRole("heading", { name: "Users" })).toBeVisible()

    const bUserRow = page.getByRole("row").filter({ hasText: bEmail })
    await expect(bUserRow).toBeVisible()
    // Open the actions dropdown on that row.
    await bUserRow.getByRole("button").click()

    await page.getByTestId("promote-system-admin").click()

    // Confirm in the dialog.
    await expect(page.getByTestId("promote-system-admin-dialog")).toBeVisible()
    await page.getByTestId("confirm-promote").click()

    await expect(page.getByText("Promoted to system admin")).toBeVisible()

    // Role badge updates to Admin. The badge is a <span data-slot="badge">
    // with exact text "Admin"; scoping to the row's cells isolates it from
    // the full-name cell which also contains the word "Admin".
    await expect(
      bUserRow.locator('span[data-slot="badge"]', { hasText: /^Admin$/ }),
    ).toBeVisible()
  })
})

test.describe("admin teams access control", () => {
  // Force an empty storageState for this describe so the superuser cookie
  // from the setup project does not leak in.
  test.use({ storageState: { cookies: [], origins: [] } })

  test("non-admin redirected away from /admin/teams", async ({ page }) => {
    // Sign up a fresh non-admin user via UI.
    const email = randomEmail()
    const password = randomPassword()
    await signupViaUI(page, "Non Admin", email, password)

    // Try to hit the admin-only /admin/teams route. The requireSystemAdmin
    // guard should redirect to "/" which in turn redirects to "/teams".
    await page.goto("/admin/teams")
    await page.waitForURL((url) => !url.pathname.startsWith("/admin"), {
      timeout: 10_000,
    })

    // End state: guard fired and we're off /admin/* entirely.
    expect(new URL(page.url()).pathname.startsWith("/admin")).toBe(false)
    // And the All Teams heading is not visible (we did not render the admin page).
    await expect(
      page.getByRole("heading", { name: "All Teams" }),
    ).not.toBeVisible()
  })
})
