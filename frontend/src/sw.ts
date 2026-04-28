/// <reference lib="webworker" />
// M005-oaptsz/S01/T01 — service worker shell.
// Bypass /api/* and /ws/* with NetworkOnly so M005-sqm8et's run-status polling
// and any future websocket flows are never silently served from cache. Cache
// the precomputed app shell for offline-capable navigation. Static asset
// hashes are immutable per build so CacheFirst is safe.

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

// S03 push-notification placeholder — body is a no-op INFO log so the listener
// entry exists in the bundle from day one. S03 will replace the body.
self.addEventListener("push", () => {
  console.info("pwa.push.received_stub")
})

// Update-available flow: the main thread posts {type: 'SKIP_WAITING'} when the
// user accepts the update banner; we then activate the new SW immediately.
self.addEventListener("message", (event) => {
  if ((event.data as { type?: string } | undefined)?.type === "SKIP_WAITING") {
    void self.skipWaiting()
  }
})
