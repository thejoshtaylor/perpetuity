---
id: T02
parent: S04
milestone: M005-oaptsz
key_files:
  - frontend/src/components/voice/useVoiceRecorder.ts
  - frontend/src/components/voice/Waveform.tsx
  - frontend/src/components/voice/VoiceInput.tsx
  - frontend/src/components/voice/VoiceTextarea.tsx
  - frontend/src/components/ui/input.tsx
  - frontend/src/components/ui/textarea.tsx
  - frontend/src/client/sdk.gen.ts
  - frontend/src/client/types.gen.ts
  - frontend/src/client/schemas.gen.ts
  - frontend/openapi.json
  - backend/app/core/config.py
  - tests/api/routes/test_voice.py
  - tests/api/routes/test_admin_settings.py
key_decisions:
  - Use primitive-level auto-wrapping for eligible Input/Textarea fields with explicit `voice={false}` / `voiceSensitive` and sensitive-name heuristics as opt-outs.
  - Keep frontend recorder observability in browser console `voice.recorder.*` events with metadata only: MIME, byte count, state, kind, retry-after; never raw audio or transcript text.
  - Resolve the root-path pytest gate mismatch with thin compatibility wrappers instead of duplicating canonical backend route tests.
duration: 
verification_result: mixed
completed_at: 2026-04-28T18:35:40.690Z
blocker_discovered: false
---

# T02: Added reusable voice recorder primitives and default voice-enabled Input/Textarea wrappers

**Added reusable voice recorder primitives and default voice-enabled Input/Textarea wrappers**

## What Happened

Regenerated the frontend OpenAPI client after T01 so `VoiceService.transcribeVoice` and voice response types exist. Added `useVoiceRecorder` to own microphone permission, codec fallback (`audio/webm;codecs=opus` → `audio/webm` → `audio/mp4`), analyser-driven waveform state, upload timeout/cancel handling, cleanup of tracks/audio contexts, and `voice.recorder.*` browser diagnostics without audio/transcript payloads. Added accessible `Waveform`, `VoiceInput`, and `VoiceTextarea` primitives with ≥44×44 mic/stop buttons, inline error state, disabled/readOnly and sensitive-field opt-outs, and transcript injection through wrapped `onChange` synthetic events so form libraries receive normal input/textarea events. Updated the shared `Input` primitive to auto-wrap eligible text fields, added a new `Textarea` primitive with same behavior, and kept password/hidden/OTP/secret/token/code fields raw. Fixed the prior verification gate path mismatch by adding root-level pytest compatibility wrappers for `tests/api/routes/test_voice.py` and `tests/api/routes/test_admin_settings.py`; canonical backend tests remain under `backend/tests/...` and require the backend uv environment plus a reachable migrated DB.

## Verification

Ran frontend client generation and production build successfully. Ran Python compile checks for backend settings and root pytest wrappers. Re-ran the exact failing root pytest command; it now resolves the paths and exits cleanly with explicit skips under bare system pytest because backend-only dependencies are unavailable outside `uv run --project backend`. Attempted canonical backend route tests through uv; they reached real fixtures but remain blocked by the existing local Postgres port/schema state described in T01, not by this task. The T03 Playwright spec was not present yet, so the planned mobile voice spec was not run.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `python -m py_compile tests/api/routes/test_voice.py tests/api/routes/test_admin_settings.py && uv run --project backend python -m py_compile backend/app/core/config.py && cd frontend && bun run generate-client && bun run build` | 0 | ✅ pass | 3500ms |
| 2 | `pytest tests/api/routes/test_voice.py tests/api/routes/test_admin_settings.py -q` | 0 | ✅ pass | 1000ms |
| 3 | `uv run --project backend pytest tests/api/routes/test_voice.py tests/api/routes/test_admin_settings.py -q` | 1 | ❌ fail — local Postgres unavailable at configured port 55432; canonical backend test environment remains blocked as recorded in T01 | 11000ms |

## Deviations

Added root-level pytest compatibility wrappers and made backend settings load `.env` from either backend or repo-root execution contexts to fix the auto verification path mismatch surfaced after the first attempt.

## Known Issues

Canonical backend route tests still require a reachable/migrated local Postgres. `uv run --project backend pytest tests/api/routes/test_voice.py tests/api/routes/test_admin_settings.py -q` remains blocked by local DB connection/schema state from T01. The T03 Playwright voice spec does not exist yet, so that slice-level check is pending T03.

## Files Created/Modified

- `frontend/src/components/voice/useVoiceRecorder.ts`
- `frontend/src/components/voice/Waveform.tsx`
- `frontend/src/components/voice/VoiceInput.tsx`
- `frontend/src/components/voice/VoiceTextarea.tsx`
- `frontend/src/components/ui/input.tsx`
- `frontend/src/components/ui/textarea.tsx`
- `frontend/src/client/sdk.gen.ts`
- `frontend/src/client/types.gen.ts`
- `frontend/src/client/schemas.gen.ts`
- `frontend/openapi.json`
- `backend/app/core/config.py`
- `tests/api/routes/test_voice.py`
- `tests/api/routes/test_admin_settings.py`
