// M005-sqm8et/S02/T05 — RunDetailPage polling spec.
//
// Drives the `/runs/$runId` route's polling shape through the
// pending → running → succeeded transition by stepping a mocked
// `GET /api/v1/workflow_runs/{run_id}` response. Also covers the
// failed-with-error_class path and the 404 'run not found' surface.
//
// Each step of the response shape is gated on the spec's own counter so
// the assertion asserts what the user sees, not just that a request fired.
//
// Run with: cd frontend && bunx playwright test --project=chromium \
//             tests/components/RunDetailPage.spec.ts

import { expect, type Page, test } from "@playwright/test"

const RUN_ID = "44444444-4444-4444-4444-444444444444"
const TEAM_ID = "55555555-5555-5555-5555-555555555555"
const WF_ID = "66666666-6666-6666-6666-666666666666"
const STEP_ID = "77777777-7777-7777-7777-777777777777"

type RunStatus = "pending" | "running" | "succeeded" | "failed" | "cancelled"
type StepStatus = "pending" | "running" | "succeeded" | "failed" | "skipped"

type RunFixture = {
  status: RunStatus
  error_class?: string | null
  started_at?: string | null
  finished_at?: string | null
  duration_ms?: number | null
  step: {
    status: StepStatus
    stdout?: string
    stderr?: string
    exit_code?: number | null
    error_class?: string | null
    duration_ms?: number | null
  }
}

function buildRunBody(fix: RunFixture) {
  return {
    id: RUN_ID,
    workflow_id: WF_ID,
    team_id: TEAM_ID,
    trigger_type: "button",
    triggered_by_user_id: null,
    target_user_id: null,
    trigger_payload: { prompt: "List the files in this repo" },
    status: fix.status,
    error_class: fix.error_class ?? null,
    started_at: fix.started_at ?? null,
    finished_at: fix.finished_at ?? null,
    duration_ms: fix.duration_ms ?? null,
    last_heartbeat_at: null,
    created_at: "2026-04-29T00:00:00Z",
    step_runs: [
      {
        id: STEP_ID,
        workflow_run_id: RUN_ID,
        step_index: 0,
        snapshot: { action: "claude", config: {} },
        status: fix.step.status,
        stdout: fix.step.stdout ?? "",
        stderr: fix.step.stderr ?? "",
        exit_code: fix.step.exit_code ?? null,
        error_class: fix.step.error_class ?? null,
        duration_ms: fix.step.duration_ms ?? null,
        started_at: null,
        finished_at: null,
        created_at: "2026-04-29T00:00:00Z",
      },
    ],
  }
}

/** Install a route handler that returns successive fixtures from `steps`,
 * advancing one entry per request and clamping at the last fixture. */
async function installSteppedRunRoute(
  page: Page,
  steps: RunFixture[],
): Promise<{ getCallCount: () => number }> {
  let callCount = 0
  await page.route(`**/api/v1/workflow_runs/${RUN_ID}`, async (route) => {
    if (route.request().method() !== "GET") {
      await route.fallback()
      return
    }
    const idx = Math.min(callCount, steps.length - 1)
    callCount += 1
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(buildRunBody(steps[idx])),
    })
  })
  return { getCallCount: () => callCount }
}

