---
estimated_steps: 56
estimated_files: 8
skills_used: []
---

# T02: Create reusable voice recorder UI primitives

---
estimated_steps: 5
estimated_files: 8
skills_used:
  - caveman
  - react-best-practices
  - accessibility
  - test
---

Build frontend voice primitives behind the existing design system. Regenerate typed client after T01 so frontend calls `/api/v1/voice/transcribe` through generated service/types where possible. `useVoiceRecorder` owns microphone permission, codec selection, waveform analyser, upload lifecycle, cleanup, and error normalization. `VoiceInput` and `VoiceTextarea` compose existing Input/textarea visuals with a mic/stop button inheriting S01 ≥44×44 touch floor. Transcript injection must go through wrapped `onChange` using an input/textarea-compatible synthetic event so react-hook-form updates normally.

Failure Modes

| Dependency | On error | On timeout | On malformed response |
|------------|----------|------------|------------------------|
| `navigator.mediaDevices.getUserMedia` | inline permission/device error; no upload | N/A | N/A |
| `MediaRecorder`/codec support | fallback `audio/webm` → `audio/mp4`; unsupported shows inline error | N/A | N/A |
| `/api/v1/voice/transcribe` | inline API error; preserve typed text | abort and show retryable error | show bad-response inline error |

Load Profile

- **Shared resources**: microphone, AudioContext, network request, backend rate limit.
- **Per-operation cost**: one MediaRecorder session, one multipart upload, one short-lived analyser.
- **10x breakpoint**: leaked tracks/audio contexts; backend rate limit if user repeats quickly.

Negative Tests

- **Malformed inputs**: no blob chunks, unsupported codec, missing transcript text.
- **Error paths**: permission denied, backend 429 with Retry-After, backend 500, upload abort.
- **Boundary conditions**: existing value plus transcript behavior documented; stop without recording cleans up.

Steps

1. Run `cd frontend && bun run generate-client` after T01.
2. Implement `useVoiceRecorder.ts` with codec fallback, analyser state, cleanup, upload, and `voice.recorder.*` signals.
3. Implement accessible `Waveform.tsx` with no raw audio logging.
4. Implement `VoiceInput.tsx` and `VoiceTextarea.tsx` wrappers with mic/stop button, labels/testids, inline error, disabled/readOnly handling, opt-out support, and onChange injection.
5. Update `components/ui/input.tsx` and create `components/ui/textarea.tsx` so eligible fields get voice by default while preserving class names/ref behavior.

Must-Haves

- [ ] Mic button has ≥44×44 CSS px target.
- [ ] Permission denied, 429, and upstream errors render inline and preserve existing value.
- [ ] `PasswordInput`, password/hidden/OTP-like, disabled/readOnly, and `voice={false}` fields never render mic.
- [ ] Audio tracks/contexts stop on unmount and after upload.

Verification

- `cd frontend && bun run generate-client && bun run build`
- `cd frontend && bunx playwright test --project=mobile-chrome m005-oaptsz-voice.spec.ts --grep "voice primitives"` after T03 creates spec.

Observability Impact

- Signals added/changed: frontend `voice.recorder.permission_denied/started/stopped/upload_failed/transcribed`.
- How a future agent inspects this: browser console logs + Playwright voice spec.
- Failure state exposed: permission, codec, upload, 429 retry-after, cleanup failures.

Inputs

- `frontend/src/components/ui/input.tsx` — existing Input primitive/touch-floor classes.
- `frontend/src/components/ui/button.tsx` — touch-safe icon button pattern.
- `frontend/src/client/sdk.gen.ts` — generated service surface.
- `frontend/src/client/types.gen.ts` — generated types.

Expected Output

- `frontend/src/components/voice/useVoiceRecorder.ts` — recorder/upload hook.
- `frontend/src/components/voice/Waveform.tsx` — waveform indicator.
- `frontend/src/components/voice/VoiceInput.tsx` — input wrapper.
- `frontend/src/components/voice/VoiceTextarea.tsx` — textarea wrapper.
- `frontend/src/components/ui/input.tsx` — eligible auto-wrapping + opt-out prop.
- `frontend/src/components/ui/textarea.tsx` — new textarea primitive with voice support.
- `frontend/src/client/sdk.gen.ts` — regenerated typed voice service.
- `frontend/src/client/types.gen.ts` — regenerated voice types.

## Inputs

- `frontend/src/components/ui/input.tsx`
- `frontend/src/components/ui/button.tsx`
- `frontend/src/client/sdk.gen.ts`
- `frontend/src/client/types.gen.ts`

## Expected Output

- `frontend/src/components/voice/useVoiceRecorder.ts`
- `frontend/src/components/voice/Waveform.tsx`
- `frontend/src/components/voice/VoiceInput.tsx`
- `frontend/src/components/voice/VoiceTextarea.tsx`
- `frontend/src/components/ui/input.tsx`
- `frontend/src/components/ui/textarea.tsx`
- `frontend/src/client/sdk.gen.ts`
- `frontend/src/client/types.gen.ts`

## Verification

cd frontend && bun run generate-client && bun run build

## Observability Impact

Adds browser recorder diagnostics and inline failure state for permission, codec, upload, and rate-limit errors.
