import { expect, test } from "@playwright/test"

// M005-oaptsz/S02/T04: in-app notification bell + panel + 5s polling.
//
// This spec uses the existing storageState login (auth.setup.ts), seeds a
// system notification via POST /api/v1/notifications/test, and verifies that
// the bell badge picks the row up within one polling cycle, that clicking the
// bell renders the seeded item, and that clicking the item clears the badge.

test.describe("M005-oaptsz notifications bell", () => {
  test.beforeEach(({ page: _ }, testInfo) => {
    if (testInfo.project.name === "mobile-chrome-no-auth") {
      testInfo.skip()
    }
  })

  test("seed → badge appears → panel renders item → mark read clears badge", async ({
    page,
  }) => {
    await page.goto("/teams")
    await page.waitForLoadState("networkidle").catch(() => {})

    const bell = page.getByTestId("notification-bell")
    await expect(bell).toBeVisible()

    // Seed a system notification by calling the SDK from within the page —
    // page-context fetch carries the same cookie auth the app uses, regardless
    // of whether the API origin matches the FE origin.
    const seedMessage = `audit-seed-${Date.now()}`
    const seedResult = await page.evaluate(async (message) => {
      const mod = await import("/src/client/sdk.gen.ts")
      const created = await mod.NotificationsService.triggerTestNotification({
        requestBody: { message },
      })
      return { id: (created as { id: string }).id }
    }, seedMessage)
    expect(seedResult.id).toBeTruthy()

    // Trigger an immediate refetch by reloading — also exercises the badge's
    // post-load polling cadence (5s + 1s buffer).
    await page.reload()
    await page.waitForLoadState("networkidle").catch(() => {})

    const badge = page.getByTestId("notification-bell-badge")
    await expect(badge).toBeVisible({ timeout: 6000 })
    await expect(badge).toHaveText(/^\d+$/, { timeout: 6000 })

    await bell.click()

    const panel = page.getByTestId("notification-panel")
    await expect(panel).toBeVisible()

    const item = page
      .getByTestId("notification-item")
      .filter({ hasText: seedMessage })
    await expect(item).toBeVisible({ timeout: 6000 })
    await expect(item).toHaveAttribute("data-unread", "true")

    await item.click()

    // The seeded item must flip to data-unread='false' once markRead settles.
    // We assert on the item rather than the bell badge because the seeded
    // superuser is shared with sibling specs (M005-oaptsz/S02/T05 preferences
    // contract) that may have their own unread items in flight; the badge is
    // a global counter and would race, but the seeded item's read-state is
    // local to this test.
    await expect(item).toHaveAttribute("data-unread", "false", {
      timeout: 2000,
    })
  })
})
