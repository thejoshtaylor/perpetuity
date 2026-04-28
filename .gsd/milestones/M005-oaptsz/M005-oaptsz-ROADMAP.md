# M005-oaptsz: PWA, Notifications & Voice - UX layer that makes Perpetuity feel like a real phone product

**Vision:** Convert the functionally-complete platform into a phone-deployable product: install-on-home-screen PWA with a correct service-worker boundary, a unified notification center with cross-device read state, real Web Push delivery on workflow events, and a microphone next to every text input that transcribes via Grok STT. When this milestone closes, a user can install the app on their phone, configure a workflow to push-notify on failure, dictate the next prompt by voice, and complete every existing flow on a 360px viewport with touch — without typing on a keyboard if they don't want to.

## Success Criteria

- Frontend ships a valid Web App Manifest and a Workbox-injected service worker; install banner appears on mobile Chrome and the home-screen-launched standalone window opens the full app (R021)
- Service worker caches the app shell with CacheFirst but uses NetworkOnly for /api/* and /ws/* — proven by an integration test that mutates a fixture API response and observes the fresh value through a SW-mediated fetch (no cached corruption of M005-sqm8et polling)
- Every existing flow (auth, dashboard, terminal, projects, GitHub admin, workflow editor, run history, run detail) passes a Playwright run on Pixel-5 + iPhone-13 + desktop-Chrome + desktop-Firefox projects with no horizontal scroll at 360px, all interactive targets ≥44x44 CSS pixels, and visual-diff baselines within 1% (R022)
- Bell icon in the top bar opens a chronological notification list with mark-as-read, mark-all-read, and show-only-unread filter; cross-device read state syncs within 5s via 5-second polling (R023)
- Per-workflow per-event-type notification routing (in-app / push / both / none) configurable from the workflow detail page; team-default applies until per-workflow override; sane defaults (failure→push+in-app, success→in-app, step_completed→none) (R024)
- Web Push subscription persists in push_subscriptions on POST /api/v1/push/subscribe; backend dispatches via pywebpush from inside the workflow run task; HTTP 410 from the push endpoint prunes the subscription automatically; multi-device delivery works (phone + laptop both subscribed → both notified) (R023)
- <VoiceInput> wraps every text input in the app (Input + Textarea consumers) with explicit voiceless opt-out for password/OTP/sensitive fields; mic click → permission prompt → live waveform → multipart upload to /api/v1/voice/transcribe → text injects via the wrapped onChange handler within 1.5s on a fast connection (R025)
- Grok STT API key + VAPID private key stored in system_settings as sensitive (Fernet-encrypted via M004/S01), never round-tripped to UI; voice transcription rate-limited at 30 req/min/user via Redis sliding window with 429 + Retry-After when exceeded (R025)
- Four real-device acceptance scenarios pass: mobile install + use; push round-trip phone→backgrounded→tap→run-detail; voice prompt on mobile to a real Anthropic workflow; cross-device read-state sync within 5s
- Redaction sweep over backend + orchestrator + frontend logs returns zero matches for Grok API key prefix, VAPID private key first 8 chars, raw audio multipart boundaries, or push subscription endpoint URLs (logged only as endpoint_hash=sha256[:8])

## Slices

- [x] **S01: S01** `risk:Service worker mis-classifies routes and caches /api/* responses, silently corrupting M005-sqm8et's run-status polling. The mobile audit may surface large structural regressions across M002–M005-sqm8et screens that exceed S01's slack budget. iOS Safari install UX requires a separate one-time-toast path because browsers can't programmatically trigger install on iOS.` `depends:[]`
  > After this: On a Pixel-class Android device running mobile Chrome, the user opens the app, the install banner appears within 30s, tapping it installs the app, the home-screen icon opens the app standalone, and the user can complete the full existing demo flow (login → dashboard → terminal → projects → run history) without horizontal scroll, with all touch targets ≥44x44 CSS pixels, while the service worker correctly bypasses /api/* and /ws/* requests (proven by an integration test that flips a fixture API response between two SW-mediated fetches and observes the new value).

- [x] **S02: S02** `risk:Notification preferences UI overwhelms users with N×M toggles — mitigated by shipping team-default-per-event-type only (workflow_id NULL); per-workflow override surface is schema-supported but UI lands when workflows exist. Cross-device read-state via 5s polling is acceptable per the CONTEXT decision; the SW NetworkOnly /api/* contract from S01 means polling is never silently cached. M005-sqm8et never shipped its stub `notify()` helper or workflow run engine, so S02 owns the helper end-to-end and wires only the call sites that exist today (team_invite_accepted, project_created, system); workflow-event call sites remain unwired until the workflow engine slice ships.` `depends:[]`
  > After this: User clicks the bell icon in the top bar, sees a chronological list of notifications (today's seed: team invite accepted, project created, plus an admin-triggered system test event) with unread badge count, mark-as-read, mark-all-as-read, and show-only-unread filter. On a Notification Preferences settings panel, the user toggles team-default per-event-type in-app routing (e.g. team_invite_accepted → off; project_created → on); save propagates and subsequent events respect the preferences. With two browser contexts open for the same user, marking a notification read in one context propagates to the other within 5s (5s polling cadence).

- [x] **S03: S03** `risk:Real-device push delivery is the highest-risk surface in this milestone — only real Mozilla Push Service / FCM round-trip proves the keys, the SW push event handler, and the notification permission flow line up. VAPID key rotation collides with cached subscriptions if not handled deliberately. iOS Safari < 16.4 has no push support and the UX must degrade gracefully.` `depends:[]`
  > After this: User opens the app on a phone, grants notification permission via the prompt UX, configures workflow X with `failure → push`, backgrounds the app, triggers workflow X to fail, and receives a Web Push notification on the device within 30s; tapping the notification opens the app to the run-detail page. Same flow on a desktop browser confirms cross-device delivery. With a subscription that has been deleted upstream (browser uninstalled the PWA), the next push delivery returns HTTP 410, and the subscription row is automatically pruned without operator intervention.

- [x] **S04: S04** `risk:The universal-coverage upgrade pass touches every <Input> and <Textarea> consumer; missing one violates R025, mis-applying it to a password/OTP/debounced field is a security or UX incident. Cross-browser MediaRecorder codec support requires explicit fallback (webm → mp4). The Grok STT API surface and exact key prefix need confirmation in slice planning to extend the redaction sweep.` `depends:[]`
  > After this: User clicks the microphone icon next to any text input in the app — login email, Claude prompt, Codex prompt, workflow form field, project search, team invite email — grants mic permission once, sees a live waveform during recording, taps stop, and watches the transcribed text appear in the field within 1.5s on a fast connection. Password, OTP, and explicitly-marked sensitive fields render the plain <Input> with no mic button. A 31st transcription request inside one minute returns 429 with a Retry-After header and the UI shows a graceful inline message.

- [x] **S05: S05** `risk:Real-device behavior on iOS Safari + Android Chrome may surface UX gaps that mocked tests can't catch (install banner copy on iOS, push permission flow on Android, voice mic UX on small screens). Redaction sweep may surface a leak that requires going back to fix logging in S01–S04.` `depends:[]`
  > After this: Operator runs the four 'Final Integrated Acceptance' scenarios from the milestone CONTEXT on real devices and records the results: (1) install + use on real Pixel-class Android — install banner appears, home-screen icon launches standalone, full demo flow completes; (2) push notification round-trip on real iPhone 16.4+ — subscribe, configure failure→push, background app, trigger failure, receive push within 30s, tap → run-detail; (3) voice prompt on real mobile Safari — tap mic, speak, see real Grok transcription within 1.5s, run workflow against real Anthropic API; (4) cross-device read state sync — dismiss notification on phone, see it marked read on desktop bell panel within 5s. Redaction sweep grep over all backend + orchestrator + frontend dev-server logs returns zero matches for Grok API key prefix, VAPID private key first 8 bytes, multipart audio boundaries, or raw push endpoint URLs.

## Boundary Map

## Boundary Map

### Frontend
- `frontend/src/sw.ts` (new) — service worker source; injectManifest target
- `frontend/public/manifest.webmanifest` (new) — Web App Manifest
- `frontend/vite.config.ts` — register vite-plugin-pwa with injectManifest strategy
- `frontend/src/main.tsx` — SW registration call (vite-plugin-pwa helper)
- `frontend/index.html` — manifest link tag, theme-color meta, apple-touch-icon
- `frontend/src/components/Common/InstallBanner.tsx` (new) — beforeinstallprompt UX
- `frontend/src/components/Common/OfflineBanner.tsx` (new) — navigator.onLine state
- `frontend/src/components/notifications/NotificationBell.tsx` (new) — top-bar bell button
- `frontend/src/components/notifications/NotificationPanel.tsx` (new) — list + mark-read
- `frontend/src/components/notifications/NotificationPreferences.tsx` (new) — workflow-detail-page section
- `frontend/src/components/notifications/PushPermissionPrompt.tsx` (new)
- `frontend/src/components/voice/VoiceInput.tsx` (new) — Input wrapper
- `frontend/src/components/voice/VoiceTextarea.tsx` (new) — Textarea wrapper
- `frontend/src/components/voice/Waveform.tsx` (new) — AnalyserNode visualizer
- `frontend/src/components/voice/useVoiceRecorder.ts` (new) — MediaRecorder hook
- `frontend/src/components/ui/input.tsx` and `frontend/src/components/ui/textarea.tsx` — wrapped by VoiceInput; not modified
- `frontend/playwright.config.ts` — extend to four projects (pixel-5, iphone-13, desktop-chrome, desktop-firefox); add visual-diff baselines

### Backend
- `backend/app/models.py` — new tables: notifications, notification_preferences, push_subscriptions
- `backend/app/alembic/versions/s12_notifications.py` (new)
- `backend/app/alembic/versions/s13_notification_preferences.py` (new)
- `backend/app/alembic/versions/s14_push_subscriptions.py` (new)
- `backend/app/api/routes/notifications.py` (new) — GET list, POST {id}/read, POST read_all
- `backend/app/api/routes/push.py` (new) — POST /push/subscribe, DELETE /push/subscribe
- `backend/app/api/routes/voice.py` (new) — POST /voice/transcribe (multipart)
- `backend/app/api/routes/admin.py` — register vapid_public_key, vapid_private_key, grok_stt_api_key, max_voice_transcribes_per_hour_global keys; vapid_keys/generate one-shot endpoint
- `backend/app/core/notify.py` (new) — notify(user_id, kind, payload, source_*) helper
- `backend/app/core/push_dispatch.py` (new) — pywebpush send + prune logic
- `backend/app/core/grok_stt.py` (new) — Grok REST client (multipart upload, system key)
- `backend/app/core/rate_limit.py` — extend with voice_transcribe sliding window
- `backend/app/main.py` — mount new routers

### Orchestrator / Workflow run engine (M005-sqm8et)
- M005-sqm8et's notify() integration point at run start / step complete / run finish — call sites stay; M005-oaptsz fills the helper body

### System Settings keys (registered in admin.py _VALIDATORS)
- `vapid_public_key` — non-sensitive, JSONB string, served to browsers via /api/v1/push/vapid_public_key
- `vapid_private_key` — sensitive (Fernet-encrypted), generate-only via vapid_keys/generate one-shot
- `grok_stt_api_key` — sensitive (Fernet-encrypted), paste-once
- `max_voice_transcribes_per_hour_global` — non-sensitive int, operator safety net

### External integrations
- Mozilla Push Service / FCM / APNs Web (per-browser) — POST per subscription via pywebpush
- Grok STT REST API — POST multipart audio, returns {text}
- Browser SW runtime, Notifications API, MediaRecorder API
