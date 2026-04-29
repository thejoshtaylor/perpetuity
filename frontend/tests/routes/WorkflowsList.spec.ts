// M005-sqm8et/S03/T05 — WorkflowsList route spec.
//
// Admin sees Create / Edit / Delete actions; member sees only list.
//
// Run with: cd frontend && bunx playwright test --project=chromium \
//             tests/routes/WorkflowsList.spec.ts

import { expect, type Page, test } from "@playwright/test"

const FAKE_TEAM_ID = "aaaaaaaa-0000-0000-0000-000000000001"
const FAKE_WF_ID = "bbbbbbbb-0000-0000-0000-000000000002"

async function stubWorkflows(page: Page, teamId: string) {
  await page.route(`**/api/v1/teams/${teamId}/workflows`, async (route) => {
    if (route.request().method() !== "GET") { await route.fallback(); return }
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({
        data: [
          { id: FAKE_WF_ID, team_id: teamId, name: "my-workflow", description: null,
            scope: "user", system_owned: false, form_schema: {},
            created_at: "2026-01-01T00:00:00Z", updated_at: null },
        ],
        count: 1,
      }),
    })
  })
}

async function gotoWorkflowsList(page: Page, teamId: string, asAdmin: boolean) {
  await stubWorkflows(page, teamId)
  await page.goto(`/workflows?teamId=${teamId}&admin=${asAdmin ? "true" : "false"}`)
  await expect(page.getByTestId("workflows-list-page")).toBeVisible({ timeout: 10000 })
}

test.describe("WorkflowsList — role-based visibility", () => {
  test.beforeEach(({ page: _ }, testInfo) => {
    if (testInfo.project.name === "mobile-chrome-no-auth") testInfo.skip()
  })

  test("admin sees Create / Edit / Delete buttons", async ({ page }) => {
    await gotoWorkflowsList(page, FAKE_TEAM_ID, true)
    await expect(page.getByTestId("workflow-create-button")).toBeVisible()
    await expect(page.getByTestId(`workflow-edit-${FAKE_WF_ID}`)).toBeVisible()
    await expect(page.getByTestId(`workflow-delete-${FAKE_WF_ID}`)).toBeVisible()
  })

  test("member sees workflow list but no Create / Edit / Delete buttons", async ({ page }) => {
    await gotoWorkflowsList(page, FAKE_TEAM_ID, false)
    await expect(page.getByTestId("workflow-create-button")).not.toBeVisible()
    await expect(page.getByTestId(`workflow-edit-${FAKE_WF_ID}`)).not.toBeVisible()
    await expect(page.getByTestId(`workflow-delete-${FAKE_WF_ID}`)).not.toBeVisible()
    // Workflow row and name ARE visible
    await expect(page.getByTestId(`workflow-row-${FAKE_WF_ID}`)).toBeVisible()
    await expect(page.getByTestId(`workflow-name-${FAKE_WF_ID}`)).toContainText("my-workflow")
  })

  test("empty state shown when no user workflows", async ({ page }) => {
    await page.route(`**/api/v1/teams/${FAKE_TEAM_ID}/workflows`, async (route) => {
      if (route.request().method() !== "GET") { await route.fallback(); return }
      await route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ data: [], count: 0 }),
      })
    })
    await page.goto(`/workflows?teamId=${FAKE_TEAM_ID}&admin=true`)
    await expect(page.getByTestId("workflows-empty")).toBeVisible({ timeout: 10000 })
  })

  test("delete confirmation flow", async ({ page }) => {
    await gotoWorkflowsList(page, FAKE_TEAM_ID, true)

    let deleteCallCount = 0
    await page.route(`**/api/v1/workflows/${FAKE_WF_ID}`, async (route) => {
      if (route.request().method() === "DELETE") {
        deleteCallCount++
        await route.fulfill({ status: 204 })
        return
      }
      await route.fallback()
    })

    await page.getByTestId(`workflow-delete-${FAKE_WF_ID}`).click()
    // Confirmation buttons appear
    await expect(page.getByTestId(`workflow-delete-confirm-${FAKE_WF_ID}`)).toBeVisible()
    await expect(page.getByTestId(`workflow-delete-cancel-${FAKE_WF_ID}`)).toBeVisible()

    // Confirm deletion
    await page.getByTestId(`workflow-delete-confirm-${FAKE_WF_ID}`).click()
    await expect(async () => {
      expect(deleteCallCount).toBe(1)
    }).toPass({ timeout: 3000 })
  })
})
