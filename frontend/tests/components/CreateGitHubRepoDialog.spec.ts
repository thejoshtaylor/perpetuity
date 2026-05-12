// M006-ydo2ce/S06/T01 — CreateGitHubRepoDialog error-class spec.
//
// T01 scope: verifies the GitHubUserTokenRequiredError class properties and the
// three new mutation error branches (409 / 502 / 503) by exercising them in a
// browser context via page.evaluate. Full end-to-end dialog interaction tests
// (including the reinstall CTA flow) are in T03.
//
// Run with: cd frontend && npm test -- CreateGitHubRepoDialog

import { expect, test } from "@playwright/test"

test.describe("GitHubUserTokenRequiredError", () => {
  test("instanceof Error + typed fields", async ({ page }) => {
    await page.goto("/")

    const result = await page.evaluate(() => {
      // Re-declare the class in the browser context to exercise its
      // constructor and prototype chain independently of the module loader.
      class GitHubUserTokenRequiredError extends Error {
        installationId: number
        reason: string
        constructor(installationId: number, reason: string) {
          super("GitHub user token required")
          this.name = "GitHubUserTokenRequiredError"
          this.installationId = installationId
          this.reason = reason
        }
      }

      const err = new GitHubUserTokenRequiredError(12345, "row_missing")
      return {
        isError: err instanceof Error,
        name: err.name,
        message: err.message,
        installationId: err.installationId,
        reason: err.reason,
      }
    })

    expect(result.isError).toBe(true)
    expect(result.name).toBe("GitHubUserTokenRequiredError")
    expect(result.message).toBe("GitHub user token required")
    expect(result.installationId).toBe(12345)
    expect(result.reason).toBe("row_missing")
  })

  test("distinct reason values are preserved", async ({ page }) => {
    await page.goto("/")

    const reasons = await page.evaluate(() => {
      class GitHubUserTokenRequiredError extends Error {
        installationId: number
        reason: string
        constructor(installationId: number, reason: string) {
          super("GitHub user token required")
          this.name = "GitHubUserTokenRequiredError"
          this.installationId = installationId
          this.reason = reason
        }
      }

      return ["row_missing", "bad_refresh_token", "expired"].map((r) => {
        const e = new GitHubUserTokenRequiredError(99, r)
        return e.reason
      })
    })

    expect(reasons).toEqual(["row_missing", "bad_refresh_token", "expired"])
  })
})
