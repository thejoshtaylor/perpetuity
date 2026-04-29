// M005-sqm8et/S02/T05 — DirectAIButtons spec.
//
// Exercises the dashboard surface that fires the auto-seeded
// `_direct_claude` / `_direct_codex` system workflows. The spec stubs the
// teams envelope + workflow registry + dispatch endpoint via
// `page.route()` so it stays self-contained — full Celery integration is
// covered by T06. The seeded superuser's storageState (set up by
// auth.setup.ts) is enough auth for the route gates that aren't stubbed
// (e.g. the layout's `/users/me` dependency); everything T05 cares about
// is stubbed.
//
// Run with: cd frontend && bunx playwright test --project=chromium \
//             tests/components/DirectAIButtons.spec.ts

import { expect, type Page, test } from "@playwright/test"

const FAKE_RUN_ID = "11111111-1111-1111-1111-111111111111"
const FAKE_TEAM_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
const FAKE_CLAUDE_WF_ID = "22222222-2222-2222-2222-222222222222"
const FAKE_CODEX_WF_ID = "33333333-3333-3333-3333-333333333333"

/** Stub `GET /api/v1/teams/` so the team-detail route can find a synthetic
 * team without going through the create-team flow. The team-detail route
 * scans the readTeams envelope for the URL teamId; if it finds a match,
 * the page renders. */
async function stubTeamsEnvelope(page: Page, teamId: string) {
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
            name: "Stub Team",
            is_personal: false,
            role: "admin",
            created_at: "2026-04-29T00:00:00Z",
            updated_at: null,
          },
        ],
        count: 1,
      }),
    })
  })
}

/** Stub `GET /api/v1/teams/{team_id}/secrets` — the TeamSecretsPanel
 * sibling component fires this on mount; without a stub it would 404 (the
 * synthetic team doesn't exist on the backend) and the panel would render
 * its error state. We don't care about that surface here, so return an
 * empty list. */
async function stubTeamSecrets(page: Page, teamId: string) {
  await page.route(
    `**/api/v1/teams/${teamId}/secrets`,
    async (route) => {
      if (route.request().method() !== "GET") {
        await route.fallback()
        return
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          { key: "claude_api_key", has_value: false, sensitive: true, updated_at: null },
          { key: "openai_api_key", has_value: false, sensitive: true, updated_at: null },
        ]),
      })
    },
  )
}

/** The team-detail route also renders MembersList and ProjectsList which
 * fetch their own data. Stub them to empty so the page settles instead of
 * hanging on Suspense boundaries. */
async function stubTeamSiblings(page: Page, teamId: string) {
  await page.route(
    `**/api/v1/teams/${teamId}/members`,
    async (route) => {
      if (route.request().method() !== "GET") {
        await route.fallback()
        return
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: [], count: 0 }),
      })
    },
  )
  await page.route(
    `**/api/v1/teams/${teamId}/projects`,
    async (route) => {
      if (route.request().method() !== "GET") {
        await route.fallback()
        return
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ data: [], count: 0 }),
      })
    },
  )
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
        body: JSON.stringify({ data: [], count: 0 }),
      })
    },
  )
}

/** Stub `GET /api/v1/teams/{team_id}/workflows` to return the two
 * auto-seeded direct-AI rows. */
async function stubWorkflowsList(page: Page, teamId: string) {
  await page.route(
    `**/api/v1/teams/${teamId}/workflows`,
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
              id: FAKE_CLAUDE_WF_ID,
              team_id: teamId,
              name: "_direct_claude",
              description: null,
              scope: "team",
              system_owned: true,
              created_at: "2026-04-29T00:00:00Z",
              updated_at: null,
            },
            {
              id: FAKE_CODEX_WF_ID,
              team_id: teamId,
              name: "_direct_codex",
              description: null,
              scope: "team",
              system_owned: true,
              created_at: "2026-04-29T00:00:00Z",
              updated_at: null,
            },
          ],
          count: 2,
        }),
      })
    },
  )
}

/** Stub the polled run-detail endpoint so the dispatch flow can land on
 * /runs/<id> without needing a live celery worker. */
async function stubRunDetail(
  page: Page,
  runId: string,
  teamId: string,
  workflowId: string,
) {
  await page.route(
    `**/api/v1/workflow_runs/${runId}`,
    async (route) => {
      if (route.request().method() !== "GET") {
        await route.fallback()
        return
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          id: runId,
          workflow_id: workflowId,
          team_id: teamId,
          trigger_type: "button",
          triggered_by_user_id: null,
          target_user_id: null,
          trigger_payload: { prompt: "List the files in this repo" },
          status: "pending",
          error_class: null,
          started_at: null,
          finished_at: null,
          duration_ms: null,
          last_heartbeat_at: null,
          created_at: "2026-04-29T00:00:00Z",
          step_runs: [
            {
              id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
              workflow_run_id: runId,
              step_index: 0,
              snapshot: { action: "claude", config: {} },
              status: "pending",
              stdout: "",
              stderr: "",
              exit_code: null,
              error_class: null,
              duration_ms: null,
              started_at: null,
              finished_at: null,
              created_at: "2026-04-29T00:00:00Z",
            },
          ],
        }),
      })
    },
  )
}

async function gotoStubbedTeamDetail(page: Page, teamId: string) {
  await stubTeamsEnvelope(page, teamId)
  await stubTeamSecrets(page, teamId)
  await stubTeamSiblings(page, teamId)
  await page.goto(`/teams/${teamId}`)
  await expect(page.getByTestId("team-detail")).toBeVisible({ timeout: 10000 })
}

