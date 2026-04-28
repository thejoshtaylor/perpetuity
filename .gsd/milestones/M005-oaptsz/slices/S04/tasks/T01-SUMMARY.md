---
id: T01
parent: S04
milestone: M005-oaptsz
key_files:
  - backend/app/api/routes/admin.py
  - backend/app/api/routes/voice.py
  - backend/app/api/main.py
  - backend/app/core/grok_stt.py
  - backend/app/core/rate_limit.py
  - backend/app/models.py
  - backend/pyproject.toml
  - backend/tests/api/routes/test_voice.py
  - backend/tests/api/routes/test_admin_settings.py
key_decisions:
  - Use Redis sorted-set sliding-window keys scoped as `voice:transcribe:{user_id}` for 30 requests per 60 seconds.
  - Keep Grok API key decryption inside the STT client call-site and never return or log sensitive key material.
  - Expose a limiter injection seam on the voice route so route tests can exercise rate-limit boundaries without live Redis.
duration: 
verification_result: mixed
completed_at: 2026-04-28T18:28:17.657Z
blocker_discovered: false
---

# T01: Added Grok STT proxy, encrypted voice settings, and per-user transcription rate limiting

**Added Grok STT proxy, encrypted voice settings, and per-user transcription rate limiting**

## What Happened

Implemented backend voice transcription foundation. Admin settings now register sensitive `grok_stt_api_key` and non-sensitive `max_voice_transcribes_per_hour_global`. Added Redis sorted-set sliding-window limiter with injection seam, Grok/xAI STT client that decrypts key only at call-site, validates upstream responses, maps timeout/status/bad JSON failures, and avoids logging key/audio/boundary/transcript data. Added authenticated `POST /api/v1/voice/transcribe` with upload content-type/empty/oversize checks, 30-per-60s per-user limiter, `Retry-After` on 429, and structured `voice.transcribe.*` logs. Added route/admin tests covering happy path, auth, validation, rate limiting, redaction, missing key, upstream errors, and settings registry behavior.

## Verification

Ran static and compile checks successfully. Ran isolated FastAPI route harness proving happy-path transcript, unsupported content-type rejection, and 429 `Retry-After` behavior without relying on broken local DB. Ran planned pytest command, but local verification DB is stale: `.env` points to unused port 55432; with `POSTGRES_PORT=5432`, setup fails before tests because `user.role` column is missing. Attempted Alembic upgrade, but local DB cannot locate revision `z2y_m041_ghl_push_layer`. This is environment state, not a code-path failure; recorded as known issue.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && uv run ruff check app/api/routes/admin.py tests/api/routes/test_admin_settings.py app/api/routes/voice.py app/core/grok_stt.py app/core/rate_limit.py tests/api/routes/test_voice.py` | 0 | ✅ pass | 6300ms |
| 2 | `cd backend && uv run python -m py_compile app/api/routes/admin.py app/api/routes/voice.py app/core/grok_stt.py app/core/rate_limit.py tests/api/routes/test_admin_settings.py tests/api/routes/test_voice.py` | 0 | ✅ pass | 1000ms |
| 3 | `cd backend && uv run python - <<'PY' ... isolated FastAPI voice route harness ... PY` | 0 | ✅ pass | 1000ms |
| 4 | `cd backend && pytest tests/api/routes/test_voice.py tests/api/routes/test_admin_settings.py -q` | 4 | ❌ fail — system python missing existing dependency `httpx_ws` | 3000ms |
| 5 | `cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_voice.py tests/api/routes/test_admin_settings.py -q` | 1 | ❌ fail — local DB fixture setup failed before tests (`user.role` column missing) | 8000ms |
| 6 | `cd backend && POSTGRES_PORT=5432 uv run alembic upgrade head` | 255 | ❌ fail — local Alembic state missing revision `z2y_m041_ghl_push_layer` | 5200ms |

## Deviations

Added explicit `redis` dependency to backend pyproject because backend now imports `redis.asyncio`. Added isolated route harness verification because local route pytest is blocked by stale DB/Alembic state.

## Known Issues

Focused pytest could not complete in this local environment: first run failed because Postgres was unavailable at `.env` port 55432; retry against compose port 5432 failed in fixture setup due stale DB schema missing `user.role`; Alembic upgrade failed with missing revision `z2y_m041_ghl_push_layer`. Full test pass needs DB migration state repair outside this task.

## Files Created/Modified

- `backend/app/api/routes/admin.py`
- `backend/app/api/routes/voice.py`
- `backend/app/api/main.py`
- `backend/app/core/grok_stt.py`
- `backend/app/core/rate_limit.py`
- `backend/app/models.py`
- `backend/pyproject.toml`
- `backend/tests/api/routes/test_voice.py`
- `backend/tests/api/routes/test_admin_settings.py`
