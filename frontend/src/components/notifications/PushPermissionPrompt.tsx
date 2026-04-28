// M005-oaptsz/S03/T04 — Push permission prompt + subscribe wire-up.
//
// Renders an inline banner offering "Enable push notifications" while
// `Notification.permission === 'default'` and the user has not yet
// dismissed (sticky via localStorage `pwa.push.dismissed_at`, mirroring
// InstallBanner's pattern from S01/T03). On Allow:
//   1. Notification.requestPermission()
//   2. PushService.getVapidPublicKey() — bails out on 503 (operator has
//      not generated a keypair yet; we don't pretend we can subscribe).
//   3. navigator.serviceWorker.ready → registration.pushManager.subscribe
//   4. POST subscription.toJSON() to /api/v1/push/subscribe via the typed
//      generated client.
//
// iOS Safari pre-16.4 has no PushManager — branch detects via UA + capability
// probe and renders nothing (S01's iOS install toast already handles the
// pre-Add-To-Home-Screen surface; we only re-prompt for push once installed).
//
// Already-granted re-mounts: silently re-POST any existing subscription so
// the row's `last_seen_at` stays fresh — keeps the dispatcher's pruning
// heuristic (S05) from cutting still-active devices.

import { useEffect, useRef, useState } from "react"

import { type ApiError, PushService } from "@/client"
import { Button } from "@/components/ui/button"
import { endpointHash, urlBase64ToUint8Array } from "@/lib/vapid"

const DISMISSED_KEY = "pwa.push.dismissed_at"

const COPY = {
  title: "Enable push notifications",
  body: "Get alerted on workflow failures even when the app is closed.",
  allow: "Allow",
  dismiss: "Not now",
} as const

const PERMISSION_DENIED_EVENT = "pwa-push-permission-denied"

interface NotificationCapableWindow extends Window {
  Notification: typeof Notification
}

function hasPushCapability(): boolean {
  if (typeof window === "undefined") return false
  if (!("serviceWorker" in navigator)) return false
  if (!("PushManager" in window)) return false
  if (!("Notification" in window)) return false
  return true
}

function isIOSPreSafariPush(): boolean {
  if (typeof window === "undefined") return false
  const ua = window.navigator.userAgent
  if (!/iPad|iPhone|iPod/.test(ua)) return false
  // PushManager is the cleanest probe: iOS 16.4+ Home-Screen-installed
  // Safari exposes it; older / non-installed versions do not.
  return !("PushManager" in window)
}

function readPermission(): NotificationPermission | null {
  if (typeof window === "undefined") return null
  if (!("Notification" in window)) return null
  return (window as NotificationCapableWindow).Notification.permission
}

function alreadyDismissed(): boolean {
  if (typeof window === "undefined") return true
  return Boolean(window.localStorage.getItem(DISMISSED_KEY))
}

async function pushSubscriptionJsonBody(
  subscription: PushSubscription,
): Promise<{
  endpoint: string
  keys: { p256dh: string; auth: string }
}> {
  const json = subscription.toJSON()
  const endpoint = json.endpoint
  const p256dh = json.keys?.p256dh
  const auth = json.keys?.auth
  if (!endpoint || !p256dh || !auth) {
    throw new Error("subscription is missing endpoint or keys")
  }
  return { endpoint, keys: { p256dh, auth } }
}

