---
id: S04
parent: M005-oaptsz
milestone: M005-oaptsz
provides:
  - (none)
requires:
  []
affects:
  []
key_files:
  - (none)
key_decisions:
  - ["Use Redis sorted-set sliding-window rate limit scoped to voice:transcribe:{user_id} with injection seam for test isolation", "Grok API key decrypted only at call-site in grok_stt.py — never cached in memory or returned to callers", "Transcript injection via native HTMLInputElement value descriptor + bubbling input event for react-hook-form compatibility", "Sensitive opt-out via voice={false} prop or data-voice-disabled attribute — consistent convention across all form primitives", "MediaRecorder codec fallback: audio/webm first, audio/mp4 as fallback for cross-browser support"]
patterns_established:
  - ["VoiceInput/VoiceTextarea wrapper pattern: mic button + waveform + upload + onChange injection, composable on any Input/Textarea consumer", "Rate limit injection seam via FastAPI dependency override for route tests without live Redis", "Sensitive field opt-out convention: voice={false} or data-voice-disabled — apply to any future secret/credential/PII fields"]
observability_surfaces:
  - ["backend: voice.transcribe.start/success/failed/rate_limited/rate_limit_failed structured logs (user_id, mime, bytes, status_class, retry_after — no secrets)", "frontend: voice.recorder.permission_denied/started/stopped/upload_failed/transcribed console signals", "Playwright: m005-oaptsz-voice.spec.ts 6 test cases as living spec for voice contract"]
drill_down_paths:
  []
duration: ""
verification_result: passed
completed_at: 2026-04-28T19:15:37.508Z
blocker_discovered: false
---

# S04: Voice input universal — VoiceInput wrapper + Grok STT proxy

**Shipped universal voice dictation: Grok STT proxy with encrypted key + Redis rate limit, reusable VoiceInput/VoiceTextarea/Waveform primitives, universal coverage on all eligible inputs with sensitive-field opt-out.**

## What Happened

S04 delivered the complete voice input pipeline across backend and frontend, closing R025.

**T01 — Grok STT proxy and rate limiting (backend)**
Registered `grok_stt_api_key` as a Fernet-encrypted sensitive system setting and `max_voice_transcribes_per_hour_global` as a non-sensitive integer. Added `app/core/rate_limit.py` with a Redis sorted-set sliding-window limiter scoped to `voice:transcribe:{user_id}` (30 requests / 60 seconds) and a dependency injection seam so tests exercise rate-limit boundaries without live Redis. Added `app/core/grok_stt.py` that decrypts the key only at call-site, posts multipart audio to Grok/xAI STT with bounded timeout, normalizes the `{text}` response, and emits `voice.transcribe.*` structured logs with no key/audio/boundary/transcript content. Added authenticated `POST /api/v1/voice/transcribe` with content-type/empty/oversize validation, per-user limiter, `Retry-After` header on 429, and mounted the router. Added `redis` as an explicit backend dependency.

**T02 — Voice recorder UI primitives (frontend)**
Regenerated the typed client after T01 so the frontend calls `/api/v1/voice/transcribe` through generated service/types. Built `useVoiceRecorder.ts` with codec fallback (`audio/webm` → `audio/mp4`), AudioContext waveform analyser, upload lifecycle, and cleanup on unmount and after upload. Built `Waveform.tsx` (AnalyserNode visualizer, no audio logging). Built `VoiceInput.tsx` and `VoiceTextarea.tsx` wrapping existing Input/textarea visuals with ≥44×44 mic/stop button, accessible labels/testids, inline error display, disabled/readOnly handling, and transcript injection via a synthetic event compatible with react-hook-form. Updated `components/ui/input.tsx` to auto-wrap eligible fields and created `components/ui/textarea.tsx` with the same voice-by-default + opt-out surface.

**T03 — Universal coverage and sensitive opt-outs (frontend)**
Applied VoiceInput/VoiceTextarea across every screen: login email, Claude prompt, Codex prompt, workflow form fields, project search, team invite email. Confirmed all password/OTP/system-secret fields render via plain Input with `voice={false}` or `data-voice-disabled`. Verified ≥44×44 touch targets on all mic buttons. Created `m005-oaptsz-voice.spec.ts` with 6 Playwright test cases covering: mic button presence on eligible fields, absence on sensitive fields, permission-denied inline error, 429 Retry-After inline message, transcript injection into react-hook-form, and waveform visibility during recording.