test.describe("RunDetailPage — polled status transitions", () => {
  test.beforeEach(({ page: _ }, testInfo) => {
    if (testInfo.project.name === "mobile-chrome-no-auth") {
      testInfo.skip()
    }
  })

  test("renders pending → running → succeeded with stdout details", async ({
    page,
  }) => {
    const tracker = await installSteppedRunRoute(page, [
      // First poll: pending.
      { status: "pending", step: { status: "pending" } },
      // Second poll: running, started_at populated.
      {
        status: "running",
        started_at: "2026-04-29T00:00:01Z",
        step: { status: "running" },
      },
      // Third poll onward: succeeded with full output and duration.
      {
        status: "succeeded",
        started_at: "2026-04-29T00:00:01Z",
        finished_at: "2026-04-29T00:00:03Z",
        duration_ms: 2000,
        step: {
          status: "succeeded",
          stdout: "README.md\npackage.json",
          exit_code: 0,
          duration_ms: 1800,
        },
      },
    ])

    await page.goto(`/runs/${RUN_ID}`)

    // First render shows pending.
    const detail = page.getByTestId("run-detail")
    await expect(detail).toBeVisible()
    await expect(detail).toHaveAttribute("data-run-status", "pending")
    await expect(page.getByTestId("run-detail-status")).toHaveText("pending")
    // While in flight, the polling indicator is visible.
    await expect(page.getByTestId("run-detail-polling")).toBeVisible()

    // Polling cadence is 1.5s; allow some headroom for the next mock to land.
    await expect(detail).toHaveAttribute("data-run-status", "running", {
      timeout: 8000,
    })
    await expect(page.getByTestId("step-run-status-0")).toHaveAttribute(
      "data-status",
      "running",
    )
    await expect(page.getByTestId("step-run-spinner-0")).toBeVisible()

    // Eventually the run finishes — polling stops, status pill flips.
    await expect(detail).toHaveAttribute("data-run-status", "succeeded", {
      timeout: 12000,
    })
    await expect(page.getByTestId("run-detail-polling")).toHaveCount(0)

    // Stdout block renders the captured output. <details> is closed by
    // default so we toContainText (works on hidden text) rather than
    // toBeVisible — the run page intentionally collapses output to keep
    // the layout calm; test that the data is in the DOM, not its
    // disclosure state.
    await expect(page.getByTestId("step-run-stdout-0")).toContainText(
      "README.md",
    )
    await expect(page.getByTestId("step-run-exit-0")).toHaveText(/exit\s+0/)
    await expect(page.getByTestId("run-detail-duration")).not.toHaveText("—")

    expect(tracker.getCallCount()).toBeGreaterThanOrEqual(3)
  })

  test("renders failed run with error_class prominently", async ({ page }) => {
    await installSteppedRunRoute(page, [
      {
        status: "failed",
        started_at: "2026-04-29T00:00:01Z",
        finished_at: "2026-04-29T00:00:02Z",
        duration_ms: 1000,
        error_class: "missing_team_secret",
        step: {
          status: "failed",
          error_class: "missing_team_secret",
          stderr: "ANTHROPIC_API_KEY is not set",
          duration_ms: 800,
        },
      },
    ])

    await page.goto(`/runs/${RUN_ID}`)

    const detail = page.getByTestId("run-detail")
    await expect(detail).toBeVisible()
    await expect(detail).toHaveAttribute("data-run-status", "failed")
    // Run-level error_class badge.
    const runError = page.getByTestId("run-detail-error-class")
    await expect(runError).toBeVisible()
    await expect(runError).toHaveAttribute(
      "data-error-class",
      "missing_team_secret",
    )
    // Step-level error_class is rendered with the alert icon.
    const stepError = page.getByTestId("step-run-error-class-0")
    await expect(stepError).toBeVisible()
    await expect(stepError).toContainText("missing_team_secret")
    // Polling indicator gone (terminal state).
    await expect(page.getByTestId("run-detail-polling")).toHaveCount(0)
  })

  test("empty stdout shows the muted 'no output' note", async ({ page }) => {
    await installSteppedRunRoute(page, [
      {
        status: "succeeded",
        started_at: "2026-04-29T00:00:01Z",
        finished_at: "2026-04-29T00:00:02Z",
        duration_ms: 1000,
        step: {
          status: "succeeded",
          stdout: "",
          exit_code: 0,
          duration_ms: 800,
        },
      },
    ])

    await page.goto(`/runs/${RUN_ID}`)
    await expect(page.getByTestId("run-detail")).toBeVisible()
    // Expand the collapsed-by-default <details> so the inner <pre>
    // becomes user-visible. Click the summary by parent locator.
    const details = page.getByTestId("step-run-stdout-details-0")
    await expect(details).toBeAttached()
    await details.locator("summary").click()
    const stdout = page.getByTestId("step-run-stdout-0")
    await expect(stdout).toBeVisible()
    await expect(stdout).toContainText("no output")
  })

  test("404 from the API renders the 'Run not found' card", async ({
    page,
  }) => {
    await page.route(`**/api/v1/workflow_runs/${RUN_ID}`, async (route) => {
      if (route.request().method() !== "GET") {
        await route.fallback()
        return
      }
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({
          detail: { detail: "workflow_run_not_found" },
        }),
      })
    })

    await page.goto(`/runs/${RUN_ID}`)

    await expect(page.getByTestId("run-detail-error")).toBeVisible()
    await expect(page.getByText(/Run not found/i).first()).toBeVisible()
  })
})
