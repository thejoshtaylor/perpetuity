# S04: Voice input universal — <VoiceInput> wrapper + Grok STT proxy

**Goal:** Ship universal voice dictation for eligible text inputs through reusable frontend voice wrappers and a backend `/api/v1/voice/transcribe` Grok STT proxy. Eligible inputs get a ≥44×44 mic button, live waveform, multipart upload, transcript injection through the wrapped `onChange`, and graceful inline errors. Password, OTP, system-secret, and explicitly sensitive fields render without mic controls. Backend secrets stay in Fernet-encrypted `system_settings`, transcription is rate-limited at 30 requests/minute/user, and logs never expose Grok key material, multipart boundaries, or audio payloads.
**Demo:** User clicks the microphone icon next to any text input in the app — login email, Claude prompt, Codex prompt, workflow form field, project search, team invite email — grants mic permission once, sees a live waveform during recording, taps stop, and watches the transcribed text appear in the field within 1.5s on a fast connection. Password, OTP, and explicitly-marked sensitive fields render the plain <Input> with no mic button. A 31st transcription request inside one minute returns 429 with a Retry-After header and the UI shows a graceful inline message.

## Must-Haves

- R025: `grok_stt_api_key` is registered as sensitive `system_settings`; GET/list never returns plaintext/ciphertext, PUT stores encrypted value, and decrypt failures remain fail-loud/redacted.
- R025: `POST /api/v1/voice/transcribe` accepts authenticated multipart audio, validates media type/size, applies 30 req/min/user sliding-window rate limit, returns `429` with `Retry-After` for the 31st request, proxies to Grok STT, and returns `{text}`.
- R025: `VoiceInput`, `VoiceTextarea`, `Waveform`, and `useVoiceRecorder` provide mic button, permission handling, live waveform, MediaRecorder codec fallback (`audio/webm` then `audio/mp4`), upload, inline error, and transcript injection through wrapped `onChange`.
- R025/R022: shared `Input` and new `Textarea` primitives apply voice controls by default for eligible text-ish fields; PasswordInput, password/OTP inputs, system settings secret controls, and `voice={false}` / `data-voice-disabled` fields have no mic button.
- Threat surface: authenticated users can spam transcription, upload oversized/invalid files, or proxy untrusted content; mitigations are auth, size/type validation, Redis sliding-window rate limit, upstream timeout, no filename/path use, and redacted logs.
- Data exposure: Grok key, raw audio, multipart boundaries, dictated emails/prompts, and sensitive form values never appear in logs; sensitive fields opt out of mic rendering.
- Requirement impact: touches R025 directly, R034/R035 via new sensitive setting, and R022 via mic buttons on mobile-audited routes; re-verify sensitive settings, forms, mobile audit helpers, and frontend/backend build/test suites.
- Slice verification commands: `cd backend && pytest tests/api/routes/test_voice.py tests/api/routes/test_admin_settings.py -q`; `cd frontend && bun run generate-client && bun run build`; `cd frontend && bunx playwright test --project=mobile-chrome m005-oaptsz-voice.spec.ts`; `cd frontend && bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts`; `cd frontend && bunx playwright test --project=iphone-13-mobile-safari m005-oaptsz-mobile-audit.spec.ts`; `! rg -n "grok_stt_api_key|xai-|multipart boundary|Content-Disposition: form-data|raw audio|audio bytes" backend frontend orchestrator --glob '!**/node_modules/**' --glob '!**/.git/**'`.

## Proof Level

- This slice proves: integration

## Integration Closure

Backend route, frontend primitive wrapping, generated client, and browser tests close the app-internal contract. Real Grok/mobile-device acceptance remains in S05 by roadmap design.

## Verification

- Backend emits structured `voice.transcribe.*` logs; frontend emits `voice.recorder.*` diagnostics and inline failure messages. Logs must omit Grok key prefix/value, raw audio, multipart boundaries, and dictated text.

## Tasks

- [x] **T01: Build Grok STT proxy with encrypted key and rate limit** `est:2h`
  ---
estimated_steps: 5
estimated_files: 9
skills_used:
  - caveman
  - tdd
  - test
  - security-review
---

