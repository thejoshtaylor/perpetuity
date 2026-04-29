// M005-sqm8et/S01/T04 — TeamSecretsPanel paste-once UI spec.
//
// Exercises the team-admin and team-member surfaces of the AI Credentials
// panel against the live backend stack. The auth setup project signs in as
// the seeded superuser (who is admin on every team they create), so the
// admin-side flow runs against real PUT/GET/DELETE endpoints from T03.
//
// The non-admin surface is verified by stubbing `GET /api/v1/teams/` to
// flip the caller's role on a real team to `member` — this avoids a second
// signup + invite handshake (which the teams.spec.ts suite already covers
// at depth) and keeps this spec scoped to the panel's role-aware UI.
//
// Run with: cd frontend && npm test -- TeamSecretsPanel

import { expect, type Page, test } from "@playwright/test"
import { randomTeamName } from "../utils/random"
import { createTeamFromUI } from "../utils/teams"

const CLAUDE_KEY = "claude_api_key"
const OPENAI_KEY = "openai_api_key"
// Length-≥40 + sk-ant- prefix → passes T02's validator.
const VALID_CLAUDE_VALUE = `sk-ant-${"a".repeat(40)}`
// The frontend client points at the backend via VITE_API_URL; the helpers
// below use the same URL so request.get/.delete hit the API directly rather
// than the Vite dev server (which serves index.html for unknown paths).
const API_BASE = process.env.VITE_API_URL ?? "http://localhost:8000"

