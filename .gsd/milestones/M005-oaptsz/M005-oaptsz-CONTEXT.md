# M005-oaptsz: PWA, Notifications & Voice — Context

**Gathered:** 2026-04-27
**Status:** Ready for planning

## Project Description

M005-oaptsz is the user-experience-layer milestone of Perpetuity. It turns the platform — by this point fully functional with auth, terminals, projects, GitHub, AI, and workflows — into something that feels like a product on a phone. Three threads land together because they collectively cover the "this is a real app, not a desktop-only dev tool" promise the project was named around:

1. **PWA installability + mobile polish.** Web App Manifest, service worker (offline shell, asset caching, runtime API caching strategy that doesn't break realtime), install prompt UX, and a top-to-bottom mobile pass against every existing flow (auth, dashboard, terminal, projects, GitHub admin, workflow editor, run history). The frontend already shipped mobile-aware on M001/S04 and has been carried through M002/S05 + M003 + M004; M005-oaptsz is the formal pass that proves every flow works on a Pixel-5-class viewport with touch-only input.
2. **Notification center + Web Push.** Bell-icon panel that lists every workflow event + system event the user needs to know about; per-workflow + per-event-type routing (`success` / `failure` / `step_completed` → in-app, push, both, none); Web Push subscription management (browser permission flow, server-side `web-push` key pair, push delivery on configured events); read/unread state persisted server-side so a phone notification is dismissed across all the user's devices.
3. **Voice input on every text field.** Microphone icon next to every text input in the app; click → request mic permission → record → display waveform → on stop, upload to Grok STT REST API (system-level API key in `system_settings`), inject transcription into the field. The same component used throughout — login form, prompt inputs, workflow form fields, project search, anywhere there's a `<Input>` or `<Textarea>`.

The threads are tightly coupled — push notifications without a service worker isn't possible (Web Push requires SW); voice without mobile polish leaves the input unreachable on phones; mobile polish without notifications leaves a half-built phone product. One milestone covers the whole UX pivot.

## Why This Milestone

R021–R025 have been on the roadmap since M001 discussion and were carried as "active, unmapped" through every prior milestone. M001/S04 shipped a mobile-aware dashboard skeleton (Pixel-5 Playwright runs); M002–M005-sqm8et added complex screens (terminal, projects, GitHub admin, workflow editor, run history) without re-checking mobile each time. By M005-sqm8et completion, the app is functionally complete but not phone-deployable — installable from the home screen, push-notifying when a workflow fails, voice-enterable for an AI prompt without typing.

It needs to land **last** in the active roadmap because:

- PWA service worker design depends on knowing the realtime surfaces (M002 WS terminal, M005-sqm8et run-page polling) — designed too early, the SW cache strategy would break realtime; designed now, it can deny-cache the right routes
- Notification routing config is **per workflow + per event type** (R024) — meaningless before workflows exist (M005-sqm8et)
- Mobile polish needs every screen to exist before the polish pass — landing it incrementally across milestones produced regression every time a new screen shipped; one consolidated pass over the final UI is the right cost shape
- Voice input is universally applied to every text field — same logic; the universe of text fields stabilizes here

Per PROJECT.md's milestone sequence, this is the launchability gate: when M005-oaptsz completes, the platform is shippable to a public beta.

## User-Visible Outcome

### When this milestone is complete, the user can:

- **Any user (mobile):** open the app in mobile Chrome / Safari, see an install banner, tap "Install" / "Add to Home Screen", and get a standalone-window app icon on their home screen that opens the app full-screen with no browser chrome
- **Any user (mobile):** complete every existing flow on a 360px-wide viewport with touch — sign up, log in, open terminal, type via voice or on-screen keyboard, navigate projects, configure GitHub, create workflow, trigger run, view run history — without horizontal scroll or off-screen controls
- **Any user (desktop OR mobile):** click the bell icon in the top bar, see a chronological list of workflow events (run started, step completed, run succeeded, run failed) and system events (team invite accepted, project created), with unread state, mark-as-read, and "show only unread" filter
- **Any user (desktop OR mobile):** on the workflow detail page, configure per-event-type notification routing (`success` → in-app only; `failure` → push + in-app; `step_completed` → none); save once, applies to all subsequent runs
- **Any user (mobile):** with the app backgrounded or closed, get a Web Push notification on workflow `failure` ("Workflow lint-on-pr failed at step 3: npm run lint exit 1"), tap it, app opens to the run-detail page
- **Any user (any device):** click the microphone icon next to any text input, grant mic permission once, see a live waveform during recording, tap stop, watch the transcribed text appear in the field within ~1s — works in Claude prompts, Codex prompts, workflow form fields, login email field, anywhere
- **System admin:** paste a Grok STT API key into `system_settings`; PWA push subscription endpoint is configured via standard VAPID key pair stored in `system_settings`; both are encrypted at rest using M004/S01's Fernet wiring

### Entry point / environment

- Entry point: any browser (mobile Chrome, mobile Safari, desktop Chrome, desktop Safari, desktop Firefox); installed PWA opens via home-screen icon
- Environment: full compose stack identical to M005-sqm8et (Postgres + Redis + orchestrator + Docker + Celery worker) plus real Web Push delivery (Mozilla autopush / FCM) and real Grok STT API for acceptance
- Live dependencies involved: real Mozilla Push Service / FCM (Web Push delivery endpoints — pushes flow through these regardless of our hosting); real Grok STT API (`api.grok.com` or equivalent — system key, no per-user config); browser Service Worker runtime + browser Notification API

## Completion Class

- **Contract complete means:** unit tests for service worker registration, push subscription persistence, notification routing logic, voice recording component (waveform + upload + injection), Grok STT API client (mocked); manifest validity tests; per-route SW cache strategy tests
- **Integration complete means:** install banner appears on mobile Chrome at the documented Lighthouse-PWA-criteria; service worker correctly caches static assets and bypasses API routes; push subscription persists and survives backend restart; bell icon updates in real-time when a notification arrives; voice recording uploads to a respx-mocked Grok endpoint and injects the response; mobile Playwright suite passes every existing flow at Pixel-5 viewport (extending M001/S04's pattern)
- **Operational complete means:** Web Push delivery succeeds end-to-end against real Mozilla Push Service / FCM (acceptance only); voice transcription succeeds end-to-end against real Grok API (acceptance only); push subscription expiry detected (HTTP 410 from push endpoint) and the subscription is pruned automatically; notification center pagination handles >100 unread without UI lag; PWA offline-shell renders the app skeleton even with no network (full offline use is out of scope, but the shell + cached static assets must load)
- **UAT complete means:** the four scenarios in "Final Integrated Acceptance" pass on a real iPhone + Android device + desktop browser

## Final Integrated Acceptance

To call this milestone complete, we must prove the following end-to-end (cannot be simulated in headless tests alone):

1. **Mobile install + use:** on a real Android device (Pixel or equivalent) running mobile Chrome, the install banner appears within 30s of normal use, tapping it installs the app, the home-screen icon opens the app standalone, and the user can complete the full demo flow (login → trigger Claude workflow → see run page → view run history) without any layout breakage
2. **Push notification round-trip:** user opens app on a phone, grants notification permission, configures workflow X with `failure → push`, backgrounds the app, triggers workflow X to fail (e.g. invalid API key), receives a Web Push notification on the device within 30s, taps it, app opens to run-detail page; same flow on desktop Chrome confirms cross-device delivery
3. **Voice prompt:** user opens dashboard on mobile, taps microphone next to "Run Claude" prompt field, grants mic permission, speaks "List the files in this project", taps stop, watches the transcribed text appear in the field within 1.5s, taps Run, sees the workflow execute against the real Anthropic API (M005-sqm8et) and return file listings; works identically on desktop with a USB mic
4. **Notification center cross-device read state:** on mobile, dismiss a notification → on desktop the same notification is marked read in the bell icon panel within 5s; same on the reverse; clicking "mark all as read" on either device propagates to the other

## Architectural Decisions

### Vite-PWA plugin for service worker + manifest

**Decision:** Adopt `vite-plugin-pwa` (Workbox-backed) with `injectManifest` strategy. The plugin owns manifest generation from `frontend/public/manifest.webmanifest` source, service worker bundling from `frontend/src/sw.ts`, and offline-shell + precaching wiring. SW cache strategies set per-route group: static assets `CacheFirst` with stale-while-revalidate; `/api/v1/*` and `/ws/*` `NetworkOnly` (no caching, realtime correctness); `/api/v1/notifications` short-TTL stale-while-revalidate for resilience.

**Rationale:** vite-plugin-pwa is the de facto standard for Vite + React PWAs, has a mature Workbox integration for SW patterns, and handles the manifest + SW + dev-mode-bypass complexity without hand-written boilerplate. `injectManifest` (vs `generateSW`) gives us full control over the SW source — needed for the realtime-route-bypass logic and Web Push event handlers. NetworkOnly for `/api` is non-negotiable: a cached API response served instead of a fresh one would corrupt every workflow run-status fetch in M005-sqm8et's polling UI. Custom SW from scratch rejected — would reinvent Workbox primitives. Server-side rendering rejected — out of scope; the app is a SPA.

**Alternatives Considered:**
- Hand-rolled service worker — rejected: Workbox primitives are battle-tested; reinvent only on need
- Workbox-cli without vite-plugin-pwa — rejected: weaker Vite integration; manual manifest wiring
- Server-side rendering with Next.js — rejected: re-architecture; out of scope

---

### Web Push uses `web-push` library + standard VAPID, stored in `system_settings`

**Decision:** Backend-side `web-push` Python library (`pywebpush`) signs and delivers pushes. VAPID public/private key pair generated once via `vapid --gen` and stored in `system_settings` as `vapid_public_key` (non-sensitive — it's served to browsers anyway) and `vapid_private_key` (sensitive — Fernet-encrypted using M004/S01 wiring). Subscriptions stored in new `push_subscriptions` table (`id, user_id FK, endpoint TEXT, keys JSONB {p256dh, auth}, user_agent, created_at, last_seen_at, last_delivery_status`). Delivery happens inside the workflow run engine's notification dispatcher (a small async helper called from the Celery task at run completion) — no Celery task per push (push delivery is fast; in-line is fine).

**Rationale:** `pywebpush` is the standard Python implementation of Web Push protocol (RFC 8030). VAPID is required for browser push subscription authentication. Storing keys in `system_settings` reuses M004/S01's encryption path and keeps the operator surface unified — no second key-management UI. Per-user subscription rows because one user can have multiple devices (phone + laptop both subscribed). Storing `last_delivery_status` lets us prune dead subscriptions automatically (HTTP 410 from push endpoints means the user uninstalled or denied notifications). In-line delivery from the run task is fine because push API calls are fast (~100ms typical) and async-non-blocking; per-push Celery task would add latency and queue depth for no operational gain.

**Alternatives Considered:**
- Push delivery via dedicated Celery task — rejected: extra queue depth, no benefit; push is fast
- Apple Push Notification service / FCM directly (skipping Web Push) — rejected: requires native app shells; our shell is a PWA, Web Push is the standard
- Per-push token refresh on every send — rejected: VAPID JWTs are 12h-lived; refresh-once-per-process is fine
- One subscription per user (overwrite on subscribe) — rejected: phone + laptop is the common case; multi-device is the right default

---

### Notifications are stored centrally; routing decided per-delivery-channel

**Decision:** New `notifications` table — `id UUID PK, user_id FK, kind ENUM(workflow_run_started|workflow_run_succeeded|workflow_run_failed|workflow_step_completed|team_invite_received|team_invite_accepted|project_created|system|...), payload JSONB, read_at TIMESTAMPTZ NULL, created_at, source_workflow_run_id FK NULL, source_team_id FK NULL`. New `notification_preferences` table — `user_id FK PK, workflow_id FK PK NULL (null = team default), event_type VARCHAR(64) PK, channels JSONB ({"in_app": true, "push": false})`. Every notifiable event in the app calls `notify(user_id, kind, payload, source_*)`; the helper inserts the `notifications` row, looks up the user's preferences for the matching `(workflow_id?, event_type)` and delivers to each enabled channel. In-app channel = the row insert is enough (UI polls / server-side events for new rows). Push channel = `pywebpush` send to all of the user's `push_subscriptions`.

**Rationale:** Single notifications table with kind-discriminator is the right shape for "central inbox" UX (one bell icon, one chronological list, one read/unread state). Per-channel routing logic is a thin function over preferences, not a multi-table dance. Preferences keyed on `(user_id, workflow_id?, event_type)` covers R024's "per workflow and per event type" — `workflow_id NULL` is the team-default, specific `workflow_id` overrides. New event kinds added by extending the enum + adding preference defaults. R023's bell icon UI consumes the table directly with simple SQL.

**Alternatives Considered:**
- Per-channel tables (`in_app_notifications`, `push_notifications`) — rejected: cross-channel queries (mark-all-read syncs in-app and push) become multi-table; unnatural
- Computed routing decisions stored on each row — rejected: changing preferences shouldn't rewrite history; route at delivery time
- Push as just an alternative renderer of in-app rows — rejected: push has lifecycle (subscription expiry, delivery failure) that doesn't apply to in-app; cleaner to keep them as channels

---

### Cross-device read state via short-poll, not WebSocket pub/sub

**Decision:** Bell icon polls `GET /api/v1/notifications?since={last_seen_id}` every 5s while the app is open. Read state synced via `POST /api/v1/notifications/{id}/read` and `POST /api/v1/notifications/read_all`. Mobile and desktop both poll; the second device sees the read state on the next poll. The PWA service worker does **not** participate in real-time delivery to in-app UI — that's only for push notifications when the app is backgrounded.

**Rationale:** Same reasoning as M005-sqm8et's run-page polling: WebSocket pub/sub for notifications would require Redis pub/sub, a backend WS endpoint, frontend reconnect logic, and a stop-streaming-on-close handshake. 5s polling is operationally indistinguishable for a notification panel that updates a few times per minute. The acceptance contract is "within 5s" cross-device, which exactly matches the polling cadence. Push is the real-time channel for backgrounded-app delivery; in-app state can poll.

**Alternatives Considered:**
- WebSocket pub/sub via Redis — rejected: scope creep
- Server-Sent Events — rejected: same as M005-sqm8et reasoning; cookie-auth WS is the chosen real-time stack and notifications don't need a second
- Push-only (no in-app polling) — rejected: doesn't cover the "app is open, mark-read syncs across devices" UX

---

### Voice input is one component reused everywhere, no per-form custom code

**Decision:** New `<VoiceInput>` component wraps the existing `<Input>` and `<Textarea>` components from the shadcn-style design system already in use. `<VoiceInput>` renders the wrapped input with a microphone icon button suffix; clicking the mic button uses `MediaRecorder` API (`audio/webm;codecs=opus`) to record, displays a live waveform via `AnalyserNode.getByteTimeDomainData`, on stop POSTs the recorded blob as `multipart/form-data` to a new `POST /api/v1/voice/transcribe` endpoint. Backend proxies to Grok STT REST API (system-level `grok_stt_api_key` setting) and returns `{text: "..."}`. Transcription injected into the input via the wrapped component's standard `onChange` handler — same as keyboard input. Every existing `<Input>` / `<Textarea>` in the app is upgraded to `<VoiceInput>` via codemod or systematic replacement.

**Rationale:** R025's "every text input shows a microphone icon" mandates universal coverage; the only sane way to do that is one component used everywhere, not per-form integration. Wrapping the existing input components keeps the design system consistent — focus rings, error states, dark mode all keep working. `MediaRecorder` is the standard Web API; supported on all target browsers (mobile Chrome/Safari, desktop everything). Backend-side proxy to Grok keeps the API key server-side (never in browser), which R025 explicitly requires ("system-level API key, no per-user config needed"). `multipart/form-data` upload because audio blobs are binary; `application/octet-stream` would lose the file metadata Grok expects.

**Alternatives Considered:**
- Per-form custom voice integration — rejected: violates "every text input" universal coverage
- Browser-side direct call to Grok — rejected: API key would have to ship to the browser, blowing the "system-level, never per-user" property
- Web Speech API (browser-native STT) — rejected: inconsistent across browsers, especially mobile Safari; Grok provides consistent quality
- Replace `<Input>` with `<VoiceInput>` everywhere via global rename — rejected: too aggressive; some inputs (numeric, password, search-with-debounce) don't make sense for voice; explicit upgrade is safer

---

### Mobile polish is enforced by extending the M001/S04 Playwright pattern

**Decision:** Add `playwright.config.ts` projects: `pixel-5-mobile-chrome`, `iphone-13-mobile-safari`, `desktop-chrome`, `desktop-firefox`, `desktop-webkit`. Every Playwright test runs against all four (M001/S04 already runs against `pixel-5-mobile-chrome` and `desktop-chrome` — extension is incremental). New mobile-specific tests (S01 below) verify install banner, home-screen icon launch behavior, touch targets >= 44x44 CSS pixels, no horizontal scroll at 360px width, voice button present and tappable on every input field. Layout regressions caught by visual diff (Playwright screenshots) on critical pages.

**Rationale:** M001/S04 already proved Playwright-mobile-Chrome works for layout regression catching. Extension to four browsers + visual diff costs CI time but no new infra. Visual diff catches mobile-overflow bugs that pure layout assertions miss. Touch-target enforcement via Playwright element bounding box assertions covers R022's "touch targets meet mobile standards" without an external a11y tool. iOS Safari coverage matters because Safari's PWA support is the historical pain point (no push notifications in iOS Safari until 16.4; install banner UX differs).

**Alternatives Considered:**
- Lighthouse CI as the gate — rejected: Lighthouse is good for high-level scores but flaky for layout regression; Playwright visual diff is the right granularity
- Manual QA only — rejected: regresses on every milestone
- BrowserStack / Sauce real-device farm — rejected: cost; local Playwright + acceptance phase real-device check is enough

---

### Service worker is a partial-functionality offline shell, not a full offline app

**Decision:** SW caches the app shell (HTML, CSS, JS, fonts, icons) for offline rendering. SW does **not** cache API responses (per the per-route strategy above) or attempt offline workflow execution. When offline, the cached shell renders a "You're offline — features will resume when reconnected" banner and queries fail with a friendly retry prompt; nothing pretends to work. Reconnect is detected via `navigator.onLine` + a heartbeat to `GET /api/v1/health`.

**Rationale:** Full offline is wildly out of scope — workflow runs depend on the orchestrator + Celery + GitHub + Anthropic; offline editing of workflow definitions with later-sync would be its own milestone. The shell-cache scope is exactly what R021's "valid Web App Manifest and service worker" + "install on home screen" requires; nothing more. Pretending to be offline-capable when it's not is worse UX than honestly reporting offline. The heartbeat-on-reconnect is borrowed from standard PWA patterns and doesn't accidentally bypass the NetworkOnly cache strategy because heartbeat is explicit.

**Alternatives Considered:**
- Full offline with later-sync — rejected: scope explosion
- No offline shell — rejected: hurts the install-banner Lighthouse score and the perceived-quality bar
- Cache `/api` responses with a TTL — rejected: corrupts realtime workflow status; not worth the partial offline gain

---

### Voice transcription endpoint is rate-limited per user, not per team

**Decision:** New backend rate-limit `voice_transcribe`: 30 requests/minute/user, enforced via Redis-backed sliding window. Over the cap → 429 with `Retry-After` header. No per-team aggregate limit (the cost vector is per-user mic clicks, and per-team aggregation would punish users on busy teams).

**Rationale:** Grok STT API costs real money per call. Without a rate limit, a stuck mic button (network error retry loop) could rack up cost. 30/min/user is generous (one transcription every 2s sustained) but caps abuse. Redis sliding window is the standard pattern; Redis is already wired. Per-team rate limits would be wrong — a team's 10 users each getting 3 transcriptions/min should not collide. System-level absolute cap (`max_voice_transcribes_per_hour_global`) is the operator's safety net, set in `system_settings`.

**Alternatives Considered:**
- No rate limit (trust the user) — rejected: Grok API cost vector
- Per-team rate limit — rejected: unfair coupling
- Per-IP rate limit — rejected: shared NAT collapses teams; user is the right unit
- Token bucket instead of sliding window — rejected: implementation complexity vs sliding window; equivalent UX

---

> See `.gsd/DECISIONS.md` for the full append-only register of all project decisions. M005-oaptsz will append D035–D041 covering the decisions above.

## Error Handling Strategy

User-facing errors get treated as first-class UX in this milestone, not afterthoughts:

- **Mic permission denied:** the `<VoiceInput>` mic button shows a "Mic blocked — re-enable in browser settings" tooltip; clicking the mic again re-prompts in browsers that allow it; falls back gracefully to keyboard-only input
- **Recording failure (mic device unavailable, audio device busy):** toast notification "Couldn't access microphone"; retry button on the toast
- **Transcription failure (Grok API 5xx, network error):** toast "Couldn't transcribe — try again"; the recorded audio is held client-side for one retry attempt; second failure releases the buffer with a "Voice unavailable right now" message
- **Transcription rate limit (429):** toast "Slow down — voice limit reached, wait a few seconds"; uses `Retry-After` for actual countdown
- **Push subscribe failure (browser denied permission):** subtle inline "Notifications disabled — enable in browser settings to receive alerts" near the bell icon; never re-prompt automatically
- **Push delivery failure (subscription expired, HTTP 410 from endpoint):** backend automatically prunes the subscription; user sees nothing; reinstall app to re-subscribe
- **Push delivery failure (other 5xx from push endpoint):** backend logs WARNING with delivery_status; per-subscription consecutive-failure counter; subscription pruned after 5 consecutive failures
- **Service worker registration failure:** non-fatal; app degrades to non-PWA mode (still functional, just not installable); console WARN logs the registration error
- **Service worker update detected:** banner at top of app "A new version is available — refresh to update" with a refresh button; no auto-refresh (user might be mid-task)
- **Notification fetch failure:** bell icon shows neutral state ("?" badge); polling backs off exponentially (5s → 10s → 20s → max 60s) until a successful fetch
- **Offline state:** banner across top; existing in-flight ops fail gracefully; UI doesn't pretend to work
- **Mobile install dismissed:** never re-prompt automatically; an "Install app" link in user settings re-triggers the prompt
- **iOS Safari (no Web Push pre-16.4):** detect via UA + feature check; show "Push notifications require iOS 16.4 or later" inline near the notification preferences UI; in-app notifications continue to work

## Risks and Unknowns

- **iOS Safari PWA quirks.** Until 16.4, no Web Push at all. 16.4+ requires home-screen-installed app for push (browser tabs don't get push). Acceptance scenario 2 ("push notification round-trip") needs iOS 16.4+ to pass on iPhone. Mitigation: document the iOS version requirement in the install UI and the README; acceptance phase tests on a real iOS 17 device; in-app notifications work on all iOS versions as the floor.
- **Mobile Safari MediaRecorder support.** `MediaRecorder` with `audio/webm` is not supported on Safari pre-14.5; iOS Safari supports `audio/mp4` instead. Mitigation: codec detection in `<VoiceInput>` — try `audio/webm;codecs=opus`, fallback `audio/mp4`; backend `voice/transcribe` accepts both and Grok handles either.
- **Grok STT API availability and rate limits at the system level.** A spike in voice usage across teams could hit Grok's per-key rate limits before our per-user limits trip. Mitigation: `max_voice_transcribes_per_hour_global` system setting + 503 with retry-after when the global cap trips; surface as "Voice unavailable right now — try again later" in UI.
- **VAPID key rotation.** If `vapid_private_key` is rotated, all existing subscriptions become unverifiable; users have to re-subscribe. Acceptable; rare event. Mitigation: document the rotation procedure (regenerate key pair, notify users, deploy); UI shows a "Subscribe to notifications" button if no subscription exists.
- **Service worker cache poisoning during updates.** A new app version with a corrupt SW could break the app for installed users until they manually update. Mitigation: vite-plugin-pwa's standard versioned-precache strategy; a safety-net `?bypass-sw=1` query parameter that skips SW entirely (documented in install help); if the situation gets dire, the SW can call `caches.delete()` on detection of a corrupted state.
- **Cross-device read-state consistency.** Eventual within 5s polling cadence. If a user marks a notification read on phone, then closes phone and opens desktop within 4s, the notification will still appear unread for ≤5s. Mitigation: acceptable per the contract; if it becomes annoying, drop poll cadence to 2s.
- **Voice on private/sensitive inputs.** A microphone next to the password field is questionable UX (audio could leak credentials). Mitigation: `<VoiceInput>` accepts a `voiceless` prop; password fields, OTP fields, and any field marked sensitive bypass the voice wrap and render the plain `<Input>`. Explicit allowlist, not blanket coverage.
- **Notification fatigue and the default settings.** R024 says users configure routing per-workflow per-event-type — but a user with 20 workflows configuring 60 toggles is a UX disaster. Default opt-out for `step_completed` (high-volume), default opt-in for `failure` (high-signal); team-default applies until per-workflow override; configuration UI clusters by workflow with sensible defaults. Acceptable starting point; real users will tell us if it's wrong.
- **Bell icon badge accuracy.** Cross-device sync within 5s means the badge might briefly show a wrong count. Acceptable per contract.
- **Mobile keyboard covers form fields.** Common mobile pitfall on the workflow form fields. Mitigation: explicit `scrollIntoView` on focus for forms below the fold; tested via the mobile Playwright suite.
- **PWA cache quota on mobile.** iOS Safari aggressively evicts PWA cache under storage pressure. Mitigation: SW cache size is small (app shell < 5 MB); no per-route cache for `/api`; users can re-fetch on demand.

## Existing Codebase / Prior Art

- `frontend/src/main.tsx` — PWA SW registration entry point lands here
- `frontend/index.html` — manifest link tag, theme-color meta, viewport meta (already present from M001/S04, may need extension)
- `frontend/playwright.config.ts` — existing M001/S04 setup with `pixel-5-mobile-chrome` + `desktop-chrome` projects; M005-oaptsz extends to four projects + adds visual-diff baselines
- `frontend/src/components/ui/input.tsx` and `frontend/src/components/ui/textarea.tsx` — shadcn-style components that `<VoiceInput>` wraps; the upgrade-to-VoiceInput pass touches every consumer
- `frontend/src/components/Common/AuthLayout.tsx` and the dashboard layout components — top bar gets a bell icon button
- `backend/app/api/routes/admin.py` — system settings router; new keys (`vapid_public_key`, `vapid_private_key`, `grok_stt_api_key`, `max_voice_transcribes_per_hour_global`) registered here using M002/S03's per-key validator pattern
- `backend/app/core/encryption.py` — Fernet helpers from M004/S01 reused for `vapid_private_key` and `grok_stt_api_key`
- `backend/app/api/team_access.py` — team access guards reused for any team-scoped notification endpoints (most notifications are user-scoped, but per-team push fan-out for system events lives here)
- `backend/app/api/routes/sessions.py` — pattern for new POST endpoints with multipart upload; `voice/transcribe` borrows from this shape
- M005-sqm8et's `notify(user_id, kind, payload)` integration point — workflow run engine calls this at run start / step complete / run finish; the helper itself is a no-op stub in M005-sqm8et that this milestone replaces with the real implementation
- M002's Redis sliding-window utilities (used for rate limiting on session creation) — extended for `voice_transcribe` rate limit
- M001/S04's mobile responsive patterns — Tailwind breakpoints, touch-target sizing, mobile nav drawer; M005-oaptsz audits and extends these across new screens
- The existing `frontend/src/components/theme-provider.tsx` — dark mode survives all new components

## Relevant Requirements

- R021 — Valid Web App Manifest + service worker; users can install on phone or desktop home screen — **directly delivered by S01 (PWA install + SW)**
- R022 — Every feature accessible/usable on phone screen; touch targets meet mobile standards; no desktop-only flows — **directly delivered by S01 (mobile polish pass) supporting M001/S04's groundwork**
- R023 — Bell icon notification center for workflow + system events; PWA push notifications for backgrounded app — **directly delivered by S02 (notification center) + S03 (push)**
- R024 — User-configurable per-workflow + per-event-type notification routing (in-app, push, none) — **directly delivered by S02 (preferences schema + UI)**
- R025 — Microphone icon on every text input; Grok STT recording + waveform + transcription injection; system-level API key — **directly delivered by S04 (voice component + backend proxy)**
- R022 (continued from M001/S04) — Mobile UX is now a milestone-level acceptance bar, not a per-screen afterthought

## Scope

### In Scope

- **PWA manifest + service worker** via `vite-plugin-pwa` with `injectManifest` strategy
- **Install prompt UX** — banner that appears when browser allows; dismissible; re-promptable from settings
- **App shell offline rendering** — cached static assets + offline banner
- **Per-route SW cache strategy** — static `CacheFirst`, `/api/*` and `/ws/*` `NetworkOnly`, notifications endpoint short-TTL stale-while-revalidate
- **Mobile responsive audit** of every existing screen at 360px and 414px viewport widths (Pixel-5 + iPhone-13 Playwright projects)
- **Touch target enforcement** — every interactive element ≥44x44 CSS pixels; Playwright assertions
- **Visual-diff baselines** for the dashboard, terminal, projects list, run-history, run-detail, workflow editor, and team settings on both mobile and desktop
- **Bell icon notification center** in the top bar; chronological list, mark-as-read, mark-all-read, "show only unread" filter, infinite-scroll pagination
- **`notifications` + `notification_preferences` tables** with Alembic migrations + migration tests
- **`notify(user_id, kind, payload)` helper** — replaces M005-sqm8et's no-op stub; routes to in-app + push channels per preferences
- **Workflow notification preferences UI** on the workflow detail page — per-event-type channel toggles
- **Web Push subscription management** — `POST /api/v1/push/subscribe` and `DELETE /api/v1/push/subscribe`; `push_subscriptions` table with migration; VAPID key generation as a one-time admin action
- **Push delivery from notification dispatcher** — `pywebpush` send to all of the user's subscriptions; subscription pruning on HTTP 410 or 5 consecutive failures
- **`<VoiceInput>` component** wrapping `<Input>` and `<Textarea>` — mic button, recording state, live waveform, transcription injection, error handling
- **`POST /api/v1/voice/transcribe` backend** — multipart upload, Grok STT proxy, rate limit (30/min/user), audit logging (no audio content stored)
- **Codec detection in `<VoiceInput>`** for cross-browser MediaRecorder support
- **Sensitive-input bypass** — password, OTP, and explicitly-marked fields don't get the voice wrapper
- **Universal voice upgrade pass** — every `<Input>` and `<Textarea>` consumer in the app upgrades to `<VoiceInput>` with explicit `voiceless` opt-out where appropriate
- **System settings additions** — `vapid_public_key` (non-sensitive), `vapid_private_key` (sensitive), `grok_stt_api_key` (sensitive), `max_voice_transcribes_per_hour_global` (non-sensitive int)
- **Notification routing defaults** — sane out-of-the-box: `failure` → push + in-app; `succeeded` → in-app only; `step_completed` → none; `team_invite_received` → in-app + push
- **Cross-device read-state sync** via 5s polling
- **Migration tests** for every new table
- **Fast tests** with respx-mocked Grok and respx-mocked Web Push endpoints
- **Integration tests** per slice with real compose stack
- **Acceptance e2e** for the four "Final Integrated Acceptance" scenarios on real devices (iPhone 16.4+, Pixel-class Android, desktop Chrome)

### Out of Scope / Non-Goals

- **Full offline app.** Cached shell only; no offline workflow editing, no offline run replay, no later-sync of edits.
- **Native iOS / Android apps.** PWA is the deployment target; React Native or native shells are out.
- **Per-user voice API keys.** R025 explicitly says system-level only.
- **Voice on every input including search-as-you-type debounced fields.** Where it would degrade UX (search debounce, autocomplete inputs that fire on each keystroke), the field uses `voiceless`.
- **Voice in non-text input components** — date pickers, color pickers, file pickers, etc. don't get voice.
- **Notification email delivery** (R030 — deferred per REQUIREMENTS.md).
- **Notification Slack delivery** (R031 — deferred).
- **Custom push notification icons / actions.** Standard browser notification rendering; no rich actions, custom icons per workflow, etc.
- **Notification history retention policies.** Forever in M005-oaptsz; an ops milestone can add retention if needed.
- **Push notification scheduling / batching.** Send-on-event; no rate limiting per user (push count is naturally capped by workflow run rate from M005-sqm8et's `max_runs_per_hour`).
- **iOS Safari pre-16.4 push.** Documented limitation; in-app notifications still work.
- **Voice-to-action ("Hey perpetuity, run lint")** — STT only; no command interpretation, no wake word.
- **Multi-language STT.** Grok handles whatever Grok handles (likely English-first); UI doesn't expose language selection.
- **Visual-diff baseline coverage of every screen.** Critical pages only (listed above); long tail covered by layout assertions, not visual diff.
- **PWA on Firefox Mobile.** Firefox Mobile PWA support is limited; we test desktop Firefox + mobile Chrome/Safari; Firefox Mobile is best-effort.
- **Background sync APIs** (Workbox `BackgroundSync` for pending API calls when offline) — overkill given the no-offline-app stance.

## Technical Constraints

- **No regression on M001–M005-sqm8et flows.** The mobile audit's job is to find and fix layout breakage introduced by M002–M005-sqm8et screens, not to pass-with-known-issues.
- **Service worker must not cache `/api/*` or `/ws/*`.** This is non-negotiable; a cached run-status response would break M005-sqm8et's polling-driven UI.
- **Voice API key never reaches the browser.** Always proxied through the backend.
- **Push delivery is in-process from the workflow run task.** No new Celery queue for pushes.
- **`<VoiceInput>` must work on mobile Safari, mobile Chrome, desktop Chrome, desktop Safari, desktop Firefox.** Codec fallback (`webm` → `mp4`) is mandatory.
- **Touch targets ≥44x44 CSS pixels** per WCAG 2.1 AA / Apple HIG / Material Design — enforced by Playwright assertion in mobile suite.
- **No horizontal scroll at 360px viewport** — enforced by Playwright assertion on every page.
- **Reuse the existing dark mode** — `theme-provider.tsx` continues to work; new components are theme-aware.
- **Accessibility** — every new interactive element has accessible label, keyboard reachable, screen reader announces state changes (recording / transcribing / done).
- **Reuse M002 redaction discipline** — voice audio bytes never logged; transcription text logged truncated (first 50 chars + length); push payload never includes secret values; subscription endpoint URLs are personal data, logged only as `endpoint_hash=sha256[:8]`.
- **Reuse the M002 + M004 + M005-sqm8et observability taxonomy** — new INFO/WARNING/ERROR keys defined for PWA, push, voice surfaces; redaction sweep extends to grok/grokai key prefixes if any.
- **Image build adds web-push deps to backend image.** `pywebpush` and its dependency `cryptography` (already present); no new system deps.
- **Backend test cwd discipline** continues — `cd backend && uv run pytest` from milestone test scripts.
- **Frontend dev server** continues to be `npm run dev` from `frontend/`; SW dev-mode bypass is a vite-plugin-pwa default.

## Integration Points

- **Mozilla Push Service / FCM / Apple Push Notification Web** (depending on browser) — Web Push delivery endpoints reached via `pywebpush`; not a service we run, just an endpoint we POST to per-subscription
- **Grok STT API** (`api.grok.com` or whatever the documented endpoint is) — system-key REST API for transcription
- **Browser Service Worker runtime** — registered via vite-plugin-pwa's helper; lifecycle managed by browser
- **Browser Notifications API** — used by SW push event handler to show notifications
- **Browser MediaRecorder API** — used by `<VoiceInput>` for audio capture
- **Postgres** — new tables `notifications`, `notification_preferences`, `push_subscriptions`; alembic revisions s12_notifications through s14_push_subscriptions (or whatever sequence the planning phase locks)
- **Redis** — sliding-window key prefix for `voice_transcribe` rate limit; existing prefixes untouched
- **System settings** — new keys `vapid_public_key`, `vapid_private_key`, `grok_stt_api_key`, `max_voice_transcribes_per_hour_global`; admin-only PUT; sensitive keys use the M004/S01 encryption path
- **M005-sqm8et workflow run engine** — `notify(user_id, kind, payload)` integration point at run start / step complete / run finish; M005-oaptsz fills the body
- **Existing M001/S04 mobile Playwright pattern** — extended to four browser projects + visual diff
- **Existing dashboard layout** (top bar) — bell icon button + count badge added; no rewrite

## Testing Requirements

**Unit tests** (per-module, fast):

- vite-plugin-pwa manifest generation produces valid Web App Manifest (Lighthouse-PWA-criteria checker library)
- SW route classification: which routes match `NetworkOnly` vs `CacheFirst` vs stale-while-revalidate
- Notification routing logic: `notify()` resolves preferences correctly across team-default and per-workflow override; in-app channel inserts row; push channel calls `pywebpush.webpush()` for each subscription
- `pywebpush` send-and-prune integration: HTTP 410 prunes the subscription, HTTP 200 updates `last_seen_at`, 5 consecutive 5xx prune
- `<VoiceInput>` codec detection: try `webm` first, fall back to `mp4`, surface error if neither
- Voice transcription rate limit: 30/min/user enforced; over-cap returns 429 with `Retry-After`
- Subscription management: `POST /api/v1/push/subscribe` upserts on `(user_id, endpoint)`, `DELETE` removes
- Notifications API: `GET /api/v1/notifications?since=` correctly filters, paginates; `POST /{id}/read` flips `read_at`

**Migration tests** (per-revision):

- s12_notifications, s13_notification_preferences, s14_push_subscriptions upgrade/downgrade round trips with the M001 session-fixture release pattern

**Integration tests** (per-slice, real compose stack):

- S01 (PWA + mobile): app loads with valid manifest; SW registers; SW caches static assets but bypasses `/api`; install banner appears on mobile Chrome; mobile Playwright suite passes every existing flow at Pixel-5 + iPhone-13 viewports; touch targets ≥44x44 on every interactive element
- S02 (notification center): workflow run completion triggers `notify()`; in-app row appears; bell icon badge increments; mark-as-read flips state; cross-device read-state sync within 5s; per-workflow preference override works
- S03 (push): browser subscribes via `POST /api/v1/push/subscribe`; backend stores subscription; workflow failure triggers push delivery; respx-mocked Mozilla Push Service receives correct VAPID-signed POST; HTTP 410 from mocked endpoint prunes subscription
- S04 (voice): `<VoiceInput>` records audio via Playwright's permission-grant API; multipart upload to `/api/v1/voice/transcribe`; respx-mocked Grok returns transcription; text injects into field; rate limit 30/min/user enforced; codec fallback works on mocked Safari user agent

**Acceptance e2e** (real devices, manual):

- iPhone 16.4+ install + push round-trip
- Pixel-class Android install + push round-trip
- Desktop Chrome cross-device read-state sync with mobile
- Real Grok STT API transcription on mobile Safari + desktop Chrome
- Mobile audit pass: complete every existing flow on real iPhone + Android, no breakage

**Per-slice run-time budget** ≤30s for fast/integration tests; acceptance phase has no time budget.

**Redaction sweep** extension: M005-oaptsz e2es grep `docker compose logs` for the Grok API key prefix (whatever it is — confirm in S04 planning), the VAPID private key first 8 chars, raw audio bytes (binary, so the grep is for the multipart boundary survival), notification subscription endpoint URLs (logged only as hashes).

## Acceptance Criteria

- **S01 — PWA install + mobile polish:** valid Web App Manifest passes Lighthouse PWA criteria; SW registers and caches app shell; SW bypasses `/api/*` and `/ws/*`; install banner appears on mobile Chrome; offline shell renders; mobile Playwright suite (Pixel-5 + iPhone-13 + desktop Chrome + desktop Firefox) passes every existing flow; touch targets ≥44x44; no horizontal scroll at 360px on any page; visual-diff baselines pass
- **S02 — Notification center + preferences:** bell icon in top bar shows count of unread; clicking opens chronological list with infinite scroll; mark-as-read and mark-all-read work; "show only unread" filter; cross-device sync within 5s; per-workflow per-event-type preference UI; team-default preference applies until per-workflow override; sane defaults (failure → push, success → in-app, step_completed → none)
- **S03 — Web Push:** browser subscribes with VAPID public key; backend stores subscription; workflow failure triggers push delivery via `pywebpush`; subscription pruned on HTTP 410; multi-device delivery works (subscribe from phone + laptop, both receive); permission-denied path shows graceful inline message; VAPID keys generated and stored in `system_settings` via admin action
- **S04 — Voice input universal:** `<VoiceInput>` wraps every text input in the app (with `voiceless` opt-out for sensitive fields); mic button shows on every wrapped input; recording shows live waveform; transcription injects within 1.5s on a fast connection; rate limit 30/min/user enforced; codec fallback works (webm primary, mp4 secondary); Grok STT API key in `system_settings`, encrypted at rest; permission-denied path shows graceful tooltip
- **S05 — Acceptance + redaction sweep:** four "Final Integrated Acceptance" scenarios pass on real devices; redaction sweep clean across all M005-oaptsz logs; all M005-oaptsz integration tests pass within budget; production-style readiness check (full compose stack + real Mozilla Push Service + real Grok API + real iPhone + real Android + real desktop Chrome)

## Open Questions

- **Slice count.** Five slices (S01 PWA+mobile, S02 notification center, S03 push, S04 voice, S05 acceptance) is the current shape. Could collapse to four (S01 PWA, S02 notifications-and-push, S03 voice, S04 acceptance) — the notification center and push share enough infrastructure that slicing them apart is partly artificial. Confirm in milestone planning.
- **VAPID key generation flow.** One-time admin action via `POST /api/v1/admin/system_settings/vapid_keys/generate` (similar to M004's webhook secret generate-and-display-once flow) is the current thinking. Returns the keypair once; subsequent operations use stored values. Confirm in S03 planning.
- **Notification preferences scope: per-team or per-user.** R024 says "users can configure", suggesting per-user. But team admins might want a team-default override? Current thinking: per-user only in M005-oaptsz; team defaults out of scope. Worth a 5-minute discussion.
- **Real-time bell-icon badge update mechanism.** 5s polling is fine for the panel; but a notification arriving while the user has the app open should ideally update the badge faster than 5s. Options: (a) accept 5s lag (b) use the same polling cadence (c) add a lightweight WS channel for badge-only updates. Current thinking: (a) — 5s is fine; the panel re-renders on next poll. Confirm in S02 planning.
- **iOS Safari install banner UX.** Browsers can't programmatically trigger install on iOS; users have to manually use the share menu. Mitigation: show a one-time toast with installation instructions for iOS users on first visit. Confirm in S01 planning.
- **`<VoiceInput>` accessibility on screen readers.** Mic button needs an accessible label that announces state ("Start recording" / "Recording" / "Stop and transcribe" / "Transcribing"). Confirm naming in S04 planning.
- **Notification persistence in service worker.** Push events arrive at the SW, which calls `self.registration.showNotification()`. Should the SW also notify the open-tab app to refresh the notification panel? Current thinking: yes, via `BroadcastChannel` — keeps the panel current without waiting for poll. Confirm in S03 planning.
- **Visual-diff tolerance threshold.** Pixel-perfect is too brittle (font rendering varies); 5% pixel diff is too lax. Current thinking: 1% pixel diff threshold via Playwright's `toHaveScreenshot` defaults. Confirm in S01 planning.
- **Mobile audit fix-it cadence.** The audit will surface a list of mobile issues across M002–M005-sqm8et screens. Should fixes be one slice (S01 broad fix-pass) or split per-screen? Current thinking: bundle into S01 — they're all small fixes; per-screen splits adds ceremony for no benefit. Confirm in milestone planning.
- **Acceptance phase device list.** iPhone (which model?), Android (which model?), desktop browsers (which set?). Current thinking: iPhone 13/14/15 + Pixel 6/7 + desktop Chrome/Safari/Firefox latest stable. Owner-confirms-availability is the gating question.
