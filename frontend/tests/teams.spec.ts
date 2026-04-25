import { type BrowserContext, expect, type Page, test } from "@playwright/test"

import { firstSuperuser, firstSuperuserPassword } from "./config.ts"
import { randomEmail, randomPassword, randomTeamName } from "./utils/random"
import {
  createTeamFromUI,
  loginViaUI,
  signupViaUI,
  teamIdFromInviteUrl,
  teamIdFromTeamUrl,
} from "./utils/teams"

// Open the user menu, which lives inside the sidebar. On mobile (Pixel 5
// viewport) the sidebar collapses to a Sheet behind the SidebarTrigger button
// in the layout header — so we click the trigger first if the user-menu isn't
// already in the accessibility tree.
async function openUserMenu(page: Page) {
  const trigger = page.getByTestId("user-menu")
  if (!(await trigger.isVisible().catch(() => false))) {
    // Sidebar is collapsed — click the SidebarTrigger to open the mobile sheet.
    await page.getByRole("button", { name: /toggle sidebar|sidebar/i }).click()
  }
  await expect(page.getByTestId("user-menu")).toBeVisible()
  await page.getByTestId("user-menu").click()
}

// =============================================================================
// Authenticated specs (run on chromium + mobile-chrome)
// =============================================================================

test.describe("teams dashboard (authenticated)", () => {
  // The mobile-chrome-no-auth project is reserved for signup flows that must
  // start unauthenticated. Skip the authenticated suite there — it intentionally
  // ships no storageState.
  test.beforeEach(({ page: _ }, testInfo) => {
    if (testInfo.project.name === "mobile-chrome-no-auth") {
      testInfo.skip()
    }
  })

  test("user creates a team and sees it in the list", async ({ page }) => {
    await page.goto("/teams")
    await expect(
      page.getByText("Welcome back, nice to see you again!"),
    ).toBeVisible()

    const name = randomTeamName()
    await createTeamFromUI(page, name)

    // Toast appears on success.
    await expect(page.getByText("Team created")).toBeVisible()

    // The new team card is present and shows admin role badge.
    const card = page.getByTestId("team-card").filter({ hasText: name })
    await expect(card).toBeVisible()
    await expect(card.getByTestId("role-badge")).toHaveAttribute(
      "data-role",
      "admin",
    )
  })

  test("admin generates invite link and copies it", async ({
    page,
    baseURL,
  }) => {
    // Need a non-personal team where the caller is admin.
    const name = randomTeamName()
    await page.goto("/teams")
    await createTeamFromUI(page, name)
    await page.getByTestId("team-card").filter({ hasText: name }).click()

    await page.waitForURL(/\/teams\/[^/]+$/)
    await expect(page.getByTestId("team-detail")).toBeVisible()

    await page.getByTestId("invite-button").click()

    const inviteUrlInput = page.getByTestId("invite-url")
    await expect(inviteUrlInput).toBeVisible()

    // Read the URL value out of the input.
    const inviteUrl = await inviteUrlInput.inputValue()
    expect(inviteUrl).toMatch(
      new RegExp(`^${(baseURL ?? "").replace(/\/$/, "")}/invite/[^/]+$`),
    )
    expect(teamIdFromInviteUrl(inviteUrl)).toBeTruthy()

    await page.getByTestId("copy-invite-url").click()
    await expect(page.getByText("Copied")).toBeVisible()
  })

  test("admin promotes then demotes a member", async ({
    page,
    browser,
    baseURL,
  }) => {
    // User A: create team + invite.
    const name = randomTeamName()
    await page.goto("/teams")
    await createTeamFromUI(page, name)
    await page.getByTestId("team-card").filter({ hasText: name }).click()
    await page.waitForURL(/\/teams\/[^/]+$/)
    await page.getByTestId("invite-button").click()
    const inviteUrl = await page.getByTestId("invite-url").inputValue()

    // User B accepts in a fresh, unauthenticated context.
    const bEmail = randomEmail()
    const bPassword = randomPassword()
    const bContext = await browser.newContext({
      storageState: { cookies: [], origins: [] },
      baseURL,
    })
    try {
      const bPage = await bContext.newPage()
      await signupViaUI(bPage, "Promo Bob", bEmail, bPassword)
      await bPage.goto(inviteUrl)
      // Joining redirects to /teams/<teamId>.
      await bPage.waitForURL(/\/teams\/[^/]+$/, { timeout: 15_000 })
      await bContext.close()
    } catch (err) {
      await bContext.close()
      throw err
    }

    // Back on User A: refresh members list.
    await page.reload()
    await expect(page.getByTestId("members-list")).toBeVisible()

    // Find member B by email.
    const bRow = page.getByTestId("member-row").filter({ hasText: bEmail })
    await expect(bRow).toBeVisible()
    await expect(bRow.getByTestId("member-role-badge")).toHaveAttribute(
      "data-role",
      "member",
    )

    // Promote.
    await bRow.getByTestId("member-actions").click()
    await page.getByTestId("member-promote").click()
    await expect(bRow.getByTestId("member-role-badge")).toHaveAttribute(
      "data-role",
      "admin",
    )
    // Wait for the actions button to re-enable after the role mutation settles.
    await expect(bRow.getByTestId("member-actions")).toBeEnabled()

    // Demote. Open the dropdown via keyboard to avoid Radix's
    // close-on-trigger-click race after the previous selection.
    await bRow.getByTestId("member-actions").focus()
    await page.keyboard.press("Enter")
    const demote = page.getByTestId("member-demote")
    await expect(demote).toBeVisible()
    await demote.click()
    await expect(bRow.getByTestId("member-role-badge")).toHaveAttribute(
      "data-role",
      "member",
    )
  })

  test("cannot demote the last admin", async ({ page, baseURL }) => {
    // Sole admin team: create a fresh non-personal team where caller is the
    // only admin and never invites anyone else.
    const name = randomTeamName()
    await page.goto("/teams")
    await createTeamFromUI(page, name)
    await page.getByTestId("team-card").filter({ hasText: name }).click()
    await page.waitForURL(/\/teams\/[^/]+$/)
    const teamId = teamIdFromTeamUrl(page.url())
    expect(teamId).not.toBeNull()

    // Caller's own member row should NOT have a member-actions trigger
    // (MembersList hides actions for self).
    const myRow = page
      .getByTestId("member-row")
      .filter({ hasText: firstSuperuser })
    await expect(myRow).toBeVisible()
    await expect(myRow.getByTestId("member-actions")).toHaveCount(0)

    // Backend defense: directly call the API. Same-origin to VITE_API_URL is
    // not guaranteed, so we hit the absolute URL with credentials so the
    // session cookie travels.
    const apiBase = process.env.VITE_API_URL ?? baseURL ?? ""
    // Resolve caller user_id from /api/v1/users/me
    const myId = await page.evaluate(async (base) => {
      const res = await fetch(`${base}/api/v1/users/me`, {
        credentials: "include",
      })
      if (!res.ok) throw new Error(`readUserMe failed: ${res.status}`)
      const body = await res.json()
      return body.id as string
    }, apiBase)
    expect(myId).toBeTruthy()

    const result = await page.evaluate(
      async ({ base, tid, uid }) => {
        const res = await fetch(
          `${base}/api/v1/teams/${tid}/members/${uid}/role`,
          {
            method: "PATCH",
            credentials: "include",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ role: "member" }),
          },
        )
        const text = await res.text()
        return { status: res.status, body: text }
      },
      { base: apiBase, tid: teamId as string, uid: myId },
    )
    expect(result.status).toBe(400)
    // Body should mention last-admin or similar; backend detail string varies
    // but must be present.
    expect(result.body.length).toBeGreaterThan(0)
  })

  test("admin removes a member", async ({ page, browser, baseURL }) => {
    // User A creates team and invites user B.
    const name = randomTeamName()
    await page.goto("/teams")
    await createTeamFromUI(page, name)
    await page.getByTestId("team-card").filter({ hasText: name }).click()
    await page.waitForURL(/\/teams\/[^/]+$/)
    await page.getByTestId("invite-button").click()
    const inviteUrl = await page.getByTestId("invite-url").inputValue()

    const bEmail = randomEmail()
    const bPassword = randomPassword()
    const bContext = await browser.newContext({
      storageState: { cookies: [], origins: [] },
      baseURL,
    })
    try {
      const bPage = await bContext.newPage()
      await signupViaUI(bPage, "Remove Bob", bEmail, bPassword)
      await bPage.goto(inviteUrl)
      await bPage.waitForURL(/\/teams\/[^/]+$/, { timeout: 15_000 })
    } finally {
      await bContext.close()
    }

    await page.reload()
    const bRow = page.getByTestId("member-row").filter({ hasText: bEmail })
    await expect(bRow).toBeVisible()

    // Open actions and click remove.
    await bRow.getByTestId("member-actions").click()
    await page.getByTestId("member-remove").click()

    // Type-to-confirm dialog: enter the literal phrase "remove".
    await expect(page.getByTestId("remove-member-dialog")).toBeVisible()
    await page.getByTestId("remove-member-confirm-input").fill("remove")
    await page.getByTestId("remove-member-confirm").click()

    await expect(page.getByText("Member removed")).toBeVisible()
    await expect(
      page.getByTestId("member-row").filter({ hasText: bEmail }),
    ).toHaveCount(0)
  })

  test("expired/unknown invite shows error", async ({ page }) => {
    await page.goto("/invite/totally-bogus")
    // The accept-invite flow shows either the "Invite not found" card or, when
    // the toast appears first, the same string in the toast region. Either is
    // an acceptable error signal — the slice contract is "user sees an error",
    // not a specific testid.
    await expect(page.getByText("Invite not found").first()).toBeVisible({
      timeout: 15_000,
    })
  })

  test("user-menu trigger is reachable on a 375px viewport", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    await page.goto("/teams")
    await openUserMenu(page)
    // Menu items appear in the dropdown.
    await expect(page.getByRole("menuitem", { name: /Log Out/i })).toBeVisible()
  })

  test("375px viewport: no horizontal scroll on /teams", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    await page.goto("/teams")
    await expect(
      page.getByText("Welcome back, nice to see you again!"),
    ).toBeVisible()
    const overflows = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    )
    expect(overflows).toBe(false)
  })
})

