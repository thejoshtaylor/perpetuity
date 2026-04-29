// M005-sqm8et/S03/T05 — RunCancelButton spec.
//
// Tests:
// - Cancel button appears only when run is in-flight (pending/running)
// - Cancel button absent for terminal statuses (succeeded, failed, cancelled)
// - Click sends POST to /cancel and updates status optimistically
//
// Run with: cd frontend && bunx playwright test --project=chromium \
//             tests/components/RunCancelButton.spec.ts

import { expect, type Page, test } from "@playwright/test"

const FAKE_RUN_ID = "eeeeeeee-0000-0000-0000-000000000001"
const FAKE_WF_ID = "ffffffff-0000-0000-0000-000000000002"
const FAKE_TEAM_ID = "10101010-0000-0000-0000-000000000003"

type RunStatus = "pending" | "running" | "succeeded" | "failed" | "cancelled"

async function stubRunDetail(page: Page, status: RunStatus) {
  await page.route(`**/api/v1/workflow_runs/${FAKE_RUN_ID}`, async (route) => {
    if (route.request().method() !== "GET") { await route.fallback(); return }
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({
        id: FAKE_RUN_ID, workflow_id: FAKE_WF_ID, team_id: FAKE_TEAM_ID,
        trigger_type: "button", triggered_by_user_id: null, target_user_id: null,
        trigger_payload: {}, status, error_class: null,
        started_at: status === "running" ? "2026-01-01T00:00:00Z" : null,
        finished_at: null, duration_ms: null, last_heartbeat_at: null,
        created_at: "2026-01-01T00:00:00Z",
        step_runs: [],
      }),
    })
  })
}

async function gotoRunDetail(page: Page, status: RunStatus) {
  await stubRunDetail(page, status)
  await page.goto(`/runs/${FAKE_RUN_ID}`)
  await expect(page.getByTestId("run-detail")).toBeVisible({ timeout: 10000 })
}

test.describe("RunCancelButton", () => {
  test.beforeEach(({ page: _ }, testInfo) => {
    if (testInfo.project.name === "mobile-chrome-no-auth") testInfo.skip()
  })

  test("cancel button visible for pending run", async ({ page }) => {
    await gotoRunDetail(page, "pending")
    await expect(page.getByTestId("run-cancel-button")).toBeVisible()
  })

  test("cancel button visible for running run", async ({ page }) => {
    await gotoRunDetail(page, "running")
    await expect(page.getByTestId("run-cancel-button")).toBeVisible()
  })

  test("cancel button absent for succeeded run", async ({ page }) => {
    await gotoRunDetail(page, "succeeded")
    await expect(page.getByTestId("run-cancel-button")).not.toBeVisible()
  })

  test("cancel button absent for failed run", async ({ page }) => {
    await gotoRunDetail(page, "failed")
    await expect(page.getByTestId("run-cancel-button")).not.toBeVisible()
  })

  test("cancel button absent for already cancelled run", async ({ page }) => {
    await gotoRunDetail(page, "cancelled")
    await expect(page.getByTestId("run-cancel-button")).not.toBeVisible()
  })

  test("clicking cancel POSTs to /cancel and optimistically updates status", async ({ page }) => {
    let cancelCallCount = 0
    await stubRunDetail(page, "running")

    await page.route(`**/api/v1/workflow_runs/${FAKE_RUN_ID}/cancel`, async (route) => {
      if (route.request().method() !== "POST") { await route.fallback(); return }
      cancelCallCount++
      await route.fulfill({
        status: 202, contentType: "application/json",
        body: JSON.stringify({ status: "cancelling" }),
      })
    })

    await gotoRunDetail(page, "running")
    await page.getByTestId("run-cancel-button").click()

    // API was called
    await expect(async () => {
      expect(cancelCallCount).toBe(1)
    }).toPass({ timeout: 3000 })

    // Cancel button should disappear (optimistic update to cancelled → no longer in-flight)
    await expect(page.getByTestId("run-cancel-button")).not.toBeVisible({ timeout: 3000 })
  })
})
