---
estimated_steps: 4
estimated_files: 6
skills_used:
  - verify-before-complete
---

# T02: Ship Web App Manifest + icon set + index.html PWA metadata

Create `frontend/public/manifest.webmanifest` with the fields Lighthouse PWA install criteria require: `name`, `short_name`, `description`, `start_url: '/'`, `scope: '/'`, `display: 'standalone'`, `orientation: 'any'`, `theme_color`, `background_color`, and an `icons` array with at least 192×192 + 512×512 PNGs (one of which is `purpose: 'maskable'`) plus a 180×180 `apple-touch-icon`. Generate the icon set from `frontend/public/assets/images/favicon.png` using the existing fastapi favicon as the source — produce `pwa-192.png`, `pwa-512.png`, `pwa-512-maskable.png`, `apple-touch-icon-180.png`, all placed under `frontend/public/`. Use `sharp` if already installed, otherwise generate via the build script using `node` + `canvas` or check in pre-rasterized variants (per CLAUDE.md rule: prefer not to add a heavy dep just for build-time icon generation — check in the four PNGs as static assets). In `frontend/index.html`, add `<link rel='manifest' href='/manifest.webmanifest'>`, `<meta name='theme-color' content='#0a0a0a'>` (matches the dark default from `theme-provider.tsx`), `<link rel='apple-touch-icon' sizes='180x180' href='/apple-touch-icon-180.png'>`, and `<meta name='apple-mobile-web-app-capable' content='yes'>` for iOS standalone mode. Update the page `<title>` to 'Perpetuity' (the current 'Full Stack FastAPI Project' is template debt). Verify the manifest parses by running it through `JSON.parse` from a node one-liner in the verify command.

## Inputs

- ``frontend/index.html` — current head tags from T01's no-op state; this task adds manifest link + apple-touch-icon + theme-color`
- ``frontend/public/assets/images/favicon.png` — source raster for icon generation`
- ``frontend/src/components/theme-provider.tsx` — referenced for theme_color hex matching the dark default`
- ``frontend/vite.config.ts` — referenced; vite-plugin-pwa from T01 reads `frontend/public/manifest.webmanifest` automatically when present`

## Expected Output

- ``frontend/public/manifest.webmanifest` — valid Web App Manifest JSON`
- ``frontend/public/pwa-192.png` — 192×192 standard icon`
- ``frontend/public/pwa-512.png` — 512×512 standard icon`
- ``frontend/public/pwa-512-maskable.png` — 512×512 maskable icon`
- ``frontend/public/apple-touch-icon-180.png` — 180×180 iOS home-screen icon`
- ``frontend/index.html` — adds manifest link, theme-color meta, apple-touch-icon link, apple-mobile-web-app-capable meta; updates title`

## Verification

cd frontend && node -e "const m=JSON.parse(require('fs').readFileSync('public/manifest.webmanifest','utf8'));if(!m.name||!m.start_url||!m.icons||m.icons.length<2)throw new Error('manifest invalid')" && test -f public/pwa-192.png && test -f public/pwa-512.png && test -f public/apple-touch-icon-180.png && grep -q 'manifest.webmanifest' index.html && grep -q 'apple-touch-icon' index.html
