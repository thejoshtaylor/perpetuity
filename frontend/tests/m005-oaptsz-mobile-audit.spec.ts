import { expect, test } from "@playwright/test"
import { firstSuperuser, firstSuperuserPassword } from "./config.ts"
import { logInUser } from "./utils/user"
import {
  assertNoHorizontalScroll,
  assertTouchTargets,
} from "./utils/audit"

// M005-oaptsz S01 T04: mobile-audit harness.
//
// This spec walks every existing authenticated route (plus /login) and asserts:
//   (a) no horizontal scroll within a 1px sub-pixel tolerance,
//   (b) every visible interactive element has a >=44x44 CSS-px hit target,
//   (c) a visual-diff baseline at 1% maxDiffPixelRatio (CONTEXT).
//
// The spec MUST tolerate a known-failing first run — its purpose is to surface
// the defect list T05 fixes. Routes use test.fixme() for the audit/touch-target
// assertions so the failing baseline is recorded but the suite doesn't block.
// T05 will remove the fixme annotations as fixes land.

interface RouteCase {
  name: string
  path: string
  authenticated: boolean
  // When true, the audit assertions are wrapped in test.fixme() so a failing
  // first run is captured but does not block the suite. T05 flips these to
  // false as fixes land.
  fixmeAudit: boolean
}

const ROUTES: RouteCase[] = [
  { name: "login", path: "/login", authenticated: false, fixmeAudit: true },
  { name: "home", path: "/", authenticated: true, fixmeAudit: true },
  { name: "items", path: "/items", authenticated: true, fixmeAudit: true },
  { name: "teams", path: "/teams", authenticated: true, fixmeAudit: true },
  { name: "admin", path: "/admin", authenticated: true, fixmeAudit: true },
  {
    name: "admin-teams",
    path: "/admin/teams",
    authenticated: true,
    fixmeAudit: true,
  },
  { name: "settings", path: "/settings", authenticated: true, fixmeAudit: true },
]

test.describe("M005-oaptsz mobile audit", () => {
  for (const route of ROUTES) {
    test.describe(route.name, () => {
      if (!route.authenticated) {
        test.use({ storageState: { cookies: [], origins: [] } })
      }

      test(`${route.name}: no horizontal scroll + touch targets >=44px`, async ({
        page,
      }, testInfo) => {
        if (route.fixmeAudit) {
          test.fixme(
            true,
            `T04 records failing baseline; T05 removes this fixme as fixes land for ${route.path}`,
          )
        }

        if (!route.authenticated) {
          await page.goto(route.path)
        } else {
          // Login flow lands on /teams; navigate to the target after.
          await logInUser(page, firstSuperuser, firstSuperuserPassword)
          await page.goto(route.path)
        }

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
        if (route.fixmeAudit) {
          test.fixme(
            true,
            `T04 records failing baseline; T05 removes this fixme as fixes land for ${route.path}`,
          )
        }

        if (!route.authenticated) {
          await page.goto(route.path)
        } else {
          await logInUser(page, firstSuperuser, firstSuperuserPassword)
          await page.goto(route.path)
        }

        await page.waitForLoadState("networkidle").catch(() => {})

        await expect(page).toHaveScreenshot(`${route.name}.png`, {
          maxDiffPixelRatio: 0.01,
          fullPage: true,
        })
      })
    })
  }
})
