---
estimated_steps: 5
estimated_files: 3
skills_used:
  - react-best-practices
  - accessibility
  - make-interfaces-feel-better
  - verify-before-complete
  - lint
---

# T03: Build InstallBanner + OfflineBanner UX with Android beforeinstallprompt + iOS one-time toast + navigator.onLine heartbeat

Create `frontend/src/components/Common/InstallBanner.tsx` that listens for the `beforeinstallprompt` event, suppresses the browser default, and renders a dismissible banner offering 'Install Perpetuity'. Clicking install calls the deferred prompt's `prompt()` and awaits `userChoice`; the banner persists `pwa.install_dismissed_at = <iso>` to localStorage on dismiss and never re-prompts automatically (per CONTEXT error-handling). Add an iOS branch: detect via `/iPad|iPhone|iPod/.test(navigator.userAgent) && !window.matchMedia('(display-mode: standalone)').matches` and render a one-time toast (using sonner — already installed) with copy 'Tap the share icon then Add to Home Screen' the first visit only; gate this toast behind a `pwa.ios_toast_shown` localStorage flag. Create `frontend/src/components/Common/OfflineBanner.tsx` that mounts a sticky top banner whenever `navigator.onLine === false`; on `online` event, trigger a heartbeat `fetch('/api/v1/utils/health-check/')` (the existing endpoint — verify the path in the route file before wiring) and clear the banner only on a 2xx response. Both components mount inside `frontend/src/routes/_layout.tsx`'s header area (above the SidebarTrigger row). Listen for the `pwa-update-available` CustomEvent (dispatched by T01's registerSW) and render a third 'Update available — refresh' inline action inside InstallBanner's container; refresh posts `{type:'SKIP_WAITING'}` to the active SW registration's waiting worker and reloads the page. All copy lives in component constants (no i18n in this milestone). Components are theme-aware (use existing tailwind tokens from `index.css`).

## Inputs

- ``frontend/src/main.tsx` — T01 dispatches `pwa-update-available` here; InstallBanner subscribes`
- ``frontend/src/routes/_layout.tsx` — current authenticated layout; mount banners inside the header`
- ``frontend/src/components/ui/sonner.tsx` — toast surface for iOS one-time install hint`
- ``frontend/src/components/ui/button.tsx` — banner action buttons reuse this`
- ``backend/app/api/routes/utils.py` or equivalent — referenced for the health-check endpoint path; if the path differs, use the actual path`

## Expected Output

- ``frontend/src/components/Common/InstallBanner.tsx` — new component with Android beforeinstallprompt + iOS toast + update-available action`
- ``frontend/src/components/Common/OfflineBanner.tsx` — new component reading navigator.onLine + heartbeat-on-reconnect`
- ``frontend/src/routes/_layout.tsx` — mounts both banners inside the header`

## Verification

cd frontend && bun run build && cd .. && grep -q 'beforeinstallprompt' frontend/src/components/Common/InstallBanner.tsx && grep -q 'navigator.onLine' frontend/src/components/Common/OfflineBanner.tsx && grep -q 'InstallBanner' frontend/src/routes/_layout.tsx && grep -q 'OfflineBanner' frontend/src/routes/_layout.tsx

## Observability Impact

Adds `pwa.install.prompt_shown`, `pwa.install.dismissed`, `pwa.install.accepted`, `pwa.offline.detected`, `pwa.online.restored` console.info lines. A future agent investigating an install-banner-not-appearing bug can grep DevTools console + inspect localStorage `pwa.install_dismissed_at` and `pwa.ios_toast_shown` to localize the state.
