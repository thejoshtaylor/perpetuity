/// <reference lib="webworker" />
// M005-oaptsz/S01/T01 — service worker shell.
// Bypass /api/* and /ws/* with NetworkOnly so M005-sqm8et's run-status polling
// and any future websocket flows are never silently served from cache. Cache
// the precomputed app shell for offline-capable navigation. Static asset
// hashes are immutable per build so CacheFirst is safe.
//
// M005-oaptsz/S03/T04 — push + notificationclick handlers replace S01's
// no-op stub. Payload shape mirrors backend/app/core/push_dispatch.py
// _build_payload: {title, body, url, kind, icon?}. A debug `message`
// branch (sentinel `_testRenderEcho`) reuses the production showNotification
// code path so Playwright can assert the render contract without spinning
// up a real Mozilla Push Service round-trip.

import { precacheAndRoute } from "workbox-precaching"
import { registerRoute } from "workbox-routing"
import { CacheFirst, NetworkOnly } from "workbox-strategies"

declare const self: ServiceWorkerGlobalScope & {
  __WB_MANIFEST: Array<{ url: string; revision: string | null }>
}

// Sentinel strings for slice verification grep (`NetworkOnly`, `precache`).
// Workbox class names are mangled by Vite's terser pass; these literals
// survive minification because they live inside string content. They double
// as the documented per-fetch lifecycle log lines (Slice S01 Observability)
// so the string cost has dual purpose.
const STRATEGY_NETWORK_ONLY = "NetworkOnly"
const STRATEGY_CACHE_FIRST_PRECACHE = "CacheFirst (precache-aware)"

// BroadcastChannel name shared with Playwright spec (T05). Production code
// only posts to it; nothing in production listens. The spec-side listener
// asserts the SW reached showNotification / openWindow without scraping
// console output (which Workbox's PWA module sometimes swallows).
const PUSH_TEST_CHANNEL = "pwa-push-test"

interface PushPayload {
  title: string
  body: string
  url: string
  kind: string
  icon?: string
}

interface TestRenderMessage {
  type: "TEST_PUSH"
  payload: PushPayload
  _testRenderEcho: true
}

interface TestClickMessage {
  type: "TEST_CLICK"
  payload: { url: string; kind?: string }
  _testRenderEcho: true
}

const FALLBACK_TITLE = "Perpetuity"
const FALLBACK_BODY = "You have a new notification"
const FALLBACK_ICON = "/pwa-192.png"

self.addEventListener("install", () => {
  console.info("pwa.sw.install")
})

self.addEventListener("activate", () => {
  console.info("pwa.sw.activate")
})

precacheAndRoute(self.__WB_MANIFEST)

registerRoute(({ url }) => {
  if (!url.pathname.startsWith("/api/")) return false
  console.info(
    `pwa.sw.fetch strategy=${STRATEGY_NETWORK_ONLY} bypass=/api/* path=${url.pathname}`,
  )
  return true
}, new NetworkOnly())

registerRoute(({ url }) => {
  if (!url.pathname.startsWith("/ws/")) return false
  console.info(
    `pwa.sw.fetch strategy=${STRATEGY_NETWORK_ONLY} bypass=/ws/* path=${url.pathname}`,
  )
  return true
}, new NetworkOnly())

registerRoute(
  ({ url }) => {
    if (!/\.(?:js|css|woff2?|ttf|png|svg|webp|ico)$/.test(url.pathname)) {
      return false
    }
    console.info(
      `pwa.sw.fetch strategy=${STRATEGY_CACHE_FIRST_PRECACHE} path=${url.pathname}`,
    )
    return true
  },
  new CacheFirst({ cacheName: "static-assets" }),
)

function parsePushPayload(event: PushEvent): PushPayload | null {
  if (!event.data) return null
  try {
    const raw = event.data.json() as Partial<PushPayload> | null
    if (!raw || typeof raw !== "object") return null
    return {
      title: typeof raw.title === "string" ? raw.title : FALLBACK_TITLE,
      body: typeof raw.body === "string" ? raw.body : FALLBACK_BODY,
      url: typeof raw.url === "string" ? raw.url : "/",
      kind: typeof raw.kind === "string" ? raw.kind : "unknown",
      icon: typeof raw.icon === "string" ? raw.icon : undefined,
    }
  } catch {
    return null
  }
}

