---
id: T03
parent: S04
milestone: M005-oaptsz
key_files:
  - frontend/src/components/Admin/AddUser.tsx
  - frontend/src/components/Admin/EditUser.tsx
  - frontend/src/components/Admin/SystemSettings/SetSecretDialog.tsx
  - frontend/tests/m005-oaptsz-voice.spec.ts
  - frontend/tests/m005-oaptsz-mobile-audit.spec.ts
key_decisions:
  - Convert raw <Input type="password"> to PasswordInput primitive in AddUser/EditUser instead of relying solely on the auto-wrap heuristic — primitive-level guarantee is more durable than name/type regex.
  - Mark SystemSettings secret controls with explicit voice={false} and data-voice-disabled='true' on top of the existing raw render path — gives audits a greppable opt-out marker even though the primitive heuristic would already opt them out.
  - Stub window.AudioContext and webkitAudioContext to undefined in the Playwright voice spec addInitScript — the hook's `if (AudioContextClass)` guard is the clean opt-out, avoiding the TypeError from createMediaStreamSource(fakeStream) that would otherwise mask rate-limit/error mocks.
duration: 
verification_result: mixed
completed_at: 2026-04-28T18:52:35.731Z
blocker_discovered: false
---

# T03: Applied universal voice coverage on real screens and locked sensitive opt-outs with browser tests

**Applied universal voice coverage on real screens and locked sensitive opt-outs with browser tests**

## What Happened

Audited every existing `<Input>` and raw `<textarea>` consumer (rg over frontend/src). Replaced the four `<Input type="password">` instances in admin user dialogs with the `PasswordInput` primitive so password fields are explicitly voiceless at the primitive level (no longer reliant on the `type="password"` heuristic in the auto-wrapping `Input`). Updated `SetSecretDialog` so both the PEM textarea and the single-line secret value carry `data-voice-disabled="true"`, and the secret string `Input` adds `voice={false}` to force the raw render path — operator-supplied secret material can never be dictated. Other consumers (login email, signup email/full name, recover-password email, project name/repo, push rule branch/workflow, team name, item title/description, user info name/email, invite URL via readOnly) were already covered transitively through the auto-wrapping primitive and required no changes.

Added `frontend/tests/m005-oaptsz-voice.spec.ts` covering: mic visibility on /login email + 44×44 touch target, password field has zero mic toggle, click→stop injects the mocked transcript through `onChange` (verifying react-hook-form-compatible synthetic events), 429 with Retry-After surfaces inline retryable error and preserves typed text, malformed (missing `text`) response surfaces inline error and preserves typed text, admin AddUser dialog shows exactly two mic toggles (email + full name) and zero on the now-PasswordInput password fields, and the system-settings Set Secret dialog input is `data-voice-disabled` with no mic toggle. The spec installs MediaRecorder + getUserMedia + AudioContext stubs via `page.addInitScript` and intercepts `/api/v1/voice/transcribe` via `page.route` so the suite runs without real microphone access or backend dependency.

Extended `frontend/tests/m005-oaptsz-mobile-audit.spec.ts` with a `voice mic toggle: visible on /login email and touch target >=44x44` regression test that asserts the mic is visible, clears 44×44, and the page still has no horizontal scroll with the mic present.

Discovered during verification: useVoiceRecorder calls `audioContext.createMediaStreamSource(stream)` — the real Chromium `AudioContext` rejects a fake `MediaStream` with a TypeError that gets normalized to a generic "Voice transcription failed" message, masking the mocked rate-limit/error response. Fixed by stubbing `window.AudioContext` and `window.webkitAudioContext` to `undefined` in the addInitScript so the hook's `if (AudioContextClass)` guard short-circuits the analyser branch. Captured as MEM387.

## Verification

Ran `cd frontend && bun run build` (tsc + vite build + sw build, all green). Ran `bunx playwright test --project=mobile-chrome m005-oaptsz-voice.spec.ts` — 7/7 pass. Ran `bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts` — 15/17 pass; the two remaining failures are pre-existing `/admin/teams` DataTable pagination chevron failures (32×44 px) documented in MEM369, unrelated to T03 changes. Ran the new mic touch-target test on `iphone-13-mobile-safari` — pass. Voice spec proves mic visibility, transcript injection, sensitive opt-outs, 429 inline error, malformed-response inline error, and admin password field opt-outs.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd frontend && bun run build` | 0 | ✅ pass | 2100ms |
| 2 | `cd frontend && bunx playwright test --project=mobile-chrome m005-oaptsz-voice.spec.ts` | 0 | ✅ pass — 7/7 | 12200ms |
| 3 | `cd frontend && bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts --grep 'voice mic toggle'` | 0 | ✅ pass — 1/1 (new mic regression test) | 10100ms |
| 4 | `cd frontend && bunx playwright test --project=iphone-13-mobile-safari m005-oaptsz-mobile-audit.spec.ts --grep 'voice mic toggle'` | 0 | ✅ pass — 1/1 (new mic regression test on iOS) | 16100ms |
| 5 | `cd frontend && bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts` | 1 | ❌ partial — 15/17 (2 failures on /admin/teams are pre-existing DataTable pagination chevron 32×44 issues per MEM369, unrelated to T03) | 13000ms |

## Deviations

Most consumer files listed in the task plan's Expected Output (login.tsx, signup.tsx, recover-password.tsx, reset-password.tsx, InviteButton.tsx, CreateProjectDialog.tsx, PushRuleForm.tsx) already comply transitively through the auto-wrapping Input primitive and the existing PasswordInput usage; no edits were needed beyond the three sensitive opt-out fixes (AddUser, EditUser, SetSecretDialog).

## Known Issues

/admin/teams DataTable pagination chevrons remain 32×44 (below 44×44 floor) when the seeded DB exceeds DataTable pageSize — pre-existing per MEM369, fixed in a separate task. Not introduced or worsened by T03.

## Files Created/Modified

- `frontend/src/components/Admin/AddUser.tsx`
- `frontend/src/components/Admin/EditUser.tsx`
- `frontend/src/components/Admin/SystemSettings/SetSecretDialog.tsx`
- `frontend/tests/m005-oaptsz-voice.spec.ts`
- `frontend/tests/m005-oaptsz-mobile-audit.spec.ts`
