// M005-sqm8et/S03/T05 — WorkflowEditor route spec.
//
// Create flow: form renders, save POSTs to /workflows.
// Validation: _direct_ name rejected server-side → toast displayed.
// Edit flow: existing data loaded, save PUTs.
// Delete confirmation tested in WorkflowsList spec.
//
// Run with: cd frontend && bunx playwright test --project=chromium \
//             tests/routes/WorkflowEditor.spec.ts

import { expect, type Page, test } from "@playwright/test"

const FAKE_TEAM_ID = "aaaaaaaa-1111-1111-1111-000000000001"
const FAKE_WF_ID = "bbbbbbbb-1111-1111-1111-000000000002"

async function stubTeamMembers(page: Page, teamId: string) {
  await page.route(`**/api/v1/teams/${teamId}/members`, async (route) => {
    if (route.request().method() !== "GET") { await route.fallback(); return }
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ data: [], count: 0 }),
    })
  })
}

async function stubGetWorkflow(page: Page, wfId: string) {
  await page.route(`**/api/v1/workflows/${wfId}`, async (route) => {
    if (route.request().method() !== "GET") { await route.fallback(); return }
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({
        id: wfId, team_id: FAKE_TEAM_ID, name: "existing-workflow",
        description: "Does stuff", scope: "user", system_owned: false,
        form_schema: { fields: [{ name: "branch", label: "Branch", kind: "string", required: true }] },
        target_user_id: null, round_robin_cursor: 0,
        steps: [
          { id: "step-1", workflow_id: wfId, step_index: 0, action: "shell",
            config: { cmd: "echo hi" }, target_container: "user_workspace",
            created_at: "2026-01-01T00:00:00Z", updated_at: null },
        ],
        created_at: "2026-01-01T00:00:00Z", updated_at: null,
      }),
    })
  })
}

async function stubWorkflowsList(page: Page, teamId: string) {
  await page.route(`**/api/v1/teams/${teamId}/workflows`, async (route) => {
    if (route.request().method() !== "GET") { await route.fallback(); return }
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ data: [], count: 0 }),
    })
  })
}

async function gotoCreate(page: Page) {
  await stubTeamMembers(page, FAKE_TEAM_ID)
  await stubWorkflowsList(page, FAKE_TEAM_ID)
  await page.goto(`/workflows/new?teamId=${FAKE_TEAM_ID}&admin=true`)
  await expect(page.getByTestId("workflow-editor-page")).toBeVisible({ timeout: 10000 })
}

async function gotoEdit(page: Page) {
  await stubTeamMembers(page, FAKE_TEAM_ID)
  await stubGetWorkflow(page, FAKE_WF_ID)
  await stubWorkflowsList(page, FAKE_TEAM_ID)
  await page.goto(`/workflows/${FAKE_WF_ID}?teamId=${FAKE_TEAM_ID}&admin=true`)
  await expect(page.getByTestId("workflow-editor-page")).toBeVisible({ timeout: 10000 })
}

test.describe("WorkflowEditor", () => {
  test.beforeEach(({ page: _ }, testInfo) => {
    if (testInfo.project.name === "mobile-chrome-no-auth") testInfo.skip()
  })

  test("create flow — form renders and POSTs on save", async ({ page }) => {
    let postedBody: Record<string, unknown> | null = null
    await page.route(`**/api/v1/teams/${FAKE_TEAM_ID}/workflows`, async (route) => {
      if (route.request().method() === "POST") {
        postedBody = JSON.parse(route.request().postData() ?? "{}")
        await route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({
            id: FAKE_WF_ID, team_id: FAKE_TEAM_ID, name: "new-wf", description: null,
            scope: "user", system_owned: false, form_schema: {}, target_user_id: null,
            round_robin_cursor: 0, steps: [],
            created_at: "2026-01-01T00:00:00Z", updated_at: null,
          }),
        })
        return
      }
      await route.fallback()
    })

    await gotoCreate(page)
    await page.getByTestId("workflow-name-input").fill("new-wf")
    await page.getByTestId("workflow-save-button").click()

    // After save, navigates back to list
    await page.waitForURL(/\/workflows\?/)
    expect(postedBody).toMatchObject({ name: "new-wf" })
  })

  test("validation rejects _direct_ name prefix via server error toast", async ({ page }) => {
    await page.route(`**/api/v1/teams/${FAKE_TEAM_ID}/workflows`, async (route) => {
      if (route.request().method() === "POST") {
        await route.fulfill({
          status: 403, contentType: "application/json",
          body: JSON.stringify({ detail: "cannot_modify_system_workflow" }),
        })
        return
      }
      await route.fallback()
    })

    await gotoCreate(page)
    await page.getByTestId("workflow-name-input").fill("_direct_my_workflow")
    await page.getByTestId("workflow-save-button").click()

    await expect(page.getByText(/cannot_modify_system_workflow/i).first()).toBeVisible()
    // Still on editor page
    await expect(page.getByTestId("workflow-editor-page")).toBeVisible()
  })

  test("edit flow — loads existing data and PUTs on save", async ({ page }) => {
    let putBody: Record<string, unknown> | null = null
    await page.route(`**/api/v1/workflows/${FAKE_WF_ID}`, async (route) => {
      if (route.request().method() === "PUT") {
        putBody = JSON.parse(route.request().postData() ?? "{}")
        await route.fulfill({
          status: 200, contentType: "application/json",
          body: JSON.stringify({
            id: FAKE_WF_ID, team_id: FAKE_TEAM_ID, name: "updated-wf", description: "Does stuff",
            scope: "user", system_owned: false, form_schema: {}, target_user_id: null,
            round_robin_cursor: 0, steps: [],
            created_at: "2026-01-01T00:00:00Z", updated_at: null,
          }),
        })
        return
      }
      await route.fallback()
    })

    await gotoEdit(page)
    // Existing name should be pre-filled
    await expect(page.getByTestId("workflow-name-input")).toHaveValue("existing-workflow")

    // Change the name
    await page.getByTestId("workflow-name-input").fill("updated-wf")
    await page.getByTestId("workflow-save-button").click()

    await page.waitForURL(/\/workflows\?/)
    expect((putBody as unknown as { name?: string })?.name).toBe("updated-wf")
  })
})
