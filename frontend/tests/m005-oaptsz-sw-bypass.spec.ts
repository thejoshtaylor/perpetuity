import { expect, test } from "@playwright/test"

// M005-oaptsz/S01/T05 — Slice contract gate: prove that the Workbox-injected
// service worker registers /api/* as NetworkOnly and therefore never serves
// stale cached responses for backend calls.
//
// The check is structural: with NetworkOnly registered, two consecutive
// fetches against the same /api/* URL must observe two different bodies when
// the underlying response is mutated between them. A CacheFirst (or
// StaleWhileRevalidate-with-bad-config) SW would return the *first* body on
// the second fetch — that's the silent-cache regression M005-sqm8et's run-
// status polling cannot tolerate.
//
// Why a dedicated project (m005-oaptsz-sw):
//   * SW only registers under the production build (vite-plugin-pwa
//     devOptions.enabled=false), so the project's webServer is `bun run
//     build && bun run preview --port 4173`, not the dev server.
//   * No auth setup — the health-check endpoint is in _PUBLIC_PATHS.
//   * Spec runs in isolation: `bunx playwright test --project=m005-oaptsz-sw`.
//
// Slice CONTEXT requirement (verbatim from S01-PLAN):
//   "an integration test that flips a fixture API response between two
//    SW-mediated fetches and observes the new value".

test.describe("M005-oaptsz SW bypass for /api/*", () => {
  // Each test gets a fresh context so SW registration starts from zero —
  // otherwise an installed SW from a prior test could short-circuit the
  // `controller === null` wait.
  test.use({ serviceWorkers: "allow" })

  test("/api/* is NetworkOnly: second fetch sees the second fixture body", async ({
    page,
    context,
  }) => {
    // Step 1 — install the route mock at the BROWSER CONTEXT level so it
    // intercepts SW-initiated fetches as well as page-initiated ones. With
    // serviceWorkers:'allow' set, context.route fires for both. We start
    // with body 'first'.
    let currentBody = "first"
    await context.route("**/api/v1/utils/health-check/", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "text/plain",
        body: currentBody,
      })
    })

    // Step 2 — load the app and wait for the SW to take control. The
    // production build registers the SW manually from main.tsx via
    // virtual:pwa-register, so navigator.serviceWorker.controller is the
    // canonical signal that the SW is intercepting future fetches.
    //
    // First navigation may not yet have a controller — Workbox's
    // clientsClaim is implicit via its install/activate lifecycle. Reload
    // once after the registration promise resolves so the page is under
    // SW control.
    await page.goto("/")
    await page.waitForFunction(async () => {
      // Wait until the registration promise resolves AND the controller
      // is non-null (i.e. the page is *served by* the SW, not just aware
      // of it).
      const reg = await navigator.serviceWorker.getRegistration()
      return Boolean(reg) && navigator.serviceWorker.controller !== null
    })

    // Step 3 — first fetch via the SW-controlled page. Because we're
    // observing a deterministic fixture body, the routing decision (cache
    // vs network) is testable without touching the real backend.
    const first = await page.evaluate(async () => {
      const r = await fetch("/api/v1/utils/health-check/")
      return r.text()
    })
    expect(first).toBe("first")

    // Step 4 — flip the fixture to a new body and re-fetch. A NetworkOnly
    // SW will hit the route mock again and return 'second'. A CacheFirst
    // SW would have populated its cache from the first fetch and would
    // return 'first' on this second call — the assertion would fail and
    // surface the regression.
    currentBody = "second"
    const second = await page.evaluate(async () => {
      const r = await fetch("/api/v1/utils/health-check/")
      return r.text()
    })
    expect(
      second,
      "SW must re-fetch /api/* on every call (NetworkOnly contract); a CacheFirst-like SW would return 'first' here",
    ).toBe("second")
  })
})
