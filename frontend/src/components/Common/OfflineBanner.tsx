// M005-oaptsz/S01/T03 — Sticky offline banner with reconnect heartbeat.
// Mounts whenever `navigator.onLine === false`. On the `online` event we issue
// a single heartbeat against the existing health-check endpoint
// (`/api/v1/utils/health-check/`, verified in backend/app/api/routes/utils.py)
// and clear the banner only on a 2xx response — flapping wifi often fires
// `online` before the new connection actually has reachability, so we treat
// the heartbeat as the authoritative signal.
//
// `/api/*` is registered as NetworkOnly in the SW (sw.ts), so this fetch
// always traverses the network and is never silently served from cache.
import { useEffect, useRef, useState } from "react"

const HEALTH_CHECK_PATH = "/api/v1/utils/health-check/"

const COPY = {
  body: "You are offline. Some features may be unavailable.",
  reconnecting: "Reconnecting…",
} as const

function buildHealthCheckUrl(): string {
  const base = import.meta.env.VITE_API_URL ?? ""
  return `${base}${HEALTH_CHECK_PATH}`
}

export function OfflineBanner() {
  const [offline, setOffline] = useState<boolean>(() => {
    if (typeof navigator === "undefined") return false
    return navigator.onLine === false
  })
  const [reconnecting, setReconnecting] = useState(false)
  // Guard against overlapping heartbeats from rapid online/offline cycling on
  // mobile networks — at most one in-flight at a time.
  const heartbeatInFlight = useRef(false)

  useEffect(() => {
    let cancelled = false

    const probe = async () => {
      if (heartbeatInFlight.current) return
      heartbeatInFlight.current = true
      setReconnecting(true)
      try {
        const response = await fetch(buildHealthCheckUrl(), {
          method: "GET",
          credentials: "include",
          cache: "no-store",
        })
        if (cancelled) return
        if (response.ok) {
          console.info("pwa.online.restored")
          setOffline(false)
        } else {
          console.info(`pwa.online.restored_failed status=${response.status}`)
        }
      } catch (error) {
        if (cancelled) return
        const reason = error instanceof Error ? error.message : String(error)
        console.info(`pwa.online.restored_failed reason=${reason}`)
      } finally {
        heartbeatInFlight.current = false
        if (!cancelled) setReconnecting(false)
      }
    }

    const handleOffline = () => {
      console.info("pwa.offline.detected")
      setOffline(true)
    }
    const handleOnline = () => {
      void probe()
    }

    window.addEventListener("offline", handleOffline)
    window.addEventListener("online", handleOnline)

    // If we mounted while already offline, log it once so the devtools console
    // tells the same story whether the user opened devtools before or after
    // the connection dropped.
    if (navigator.onLine === false) {
      console.info("pwa.offline.detected source=mount")
    }

    return () => {
      cancelled = true
      window.removeEventListener("offline", handleOffline)
      window.removeEventListener("online", handleOnline)
    }
  }, [])

  if (!offline) return null

  return (
    <output
      aria-live="polite"
      className="sticky top-0 z-20 flex w-full items-center justify-center gap-2 border-b bg-destructive px-4 py-2 text-destructive-foreground"
    >
      <span className="text-sm font-medium">
        {reconnecting ? COPY.reconnecting : COPY.body}
      </span>
    </output>
  )
}

export default OfflineBanner
