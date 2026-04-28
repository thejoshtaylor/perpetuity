# S05: Final integrated acceptance + redaction sweep + real-device round-trip

**Goal:** Run the automated redaction sweep over all backend + frontend log paths and produce the structured real-device acceptance checklist, confirming zero secret leaks in any log surface and that the full test matrix (backend 70/70, all frontend Playwright suites) passes clean before handing off to operator UAT.
**Demo:** Operator runs the four 'Final Integrated Acceptance' scenarios from the milestone CONTEXT on real devices and records the results: (1) install + use on real Pixel-class Android — install banner appears, home-screen icon launches standalone, full demo flow completes; (2) push notification round-trip on real iPhone 16.4+ — subscribe, configure failure→push, background app, trigger failure, receive push within 30s, tap → run-detail; (3) voice prompt on real mobile Safari — tap mic, speak, see real Grok transcription within 1.5s, run workflow against real Anthropic API; (4) cross-device read state sync — dismiss notification on phone, see it marked read on desktop bell panel within 5s. Redaction sweep grep over all backend + orchestrator + frontend dev-server logs returns zero matches for Grok API key prefix, VAPID private key first 8 bytes, multipart audio boundaries, or raw push endpoint URLs.

## Must-Haves

- 1. Redaction sweep script runs without error and reports zero matches for: Grok API key prefix `xai-`, VAPID private key first 8 chars (the key is operator-provided so sweep checks the pattern `-----BEGIN EC PRIVATE KEY-----` / raw base64url blocks appearing in log strings), raw multipart audio boundary strings (`--`, `Content-Disposition: form-data`), and raw push endpoint URLs (https://fcm.googleapis.com, https://updates.push.services.mozilla.com, or any https:// URL appearing in a logger.* call that isn't endpoint_hash). 2. Full backend test suite passes: `POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app pytest tests/ -q` → 70+ passed, 0 failed. 3. Full Playwright suite passes: mobile-audit (all 4 projects), SW-bypass, notifications, notifications-preferences, push, voice specs. 4. S05-CHECKLIST.md exists at `.gsd/milestones/M005-oaptsz/slices/S05/S05-CHECKLIST.md` with step-by-step instructions for all four real-device acceptance scenarios.

## Proof Level

- This slice proves: final-assembly — this slice assembles evidence from all prior slices and proves the system is clean before operator UAT. No new runtime code ships; the deliverables are a sweep script, a test run, and a checklist artifact.

## Integration Closure

All four S01–S04 slices complete. This slice exercises the combined system end-to-end at the log-path level (T01) and test-matrix level (T02). Real push delivery to physical devices is deferred to operator UAT per the S03 boundary decision.

## Verification

- Not provided.

## Tasks

- [x] **T01: Redaction sweep: write and run grep assertions over all log call paths** `est:45m`
  Write a bash script `scripts/redaction-sweep.sh` that greps all backend Python source (app/ directory), all frontend TypeScript source (src/), and the built service worker dist/sw.js for forbidden patterns, then asserts zero matches for each pattern. Patterns to check: (1) raw Grok key prefix — any string matching `xai-[A-Za-z0-9]` appearing inside a logger.* or console.* call; (2) raw VAPID private key material — `-----BEGIN EC PRIVATE KEY-----` or a base64url block > 40 chars in a logger/console call; (3) raw multipart boundary strings — `Content-Disposition: form-data` or `--WebKit` appearing in logger/console calls; (4) raw push endpoint URLs — `https://fcm.googleapis.com` or `https://updates.push.services.mozilla.com` or any `https://` URL appearing on a logger.* line that does NOT contain `endpoint_hash`.

Also verify the existing test-level redaction assertions remain in place: `test_voice_transcribe_happy_path_returns_text_and_redacts_logs` asserts `TRANSCRIPT_VALUE not in logs` and `SECRET_VALUE not in logs`; `test_grok_key_stored_encrypted_and_transcribe_never_logs_key_or_text` asserts the same.

The script exits 0 if all checks pass, non-zero with a clear failure message if any pattern is found. Run the script and capture its output as the verification evidence for this task.
  - Files: `scripts/redaction-sweep.sh`, `backend/app/core/grok_stt.py`, `backend/app/core/push_dispatch.py`, `backend/app/api/routes/voice.py`, `backend/app/core/notify.py`, `frontend/src/sw.ts`, `frontend/src/components/voice/useVoiceRecorder.ts`, `frontend/src/components/notifications/PushPermissionPrompt.tsx`
  - Verify: bash scripts/redaction-sweep.sh exits 0 with output: 'PASS: no Grok key prefix in log paths', 'PASS: no VAPID private key material in log paths', 'PASS: no multipart boundary in log paths', 'PASS: no raw push endpoint URLs in log paths'. Each PASS line printed. Script exit code 0.

- [x] **T02: Full regression gate + real-device acceptance checklist artifact** `est:60m`
  Run the complete test matrix to confirm no regression across S01–S04, then write the structured real-device acceptance checklist artifact.

**Step 1 — Backend regression gate:** Run `POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app pytest tests/ -q` from the `backend/` directory. Expected: 70+ passed, 0 failed. If failures appear, investigate root cause and fix before proceeding.

**Step 2 — Frontend build:** Run `bun run build` from `frontend/`. Expected: clean build with no TypeScript errors (the existing >500kB chunk size warning is acceptable and pre-existing).

**Step 3 — Playwright suites:** From `frontend/`, run each suite that S01–S04 produced:
- `bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts` → 15+ passed (note: 2 pre-existing /admin/teams DataTable chevron failures documented as MEM369 are acceptable)
- `bunx playwright test --project=m005-oaptsz-sw m005-oaptsz-sw-bypass.spec.ts` → 1 passed
- `bunx playwright test --project=chromium m005-oaptsz-notifications.spec.ts m005-oaptsz-notifications-preferences.spec.ts` → 4+ passed
- `bunx playwright test --project=m005-oaptsz-push m005-oaptsz-push.spec.ts` → 2+ passed
- `bunx playwright test --project=mobile-chrome m005-oaptsz-voice.spec.ts` → 6 passed

Note: the push and SW tests require the production preview (port 4173) and a running backend at port 8000 with a seeded DB. If the environment is not set up for those, document the gap; do not treat it as a failure.

**Step 4 — Write `.gsd/milestones/M005-oaptsz/slices/S05/S05-CHECKLIST.md`:** A structured operator UAT script for the four real-device acceptance scenarios from the CONTEXT. Each scenario must have: prerequisites, step-by-step instructions, success criterion, and what to do if it fails. Include device prerequisites (Android Pixel-class + Chrome, iPhone 16.4+ Safari, desktop Chrome). Reference the VAPID key generation endpoint, the notification preferences UI, and the admin settings for Grok key configuration.

Scenario 1: Mobile install + use (Android Chrome install banner → standalone launch → full demo flow)
Scenario 2: Push notification round-trip (iPhone 16.4+ → subscribe → configure failure→push → background → trigger failure → receive push within 30s → tap → run-detail)
Scenario 3: Voice prompt on real mobile Safari (tap mic → speak → Grok transcription within 1.5s → run workflow)
Scenario 4: Cross-device read state sync (dismiss on phone → marked read on desktop within 5s; reverse direction also)

Also document the known limitations: iOS Safari < 16.4 no push support, /admin/teams DataTable chevron MEM369 pre-existing, real push requires VAPID keys configured in admin settings.
  - Files: `.gsd/milestones/M005-oaptsz/slices/S05/S05-CHECKLIST.md`
  - Verify: 1. `POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app pytest tests/ -q` exits 0 with 70+ passed. 2. `bun run build` from frontend/ exits 0. 3. Playwright mobile-chrome voice spec 6/6, SW-bypass 1/1, chromium notifications 4/4. 4. S05-CHECKLIST.md exists and contains all four scenario headings with step-by-step instructions.

## Files Likely Touched

- scripts/redaction-sweep.sh
- backend/app/core/grok_stt.py
- backend/app/core/push_dispatch.py
- backend/app/api/routes/voice.py
- backend/app/core/notify.py
- frontend/src/sw.ts
- frontend/src/components/voice/useVoiceRecorder.ts
- frontend/src/components/notifications/PushPermissionPrompt.tsx
- .gsd/milestones/M005-oaptsz/slices/S05/S05-CHECKLIST.md
