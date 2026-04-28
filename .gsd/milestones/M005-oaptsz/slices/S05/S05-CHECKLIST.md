# S05 Real-Device Acceptance Checklist — M005-oaptsz

**Milestone:** M005-oaptsz (PWA, Notifications & Voice)
**Slice:** S05 — Acceptance + Redaction Sweep
**Prepared:** 2026-04-28
**Status:** PENDING OPERATOR SIGN-OFF

This checklist is the **operator UAT script** for the four Final Integrated Acceptance scenarios required to close M005-oaptsz. Each scenario must be executed on real devices. Emulators, simulators, and headless browsers do not satisfy these checks.

---

## Device Prerequisites

| Role | Device | Browser | Minimum OS | Notes |
|------|--------|---------|-----------|-------|
| Android primary | Pixel 6 / 7 (or equivalent) | Chrome (latest stable) | Android 12+ | Used for Scenarios 1 + 4 |
| iPhone | iPhone 13/14/15 | Safari (built-in) | iOS 16.4+ | **16.4+ required for push** — see Known Limitations |
| Desktop | Any machine | Chrome (latest stable) | Any | Used for cross-device verification in Scenarios 2 + 4 |

> **Pre-session environment check:**
> - Full compose stack running: `docker compose up -d` (Postgres + Redis + orchestrator + Celery worker + backend + frontend)
> - Backend accessible at `https://<your-host>:8000` (or the configured URL)
> - Admin user credentials available
> - VAPID keys configured (see admin panel or run `POST /api/v1/admin/system_settings/vapid_keys/generate` if not yet set)
> - Grok STT API key configured in admin settings (`grok_stt_api_key`)

---

## Scenario 1: Mobile Install + Use (Android Chrome)

**Goal:** Verify the PWA install banner appears, the home-screen icon launches the app standalone, and the full demo flow completes on a real Android device.

### Prerequisites

- Android device (Pixel 6/7 or equivalent) with Chrome latest stable
- App URL reachable over HTTPS from the device (self-signed certs must be trusted in Chrome first)
- User account credentials (can be the admin account)

### Steps

1. Open Chrome on the Android device and navigate to the app URL.
2. Use the app normally for 15–30 seconds: log in, view the dashboard, navigate to a project.
3. **Verify:** The install banner ("Add Perpetuity to Home screen" or equivalent Chrome install prompt) appears within 30 seconds of first meaningful interaction.
4. Tap the install banner / "Install" button.
5. **Verify:** The home-screen icon for "Perpetuity" (or the configured app name) appears on the Android home screen.
6. Close Chrome entirely (swipe away from recents).
7. Tap the home-screen icon.
8. **Verify:** The app opens in **standalone mode** — no Chrome address bar, no browser chrome, full-screen app window.
9. Complete the full demo flow:
   a. Log in (if not already authenticated).
   b. Navigate to the Workflows section.
   c. Select or create a workflow that calls Claude (e.g., "List files in project").
   d. Tap the mic icon next to a text prompt field and speak the prompt (or type it).
   e. Trigger the workflow run.
   f. Navigate to the Run detail page and verify it shows the run in progress or completed.
   g. Navigate to Run History and confirm the run appears.
10. **Verify:** No horizontal scroll at any step. All interactive elements are reachable by touch. No layout overflow or clipped content.

### Success Criterion

- Install banner appeared within 30s of normal use ✓
- Home-screen icon present ✓
- App opens standalone (no browser chrome) ✓
- Full demo flow completed without layout breakage ✓

### If It Fails

- **Banner doesn't appear:** Check that the app is served over HTTPS, has a valid `manifest.webmanifest` (test at `<url>/manifest.webmanifest`), and the service worker is registered (`chrome://inspect` → Remote devices → Service Workers). The Lighthouse PWA audit run from desktop Chrome DevTools against the mobile URL will show which criterion is missing.
- **Standalone mode doesn't activate:** Confirm the manifest has `"display": "standalone"` and the `start_url` matches the URL Chrome is adding to the home screen.
- **Layout breakage:** Note the screen (URL) and describe the issue. File as a mobile-polish follow-up. The milestone is blocked only if the demo flow cannot be completed at all.

