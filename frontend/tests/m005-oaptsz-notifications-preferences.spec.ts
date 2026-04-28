import { expect, type Page, test } from "@playwright/test"

// M005-oaptsz/S02/T05: notification preferences UI + slice contract.
//
// This spec proves two things the slice plan calls out:
//
//   A) Cross-device 5s read-state sync — two BrowserContexts authenticated as
//      the same user; ContextA seeds + reads → ContextB's bell picks the
//      changes up within one polling cycle (6s budget).
//   B) Preference-off skips the in_app insert — toggle team_invite_accepted
//      off in the preferences UI, fire the test endpoint with
//      kind=team_invite_accepted, assert no row landed; toggle back on, fire
//      again, assert it lands.
//
// Both scenarios use the existing storageState (auth.setup.ts logs in as the
// seeded superuser); the test endpoint is gated to system_admin so the same
// account can also call POST /notifications/test with a kind override.

const STORAGE_STATE = "playwright/.auth/user.json"

async function listNotifications(
  page: Page,
): Promise<{ kind: string; created_at: string | null }[]> {
  return await page.evaluate(async () => {
    const mod = await import("/src/client/sdk.gen.ts")
    const res = (await mod.NotificationsService.listNotifications({
      limit: 50,
    })) as { data: { kind: string; created_at: string | null }[] }
    return res.data
  })
}

async function fireTestNotification(
  page: Page,
  body: { message: string; kind?: string },
): Promise<{ id: string | null }> {
  return await page.evaluate(async (b) => {
    const mod = await import("/src/client/sdk.gen.ts")
    const created = (await mod.NotificationsService.triggerTestNotification({
      requestBody: b,
    })) as { id: string } | null
    return { id: created?.id ?? null }
  }, body)
}

async function setPreference(
  page: Page,
  eventType: string,
  inApp: boolean,
): Promise<void> {
  await page.evaluate(
    async (args) => {
      const mod = await import("/src/client/sdk.gen.ts")
      await mod.NotificationsService.upsertPreference({
        eventType: args.eventType,
        requestBody: { in_app: args.inApp, push: false },
      })
    },
    { eventType, inApp },
  )
}