Implement backend half of R025 before frontend depends on it. Add sensitive `grok_stt_api_key` and non-sensitive `max_voice_transcribes_per_hour_global` to existing admin setting registry. Add `app/core/rate_limit.py` with Redis sliding-window limiter for `voice:transcribe:{user_id}` (30 requests / 60 seconds) and test injection seam. Add `app/core/grok_stt.py` to decrypt the key at call-site, post multipart audio to Grok/xAI STT with bounded timeout, normalize `{text}`, and log only redacted diagnostics. Add and mount authenticated `POST /voice/transcribe` with upload validation, limiter, Retry-After, and response model.

Failure Modes

| Dependency | On error | On timeout | On malformed response |
|------------|----------|------------|------------------------|
| `system_settings.grok_stt_api_key` decrypt | 503 via existing decrypt handler; log key only | N/A | N/A |
| Redis limiter | 503 `voice_rate_limit_unavailable`; tests may inject fake limiter | bounded client timeout | log `voice.transcribe.rate_limit_failed` |
| Grok STT HTTP API | 502 `voice_transcribe_failed`; log status class only | 504 `voice_transcribe_timeout` | 502 `voice_transcribe_bad_response` |

Load Profile

- **Shared resources**: Redis sorted-set keys per user, DB session, outbound Grok HTTP connection.
- **Per-operation cost**: 1 Redis transaction, 1 sensitive setting read/decrypt, 1 outbound multipart POST, bounded audio bytes.
- **10x breakpoint**: upstream Grok latency/quotas first; Redis keys expire naturally.

Negative Tests

- **Malformed inputs**: missing file, unsupported content type, empty audio, oversized audio.
- **Error paths**: missing key, Grok timeout/5xx/bad JSON, Redis unavailable.
- **Boundary conditions**: first 30 requests pass; 31st inside 60 seconds returns 429 with positive `Retry-After`; after window advances request passes.

Steps

1. Extend admin setting validators/models/tests for voice settings.
2. Add Redis sliding-window helper with test injection seam.
3. Add Grok STT client with decrypt-at-call-site, timeout handling, redacted logs.
4. Add and mount `voice.py` route with upload validation, limiter, proxy call, response model.
5. Add backend tests covering auth, validation, rate limit, Retry-After, upstream success/failure, and log redaction.

Must-Haves

- [ ] Sensitive Grok key never round-trips and never appears in logs.
- [ ] 31st transcription request in one minute returns `429` plus `Retry-After`.
- [ ] Happy path returns mocked `{"text":"..."}`.
- [ ] Unsupported/empty/oversized uploads fail before Grok call.

Verification

- `cd backend && pytest tests/api/routes/test_voice.py tests/api/routes/test_admin_settings.py -q`
- `cd backend && ruff check app/api/routes/voice.py app/core/grok_stt.py app/core/rate_limit.py tests/api/routes/test_voice.py`

Observability Impact

- Signals added/changed: backend `voice.transcribe.start/success/failed/rate_limited/rate_limit_failed` logs with user id, mime, bytes, status class, retry_after.
- How a future agent inspects this: run `pytest tests/api/routes/test_voice.py -q` and grep logs for `voice.transcribe.`.
- Failure state exposed: missing key, limiter unavailable, unsupported content type, upstream timeout/status, retry-after.

Inputs

- `backend/app/api/routes/admin.py` — sensitive settings registry.
- `backend/app/api/main.py` — router include site.
- `backend/app/core/encryption.py` — decrypt failure contract.
- `backend/tests/api/routes/test_push.py` — VAPID sensitive-key test pattern.
- `backend/pyproject.toml` — dependency list.

Expected Output

- `backend/app/api/routes/admin.py` — registers voice settings.
- `backend/app/api/routes/voice.py` — new transcribe endpoint.
- `backend/app/api/main.py` — mounts voice router.
- `backend/app/core/grok_stt.py` — Grok STT client.
- `backend/app/core/rate_limit.py` — sliding-window limiter.
- `backend/app/models.py` — response models if needed.
- `backend/pyproject.toml` — Redis dependency if needed.
- `backend/tests/api/routes/test_voice.py` — route/unit coverage.
- `backend/tests/api/routes/test_admin_settings.py` — sensitive setting regression coverage.
  - Files: `backend/app/api/routes/admin.py`, `backend/app/api/routes/voice.py`, `backend/app/api/main.py`, `backend/app/core/grok_stt.py`, `backend/app/core/rate_limit.py`, `backend/app/models.py`, `backend/pyproject.toml`, `backend/tests/api/routes/test_voice.py`, `backend/tests/api/routes/test_admin_settings.py`
  - Verify: cd backend && pytest tests/api/routes/test_voice.py tests/api/routes/test_admin_settings.py -q

