import { expect, type Page } from "@playwright/test"

export async function signupViaUI(
  page: Page,
  fullName: string,
  email: string,
  password: string,
) {
  await page.goto("/signup")
  await page.getByTestId("full-name-input").fill(fullName)
  await page.getByTestId("email-input").fill(email)
  await page.getByTestId("password-input").fill(password)
  await page.getByTestId("confirm-password-input").fill(password)
  await page.getByRole("button", { name: "Sign Up" }).click()
  // Backend signup issues the session cookie and useAuth navigates to "/"
  // which redirects to /teams.
  await page.waitForURL("/teams")
}

export async function loginViaUI(page: Page, email: string, password: string) {
  await page.goto("/login")
  await page.getByTestId("email-input").fill(email)
  await page.getByTestId("password-input").fill(password)
  await page.getByRole("button", { name: "Log In" }).click()
  await page.waitForURL("/teams")
}

export async function createTeamFromUI(page: Page, name: string) {
  await page.getByTestId("create-team-button").first().click()
  await page.getByTestId("create-team-name-input").fill(name)
  await page.getByTestId("create-team-submit").click()
  // Wait for the dialog to close and the new team card to appear.
  await expect(
    page.getByTestId("team-card").filter({ hasText: name }),
  ).toBeVisible()
}

export function teamIdFromInviteUrl(url: string): string | null {
  // Invite urls have shape `${baseURL}/invite/<code>`. We don't expose teamId
  // directly there — caller can grab it from the URL after redirect via
  // page.url() / parseTeamIdFromUrl.
  const m = url.match(/\/invite\/([^/?#]+)/)
  return m ? m[1] : null
}

export function teamIdFromTeamUrl(url: string): string | null {
  const m = url.match(/\/teams\/([^/?#]+)/)
  return m ? m[1] : null
}
