---
estimated_steps: 58
estimated_files: 9
skills_used: []
---

# T01: Build Grok STT proxy with encrypted key and rate limit

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

## Inputs

- `backend/app/api/routes/admin.py`
- `backend/app/api/main.py`
- `backend/app/core/encryption.py`
- `backend/tests/api/routes/test_push.py`
- `backend/pyproject.toml`

## Expected Output

- `backend/app/api/routes/admin.py`
- `backend/app/api/routes/voice.py`
- `backend/app/api/main.py`
- `backend/app/core/grok_stt.py`
- `backend/app/core/rate_limit.py`
- `backend/app/models.py`
- `backend/pyproject.toml`
- `backend/tests/api/routes/test_voice.py`
- `backend/tests/api/routes/test_admin_settings.py`

## Verification

cd backend && pytest tests/api/routes/test_voice.py tests/api/routes/test_admin_settings.py -q

## Observability Impact

Adds backend voice diagnostics and rate-limit/upstream failure state without leaking key/audio/multipart data.