- [x] **T02: Create reusable voice recorder UI primitives** `est:2h`
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
  - Files: `frontend/src/components/voice/useVoiceRecorder.ts`, `frontend/src/components/voice/Waveform.tsx`, `frontend/src/components/voice/VoiceInput.tsx`, `frontend/src/components/voice/VoiceTextarea.tsx`, `frontend/src/components/ui/input.tsx`, `frontend/src/components/ui/textarea.tsx`, `frontend/src/client/sdk.gen.ts`, `frontend/src/client/types.gen.ts`
  - Verify: cd frontend && bun run generate-client && bun run build

- [x] **T03: Apply universal coverage and protect sensitive opt-outs** `est:2h`
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
  - Files: `frontend/src/routes/login.tsx`, `frontend/src/routes/signup.tsx`, `frontend/src/routes/recover-password.tsx`, `frontend/src/routes/reset-password.tsx`, `frontend/src/components/Admin/AddUser.tsx`, `frontend/src/components/Admin/EditUser.tsx`, `frontend/src/components/Admin/SystemSettings/SetSecretDialog.tsx`, `frontend/src/components/Teams/InviteButton.tsx`, `frontend/src/components/Teams/Projects/CreateProjectDialog.tsx`, `frontend/src/components/Teams/Projects/PushRuleForm.tsx`, `frontend/tests/m005-oaptsz-voice.spec.ts`, `frontend/tests/m005-oaptsz-mobile-audit.spec.ts`
  - Verify: cd frontend && bunx playwright test --project=mobile-chrome m005-oaptsz-voice.spec.ts && bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts

- [x] **T04: Close slice with cross-boundary verification and redaction gates** `est:1h`
  ---
estimated_steps: 4
estimated_files: 6
skills_used:
  - caveman
  - verify-before-complete
  - test
  - security-review
---

Close final contract gates after backend + frontend implementation land. Update tests and small wiring defects found by the full verification loop. Confirm generated frontend client matches backend OpenAPI, backend tests prove STT/rate-limit contract, browser tests prove mocked mic-to-transcript injection, mobile audits pass on Chrome/WebKit projects, and redaction grep finds no obvious secret/audio leaks in tracked code.

Failure Modes

| Dependency | On error | On timeout | On malformed response |
|------------|----------|------------|------------------------|
| Backend tests | fix route/model/rate-limit wiring | inspect pytest timeout/failing fixture | assertion identifies response drift |
| Playwright tests | fix selectors/mock/client calls; run from `frontend/` per MEM336 | inspect trace/stdout | assertion identifies UI/API mismatch |
| Redaction grep | remove or hash leaked strings | N/A | N/A |

Load Profile

- **Shared resources**: backend test DB/system settings, Playwright auth state, generated OpenAPI client.
- **Per-operation cost**: test-only.
- **10x breakpoint**: Playwright duration; keep voice spec focused.

Negative Tests

- **Malformed inputs**: backend invalid upload + frontend missing text response remain covered.
- **Error paths**: permission denied, upload failure, upstream failure remain covered.
- **Boundary conditions**: 30th/31st request and sensitive no-mic assertions remain covered.

Steps

1. Run backend voice/admin tests; fix response/log/rate-limit drift.
2. Regenerate frontend client, build frontend, fix type errors.
3. Run voice Playwright spec and mobile-audit projects from `frontend/`; fix layout/selectors/mock issues.
4. Run redaction grep over tracked backend/frontend/orchestrator files; remove or hash any leaked Grok key, multipart boundary, or raw audio logging pattern.

Must-Haves

- [ ] Backend test contract passes for happy path, 429, validation, missing key, redacted logs.
- [ ] Frontend build passes with generated client.
- [ ] Voice Playwright spec proves mic visibility, transcript injection, sensitive opt-outs, graceful 429.
- [ ] Mobile audit passes on `mobile-chrome` and `iphone-13-mobile-safari` with mic UI present.
- [ ] Redaction grep gate has zero matches for planned secret/audio leak patterns.

