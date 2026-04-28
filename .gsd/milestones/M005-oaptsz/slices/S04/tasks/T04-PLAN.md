---
estimated_steps: 60
estimated_files: 6
skills_used: []
---

# T04: Close slice with cross-boundary verification and redaction gates

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

## Inputs

- `backend/app/api/routes/voice.py`
- `backend/tests/api/routes/test_voice.py`
- `frontend/src/client/sdk.gen.ts`
- `frontend/src/client/types.gen.ts`
- `frontend/tests/m005-oaptsz-voice.spec.ts`
- `frontend/tests/m005-oaptsz-mobile-audit.spec.ts`

## Expected Output

- `backend/tests/api/routes/test_voice.py`
- `frontend/tests/m005-oaptsz-voice.spec.ts`
- `frontend/tests/m005-oaptsz-mobile-audit.spec.ts`
- `frontend/src/client/sdk.gen.ts`
- `frontend/src/client/types.gen.ts`
- `backend/app/api/routes/voice.py`

## Verification

cd backend && pytest tests/api/routes/test_voice.py tests/api/routes/test_admin_settings.py -q && cd ../frontend && bun run generate-client && bun run build && bunx playwright test --project=mobile-chrome m005-oaptsz-voice.spec.ts

## Observability Impact

Locks final diagnostic and redaction contracts into executable tests before slice completion.
