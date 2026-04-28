import { expect, test } from "@playwright/test"
import { assertNoHorizontalScroll, assertTouchTargets } from "./utils/audit"

// M005-oaptsz S01 T04+T05: mobile-audit harness (slice contract gate for
// touch-target + horizontal-scroll regressions).
//
// This spec walks every existing authenticated route (plus /login) and asserts:
//   (a) no horizontal scroll within a 1px sub-pixel tolerance,
//   (b) every visible interactive element has a >=44x44 CSS-px hit target,
//   (c) a visual-diff baseline at 1% maxDiffPixelRatio (CONTEXT).
// T05 fixed the defects surfaced by T04's failing baseline; the per-route
// expected-fail annotations from T04 have been removed and the suite is now
// expected to pass on every project.

interface RouteCase {
  name: string
  path: string
  authenticated: boolean
}

const ROUTES: RouteCase[] = [
  { name: "login", path: "/login", authenticated: false },
  { name: "home", path: "/", authenticated: true },
  { name: "items", path: "/items", authenticated: true },
  { name: "teams", path: "/teams", authenticated: true },
  { name: "admin", path: "/admin", authenticated: true },
  { name: "admin-teams", path: "/admin/teams", authenticated: true },
  { name: "settings", path: "/settings", authenticated: true },
]

test.describe("M005-oaptsz mobile audit", () => {
  for (const route of ROUTES) {
    test.describe(route.name, () => {
      if (!route.authenticated) {
        test.use({ storageState: { cookies: [], origins: [] } })
      }

      test(`${route.name}: no horizontal scroll + touch targets >=44px`, async ({
        page,
      }) => {
        // Authenticated routes rely on the chromium/mobile-chrome storageState
        // (written by tests/auth.setup.ts) — re-running logInUser here would
        // hit /login while already authenticated and the route's beforeLoad
        // would redirect to /, leaving the email-input locator unresolvable.
        // Just navigate directly.
        await page.goto(route.path)

        // Wait for the route's main content to settle before measuring layout.
        await page.waitForLoadState("networkidle").catch(() => {
          /* networkidle can race with long-poll WS in dev; tolerate */
        })

        await assertNoHorizontalScroll(page)
        await assertTouchTargets(page)
      })

      test(`${route.name}: visual-diff baseline (1% tolerance)`, async ({
        page,
      }) => {
        await page.goto(route.path)

        await page.waitForLoadState("networkidle").catch(() => {})

        await expect(page).toHaveScreenshot(`${route.name}.png`, {
          maxDiffPixelRatio: 0.01,
          fullPage: true,
        })
      })
    })
  }
})
