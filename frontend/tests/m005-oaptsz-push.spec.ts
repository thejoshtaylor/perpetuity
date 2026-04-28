import { expect, test } from "@playwright/test"

// M005-oaptsz/S03/T05 — slice contract gate for the Web Push channel.
//
// This spec is the closing gate of the slice. It exercises the end-to-end
// shape that the prior tasks built up:
//
//   T01 — VAPID keypair + admin generate endpoint + public-key route.
//   T02 — pywebpush dispatcher with VAPID-signed fan-out + 410/5xx prune.
//   T03 — POST/DELETE/GET /push/subscribe + /push/subscriptions routes,
//          typed SDK regen, Push preference toggle.
//   T04 — service-worker push + notificationclick + TEST_PUSH/TEST_CLICK
//          debug branches, PushPermissionPrompt UI.
//
// The spec runs in its own dedicated `m005-oaptsz-push` Playwright project
// (preview build :4173 + serviceWorkers:'allow' + permissions:['notifications']
// + the seeded superuser's storageState). Three scenarios run sequentially in
// one test() to avoid SW re-registration churn:
//
//   A) Subscribe round-trip: PushPermissionPrompt → permission granted →
//      pushManager.subscribe → POST /push/subscribe → row visible via
//      GET /push/subscriptions.
//   B) Push render via TEST_PUSH debug message: SW reuses production
//      showPushNotification code path; spec listens on
//      BroadcastChannel('pwa-push-test') for {type:'RECEIVED'}.
//   C) Notificationclick navigation via TEST_CLICK debug message: SW reuses
//      production notificationclick code path; spec listens for
//      {type:'CLICKED'} on the same channel.
//
// Why the BroadcastChannel sentinel and not console.info-scraping (MEM370):
// Workbox's PWA module sometimes swallows SW console output, so the spec
// asserts on the broadcast-channel contract that production code already
// posts to. The TEST_PUSH / TEST_CLICK message branches are gated on an
// explicit `_testRenderEcho:true` sentinel so production code paths never
// trigger them.