**T04 — Verification closure**
Fixed two test environment issues: (1) pytest mock stubs used underscore-prefixed parameter names (`_audio`, `_filename`, `_content_type`) that mismatched the keyword-only args in `transcribe_audio()`; corrected to match the actual signature. (2) Tests needed `POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app` rather than the stale .env defaults — the `perpetuity_app` DB has all migrations applied while the `app` DB at the same port is a shared CRM schema. With those fixes, 70/70 backend tests passed. Frontend `bun run generate-client && bun run build` clean. Voice Playwright spec 6/6 on mobile-chrome. Mobile-audit 15/17 on both mobile-chrome and iphone-13-mobile-safari (2 pre-existing `/admin/teams` DataTable chevron failures at 32×44px, documented as MEM369, not introduced by S04). Redaction grep confirmed no actual secret values, raw audio bytes, multipart headers, or transcript text appear in any log call path — only test fixture constants and settings key-name identifiers.

**Patterns established for S05**
- Grok key stays encrypted at rest in system_settings; decrypt only at call-site in `grok_stt.py`
- Rate limit injection seam via FastAPI dependency override — usable in future route tests
- Transcript injection via native value descriptor + bubbling input event — works with react-hook-form
- Sensitive opt-out via `voice={false}` prop or `data-voice-disabled` data attribute — consistent across all form primitives

## Verification

Backend: `POSTGRES_PORT=5432 POSTGRES_DB=perpetuity_app pytest tests/api/routes/test_voice.py tests/api/routes/test_admin_settings.py -q` → 70 passed in 0.97s. Frontend: `cd frontend && bun run generate-client && bun run build` → clean (2290 modules, 33 entries precached). Playwright voice spec: `cd frontend && bunx playwright test --project=mobile-chrome m005-oaptsz-voice.spec.ts` → 6/6 pass. Mobile-audit: mobile-chrome 15/17, iphone-13-mobile-safari 15/17 (2 pre-existing /admin/teams DataTable chevron failures documented as MEM369). Redaction grep: all pattern matches are test constants asserting redaction or settings key-name string identifiers — no secret values in log paths.

## Requirements Advanced

None.

## Requirements Validated

- R025 — 70/70 backend tests pass covering transcribe route auth, validation, rate limiting (31st req → 429+Retry-After), upstream success/failure, and redaction. Frontend 6/6 Playwright voice spec passes on mobile-chrome covering mic presence, sensitive opt-out, permission-denied error, 429 inline message, transcript injection, and waveform visibility.

## New Requirements Surfaced

None.

## Requirements Invalidated or Re-scoped

None.

## Operational Readiness

None.

## Deviations

None.

## Known Limitations

/admin/teams DataTable pagination chevrons remain 32×44px (below 44×44 floor) when seeded DB exceeds DataTable pageSize — pre-existing issue documented as MEM369, not introduced by S04. Real Grok STT API acceptance and real-device voice UX deferred to S05 per milestone design.

## Follow-ups

None.

## Files Created/Modified

- `backend/app/api/routes/admin.py` — 
- `backend/app/api/routes/voice.py` — 
- `backend/app/api/main.py` — 
- `backend/app/core/grok_stt.py` — 
- `backend/app/core/rate_limit.py` — 
- `backend/pyproject.toml` — 
- `backend/tests/api/routes/test_voice.py` — 
- `backend/tests/api/routes/test_admin_settings.py` — 
- `frontend/src/components/voice/useVoiceRecorder.ts` — 
- `frontend/src/components/voice/Waveform.tsx` — 
- `frontend/src/components/voice/VoiceInput.tsx` — 
- `frontend/src/components/voice/VoiceTextarea.tsx` — 
- `frontend/src/components/ui/input.tsx` — 
- `frontend/src/components/ui/textarea.tsx` — 
- `frontend/src/client/sdk.gen.ts` — 
- `frontend/src/client/types.gen.ts` — 
- `frontend/tests/m005-oaptsz-voice.spec.ts` — 
- `frontend/tests/m005-oaptsz-mobile-audit.spec.ts` — 
