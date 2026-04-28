---
estimated_steps: 63
estimated_files: 12
skills_used: []
---

# T03: Apply universal coverage and protect sensitive opt-outs

---
estimated_steps: 5
estimated_files: 12
skills_used:
  - caveman
  - accessibility
  - react-best-practices
  - test
---

Make D026 true in real app screens. Because `Input` auto-wraps eligible fields, most normal text/email/search fields inherit mic automatically. Audit every existing `Input` and raw `textarea` consumer, convert secret/password fields to `PasswordInput` or add `voice={false}` / `data-voice-disabled`, replace raw non-sensitive textareas with `Textarea`, and add tests that fail if a future sensitive field accidentally renders mic. Demo representatives: login email, team invite email, project fields, push-rule fields, and any search/text field in repo.

Failure Modes

| Dependency | On error | On timeout | On malformed response |
|------------|----------|------------|------------------------|
| Existing forms/react-hook-form | value may not update if injection bypasses field handler | N/A | validation should still show existing FormMessage |
| Browser mock MediaRecorder | spec fails before upload; helper stays isolated | N/A | spec reports missing transcript injection |
| Backend voice endpoint mock | inline error, no value clobber | request timeout inline | malformed body inline error |

Load Profile

- **Shared resources**: all app forms now render extra mic buttons; mobile layout width/touch budget.
- **Per-operation cost**: idle cost is one button per eligible input; recording cost only on click.
- **10x breakpoint**: crowded forms causing horizontal scroll; mobile audit catches.

Negative Tests

- **Malformed inputs**: mocked API returns missing text; verify inline error and unchanged field.
- **Error paths**: mocked 429 returns Retry-After; UI displays graceful retry message.
- **Boundary conditions**: password/system-secret/OTP fields have zero mic buttons; login email and team invite email have one.

Steps

1. Audit consumers with `rg "<Input|<textarea|PasswordInput|otp|OTP|password" frontend/src`.
2. Replace password-like plain inputs with `PasswordInput` where appropriate, or add explicit voiceless opt-out.
3. Replace raw non-sensitive textarea consumers with `Textarea`; keep system-secret PEM textarea voiceless.
4. Create `frontend/tests/m005-oaptsz-voice.spec.ts` with MediaRecorder/getUserMedia/API mocks proving mic visibility, transcript injection, opt-outs, and 429 inline error.
5. Extend `m005-oaptsz-mobile-audit.spec.ts` with representative mic touch-target assertion and verify no horizontal scroll.

Must-Haves

- [ ] Every eligible text/email/search input visibly has mic through primitive/wrapper.
- [ ] Every password/secret/OTP/sensitive field is explicitly voiceless and tested.
- [ ] Transcript injection updates controlled react-hook-form fields through existing `onChange`.
- [ ] Mobile audit still passes with mic buttons present.

Verification

- `cd frontend && bunx playwright test --project=mobile-chrome m005-oaptsz-voice.spec.ts`
- `cd frontend && bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts`
- `cd frontend && bunx playwright test --project=iphone-13-mobile-safari m005-oaptsz-mobile-audit.spec.ts`
- `cd frontend && bun run build`

Observability Impact

- Signals added/changed: test-visible voice button labels/testids and recorder logs on real forms.
- How a future agent inspects this: `rg "voice={false}|data-voice-disabled|<Input|<textarea" frontend/src` and Playwright voice spec.
- Failure state exposed: missing mic, accidental mic on sensitive field, injection failure, rate-limit UI copy.

Inputs

- `frontend/src/components/ui/input.tsx` — auto-wrapped primitive from T02.
- `frontend/src/components/ui/textarea.tsx` — textarea primitive from T02.
- `frontend/src/components/voice/VoiceInput.tsx` — mic wrapper from T02.
- `frontend/src/components/voice/VoiceTextarea.tsx` — mic wrapper from T02.
- `frontend/tests/utils/audit.ts` — mobile audit helpers.

Expected Output

- `frontend/src/routes/login.tsx` — login email covered, password opt-out preserved.
- `frontend/src/routes/signup.tsx` — signup text/email covered, passwords opt-out preserved.
- `frontend/src/routes/recover-password.tsx` — recovery email covered.
- `frontend/src/routes/reset-password.tsx` — reset passwords opt-out preserved.
- `frontend/src/components/Admin/AddUser.tsx` — admin user text/email covered, passwords opt-out preserved.
- `frontend/src/components/Admin/EditUser.tsx` — admin user text/email covered, password opt-out preserved.
- `frontend/src/components/Admin/SystemSettings/SetSecretDialog.tsx` — secret controls explicitly voiceless.
- `frontend/src/components/Teams/InviteButton.tsx` — team invite email covered.
- `frontend/src/components/Teams/Projects/CreateProjectDialog.tsx` — project fields covered.
- `frontend/src/components/Teams/Projects/PushRuleForm.tsx` — push-rule fields covered unless explicitly sensitive.
- `frontend/tests/m005-oaptsz-voice.spec.ts` — browser contract tests.
- `frontend/tests/m005-oaptsz-mobile-audit.spec.ts` — mic touch-target/audit extension.

## Inputs

- `frontend/src/components/ui/input.tsx`
- `frontend/src/components/ui/textarea.tsx`
- `frontend/src/components/voice/VoiceInput.tsx`
- `frontend/src/components/voice/VoiceTextarea.tsx`
- `frontend/tests/utils/audit.ts`

## Expected Output

- `frontend/src/routes/login.tsx`
- `frontend/src/routes/signup.tsx`
- `frontend/src/routes/recover-password.tsx`
- `frontend/src/routes/reset-password.tsx`
- `frontend/src/components/Admin/AddUser.tsx`
- `frontend/src/components/Admin/EditUser.tsx`
- `frontend/src/components/Admin/SystemSettings/SetSecretDialog.tsx`
- `frontend/src/components/Teams/InviteButton.tsx`
- `frontend/src/components/Teams/Projects/CreateProjectDialog.tsx`
- `frontend/src/components/Teams/Projects/PushRuleForm.tsx`
- `frontend/tests/m005-oaptsz-voice.spec.ts`
- `frontend/tests/m005-oaptsz-mobile-audit.spec.ts`

## Verification

cd frontend && bunx playwright test --project=mobile-chrome m005-oaptsz-voice.spec.ts && bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts

## Observability Impact

Makes recorder diagnostics visible on real forms and adds opt-out evidence for future triage.