---

## Scenario 2: Push Notification Round-Trip (iPhone 16.4+ Safari)

**Goal:** Verify a Web Push notification is delivered to a real iPhone within 30 seconds of a workflow failure, and tapping it opens the run-detail page.

### Prerequisites

- iPhone running **iOS 16.4 or later** with Safari (built-in)
- App must be **installed to the home screen first** — Web Push on iOS requires the app to be running as an installed PWA, not in a browser tab
  - To install on iOS: open app in Safari → tap Share icon → "Add to Home Screen"
- Notification permission must be granted (the app will prompt when you subscribe)
- VAPID keys must be configured in admin settings (`POST /api/v1/admin/system_settings/vapid_keys/generate` if not done yet)
- A test workflow configured with `failure → push` notification routing (configure in the workflow's notification preferences UI)
- Desktop Chrome open and logged in as the **same user** for cross-device delivery confirmation

### Steps

1. **On iPhone:** Open the installed PWA from the home-screen icon.
2. Navigate to the Notifications preferences for a workflow (Workflow detail page → "Notification Settings").
3. Set `workflow_run_failed` → **Push + In-app**.
4. Subscribe to push notifications: click the bell icon → "Enable push notifications" → grant permission when the browser prompts.
5. **Verify on iPhone:** The bell icon shows no error state. The subscription was accepted.
6. Background the app on iPhone (swipe up, don't close).
7. **On desktop Chrome (same user):** Trigger the configured workflow to fail. Use an invalid API key or a step that is guaranteed to fail (e.g., set the Anthropic API key to `invalid-key-for-test`).
8. Wait up to **30 seconds**.
9. **Verify on iPhone:** A push notification appears on the lock screen or notification center with a message like "Workflow [name] failed at step [n]: [reason]".
10. Tap the notification.
11. **Verify:** The Perpetuity PWA opens (or foregrounds) and navigates directly to the **run-detail page** for the failed run.
12. **Verify on desktop Chrome:** The same notification appears in the bell icon panel for the same user and is marked as unread.

### Success Criterion

- Push notification received on iPhone within 30 seconds of workflow failure ✓
- Tapping notification navigates to the correct run-detail page ✓
- Same notification visible (unread) in desktop bell icon panel ✓

### If It Fails

- **Push not received:** Check the backend logs for `pywebpush` delivery attempt. Look for HTTP 410 (expired subscription — re-subscribe) or 5xx errors (Mozilla Push Service issue). Confirm VAPID keys are correctly set in admin settings.
- **No notification permission shown:** iOS requires the app to be installed as a PWA (home-screen icon). Browser tab push is not supported on iOS regardless of iOS version.
- **Notification received but tapping doesn't navigate:** Check the service worker's `notificationclick` handler. Open the app and navigate manually to the run-detail URL to confirm routing works.
- **iOS Safari < 16.4:** Web Push is not supported. See Known Limitations below.

---

## Scenario 3: Voice Prompt on Real Mobile Safari (iPhone)

**Goal:** Verify that the voice input component works on real iOS Safari — mic access, waveform, Grok transcription within 1.5s, and workflow execution.

### Prerequisites

- iPhone (same device from Scenario 2, iOS 16.4+ Safari) **or** a second iOS device
- Grok STT API key configured in admin settings (`grok_stt_api_key`) — encrypted at rest via Fernet
- Microphone permission not yet permanently denied for this app in iOS Settings → Privacy → Microphone (if denied, revoke and re-test)
- A workflow that accepts a text prompt (e.g., the Claude "List files" workflow used in Scenario 1)

### Steps

1. On the iPhone, open the app (installed PWA or Safari tab — both work for voice; push is the only feature requiring installed PWA on iOS).
2. Navigate to the workflow that accepts a Claude prompt.
3. Locate the text prompt input field. **Verify:** A microphone icon is visible next to the field.
4. Tap the microphone icon.
5. **Verify:** The browser shows a permission prompt "Allow [app] to use your microphone?" (first use only).
6. Tap **Allow**.
7. **Verify:** A live audio waveform animation appears in or near the input field, confirming recording is active.
8. Speak clearly: "List the files in this project" (or another short phrase).
9. Tap the Stop button (the mic icon changes to a stop/square button during recording).
10. **Verify (timing):** The transcribed text appears in the prompt field **within 1.5 seconds** of tapping stop.
11. **Verify (accuracy):** The transcribed text matches what was spoken (minor word differences acceptable; gross failure is a blank field or error).
12. Tap **Run** to execute the workflow.
13. **Verify:** The workflow executes and returns real output from the Anthropic API (file listings or equivalent for the chosen prompt).

### Success Criterion

- Microphone permission prompt appeared and was granted ✓
- Live waveform visible during recording ✓
- Transcription appeared in field within 1.5s ✓
- Transcribed text is recognizable as what was spoken ✓
- Workflow executed successfully against real Anthropic API ✓

### If It Fails

- **No mic icon visible:** Confirm the input field is using `<VoiceInput>` (not `voiceless`). Check the admin settings for `grok_stt_api_key` — if the key is missing, the backend may disable voice.
- **Permission prompt appears but recording fails:** On iOS Safari, MediaRecorder uses `audio/mp4` codec (not `audio/webm`). The app should auto-detect and fall back. Check browser console for codec errors.
- **Transcription times out or returns error:** Check backend logs for the Grok API response. A 429 from Grok means the system-level rate limit was hit; a 5xx means the Grok service is unavailable. The app should show a toast "Couldn't transcribe — try again".
- **Transcription appears but is blank:** Check that the audio upload completed (network tab in Safari Web Inspector) and that the backend's `POST /api/v1/voice/transcribe` returned a non-empty `text` field.

---

## Scenario 4: Cross-Device Read State Sync

**Goal:** Verify that dismissing a notification on one device marks it read on a second device within 5 seconds, in both directions.

### Prerequisites

- Two active sessions for the **same user** simultaneously:
  - **Phone session:** iPhone or Android (Chrome or Safari) with the app open
  - **Desktop session:** Chrome (or any desktop browser) with the app open in a separate window/tab
- At least **two unread notifications** visible in the bell icon panel (trigger workflow runs to generate them, or use notifications from Scenarios 1–3)
- Both sessions should be on the same network (or have consistent access to the backend)

### Steps

**Direction A: Phone → Desktop**

1. On the **phone**, open the notification bell panel.
2. **Note** the names/IDs of 1–2 unread notifications visible on both devices.
3. On the phone, tap one notification to mark it as read (or use "Mark as read" action).
4. **Start a 5-second timer.**
5. On the **desktop**, observe the bell icon panel (it should be open or refresh it).
6. **Verify within 5 seconds:** The same notification is now shown as **read** (no bold text / unread indicator) on desktop.
7. **Verify:** The unread count badge on the desktop bell icon has decremented accordingly.

**Direction B: Desktop → Phone**

8. On the **desktop**, tap a different unread notification to mark it as read.
9. **Start a 5-second timer.**
10. On the **phone**, observe the bell icon panel.
11. **Verify within 5 seconds:** The same notification is now shown as **read** on phone.

**Mark All As Read (bonus check)**

12. On either device, click **"Mark all as read"**.
13. **Verify within 5 seconds:** The other device's bell icon badge drops to 0 and all notifications appear read.

### Success Criterion

- Direction A (phone → desktop): read state propagates within 5s ✓
- Direction B (desktop → phone): read state propagates within 5s ✓
- Unread count badge is accurate on both devices after sync ✓
- "Mark all as read" propagates to the other device within 5s ✓

### If It Fails

- **Read state not propagating within 5s:** The bell icon polls every 5s. If it's been more than 5s and still unread, open browser DevTools on the lagging device and check the Network tab for `GET /api/v1/notifications?since=...` requests. If no requests are appearing, the polling loop may have stopped (check for JS errors). If requests appear but the response still shows `read_at: null`, the `POST /{id}/read` request may not have persisted (check backend logs).
- **Badge count wrong:** The unread count is derived from the notification list response. A discrepancy usually means the client is using a stale `since` cursor. Hard-refresh the page to reset the cursor.
- **"Mark all as read" doesn't propagate:** Check that `POST /api/v1/notifications/read_all` is being called and returns 200. The other device will pick it up on the next poll.

---

## Automated Regression Evidence (Pre-UAT)

Before starting real-device UAT, confirm the following automated checks have passed:

| Check | Command | Expected |
|-------|---------|---------|
| Backend regression | `cd backend && POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app pytest tests/ -q` | 70+ passed, 0 failed |
| Frontend build | `cd frontend && bun run build` | Exit 0, no TS errors |
| Redaction sweep | `bash scripts/redaction-sweep.sh` | Exit 0, all PASS |
| Mobile audit spec | `cd frontend && bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts` | 15+ passed |
| SW bypass spec | `cd frontend && bunx playwright test --project=m005-oaptsz-sw m005-oaptsz-sw-bypass.spec.ts` | 1 passed |
| Notifications spec | `cd frontend && bunx playwright test --project=chromium m005-oaptsz-notifications.spec.ts m005-oaptsz-notifications-preferences.spec.ts` | 4+ passed |
| Push spec | `cd frontend && bunx playwright test --project=m005-oaptsz-push m005-oaptsz-push.spec.ts` | 2+ passed |
| Voice spec | `cd frontend && bunx playwright test --project=mobile-chrome m005-oaptsz-voice.spec.ts` | 6 passed |

---

## Known Limitations

### iOS Safari < 16.4 — No Web Push Support

Web Push notifications are **not available** on iOS Safari before version 16.4. This is a platform limitation (Apple added Web Push to iOS in iOS 16.4). Users on iOS < 16.4 will see a message "Push notifications require iOS 16.4 or later" in the notification preferences UI. In-app notifications (bell icon panel) continue to work on all iOS versions.

**Workaround for acceptance testing:** Use an iPhone running iOS 16.4 or later. If no such device is available, Scenario 2 cannot be fully validated on iOS — document this gap in the acceptance sign-off.

### /admin/teams DataTable Chevron — MEM369 (Pre-Existing)

Two test failures in the mobile-chrome Playwright suite for `/admin/teams` DataTable expand/collapse chevrons are **pre-existing and documented as MEM369**. They do not represent a regression introduced in M005-oaptsz. These failures are acceptable in the automated gate (15+ of 17 tests passing is the bar, not 17/17).

**Impact:** The teams admin table's row-expand UI has an existing touch-target issue on mobile. This does not affect the core acceptance scenarios (the demo flow in Scenario 1 does not require the teams DataTable expand).

### Real Push Requires VAPID Keys Configured in Admin Settings

Web Push delivery requires VAPID keys to be generated and stored in `system_settings`. If VAPID keys are not configured, the backend will return an error on push subscription attempts and no pushes will be delivered.

**To configure:** In the admin panel, navigate to System Settings → Push Notifications → "Generate VAPID Keys" (or `POST /api/v1/admin/system_settings/vapid_keys/generate`). This is a one-time action. The public key is served to browsers; the private key is Fernet-encrypted at rest.

**Key rotation warning:** Rotating VAPID keys invalidates all existing push subscriptions. Users will need to re-subscribe after a key rotation. There is no automatic re-subscription — users will be prompted on next visit to the notification preferences UI.

---

## Sign-Off

| Scenario | Device | OS / Browser | Tester | Date | Result | Notes |
|----------|--------|-------------|--------|------|--------|-------|
| 1 — Mobile install + use | Android (Pixel class) | Android 12+ / Chrome | | | ☐ Pass / ☐ Fail | |
| 2 — Push round-trip | iPhone 16.4+ | iOS 16.4+ / Safari PWA | | | ☐ Pass / ☐ Fail | |
| 3 — Voice prompt | iPhone | iOS / Safari | | | ☐ Pass / ☐ Fail | |
| 4 — Cross-device read sync (Phone→Desktop) | Android + Desktop | Chrome | | | ☐ Pass / ☐ Fail | |
| 4 — Cross-device read sync (Desktop→Phone) | Desktop + Android | Chrome | | | ☐ Pass / ☐ Fail | |
| Redaction sweep (all logs) | — | — | | | ☐ Pass / ☐ Fail | |

**Milestone close requires:** All 6 rows marked Pass (or any Fail row has a documented waiver with owner sign-off).