test.describe("DirectAIButtons — dispatch flow", () => {
  test.beforeEach(({ page: _ }, testInfo) => {
    if (testInfo.project.name === "mobile-chrome-no-auth") {
      testInfo.skip()
    }
  })

  test("renders both buttons for a team member", async ({ page }) => {
    await stubWorkflowsList(page, FAKE_TEAM_ID)
    await gotoStubbedTeamDetail(page, FAKE_TEAM_ID)

    const buttons = page.getByTestId("direct-ai-buttons")
    await expect(buttons).toBeVisible()
    await expect(page.getByTestId("direct-ai-button-claude")).toBeVisible()
    await expect(page.getByTestId("direct-ai-button-codex")).toBeVisible()
    await expect(page.getByTestId("direct-ai-button-claude")).toBeEnabled()
    await expect(page.getByTestId("direct-ai-button-codex")).toBeEnabled()
    // The data-workflow-id reflects the resolved id from the registry.
    await expect(page.getByTestId("direct-ai-button-claude")).toHaveAttribute(
      "data-workflow-id",
      FAKE_CLAUDE_WF_ID,
    )
    await expect(page.getByTestId("direct-ai-button-codex")).toHaveAttribute(
      "data-workflow-id",
      FAKE_CODEX_WF_ID,
    )
  })

  test("clicking 'Run Claude' opens the prompt modal", async ({ page }) => {
    await stubWorkflowsList(page, FAKE_TEAM_ID)
    await gotoStubbedTeamDetail(page, FAKE_TEAM_ID)

    await page.getByTestId("direct-ai-button-claude").click()
    const dialog = page.getByTestId("direct-ai-prompt-dialog-claude")
    await expect(dialog).toBeVisible()
    await expect(
      page.getByTestId("direct-ai-prompt-input-claude"),
    ).toBeVisible()
    // Submit is disabled until a non-empty prompt is entered.
    await expect(
      page.getByTestId("direct-ai-prompt-submit-claude"),
    ).toBeDisabled()
  })

  test("submitting the prompt POSTs to /run and routes to /runs/<id>", async ({
    page,
  }) => {
    await stubWorkflowsList(page, FAKE_TEAM_ID)
    await stubRunDetail(page, FAKE_RUN_ID, FAKE_TEAM_ID, FAKE_CLAUDE_WF_ID)

    let postedBody: Record<string, unknown> | null = null
    await page.route(
      `**/api/v1/workflows/${FAKE_CLAUDE_WF_ID}/run`,
      async (route) => {
        if (route.request().method() !== "POST") {
          await route.fallback()
          return
        }
        postedBody = JSON.parse(route.request().postData() ?? "{}")
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            run_id: FAKE_RUN_ID,
            status: "pending",
          }),
        })
      },
    )

    await gotoStubbedTeamDetail(page, FAKE_TEAM_ID)

    await page.getByTestId("direct-ai-button-claude").click()
    const input = page.getByTestId("direct-ai-prompt-input-claude")
    await input.fill("List the files in this repo")
    await page.getByTestId("direct-ai-prompt-submit-claude").click()

    await page.waitForURL(`**/runs/${FAKE_RUN_ID}`)
    await expect(page.getByTestId("run-detail")).toBeVisible()

    expect(postedBody).toEqual({
      trigger_payload: { prompt: "List the files in this repo" },
    })
  })

  test("Codex button dispatches against the codex workflow id", async ({
    page,
  }) => {
    await stubWorkflowsList(page, FAKE_TEAM_ID)
    await stubRunDetail(page, FAKE_RUN_ID, FAKE_TEAM_ID, FAKE_CODEX_WF_ID)

    let postedUrl: string | null = null
    await page.route(
      `**/api/v1/workflows/${FAKE_CODEX_WF_ID}/run`,
      async (route) => {
        if (route.request().method() !== "POST") {
          await route.fallback()
          return
        }
        postedUrl = route.request().url()
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            run_id: FAKE_RUN_ID,
            status: "pending",
          }),
        })
      },
    )

    await gotoStubbedTeamDetail(page, FAKE_TEAM_ID)

    await page.getByTestId("direct-ai-button-codex").click()
    await page.getByTestId("direct-ai-prompt-input-codex").fill("hello")
    await page.getByTestId("direct-ai-prompt-submit-codex").click()

    await page.waitForURL(`**/runs/${FAKE_RUN_ID}`)
    expect(postedUrl).toContain(`/api/v1/workflows/${FAKE_CODEX_WF_ID}/run`)
  })

  test("dispatch failure surfaces the discriminator and keeps the dialog open", async ({
    page,
  }) => {
    await stubWorkflowsList(page, FAKE_TEAM_ID)

    await page.route(
      `**/api/v1/workflows/${FAKE_CLAUDE_WF_ID}/run`,
      async (route) => {
        if (route.request().method() !== "POST") {
          await route.fallback()
          return
        }
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ detail: { detail: "task_dispatch_failed" } }),
        })
      },
    )

    await gotoStubbedTeamDetail(page, FAKE_TEAM_ID)

    await page.getByTestId("direct-ai-button-claude").click()
    await page
      .getByTestId("direct-ai-prompt-input-claude")
      .fill("List the files")
    await page.getByTestId("direct-ai-prompt-submit-claude").click()

    // Toast surfaces the discriminator. The dialog stays open so the user
    // can retry or cancel.
    await expect(
      page.getByText(/task_dispatch_failed/i).first(),
    ).toBeVisible()
    await expect(
      page.getByTestId("direct-ai-prompt-dialog-claude"),
    ).toBeVisible()
    // URL did not navigate.
    expect(page.url()).not.toContain("/runs/")
  })
})
