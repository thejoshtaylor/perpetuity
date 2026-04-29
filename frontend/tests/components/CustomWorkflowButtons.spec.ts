// M005-sqm8et/S03/T05 — CustomWorkflowButtons spec.
//
// Exercises the custom workflow buttons row on the team-detail dashboard.
// Tests: 0 user workflows (row hidden), N user workflows (buttons visible),
// submit-with-form (dialog opens, payload sent), submit-without-form (direct
// dispatch, no modal), 503 error path (toast, no navigation).
//
// Run with: cd frontend && bunx playwright test --project=chromium \
//             tests/components/CustomWorkflowButtons.spec.ts

import { expect, type Page, test } from "@playwright/test"

const FAKE_RUN_ID = "aaaaaaaa-aaaa-aaaa-aaaa-111111111111"
const FAKE_TEAM_ID = "bbbbbbbb-bbbb-bbbb-bbbb-222222222222"
const FAKE_WF_ID_A = "cccccccc-cccc-cccc-cccc-333333333333"
const FAKE_WF_ID_B = "dddddddd-dddd-dddd-dddd-444444444444"

async function stubTeamsEnvelope(page: Page, teamId: string) {
  await page.route("**/api/v1/teams/", async (route) => {
    if (route.request().method() !== "GET") { await route.fallback(); return }
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({
        data: [{ id: teamId, name: "Test Team", is_personal: false, role: "admin",
                 created_at: "2026-01-01T00:00:00Z", updated_at: null }],
        count: 1,
      }),
    })
  })
}

async function stubTeamSiblings(page: Page, teamId: string) {
  for (const path of [`**/api/v1/teams/${teamId}/secrets`,
                       `**/api/v1/teams/${teamId}/members`,
                       `**/api/v1/teams/${teamId}/projects`,
                       `**/api/v1/teams/${teamId}/github/installations`]) {
    await page.route(path, async (route) => {
      if (route.request().method() !== "GET") { await route.fallback(); return }
      const empty = path.includes("secrets")
        ? JSON.stringify([])
        : JSON.stringify({ data: [], count: 0 })
      await route.fulfill({ status: 200, contentType: "application/json", body: empty })
    })
  }
}

async function stubWorkflowsList(
  page: Page,
  teamId: string,
  userWorkflows: Array<{ id: string; name: string; form_schema?: object }> = [],
) {
  await page.route(`**/api/v1/teams/${teamId}/workflows`, async (route) => {
    if (route.request().method() !== "GET") { await route.fallback(); return }
    const systemWorkflows = [
      { id: "sys-1", team_id: teamId, name: "_direct_claude", description: null,
        scope: "team", system_owned: true, created_at: "2026-01-01T00:00:00Z", updated_at: null },
      { id: "sys-2", team_id: teamId, name: "_direct_codex", description: null,
        scope: "team", system_owned: true, created_at: "2026-01-01T00:00:00Z", updated_at: null },
    ]
    const data = [
      ...systemWorkflows,
      ...userWorkflows.map((w) => ({
        id: w.id, team_id: teamId, name: w.name, description: null,
        scope: "user", system_owned: false,
        form_schema: w.form_schema ?? {},
        created_at: "2026-01-01T00:00:00Z", updated_at: null,
      })),
    ]
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ data, count: data.length }),
    })
  })
}

async function stubRunDetail(page: Page, runId: string, teamId: string, wfId: string) {
  await page.route(`**/api/v1/workflow_runs/${runId}`, async (route) => {
    if (route.request().method() !== "GET") { await route.fallback(); return }
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({
        id: runId, workflow_id: wfId, team_id: teamId, trigger_type: "button",
        triggered_by_user_id: null, target_user_id: null,
        trigger_payload: {}, status: "pending", error_class: null,
        started_at: null, finished_at: null, duration_ms: null,
        last_heartbeat_at: null, created_at: "2026-01-01T00:00:00Z", step_runs: [],
      }),
    })
  })
}

async function gotoTeamDetail(page: Page, teamId: string) {
  await stubTeamsEnvelope(page, teamId)
  await stubTeamSiblings(page, teamId)
  await page.goto(`/teams/${teamId}`)
  await expect(page.getByTestId("team-detail")).toBeVisible({ timeout: 10000 })
}