// =============================================================================
// Unauthenticated specs — run on every project; we override storageState so
// they pass on chromium / mobile-chrome too.
// =============================================================================

test.describe("teams dashboard (unauthenticated entry)", () => {
  test.use({ storageState: { cookies: [], origins: [] } })

  test("signup creates personal team and lands on dashboard", async ({
    page,
  }) => {
    const email = randomEmail()
    const password = randomPassword()
    await signupViaUI(page, "Signup Sam", email, password)

    await expect(page).toHaveURL(/\/teams$/)
    await expect(
      page.getByText("Welcome back, nice to see you again!"),
    ).toBeVisible()

    // Personal team card present with admin badge + Personal chip.
    const personalCard = page
      .getByTestId("team-card")
      .filter({ has: page.getByTestId("personal-badge") })
    await expect(personalCard).toBeVisible()
    await expect(personalCard.getByTestId("role-badge")).toHaveAttribute(
      "data-role",
      "admin",
    )
  })

  test("second user accepts invite via /invite/{code}", async ({
    page,
    browser,
    baseURL,
  }) => {
    // User A: log in with the seeded superuser, create a team + invite.
    const aContext: BrowserContext = await browser.newContext({ baseURL })
    try {
      const aPage = await aContext.newPage()
      await loginViaUI(aPage, firstSuperuser, firstSuperuserPassword)
      const name = randomTeamName()
      await createTeamFromUI(aPage, name)
      await aPage.getByTestId("team-card").filter({ hasText: name }).click()
      await aPage.waitForURL(/\/teams\/[^/]+$/)
      const expectedTeamId = teamIdFromTeamUrl(aPage.url())
      await aPage.getByTestId("invite-button").click()
      const inviteUrl = await aPage.getByTestId("invite-url").inputValue()

      // User B: this `page` fixture, unauthenticated. Sign up then accept.
      const bEmail = randomEmail()
      const bPassword = randomPassword()
      await signupViaUI(page, "Invite Bob", bEmail, bPassword)
      await page.goto(inviteUrl)
      await page.waitForURL(/\/teams\/[^/]+$/, { timeout: 15_000 })
      const arrivedTeamId = teamIdFromTeamUrl(page.url())
      expect(arrivedTeamId).toBe(expectedTeamId)

      // Team appears in B's dashboard with role = member.
      await page.goto("/teams")
      const card = page.getByTestId("team-card").filter({ hasText: name })
      await expect(card).toBeVisible()
      await expect(card.getByTestId("role-badge")).toHaveAttribute(
        "data-role",
        "member",
      )
    } finally {
      await aContext.close()
    }
  })
})