export function PushPermissionPrompt() {
  const [supported, setSupported] = useState<boolean>(false)
  const [permission, setPermission] = useState<NotificationPermission | null>(
    null,
  )
  const [dismissed, setDismissed] = useState<boolean>(false)
  const [working, setWorking] = useState<boolean>(false)
  const promptShownRef = useRef<boolean>(false)
  const grantedHandledRef = useRef<boolean>(false)

  useEffect(() => {
    if (isIOSPreSafariPush()) {
      setSupported(false)
      return
    }
    setSupported(hasPushCapability())
    setPermission(readPermission())
    setDismissed(alreadyDismissed())
  }, [])

  // One-shot console.info when the banner first becomes visible.
  useEffect(() => {
    if (!supported) return
    if (dismissed) return
    if (permission !== "default") return
    if (promptShownRef.current) return
    promptShownRef.current = true
    console.info("pwa.push.permission_prompt_shown")
  }, [supported, dismissed, permission])

  // Already-granted-on-mount: re-POST the existing subscription to refresh
  // last_seen_at, so the dispatcher's stale-row pruning never drops a
  // browser the user is still actively using.
  useEffect(() => {
    if (!supported) return
    if (permission !== "granted") return
    if (grantedHandledRef.current) return
    grantedHandledRef.current = true
    void (async () => {
      try {
        const registration = await navigator.serviceWorker.ready
        const existing = await registration.pushManager.getSubscription()
        if (!existing) return
        const body = await pushSubscriptionJsonBody(existing)
        await PushService.subscribe({ requestBody: body })
        const hash = await endpointHash(body.endpoint)
        console.info(`pwa.push.subscribed endpoint_hash=${hash}`)
      } catch (err) {
        const cause = err instanceof Error ? err.message : String(err)
        console.info(`pwa.push.subscribe_failed cause=${cause}`)
      }
    })()
  }, [supported, permission])

  const handleAllow = async () => {
    if (working) return
    setWorking(true)
    try {
      const result = await window.Notification.requestPermission()
      setPermission(result)
      if (result === "granted") {
        console.info("pwa.push.permission_granted")
      } else if (result === "denied") {
        console.info("pwa.push.permission_denied")
        window.dispatchEvent(new CustomEvent(PERMISSION_DENIED_EVENT))
        return
      } else {
        // 'default' — user dismissed the browser prompt without choosing.
        return
      }

      // Vapid public key — backend returns 503 if the operator has not
      // generated a keypair yet. We surface that as a structured failure
      // and stop; we do NOT try to subscribe with no key.
      let vapidPublicKey: string
      try {
        const keyResp = await PushService.getVapidPublicKey()
        vapidPublicKey = keyResp.public_key
      } catch (err) {
        const apiErr = err as ApiError
        const cause =
          apiErr?.status === 503
            ? "vapid_not_configured"
            : apiErr?.message || "vapid_fetch_failed"
        console.info(`pwa.push.subscribe_failed cause=${cause}`)
        return
      }

      const registration = await navigator.serviceWorker.ready
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(vapidPublicKey),
      })
      const body = await pushSubscriptionJsonBody(subscription)
      await PushService.subscribe({ requestBody: body })
      const hash = await endpointHash(body.endpoint)
      console.info(`pwa.push.subscribed endpoint_hash=${hash}`)
    } catch (err) {
      const cause = err instanceof Error ? err.message : String(err)
      console.info(`pwa.push.subscribe_failed cause=${cause}`)
    } finally {
      setWorking(false)
    }
  }

  const handleDismiss = () => {
    window.localStorage.setItem(DISMISSED_KEY, new Date().toISOString())
    setDismissed(true)
  }

  if (!supported) return null
  if (permission !== "default") return null
  if (dismissed) return null

  return (
    <section
      aria-label="Push notification permission"
      data-testid="push-permission-prompt"
      className="flex w-full flex-col gap-2 border-b bg-muted/40 px-4 py-2 sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex flex-col">
        <span className="text-sm font-medium">{COPY.title}</span>
        <span className="text-muted-foreground text-xs">{COPY.body}</span>
      </div>
      <div className="flex items-center gap-2">
        <Button
          type="button"
          size="sm"
          onClick={handleAllow}
          disabled={working}
          className="min-h-11"
        >
          {COPY.allow}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={handleDismiss}
          className="min-h-11"
          aria-label="Dismiss push permission prompt"
        >
          {COPY.dismiss}
        </Button>
      </div>
    </section>
  )
}

export default PushPermissionPrompt