test.describe("CustomWorkflowButtons", () => {
  test.beforeEach(({ page: _ }, testInfo) => {
    if (testInfo.project.name === "mobile-chrome-no-auth") testInfo.skip()
  })

  test("renders nothing when team has no user workflows", async ({ page }) => {
    await stubWorkflowsList(page, FAKE_TEAM_ID, [])
    await gotoTeamDetail(page, FAKE_TEAM_ID)
    // The section should be present but the inner buttons container absent
    const section = page.getByTestId("custom-workflows-section")
    await expect(section).toBeVisible()
    await expect(page.getByTestId("custom-workflow-buttons")).not.toBeVisible()
  })

  test("renders a button per user workflow", async ({ page }) => {
    await stubWorkflowsList(page, FAKE_TEAM_ID, [
      { id: FAKE_WF_ID_A, name: "Lint report" },
      { id: FAKE_WF_ID_B, name: "Deploy staging" },
    ])
    await gotoTeamDetail(page, FAKE_TEAM_ID)
    await expect(page.getByTestId(`custom-workflow-button-${FAKE_WF_ID_A}`)).toBeVisible()
    await expect(page.getByTestId(`custom-workflow-button-${FAKE_WF_ID_B}`)).toBeVisible()
  })

  test("workflow with form fields opens dialog before dispatch", async ({ page }) => {
    const formSchema = { fields: [{ name: "branch", label: "Branch", kind: "string", required: true }] }
    await stubWorkflowsList(page, FAKE_TEAM_ID, [
      { id: FAKE_WF_ID_A, name: "Lint report", form_schema: formSchema },
    ])
    await gotoTeamDetail(page, FAKE_TEAM_ID)

    await page.getByTestId(`custom-workflow-button-${FAKE_WF_ID_A}`).click()
    await expect(page.getByTestId(`workflow-form-dialog-${FAKE_WF_ID_A}`)).toBeVisible()
    await expect(page.getByTestId("wf-field-branch")).toBeVisible()
  })

  test("submitting form dialog dispatches with payload and navigates to run", async ({ page }) => {
    const formSchema = { fields: [{ name: "branch", label: "Branch", kind: "string", required: true }] }
    await stubWorkflowsList(page, FAKE_TEAM_ID, [
      { id: FAKE_WF_ID_A, name: "Lint report", form_schema: formSchema },
    ])
    await stubRunDetail(page, FAKE_RUN_ID, FAKE_TEAM_ID, FAKE_WF_ID_A)

    let postedBody: Record<string, unknown> | null = null
    await page.route(`**/api/v1/workflows/${FAKE_WF_ID_A}/run`, async (route) => {
      if (route.request().method() !== "POST") { await route.fallback(); return }
      postedBody = JSON.parse(route.request().postData() ?? "{}")
      await route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ run_id: FAKE_RUN_ID, status: "pending" }),
      })
    })

    await gotoTeamDetail(page, FAKE_TEAM_ID)
    await page.getByTestId(`custom-workflow-button-${FAKE_WF_ID_A}`).click()
    await page.getByTestId("wf-field-branch").fill("main")
    await page.getByTestId(`workflow-form-dialog-submit-${FAKE_WF_ID_A}`).click()

    await page.waitForURL(`**/runs/${FAKE_RUN_ID}`)
    expect(postedBody).toMatchObject({ trigger_payload: { branch: "main" } })
  })

  test("workflow without form fields dispatches directly without modal", async ({ page }) => {
    await stubWorkflowsList(page, FAKE_TEAM_ID, [
      { id: FAKE_WF_ID_A, name: "Quick deploy", form_schema: {} },
    ])
    await stubRunDetail(page, FAKE_RUN_ID, FAKE_TEAM_ID, FAKE_WF_ID_A)

    await page.route(`**/api/v1/workflows/${FAKE_WF_ID_A}/run`, async (route) => {
      if (route.request().method() !== "POST") { await route.fallback(); return }
      await route.fulfill({
        status: 200, contentType: "application/json",
        body: JSON.stringify({ run_id: FAKE_RUN_ID, status: "pending" }),
      })
    })

    await gotoTeamDetail(page, FAKE_TEAM_ID)
    await page.getByTestId(`custom-workflow-button-${FAKE_WF_ID_A}`).click()

    // Dialog should NOT appear — direct dispatch
    await expect(page.getByTestId(`workflow-form-dialog-${FAKE_WF_ID_A}`)).not.toBeVisible()
    await page.waitForURL(`**/runs/${FAKE_RUN_ID}`)
  })

  test("503 dispatch error shows toast without navigating", async ({ page }) => {
    await stubWorkflowsList(page, FAKE_TEAM_ID, [
      { id: FAKE_WF_ID_A, name: "Quick deploy", form_schema: {} },
    ])

    await page.route(`**/api/v1/workflows/${FAKE_WF_ID_A}/run`, async (route) => {
      if (route.request().method() !== "POST") { await route.fallback(); return }
      await route.fulfill({
        status: 503, contentType: "application/json",
        body: JSON.stringify({ detail: { detail: "task_dispatch_failed" } }),
      })
    })

    await gotoTeamDetail(page, FAKE_TEAM_ID)
    await page.getByTestId(`custom-workflow-button-${FAKE_WF_ID_A}`).click()

    await expect(page.getByText(/task_dispatch_failed/i).first()).toBeVisible()
    expect(page.url()).not.toContain("/runs/")
  })
})
