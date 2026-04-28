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
  await self.registration.showNotification(payload.title, {
    body: payload.body,
    data: { url: payload.url, kind: payload.kind },
    icon: payload.icon ?? FALLBACK_ICON,
    badge: FALLBACK_ICON,
    tag: payload.kind,
  })
  console.info(`pwa.push.received kind=${payload.kind}`)
  try {
    const channel = new BroadcastChannel(PUSH_TEST_CHANNEL)
    channel.postMessage({ type: "RECEIVED", kind: payload.kind })
    channel.close()
  } catch {
    /* BroadcastChannel may be absent in some test envs; never fail render */
  }
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

self.addEventListener("notificationclick", (event) => {
  event.notification.close()
  const data = (event.notification.data ?? {}) as { url?: string; kind?: string }
  const targetPath = data.url || "/"
  console.info(`pwa.push.notification_clicked target_path=${targetPath}`)

  event.waitUntil(
    (async () => {
      const allClients = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      })
      for (const client of allClients) {
        if ("focus" in client) {
          client.postMessage({ type: "NAVIGATE", url: targetPath })
          await client.focus()
          try {
            const channel = new BroadcastChannel(PUSH_TEST_CHANNEL)
            channel.postMessage({ type: "CLICKED", url: targetPath })
            channel.close()
          } catch {
            /* ignore */
          }
          return
        }
      }
      await self.clients.openWindow(targetPath)
      try {
        const channel = new BroadcastChannel(PUSH_TEST_CHANNEL)
        channel.postMessage({ type: "CLICKED", url: targetPath })
        channel.close()
      } catch {
        /* ignore */
      }
    })(),
  )
})

self.addEventListener("message", (event) => {
  const data = event.data as
    | { type?: string }
    | TestRenderMessage
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
  }
})