test.describe("M005-oaptsz S03 push slice contract", () => {
  test.use({ serviceWorkers: "allow" })

  test.beforeAll(async ({ browser }) => {
    // VAPID keys must exist before any browser tries to subscribe — the
    // public route returns 503 otherwise. Generate them via the admin route
    // from a throwaway context that inherits the seeded superuser's cookie.
    // Idempotent: the endpoint overwrites existing keys.
    //
    // We use Playwright's APIRequestContext (ctx.request) rather than
    // page.evaluate(fetch) because:
    //   * The preview build at :4173 ships only bundled output, so the typed
    //     SDK's `/src/client/sdk.gen.ts` import path is unavailable.
    //   * Cross-origin fetch from a SW-controlled page can fail in subtle
    //     ways (Workbox routes match by url.pathname regardless of origin);
    //     APIRequestContext bypasses the SW entirely while still carrying
    //     the storageState's `perpetuity_session` cookie.
    const ctx = await browser.newContext({
      storageState: "playwright/.auth/user.json",
      baseURL: "http://localhost:8000",
    })
    try {
      const resp = await ctx.request.post(
        "/api/v1/admin/settings/vapid_keys/generate",
      )
      expect(
        resp.status(),
        `vapid_keys/generate must succeed: ${await resp.text()}`,
      ).toBe(200)
      const body = (await resp.json()) as { public_key: string }
      expect(body.public_key.length).toBeGreaterThan(0)
    } finally {
      await ctx.close()
    }
  })

  test("subscribe → push render → notificationclick navigation", async ({
    page,
  }) => {
    // Stream SW console output back through the page log so a failed
    // showNotification (e.g. "No notification permission has been granted
    // for this origin") doesn't masquerade as a TEST_PUSH timeout — it'll
    // surface as a `pwa.push.show_failed cause=...` error in the test's
    // captured stderr.
    const swLogs: string[] = []
    page.context().on("serviceworker", (sw) => {
      sw.on("console", (msg) => {
        swLogs.push(`sw:${msg.type()}: ${msg.text()}`)
      })
    })

    // The Playwright project pre-grants ['notifications'], but the SW's
    // showNotification call (Scenario B) checks permission against the
    // origin and rejects with "No notification permission has been granted
    // for this origin" unless the grant is also scoped to that origin. Add
    // it explicitly here.
    await page.context().grantPermissions(["notifications"], {
      origin: "http://localhost:4173",
    })

    // -----------------------------------------------------------------------
    // Step 0 — wait for the SW to register and become active. We do NOT wait
    // for `controller !== null` because Workbox's `registerType:'prompt'`
    // intentionally avoids `clients.claim()` (so a new SW doesn't take over
    // until the user accepts the update banner). The SW is still reachable
    // for postMessage via `registration.active`, which is what Scenarios
    // B/C use.
    // -----------------------------------------------------------------------
    await page.goto("/")
    await page.waitForFunction(async () => {
      const reg = await navigator.serviceWorker.getRegistration()
      return Boolean(reg?.active)
    })

    // -----------------------------------------------------------------------
    // Scenario A — subscribe round-trip.
    //
    // The slice contract for the subscribe leg is "POST /push/subscribe
    // persists the device, GET /push/subscriptions surfaces it". The actual
    // `pushManager.subscribe()` browser call needs a real push service (or a
    // Chromium internal push test endpoint) which is unavailable to a
    // headless Chromium under Playwright — `pushManager.subscribe` returns
    // `AbortError: Registration failed - permission denied`. This is the
    // expected failure mode that the slice plan calls out in the boundary:
    //
    //   "Real-device round-trip explicitly NOT required — that's S05
    //    acceptance scenario 2."
    //
    // Per S03's plan + T02's contract, the upstream-facing leg is verified
    // against an respx-mocked Mozilla Push Service in the backend
    // integration test (test_multi_device_410_prune_end_to_end). What this
    // spec proves is the FE→BE half: a synthetic subscription body POSTs to
    // /push/subscribe (idempotent insert), and GET /push/subscriptions
    // surfaces the row with the hash-only projection. The shape of
    // `subscriptionBody` is identical to what `pushManager.subscribe(...)
    // .toJSON()` would have produced.
    //
    // Backend HTTP calls go through ctx.request (Playwright's
    // APIRequestContext) which bypasses the SW + sidesteps cross-origin
    // fetch fragility from a SW-controlled page. Both the page and the
    // request share the storageState's `perpetuity_session` cookie because
    // the cookie's domain is `localhost` (matches both :4173 and :8000).
    // -----------------------------------------------------------------------

    await page.waitForLoadState("networkidle").catch(() => {})
    const apiCtx = page.context()

    // Public-key endpoint is part of the slice contract and must respond
    // before any browser would even attempt to subscribe.
    const vapidResp = await apiCtx.request.get(
      "http://localhost:8000/api/v1/push/vapid_public_key",
    )
    expect(vapidResp.status()).toBe(200)
    const { public_key: vapidPublic } = (await vapidResp.json()) as {
      public_key: string
    }
    expect(vapidPublic.length).toBeGreaterThan(0)

    // Synthetic subscription body — same wire shape as a real
    // PushSubscription.toJSON() would emit. Endpoint is the unique-per-row
    // upsert key on the backend, so the test_id suffix makes this safe to
    // re-run on the same DB.
    const subscriptionBody = {
      endpoint: `https://mock-push-fe.invalid/test-${Date.now()}-${Math.random()
        .toString(36)
        .slice(2, 8)}`,
      keys: {
        p256dh:
          "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM",
        auth: "tBHItJI5svbpez7KI4CCXg",
      },
    }

    const subResp = await apiCtx.request.post(
      "http://localhost:8000/api/v1/push/subscribe",
      { data: subscriptionBody },
    )
    expect(
      [200, 201].includes(subResp.status()),
      `subscribe must succeed: ${subResp.status()} ${await subResp.text()}`,
    ).toBeTruthy()

    // Slice contract: GET /push/subscriptions must show ≥1 row for the
    // seeded superuser, hash-only. We assert the seeded subscription's
    // endpoint hash specifically so this test stays deterministic even when
    // the user has accumulated other rows from prior runs.
    const listResp = await apiCtx.request.get(
      "http://localhost:8000/api/v1/push/subscriptions",
    )
    expect(listResp.status()).toBe(200)
    const subscriptions = (await listResp.json()) as {
      data: { id: string; endpoint_hash: string }[]
      count: number
    }
    expect(subscriptions.count).toBeGreaterThanOrEqual(1)
    for (const row of subscriptions.data) {
      expect(row.endpoint_hash).toMatch(/^[0-9a-f]{8}$/)
    }
    // Compute the expected sha256[:8] of the seeded endpoint and assert it
    // appears in the list.
    const expectedHash = await page.evaluate(async (endpoint) => {
      const bytes = new TextEncoder().encode(endpoint)
      const digest = await crypto.subtle.digest("SHA-256", bytes)
      const hex = Array.from(new Uint8Array(digest))
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("")
      return hex.slice(0, 8)
    }, subscriptionBody.endpoint)
    expect(
      subscriptions.data.map((r) => r.endpoint_hash),
      "seeded subscription must surface in /push/subscriptions",
    ).toContain(expectedHash)

    // -----------------------------------------------------------------------
    // Scenario B — push render via TEST_PUSH debug message.
    //
    // The spec subscribes to BroadcastChannel('pwa-push-test') in the page
    // context, posts a TEST_PUSH message into the SW, and waits for the
    // SW's RECEIVED echo. This proves showPushNotification() ran with the
    // payload's title/body (the SW posts {type:'RECEIVED', kind} after
    // showNotification resolves).
    // -----------------------------------------------------------------------

    let renderResult: {
      type: string
      kind?: string
      title?: string
      body?: string
    }
    try {
      renderResult = await page.evaluate(async () => {
        const channel = new BroadcastChannel("pwa-push-test")
        const got = new Promise<{
          type: string
          kind?: string
          title?: string
          body?: string
        }>((resolve) => {
          channel.onmessage = (ev) => {
            if (ev.data?.type === "RECEIVED") resolve(ev.data)
          }
        })
        // Workbox's `registerType:'prompt'` means the SW does NOT call
        // clients.claim(), so navigator.serviceWorker.controller stays null
        // on the first navigation. Reach the SW via registration.active
        // instead — same SW, just not promoted to controller-of-this-page.
        const reg = await navigator.serviceWorker.getRegistration()
        const target = navigator.serviceWorker.controller ?? reg?.active ?? null
        if (!target) throw new Error("no active SW to postMessage to")
        target.postMessage({
          type: "TEST_PUSH",
          _testRenderEcho: true,
          payload: {
            title: "Test",
            body: "Body",
            url: "/items",
            kind: "system",
          },
        })
        const out = await Promise.race([
          got,
          new Promise<{ type: string }>((_r, rej) =>
            setTimeout(() => rej(new Error("TEST_PUSH render timeout")), 8_000),
          ),
        ])
        channel.close()
        return out
      })
    } catch (err) {
      throw new Error(
        `Scenario B failed: ${err instanceof Error ? err.message : String(err)}\n` +
          `SW logs:\n${swLogs.join("\n")}`,
      )
    }
    expect(renderResult.type).toBe("RECEIVED")
    expect(renderResult.kind).toBe("system")
    // Slice contract: SW reached showPushNotification with the right args.
    expect(renderResult.title).toBe("Test")
    expect(renderResult.body).toBe("Body")

    // -----------------------------------------------------------------------
    // Scenario C — notificationclick navigation via TEST_CLICK.
    //
    // Same broadcast-channel pattern: post TEST_CLICK with a payload.url and
    // wait for the CLICKED echo carrying that URL. Proves the SW's
    // handleNotificationClick code path posts NAVIGATE to clients +
    // CLICKED to the broadcast channel.
    // -----------------------------------------------------------------------

    const clickResult = await page.evaluate(async () => {
      const channel = new BroadcastChannel("pwa-push-test")
      const got = new Promise<{ type: string; url?: string }>((resolve) => {
        channel.onmessage = (ev) => {
          if (ev.data?.type === "CLICKED") resolve(ev.data)
        }
      })
      const reg = await navigator.serviceWorker.getRegistration()
      const target = navigator.serviceWorker.controller ?? reg?.active ?? null
      if (!target) throw new Error("no active SW to postMessage to")
      target.postMessage({
        type: "TEST_CLICK",
        _testRenderEcho: true,
        payload: { url: "/items" },
      })
      const out = await Promise.race([
        got,
        new Promise<{ type: string }>((_r, rej) =>
          setTimeout(() => rej(new Error("TEST_CLICK echo timeout")), 5_000),
        ),
      ])
      channel.close()
      return out
    })
    expect(clickResult.type).toBe("CLICKED")
    expect(clickResult.url).toBe("/items")
  })
})