async function gotoTeamDetail(page: Page, teamName: string): Promise<string> {
  await page.goto("/teams")
  await createTeamFromUI(page, teamName)
  await page.getByTestId("team-card").filter({ hasText: teamName }).click()
  await page.waitForURL(/\/teams\/[^/]+$/)
  await expect(page.getByTestId("team-detail")).toBeVisible()
  const url = page.url()
  const match = url.match(/\/teams\/([^/?#]+)/)
  if (!match) throw new Error(`Could not parse teamId from ${url}`)
  return match[1]
}

async function ensureSecretCleared(page: Page, teamId: string, key: string) {
  // Best-effort cleanup so reruns against a long-lived dev stack stay
  // deterministic. The DELETE endpoint is idempotent (404 on missing).
  await page.request
    .delete(`${API_BASE}/api/v1/teams/${teamId}/secrets/${key}`)
    .catch(() => undefined)
}

test.describe("TeamSecretsPanel — admin surface", () => {
  test.beforeEach(({ page: _ }, testInfo) => {
    if (testInfo.project.name === "mobile-chrome-no-auth") {
      testInfo.skip()
    }
  })

  test("renders both registered keys with not-set badges and admin buttons", async ({
    page,
  }) => {
    const name = randomTeamName()
    await gotoTeamDetail(page, name)

    const panel = page.getByTestId("team-secrets-panel")
    await expect(panel).toBeVisible()

    const claudeRow = page.getByTestId(`team-secret-row-${CLAUDE_KEY}`)
    const openaiRow = page.getByTestId(`team-secret-row-${OPENAI_KEY}`)
    await expect(claudeRow).toBeVisible()
    await expect(openaiRow).toBeVisible()
    await expect(claudeRow).toHaveAttribute("data-has-value", "false")
    await expect(openaiRow).toHaveAttribute("data-has-value", "false")

    // Admin sees Set buttons on both rows; no Delete button when not set.
    await expect(
      page.getByTestId(`team-secret-set-button-${CLAUDE_KEY}`),
    ).toBeVisible()
    await expect(
      page.getByTestId(`team-secret-set-button-${OPENAI_KEY}`),
    ).toBeVisible()
    await expect(
      page.getByTestId(`team-secret-delete-button-${CLAUDE_KEY}`),
    ).toHaveCount(0)
  })

  test("paste-once modal submits, closes, refreshes list, then DELETE clears", async ({
    page,
  }) => {
    const name = randomTeamName()
    const teamId = await gotoTeamDetail(page, name)

    try {
      // 1) Open paste-once dialog and submit a valid Claude key.
      await page.getByTestId(`team-secret-set-button-${CLAUDE_KEY}`).click()
      const dialog = page.getByTestId(`team-secret-paste-dialog-${CLAUDE_KEY}`)
      await expect(dialog).toBeVisible()

      const input = page.getByTestId(`team-secret-paste-input-${CLAUDE_KEY}`)
      await expect(input).toHaveAttribute("type", "password")
      await input.fill(VALID_CLAUDE_VALUE)

      await page.getByTestId(`team-secret-paste-submit-${CLAUDE_KEY}`).click()

      // 2) Modal closes, panel re-renders with has_value=true.
      await expect(dialog).toBeHidden()
      const claudeRow = page.getByTestId(`team-secret-row-${CLAUDE_KEY}`)
      await expect(claudeRow).toHaveAttribute("data-has-value", "true")
      // Replace label flips on the button.
      await expect(
        page.getByTestId(`team-secret-set-button-${CLAUDE_KEY}`),
      ).toHaveText("Replace")
      // Delete button now visible.
      const deleteBtn = page.getByTestId(
        `team-secret-delete-button-${CLAUDE_KEY}`,
      )
      await expect(deleteBtn).toBeVisible()

      // 3) Click Delete → confirm → row flips back to not-set.
      await deleteBtn.click()
      const confirmDialog = page.getByTestId(
        `team-secret-delete-dialog-${CLAUDE_KEY}`,
      )
      await expect(confirmDialog).toBeVisible()
      await page.getByTestId(`team-secret-delete-confirm-${CLAUDE_KEY}`).click()
      await expect(confirmDialog).toBeHidden()
      await expect(claudeRow).toHaveAttribute("data-has-value", "false")
    } finally {
      await ensureSecretCleared(page, teamId, CLAUDE_KEY)
    }
  })

  test("invalid value surfaces validator error in the dialog", async ({
    page,
  }) => {
    const name = randomTeamName()
    await gotoTeamDetail(page, name)

    await page.getByTestId(`team-secret-set-button-${CLAUDE_KEY}`).click()
    const input = page.getByTestId(`team-secret-paste-input-${CLAUDE_KEY}`)
    // Bad prefix — fails the `sk-ant-` validator with 400 invalid_value_shape.
    await input.fill(`wrong-prefix-${"x".repeat(40)}`)
    await page.getByTestId(`team-secret-paste-submit-${CLAUDE_KEY}`).click()

    // Toast surfaces the discriminator.
    await expect(page.getByText(/invalid_value_shape/i).first()).toBeVisible()
    // Dialog stays open so the operator can correct the value.
    await expect(
      page.getByTestId(`team-secret-paste-dialog-${CLAUDE_KEY}`),
    ).toBeVisible()
  })
})

test.describe("TeamSecretsPanel — non-admin surface", () => {
  test.beforeEach(({ page: _ }, testInfo) => {
    if (testInfo.project.name === "mobile-chrome-no-auth") {
      testInfo.skip()
    }
  })

  test("read-only badges with no Replace/Delete buttons when caller is member", async ({
    page,
  }) => {
    // Create a real team so the team-detail route resolves, then stub the
    // /teams envelope to return the same team with role=member. The
    // GET /api/v1/teams/{id}/secrets endpoint runs the team-MEMBER gate, so
    // the seeded superuser still passes it — only the FE's role-aware UI
    // toggles. This isolates the panel's role behavior without a second
    // signup/invite dance.
    const name = randomTeamName()
    const teamId = await gotoTeamDetail(page, name)

    // Capture the real envelope shape, then re-serve it with role=member.
    const realResp = await page.request.get(`${API_BASE}/api/v1/teams/`)
    const realEnvelope = (await realResp.json()) as {
      data: Array<Record<string, unknown>>
      count: number
    }
    const flipped = {
      ...realEnvelope,
      data: realEnvelope.data.map((t) =>
        t.id === teamId ? { ...t, role: "member" } : t,
      ),
    }

    await page.route("**/api/v1/teams/", async (route) => {
      if (route.request().method() !== "GET") {
        await route.fallback()
        return
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(flipped),
      })
    })

    await page.reload()
    await expect(page.getByTestId("team-detail")).toBeVisible()

    // Role badge flipped, panel still rendered.
    await expect(page.getByTestId("role-badge")).toHaveAttribute(
      "data-role",
      "member",
    )
    await expect(page.getByTestId("team-secrets-panel")).toBeVisible()

    // Both rows render with read-only badges.
    await expect(
      page.getByTestId(`team-secret-row-${CLAUDE_KEY}`),
    ).toBeVisible()
    await expect(
      page.getByTestId(`team-secret-row-${OPENAI_KEY}`),
    ).toBeVisible()

    // Admin-only buttons are absent for both keys.
    await expect(
      page.getByTestId(`team-secret-set-button-${CLAUDE_KEY}`),
    ).toHaveCount(0)
    await expect(
      page.getByTestId(`team-secret-set-button-${OPENAI_KEY}`),
    ).toHaveCount(0)
    await expect(
      page.getByTestId(`team-secret-delete-button-${CLAUDE_KEY}`),
    ).toHaveCount(0)
  })
})
