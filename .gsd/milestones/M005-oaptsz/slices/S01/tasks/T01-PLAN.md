---
estimated_steps: 5
estimated_files: 5
skills_used:
  - react-best-practices
  - verify-before-complete
  - lint
---

# T01: Wire vite-plugin-pwa with injectManifest, ship sw.ts with route-classified strategies, register from main.tsx

Add vite-plugin-pwa as a dev dependency. Configure it in `frontend/vite.config.ts` with `strategies: 'injectManifest'`, `srcDir: 'src'`, `filename: 'sw.ts'`, `registerType: 'prompt'` (we surface our own update banner — no auto-reload), and `devOptions: { enabled: false }` (dev mode bypass per CONTEXT). Write `frontend/src/sw.ts` from scratch using the Workbox primitives: `precacheAndRoute(self.__WB_MANIFEST)` for the app shell, `registerRoute(({url}) => url.pathname.startsWith('/api/'), new NetworkOnly())`, `registerRoute(({url}) => url.pathname.startsWith('/ws/'), new NetworkOnly())`, `registerRoute(/.(?:js|css|woff2?|ttf|png|svg|webp|ico)$/, new CacheFirst({cacheName: 'static-assets'}))`. Add an empty `self.addEventListener('push', () => {})` placeholder so S03 has a known handler entry — keep the body a no-op INFO log (`console.info('pwa.push.received_stub')`) for now. Add `self.addEventListener('message', (event) => { if (event.data?.type === 'SKIP_WAITING') self.skipWaiting() })` for the update-available flow. In `frontend/src/main.tsx`, call `registerSW` from `virtual:pwa-register` with `onNeedRefresh` and `onOfflineReady` callbacks that emit `console.info` with the documented signal names (`pwa.sw.update_available`, `pwa.sw.registered`); `onNeedRefresh` should also fire a `CustomEvent('pwa-update-available')` on `window` so a future component (or T03's banner) can listen. Skip TypeScript type errors on the `virtual:pwa-register` import by adding `frontend/src/vite-env.d.ts` triple-slash references for `vite-plugin-pwa/client` and `vite-plugin-pwa/info`. Verification rests on T05's SW integration test — for this task, `bun run build` must succeed and emit a `dist/sw.js` with `precacheAndRoute` and `NetworkOnly` strings present.

## Inputs

- ``frontend/vite.config.ts` — current Vite config (tanstack-router + react-swc + tailwindcss); add vite-plugin-pwa to the plugins array`
- ``frontend/src/main.tsx` — current entry; add registerSW import + call`
- ``frontend/index.html` — referenced for build-output expectations; not modified in this task (T02 owns it)`
- ``frontend/package.json` — add vite-plugin-pwa devDependency`

## Expected Output

- ``frontend/package.json` — adds `vite-plugin-pwa` (^1.x) and `workbox-window` to devDependencies`
- ``frontend/vite.config.ts` — adds `VitePWA` plugin call with `injectManifest` strategy`
- ``frontend/src/sw.ts` — new SW source with precaching, NetworkOnly /api/* and /ws/*, CacheFirst static assets, push-event stub, message-event SKIP_WAITING handler`
- ``frontend/src/main.tsx` — adds `registerSW` call with `onNeedRefresh` + `onOfflineReady` callbacks emitting documented console.info signals`
- ``frontend/src/vite-env.d.ts` — adds triple-slash references for `vite-plugin-pwa/client` and `vite-plugin-pwa/info``

## Verification

cd frontend && bun install && bun run build && test -f dist/sw.js && grep -q 'NetworkOnly' dist/sw.js && grep -q 'precache' dist/sw.js

## Observability Impact

Adds the `pwa.sw.registered`, `pwa.sw.update_available`, `pwa.sw.register_failed reason=<message>` console.info lines plus the per-fetch SW lifecycle logs documented in the slice Observability/Diagnostics section. A future agent debugging install/refresh issues can read these in DevTools → Console; SW registration state is also visible in DevTools → Application → Service Workers.
