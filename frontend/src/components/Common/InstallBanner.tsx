// M005-oaptsz/S01/T03 — Install + update-available banner.
// Listens for `beforeinstallprompt` (Android Chrome), suppresses the browser
// default, and renders a dismissible banner. Dismissal is sticky via the
// `pwa.install_dismissed_at` localStorage key — the slice CONTEXT calls for
// "never re-prompt automatically" once the user dismisses.
//
// iOS branch: Safari does not implement beforeinstallprompt, so we detect via
// userAgent + display-mode and show a one-time sonner toast directing the user
// to "Share → Add to Home Screen". Gated by `pwa.ios_toast_shown`.
//
// Update-available branch: T01 dispatches the `pwa-update-available`
// CustomEvent on the window when Workbox surfaces a waiting SW. We render an
// inline "Update available — refresh" action that calls the event's
// `detail.acceptUpdate()` (which posts {type:'SKIP_WAITING'} to the waiting
// worker and triggers a page reload via the registerSW return value).
import { useEffect, useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"

const DISMISSED_KEY = "pwa.install_dismissed_at"
const IOS_TOAST_KEY = "pwa.ios_toast_shown"

const COPY = {
  title: "Install Perpetuity",
  body: "Add it to your home screen for a faster, app-like experience.",
  install: "Install",
  dismiss: "Not now",
  updateBody: "A new version is available.",
  update: "Refresh",
  iosBody: "Tap the share icon then Add to Home Screen.",
} as const

interface BeforeInstallPromptEvent extends Event {
  readonly platforms: ReadonlyArray<string>
  prompt: () => Promise<void>
  readonly userChoice: Promise<{
    outcome: "accepted" | "dismissed"
    platform: string
  }>
}

interface PwaUpdateAvailableEventDetail {
  acceptUpdate: () => Promise<void>
}

function isIOSStandalone(): boolean {
  if (typeof window === "undefined") return true
  const ua = window.navigator.userAgent
  const isIOS = /iPad|iPhone|iPod/.test(ua)
  if (!isIOS) return false
  // Already installed / running standalone — nothing to prompt about.
  return window.matchMedia("(display-mode: standalone)").matches
}

function isIOSEligible(): boolean {
  if (typeof window === "undefined") return false
  const ua = window.navigator.userAgent
  return /iPad|iPhone|iPod/.test(ua) && !isIOSStandalone()
}

export function InstallBanner() {
  const [installEvent, setInstallEvent] =
    useState<BeforeInstallPromptEvent | null>(null)
  const [updateHandler, setUpdateHandler] = useState<
    (() => Promise<void>) | null
  >(null)
  const [dismissed, setDismissed] = useState<boolean>(() => {
    if (typeof window === "undefined") return true
    return Boolean(window.localStorage.getItem(DISMISSED_KEY))
  })

  useEffect(() => {
    const onBeforeInstall = (event: Event) => {
      // Suppress the browser's mini-infobar so we own the surface.
      event.preventDefault()
      const installPrompt = event as BeforeInstallPromptEvent
      setInstallEvent(installPrompt)
      console.info("pwa.install.prompt_shown")
    }
    const onUpdate = (event: Event) => {
      const detail = (event as CustomEvent<PwaUpdateAvailableEventDetail>)
        .detail
      if (!detail?.acceptUpdate) return
      // Wrap in a getter so React's setState does not invoke the function
      // (setState treats raw functions as updaters).
      setUpdateHandler(() => detail.acceptUpdate)
    }
    window.addEventListener("beforeinstallprompt", onBeforeInstall)
    window.addEventListener("pwa-update-available", onUpdate as EventListener)
    return () => {
      window.removeEventListener("beforeinstallprompt", onBeforeInstall)
      window.removeEventListener(
        "pwa-update-available",
        onUpdate as EventListener,
      )
    }
  }, [])

  useEffect(() => {
    if (!isIOSEligible()) return
    if (window.localStorage.getItem(IOS_TOAST_KEY)) return
    window.localStorage.setItem(IOS_TOAST_KEY, new Date().toISOString())
    toast.info(COPY.iosBody, { duration: 8000 })
    console.info("pwa.install.prompt_shown platform=ios")
  }, [])

  const handleInstall = async () => {
    if (!installEvent) return
    await installEvent.prompt()
    const choice = await installEvent.userChoice
    if (choice.outcome === "accepted") {
      console.info("pwa.install.accepted")
    } else {
      console.info("pwa.install.dismissed reason=user_choice")
      window.localStorage.setItem(DISMISSED_KEY, new Date().toISOString())
      setDismissed(true)
    }
    setInstallEvent(null)
  }

  const handleDismiss = () => {
    window.localStorage.setItem(DISMISSED_KEY, new Date().toISOString())
    setDismissed(true)
    setInstallEvent(null)
    console.info("pwa.install.dismissed reason=banner_x")
  }

  const handleUpdate = async () => {
    if (!updateHandler) return
    await updateHandler()
    setUpdateHandler(null)
  }

  const showInstall = Boolean(installEvent) && !dismissed
  const showUpdate = Boolean(updateHandler)
  if (!showInstall && !showUpdate) return null

  return (
    <section
      aria-label="App install and update notifications"
      className="flex w-full flex-col gap-2 border-b bg-muted/40 px-4 py-2 sm:flex-row sm:items-center sm:justify-between"
    >
      {showInstall ? (
        <div className="flex flex-col gap-2 sm:flex-1 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-col">
            <span className="text-sm font-medium">{COPY.title}</span>
            <span className="text-muted-foreground text-xs">{COPY.body}</span>
          </div>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              size="sm"
              onClick={handleInstall}
              className="min-h-11"
            >
              {COPY.install}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={handleDismiss}
              className="min-h-11"
              aria-label="Dismiss install banner"
            >
              {COPY.dismiss}
            </Button>
          </div>
        </div>
      ) : null}
      {showUpdate ? (
        <div className="flex items-center justify-between gap-2 sm:gap-4">
          <span className="text-muted-foreground text-xs">
            {COPY.updateBody}
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={handleUpdate}
            className="min-h-11"
          >
            {COPY.update}
          </Button>
        </div>
      ) : null}
    </section>
  )
}

export default InstallBanner
