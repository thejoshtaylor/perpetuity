# S04: Voice input universal — VoiceInput wrapper + Grok STT proxy — UAT

**Milestone:** M005-oaptsz
**Written:** 2026-04-28T19:15:37.509Z

# S04 UAT: Voice Input Universal Coverage

## Preconditions
- App running at http://localhost:5173 (dev) or :4173 (preview)
- Logged in as seeded superuser (admin@example.com)
- Browser with microphone access (real or mocked)
- Backend running with `grok_stt_api_key` configured in admin settings (real or test key)
- Redis available for rate limiting

---

## Test Case 1: Mic button present on eligible inputs

**Steps:**
1. Navigate to the login page `/login`
2. Inspect the email input field
3. Navigate to the dashboard and open a workflow Claude prompt field
4. Navigate to the team invite page and inspect the invite email field
5. Navigate to project search

**Expected:** A mic icon button (≥44×44 CSS px) appears next to each of these fields. The button has an accessible `aria-label` containing "Record" or similar.

---

## Test Case 2: Mic button absent on sensitive inputs

**Steps:**
1. Navigate to `/login` and inspect the password input
2. Navigate to `/admin/settings` and inspect system-secret credential fields (e.g., `grok_stt_api_key` value input)
3. Inspect any OTP/MFA input fields

**Expected:** No mic button appears next to password, OTP, or explicitly sensitive fields. These fields render as plain Input with no voice wrapper UI.

---

## Test Case 3: Mic permission request and recording flow

**Steps:**
1. Navigate to any page with an eligible text input (e.g., Claude prompt field)
2. Click the mic button
3. Browser prompts for microphone permission — grant it
4. Observe the waveform animating during recording
5. Click the stop button (or wait for auto-stop)

**Expected:** Permission prompt appears once on first use. Waveform renders and animates. Recording stops cleanly. Mic state resets after recording ends.

---

## Test Case 4: Transcript injection into field

**Steps:**
1. Navigate to a workflow Claude prompt field
2. Click the mic button, grant permission, speak a phrase, stop
3. Observe the field value after transcription completes

**Expected:** Transcribed text appears in the input field within 1.5s on fast connection. If field had existing text, transcript is appended (or replaces, per documented behavior). The `onChange` handler fires correctly — react-hook-form validation state updates.

---

## Test Case 5: Permission denied — inline error

**Steps:**
1. Open browser settings and deny microphone permission for localhost
2. Navigate to any page with a mic button
3. Click the mic button

**Expected:** An inline error message appears below the field (e.g., "Microphone access denied"). The existing field value is preserved. The error is dismissable or clears on next click.

---

## Test Case 6: 429 rate limit — inline Retry-After message

**Steps:**
1. Configure the backend to set rate limit to 1 req/60s (or send 31 transcription requests rapidly)
2. Send a 31st transcription request within one minute
3. Observe the UI response

**Expected:** The field shows an inline message indicating the rate limit was hit and how many seconds to wait (from `Retry-After` header). The existing field value is preserved. No crash or console error.

---

## Test Case 7: Codec fallback

**Steps:**
1. Open browser DevTools and override `MediaRecorder.isTypeSupported` to return false for `audio/webm`
2. Start a recording

**Expected:** Recording still starts using `audio/mp4` codec fallback. No crash or unhandled error.

---

## Test Case 8: Audio cleanup on unmount

**Steps:**
1. Start a recording on a field
2. Navigate away from the page before stopping

**Expected:** No lingering microphone indicator in the browser tab. No "AudioContext was not closed" console errors. Audio track is properly released.

---

## Test Case 9: Mobile mic button touch target

**Steps:**
1. Open the app on a Pixel 5 or iPhone 13 (or use browser DevTools mobile simulation)
2. Navigate to an eligible input
3. Tap the mic button

**Expected:** The mic button is ≥44×44 CSS pixels, easily tappable. No accidental activation of adjacent controls.

---

## Test Case 10: Redaction — no secrets in logs

**Steps:**
1. Enable backend debug logging
2. Submit a voice transcription request
3. Inspect backend logs

**Expected:** No `grok_stt_api_key` value, no raw audio bytes, no `Content-Disposition: form-data` multipart headers, no dictated transcript text appears in any log line. Only structured diagnostic fields (user_id, mime_type, byte_count, status_class, duration_ms) are logged.