test.describe("M005-oaptsz notifications preferences", () => {
  test.beforeEach(({ page: _ }, testInfo) => {
    if (testInfo.project.name === "mobile-chrome-no-auth") {
      testInfo.skip()
    }
  })

  test.beforeEach(async ({ page }) => {
    // Re-enable any preference a previous run of scenario B may have left
    // disabled. We deliberately do NOT mark-all-read here because the seeded
    // superuser is shared with the sibling notifications spec; clobbering its
    // unread items would cause that spec to fail.
    await page.goto("/teams")
    await page.waitForLoadState("networkidle").catch(() => {})
    for (const k of [
      "team_invite_accepted",
      "system",
      "project_created",
      "workflow_run_started",
      "workflow_run_succeeded",
      "workflow_run_failed",
      "workflow_step_completed",
    ]) {
      await setPreference(page, k, true).catch(() => {})
    }
  })

  test("A: cross-device 5s read-state sync between two contexts", async ({
    browser,
    baseURL,
  }) => {
    // Two BrowserContexts authenticated as the same user via storageState.
    // The slice contract is: a read-state mutation in ContextA propagates to
    // ContextB within one polling cycle (5s + 1s jitter). We prove that on
    // the specific seeded item — NOT on the global badge — because other
    // specs share the same superuser and may be inserting unreads in
    // parallel; clobbering them would break those specs.
    const ctxA = await browser.newContext({
      storageState: STORAGE_STATE,
      baseURL,
    })
    const ctxB = await browser.newContext({
      storageState: STORAGE_STATE,
      baseURL,
    })
    try {
      const pageA = await ctxA.newPage()
      const pageB = await ctxB.newPage()

      await pageA.goto("/teams")
      await pageB.goto("/teams")
      await pageA.waitForLoadState("networkidle").catch(() => {})
      await pageB.waitForLoadState("networkidle").catch(() => {})

      // Seed a uniquely-identifiable system notification from ContextA.
      const seedMessage = `cross-device-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
      const seeded = await fireTestNotification(pageA, { message: seedMessage })
      expect(seeded.id).toBeTruthy()

      // Open the bell on both contexts; both must show the seeded item with
      // data-unread='true' within one polling cycle.
      await pageA.getByTestId("notification-bell").click()
      const itemA = pageA
        .getByTestId("notification-item")
        .filter({ hasText: seedMessage })
      await expect(itemA).toBeVisible({ timeout: 6000 })
      await expect(itemA).toHaveAttribute("data-unread", "true")

      await pageB.getByTestId("notification-bell").click()
      const itemB = pageB
        .getByTestId("notification-item")
        .filter({ hasText: seedMessage })
      await expect(itemB).toBeVisible({ timeout: 6000 })
      await expect(itemB).toHaveAttribute("data-unread", "true")

      // ContextA marks the seeded item read by clicking it.
      await itemA.click()
      await expect(itemA).toHaveAttribute("data-unread", "false", {
        timeout: 6000,
      })

      // Slice contract: ContextB's view of the same item must flip to
      // data-unread='false' within one polling cycle (5s + 1s buffer).
      await expect(itemB).toHaveAttribute("data-unread", "false", {
        timeout: 6000,
      })
    } finally {
      await ctxA.close()
      await ctxB.close()
    }
  })

  test("B: preference-off skips the in_app insert; toggling on re-enables it", async ({
    page,
  }) => {
    await page.goto("/settings")
    await page.waitForLoadState("networkidle").catch(() => {})

    // Click the Notifications tab.
    await page.getByRole("tab", { name: "Notifications" }).click()
    await expect(page.getByTestId("notification-preferences")).toBeVisible()

    // Toggle team_invite_accepted's in-app switch off via the UI. Use the
    // testid-keyed switch the component exposes.
    const inviteSwitch = page.getByTestId(
      "notification-pref-in-app-team_invite_accepted",
    )
    await expect(inviteSwitch).toBeVisible()
    // The default for team_invite_accepted is on, so first click flips it off.
    if ((await inviteSwitch.getAttribute("data-state")) !== "unchecked") {
      await inviteSwitch.click()
      await expect(inviteSwitch).toHaveAttribute("data-state", "unchecked", {
        timeout: 4000,
      })
    }

    // Snapshot existing team_invite_accepted rows so we measure the delta
    // (other specs might have left rows behind for the same superuser).
    const before = await listNotifications(page)
    const beforeInvites = before.filter(
      (n) => n.kind === "team_invite_accepted",
    )

    // Fire the test endpoint with kind=team_invite_accepted. The endpoint
    // returns 200 with a null body when the recipient's preference suppresses
    // the kind — that null body IS the contract signal.
    const suppressed = await fireTestNotification(page, {
      message: "should be suppressed",
      kind: "team_invite_accepted",
    })
    expect(suppressed.id).toBeNull()

    // The list must NOT have a new team_invite_accepted row.
    const afterOff = await listNotifications(page)
    const afterOffInvites = afterOff.filter(
      (n) => n.kind === "team_invite_accepted",
    )
    expect(afterOffInvites.length).toBe(beforeInvites.length)

    // Toggle back on — the switch must reflect the server's new state via
    // the optimistic re-anchor + invalidation in NotificationPreferences.
    await inviteSwitch.click()
    await expect(inviteSwitch).toHaveAttribute("data-state", "checked", {
      timeout: 4000,
    })

    // Fire again — the row should land this time.
    const landed = await fireTestNotification(page, {
      message: "should land",
      kind: "team_invite_accepted",
    })
    expect(landed.id).toBeTruthy()

    const afterOn = await listNotifications(page)
    const afterOnInvites = afterOn.filter(
      (n) => n.kind === "team_invite_accepted",
    )
    expect(afterOnInvites.length).toBe(beforeInvites.length + 1)

    // Cleanup: mark the seeded notification read so it does not leave the
    // shared superuser's badge stuck visible for the sibling notifications
    // spec (which asserts badge-hidden after its own mark-read).
    if (landed.id) {
      await page.evaluate(async (id) => {
        const mod = await import("/src/client/sdk.gen.ts")
        await mod.NotificationsService.markRead({ notificationId: id })
      }, landed.id)
    }
  })
})
