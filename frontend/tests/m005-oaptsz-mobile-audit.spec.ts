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
  // M005-oaptsz/S02/T04: NotificationBell ships in `_layout.tsx` for every
  // authenticated route. The button uses size='icon' which inherits
  // min-h-11/min-w-11 from button.tsx — verify the live boundingBox is
  // >=44x44 once at a representative authenticated route so a regression in
  // the bell's button-variant wiring is caught by all four projects.
  test("notification bell: visible and touch target >=44x44", async ({
    page,
  }) => {
    await page.goto("/teams")
    await page.waitForLoadState("networkidle").catch(() => {})
    const bell = page.getByTestId("notification-bell")
    await expect(bell).toBeVisible()
    const box = await bell.boundingBox()
    expect(box, "notification bell has no boundingBox").not.toBeNull()
    expect(box?.width ?? 0).toBeGreaterThanOrEqual(44)
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44)
  })

  // M005-oaptsz/S04/T03: voice-mic toggle is the universal-coverage signal
  // for D026. /login is the canonical unauthenticated representative — its
  // email field is auto-wrapped by the <Input> primitive and renders a mic
  // toggle. Verify (a) the mic is present, (b) it clears the >=44x44 mobile
  // touch-target floor, and (c) the page still has no horizontal scroll
  // with the mic visible (the mic adds layout pressure at the right edge).
  test("voice mic toggle: visible on /login email and touch target >=44x44", async ({
    page,
  }, testInfo) => {
    // No auth required — /login is unauthenticated.
    await testInfo.attach("note", {
      body: "voice-mic visibility + 44x44 floor on /login (M005-oaptsz/S04/T03)",
      contentType: "text/plain",
    })
    await page
      .context()
      .clearCookies()
      .catch(() => {})
    await page.goto("/login")
    await page.waitForLoadState("networkidle").catch(() => {})

    const mic = page.getByTestId("voice-input-toggle").first()
    await expect(
      mic,
      "login email must auto-wrap into a VoiceInput with a mic toggle",
    ).toBeVisible()
    const box = await mic.boundingBox()
    expect(box, "voice mic has no boundingBox").not.toBeNull()
    expect(box?.width ?? 0).toBeGreaterThanOrEqual(44)
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44)

    // Layout regression catch: the mic must not push the page wider than the
    // viewport on the mobile-chrome / iphone-13 projects.
    await assertNoHorizontalScroll(page)
  })

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
