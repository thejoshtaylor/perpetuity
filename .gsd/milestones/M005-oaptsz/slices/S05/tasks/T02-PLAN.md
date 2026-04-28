---
estimated_steps: 16
estimated_files: 1
skills_used: []
---

# T02: Full regression gate + real-device acceptance checklist artifact

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

## Inputs

- `backend/tests/api/routes/test_voice.py`
- `backend/tests/api/routes/test_push.py`
- `backend/tests/api/routes/test_push_dispatch.py`
- `backend/tests/api/routes/test_notifications.py`
- `frontend/tests/m005-oaptsz-mobile-audit.spec.ts`
- `frontend/tests/m005-oaptsz-sw-bypass.spec.ts`
- `frontend/tests/m005-oaptsz-notifications.spec.ts`
- `frontend/tests/m005-oaptsz-notifications-preferences.spec.ts`
- `frontend/tests/m005-oaptsz-push.spec.ts`
- `frontend/tests/m005-oaptsz-voice.spec.ts`
- `.gsd/milestones/M005-oaptsz/M005-oaptsz-CONTEXT.md`

## Expected Output

- `.gsd/milestones/M005-oaptsz/slices/S05/S05-CHECKLIST.md`

## Verification

1. `POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app pytest tests/ -q` exits 0 with 70+ passed. 2. `bun run build` from frontend/ exits 0. 3. Playwright mobile-chrome voice spec 6/6, SW-bypass 1/1, chromium notifications 4/4. 4. S05-CHECKLIST.md exists and contains all four scenario headings with step-by-step instructions.