Verification

- `cd backend && pytest tests/api/routes/test_voice.py tests/api/routes/test_admin_settings.py -q`
- `cd frontend && bun run generate-client && bun run build`
- `cd frontend && bunx playwright test --project=mobile-chrome m005-oaptsz-voice.spec.ts`
- `cd frontend && bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts`
- `cd frontend && bunx playwright test --project=iphone-13-mobile-safari m005-oaptsz-mobile-audit.spec.ts`
- `! rg -n "grok_stt_api_key|xai-|multipart boundary|Content-Disposition: form-data|raw audio|audio bytes" backend frontend orchestrator --glob '!**/node_modules/**' --glob '!**/.git/**'`

Observability Impact

- Signals added/changed: test assertions lock diagnostic names and redaction posture.
- How a future agent inspects this: verification commands plus Playwright traces.
- Failure state exposed: OpenAPI/client, route, recorder UI, and redaction mismatches.

Inputs

- `backend/app/api/routes/voice.py` — backend endpoint.
- `backend/tests/api/routes/test_voice.py` — backend contract tests.
- `frontend/src/client/sdk.gen.ts` — generated client.
- `frontend/src/client/types.gen.ts` — generated types.
- `frontend/tests/m005-oaptsz-voice.spec.ts` — browser contract.
- `frontend/tests/m005-oaptsz-mobile-audit.spec.ts` — mobile audit.

Expected Output

- `backend/tests/api/routes/test_voice.py` — green backend contract tests.
- `frontend/tests/m005-oaptsz-voice.spec.ts` — green browser voice contract.
- `frontend/tests/m005-oaptsz-mobile-audit.spec.ts` — green mic touch-target regression assertion.
- `frontend/src/client/sdk.gen.ts` — current generated client.
- `frontend/src/client/types.gen.ts` — current generated types.
- `backend/app/api/routes/voice.py` — final route fixes if tests reveal drift.
  - Files: `frontend/tests/m005-oaptsz-voice.spec.ts`, `frontend/tests/m005-oaptsz-mobile-audit.spec.ts`, `backend/tests/api/routes/test_voice.py`, `frontend/src/client/sdk.gen.ts`, `frontend/src/client/types.gen.ts`, `backend/app/api/routes/voice.py`
  - Verify: cd backend && pytest tests/api/routes/test_voice.py tests/api/routes/test_admin_settings.py -q && cd ../frontend && bun run generate-client && bun run build && bunx playwright test --project=mobile-chrome m005-oaptsz-voice.spec.ts

## Files Likely Touched

- backend/app/api/routes/admin.py
- backend/app/api/routes/voice.py
- backend/app/api/main.py
- backend/app/core/grok_stt.py
- backend/app/core/rate_limit.py
- backend/app/models.py
- backend/pyproject.toml
- backend/tests/api/routes/test_voice.py
- backend/tests/api/routes/test_admin_settings.py
- frontend/src/components/voice/useVoiceRecorder.ts
- frontend/src/components/voice/Waveform.tsx
- frontend/src/components/voice/VoiceInput.tsx
- frontend/src/components/voice/VoiceTextarea.tsx
- frontend/src/components/ui/input.tsx
- frontend/src/components/ui/textarea.tsx
- frontend/src/client/sdk.gen.ts
- frontend/src/client/types.gen.ts
- frontend/src/routes/login.tsx
- frontend/src/routes/signup.tsx
- frontend/src/routes/recover-password.tsx
- frontend/src/routes/reset-password.tsx
- frontend/src/components/Admin/AddUser.tsx
- frontend/src/components/Admin/EditUser.tsx
- frontend/src/components/Admin/SystemSettings/SetSecretDialog.tsx
- frontend/src/components/Teams/InviteButton.tsx
- frontend/src/components/Teams/Projects/CreateProjectDialog.tsx
- frontend/src/components/Teams/Projects/PushRuleForm.tsx
- frontend/tests/m005-oaptsz-voice.spec.ts
- frontend/tests/m005-oaptsz-mobile-audit.spec.ts