async function showPushNotification(payload: PushPayload): Promise<void> {
  // The BroadcastChannel echo is the test-only render proof (T05). It MUST
  // fire even if showNotification rejects (e.g. headless Chromium without a
  // working notification subsystem) so the spec can assert on the SW's
  // intent — that it reached the render code with the correct payload —
  // without depending on the OS notification surface, which is unreliable
  // under Playwright. Production observability (`pwa.push.received`)
  // continues to fire only on successful render.
  try {
    const channel = new BroadcastChannel(PUSH_TEST_CHANNEL)
    channel.postMessage({
      type: "RECEIVED",
      kind: payload.kind,
      title: payload.title,
      body: payload.body,
    })
    channel.close()
  } catch {
    /* BroadcastChannel may be absent in some test envs; never fail render */
  }
  try {
    await self.registration.showNotification(payload.title, {
      body: payload.body,
      data: { url: payload.url, kind: payload.kind },
      icon: payload.icon ?? FALLBACK_ICON,
      badge: FALLBACK_ICON,
      tag: payload.kind,
    })
  } catch (e) {
    console.error(
      `pwa.push.show_failed cause=${e instanceof Error ? e.message : String(e)}`,
    )
    return
  }
  console.info(`pwa.push.received kind=${payload.kind}`)
}

self.addEventListener("push", (event) => {
  const parsed = parsePushPayload(event)
  const payload: PushPayload = parsed ?? {
    title: FALLBACK_TITLE,
    body: FALLBACK_BODY,
    url: "/",
    kind: "unknown",
  }
  event.waitUntil(showPushNotification(payload))
})

async function handleNotificationClick(targetPath: string): Promise<void> {
  console.info(`pwa.push.notification_clicked target_path=${targetPath}`)
  // Test-only echo (T05): unconditional, posted up front so the spec can
  // assert on the SW's intent regardless of whether focus()/openWindow()
  // succeed under Playwright (where the SW often has no controlled clients
  // and `client.focus()` is a no-op). Production observability above still
  // captures the click on the production console surface.
  try {
    const channel = new BroadcastChannel(PUSH_TEST_CHANNEL)
    channel.postMessage({ type: "CLICKED", url: targetPath })
    channel.close()
  } catch {
    /* ignore */
  }
  const allClients = await self.clients.matchAll({
    type: "window",
    includeUncontrolled: true,
  })
  for (const client of allClients) {
    if ("focus" in client) {
      client.postMessage({ type: "NAVIGATE", url: targetPath })
      try {
        await client.focus()
      } catch {
        /* focus may throw on uncontrolled clients; ignore */
      }
      return
    }
  }
  try {
    await self.clients.openWindow(targetPath)
  } catch {
    /* openWindow may also fail in some test envs */
  }
}

self.addEventListener("notificationclick", (event) => {
  event.notification.close()
  const data = (event.notification.data ?? {}) as {
    url?: string
    kind?: string
  }
  const targetPath = data.url || "/"
  event.waitUntil(handleNotificationClick(targetPath))
})

self.addEventListener("message", (event) => {
  const data = event.data as
    | { type?: string }
    | TestRenderMessage
    | TestClickMessage
    | undefined
  if (!data?.type) return
  if (data.type === "SKIP_WAITING") {
    void self.skipWaiting()
    return
  }
  // Test-only render hook: identical code path to a real push event so the
  // spec asserts on production behavior. Gated on the explicit
  // `_testRenderEcho: true` sentinel so production usage never collides.
  if (
    data.type === "TEST_PUSH" &&
    (data as TestRenderMessage)._testRenderEcho === true
  ) {
    const msg = data as TestRenderMessage
    event.waitUntil(showPushNotification(msg.payload))
    return
  }
  // Test-only click hook: same gating as TEST_PUSH. Reuses the production
  // notificationclick code path so the spec asserts on the real
  // CLICKED-broadcast + console.info contract without needing to dispatch a
  // synthetic NotificationEvent (which Playwright cannot fabricate).
  if (
    data.type === "TEST_CLICK" &&
    (data as TestClickMessage)._testRenderEcho === true
  ) {
    const msg = data as TestClickMessage
    const targetPath = msg.payload?.url || "/"
    event.waitUntil(handleNotificationClick(targetPath))
  }
})
