---
id: T03
parent: S01
milestone: M005-oaptsz
key_files:
  - frontend/src/components/Common/InstallBanner.tsx
  - frontend/src/components/Common/OfflineBanner.tsx
  - frontend/src/routes/_layout.tsx
key_decisions:
  - Reused T01's `pwa-update-available` CustomEvent's `detail.acceptUpdate` closure for the Refresh action rather than re-implementing postMessage(SKIP_WAITING) + reload — single source of truth for the upgrade flow lives in main.tsx's registerSW callback.
  - Health-check is the authoritative reconnect signal. The `online` event alone is unreliable on mobile networks (fires before reachability is real); a 2xx response from `/api/v1/utils/health-check/` is the only thing that clears the offline banner.
  - Sticky-dismiss on install banner. Once dismissed via the X (or via accepting then later denying the native prompt), `pwa.install_dismissed_at` is set and the banner never re-renders for that browser/profile — per CONTEXT error-handling (no nag).
  - iOS toast gated by single localStorage key. `pwa.ios_toast_shown` flips on first eligible mount and the share/A2HS hint never re-fires.
  - Replaced `role="region"` and `role="status"` with `<section aria-label>` and `<output aria-live>` after Biome a11y/useSemanticElements flagged them — same SR semantics, satisfies linter.
  - Update handler stored via setState getter (`setUpdateHandler(() => fn)`) to avoid React invoking the function as an updater.
duration: 
verification_result: passed
completed_at: 2026-04-28T08:37:04.190Z
blocker_discovered: false
---

# T03: feat(pwa): add InstallBanner (Android beforeinstallprompt + iOS one-time toast + update-available action) and OfflineBanner with reconnect heartbeat

**feat(pwa): add InstallBanner (Android beforeinstallprompt + iOS one-time toast + update-available action) and OfflineBanner with reconnect heartbeat**

## What Happened

Built the install + offline UX surface for M005's PWA. InstallBanner.tsx owns three lifecycle channels in one component: (1) Android Chrome's `beforeinstallprompt` is captured with `preventDefault()`, the deferred event is stashed in state, and a dismissible banner renders "Install" / "Not now" actions — accepting calls `prompt()` + awaits `userChoice`, dismissal stamps `pwa.install_dismissed_at` to localStorage so we never re-prompt automatically (per CONTEXT error-handling). (2) iOS branch detects via `/iPad|iPhone|iPod/.test(navigator.userAgent) && !matchMedia('(display-mode: standalone)').matches` and fires a one-time sonner `toast.info` with the share/A2HS copy, gated by `pwa.ios_toast_shown` localStorage. (3) Listens for the `pwa-update-available` CustomEvent that T01's main.tsx dispatches and renders an inline "Refresh" action that calls `event.detail.acceptUpdate()` — the registerSW return value already posts {type:'SKIP_WAITING'} and reloads, so the banner just invokes that closure rather than re-implementing the postMessage. Emits `pwa.install.prompt_shown` / `pwa.install.accepted` / `pwa.install.dismissed` console.info lines per the slice's Observability contract.

OfflineBanner.tsx is a separate sticky banner driven by `navigator.onLine`. On mount it logs `pwa.offline.detected source=mount` if the page loaded offline. The `online` event triggers a single heartbeat `fetch('${VITE_API_URL}/api/v1/utils/health-check/')` (path verified in `backend/app/api/routes/utils.py:29` + `backend/app/main.py` API_V1_STR mounting) with `cache:'no-store'` and `credentials:'include'`; the banner is cleared only on 2xx — flapping mobile wifi often emits `online` before reachability is real, so the heartbeat is the authoritative signal. A `heartbeatInFlight` ref prevents overlapping probes from rapid online/offline cycling. Logs `pwa.online.restored` on success, `pwa.online.restored_failed status=...` or `reason=...` on failure.

Both banners mount inside `_layout.tsx` `<SidebarInset>` above the existing header (the slice plan said "above the SidebarTrigger row"; SidebarTrigger lives inside the header, so banners sit just above the header — which is the closest match that keeps the install/offline surface above all primary chrome). Touch targets use `min-h-11` (44px) per the slice's mobile-audit requirement. Theme-aware via existing tailwind tokens (`bg-muted/40`, `bg-destructive`).

A11y: the linter flagged `role="region"` and `role="status"` and asked for semantic elements (Biome `a11y/useSemanticElements`). InstallBanner now uses `<section aria-label>` and OfflineBanner uses `<output aria-live="polite">` — same screen-reader semantics, no ARIA roles needed.

Implementation discipline: real beforeinstallprompt typing via a local `BeforeInstallPromptEvent` interface (lib.dom.d.ts doesn't ship it). The update handler is stored as a getter (`setUpdateHandler(() => detail.acceptUpdate)`) because React's `setState` treats raw functions as updaters and would invoke `acceptUpdate` immediately. Cancellation flag in OfflineBanner's effect prevents stale-closure setState if the component unmounts mid-fetch.

## Verification

Ran the full verification command from the task plan: `cd frontend && bun run build && grep -q 'beforeinstallprompt' frontend/src/components/Common/InstallBanner.tsx && grep -q 'navigator.onLine' frontend/src/components/Common/OfflineBanner.tsx && grep -q 'InstallBanner' frontend/src/routes/_layout.tsx && grep -q 'OfflineBanner' frontend/src/routes/_layout.tsx`. Build produced both `dist/index.html` (with PWA manifest + icons from T02) and `dist/sw.js` (Workbox SW from T01) cleanly. All four greps passed. `bun run lint` (Biome with --write) reports clean — initial run flagged `role="region"`/`role="status"` (a11y/useSemanticElements); fixed by switching to `<section aria-label>` and `<output aria-live>`. Browser-side runtime verification (devtools install banner, beforeinstallprompt firing on Pixel-class device, navigator.onLine flipping) is part of the slice-level mobile audit in the final task of S01 — at this task's level, wiring is verified by build + grep + lint per the plan's Verification section. The slice's other runtime signals (SW console lifecycle logs, manifest validity in DevTools) are produced by T01/T02 and do not regress with this change.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd frontend && bun run build` | 0 | ✅ pass | 2110ms |
| 2 | `grep -q 'beforeinstallprompt' frontend/src/components/Common/InstallBanner.tsx` | 0 | ✅ pass | 5ms |
| 3 | `grep -q 'navigator.onLine' frontend/src/components/Common/OfflineBanner.tsx` | 0 | ✅ pass | 5ms |
| 4 | `grep -q 'InstallBanner' frontend/src/routes/_layout.tsx` | 0 | ✅ pass | 5ms |
| 5 | `grep -q 'OfflineBanner' frontend/src/routes/_layout.tsx` | 0 | ✅ pass | 5ms |
| 6 | `cd frontend && bun run lint` | 0 | ✅ pass | 31ms |

## Deviations

"Slice plan said banners mount 'above the SidebarTrigger row' — SidebarTrigger lives inside the layout header, so banners are mounted at the top of `<SidebarInset>` directly above the header, which is the closest faithful interpretation. Functionally identical outcome (banners are above all primary chrome)."

## Known Issues

None.

## Files Created/Modified

- `frontend/src/components/Common/InstallBanner.tsx`
- `frontend/src/components/Common/OfflineBanner.tsx`
- `frontend/src/routes/_layout.tsx`
