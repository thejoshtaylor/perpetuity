---
id: T03
parent: S01
milestone: M004-guylpp
key_files:
  - backend/app/api/routes/admin.py
  - backend/app/main.py
  - backend/tests/api/routes/test_admin_settings.py
key_decisions:
  - _SettingSpec frozen dataclass + module-load assertion 'generators are sensitive-only' — misregistrations fail at import, not at first leak (MEM232)
  - Single global FastAPI exception_handler in main.py for SystemSettingDecryptError — every decrypt site raises and never catches; the handler is the one place that emits 503 + ERROR log, eliminating call-site drift (MEM233)
  - Structural PEM validator at the API boundary (begins/ends/length 64..16384) rather than load_pem_private_key — keeps hazmat off the PUT hot path; semantic validation deferred to S02's first JWT-sign call where bad bytes will surface with the same fail-loud structured error
  - PutResponse for sensitive keys returns value=None — plaintext crosses the API boundary EXACTLY ONCE, on the generate response; subsequent GETs/list/PUT-responses always redact
  - Tests monkeypatch SYSTEM_SETTINGS_ENCRYPTION_KEY and clear app.core.encryption._load_key.cache because pydantic-settings populates Settings but encryption.py reads os.environ directly (gotcha MEM234)
duration: 
verification_result: passed
completed_at: 2026-04-26T00:11:53.188Z
blocker_discovered: false
---

# T03: Extend admin /settings registry with sensitive _SettingSpec, redacted GET/list, generate endpoint, and global decrypt-failure 503 handler

**Extend admin /settings registry with sensitive _SettingSpec, redacted GET/list, generate endpoint, and global decrypt-failure 503 handler**

## What Happened

Refactored `_VALIDATORS: dict[str, Callable]` in `backend/app/api/routes/admin.py` into `_VALIDATORS: dict[str, _SettingSpec]` where `_SettingSpec` is a `dataclass(frozen=True)` carrying `validator | None`, `sensitive: bool`, `generator | None`. The two existing M002 keys (`workspace_volume_size_gb`, `idle_timeout_seconds`) are wrapped with `sensitive=False, generator=None` and unchanged validators. Registered four new GitHub-App keys: `github_app_id` (int 1..2**63-1, JSONB), `github_app_client_id` (non-empty ASCII ≤255, JSONB), `github_app_private_key` (structural PEM 64..16384, sensitive), `github_app_webhook_secret` (validator=None, sensitive, generator=`secrets.token_urlsafe(32)`). A module-load assertion enforces "generators are sensitive-only" so a future misregistration that would store a generated secret as plaintext JSONB fails at import.

Reworked `put_system_setting`: when `spec.sensitive` is true, plaintext is run through `encrypt_setting` from T01 and UPSERTed into BYTEA `value_encrypted` with `value=NULL, sensitive=true, has_value=true` via a new `_upsert_encrypted` helper; otherwise it takes the existing JSONB path through `_upsert_jsonb` with `sensitive=false, has_value=true`. The shrink-warnings branch is unchanged for `workspace_volume_size_gb`. The PUT response for sensitive keys returns `value=None` — the plaintext does NOT cross the API boundary on PUT, only on the one-shot generate response. The log line now extends M002's shape with `sensitive=<bool>`: `system_setting_updated actor_id=<uuid> key=<name> sensitive=<bool> previous_value_present=<bool>`. Reworked `get_system_setting` and `list_system_settings` through a single `_redact()` helper that always returns `value=None` for sensitive rows; `has_value` is the source of truth for the FE's Set vs Replace UI decision.

Added `POST /admin/settings/{key}/generate` returning `SystemSettingGenerateResponse(key, value, has_value=true, generated=true, updated_at)`. 422 with `unknown_setting_key` for unregistered keys; 422 with `no_generator_for_key` for registered keys without a generator (e.g. `github_app_private_key` whose seed is operator-provided). Successful generate emits INFO `system_setting_generated actor_id=<uuid> key=<name>` (plaintext is never logged). Re-calling generate is intentionally destructive on every call (D025) — documented inline; an operator must rotate upstream first to avoid breaking in-flight webhook deliveries.

Registered the global `SystemSettingDecryptError` exception handler in `backend/app/main.py`. The single fan-in for every decrypt failure (admin GETs today, S02 JWT-sign callers tomorrow): the handler emits ERROR `system_settings_decrypt_failed key=<name>` and returns `JSONResponse(status_code=503, content={'detail': 'system_settings_decrypt_failed', 'key': exc.key})`. Plaintext is impossible to leak through this path because `decrypt_setting` never carries it on the exception. The PEM validator at the API boundary is intentionally structural (begins/ends/length) — semantic validation is deferred to S02's first JWT-sign call so the operator gets a fast PUT response and bad-PEM bytes surface at the call site that actually needs to use them, with the same fail-loud structured error.

Added 19 new unit tests in `backend/tests/api/routes/test_admin_settings.py` covering: sensitive PUT redaction (response + GET + list), the new `system_setting_updated sensitive=true` log line + plaintext-not-leaked invariant, PEM validator (happy, malformed, too-short), non-sensitive `github_app_id` (int range + bool rejection) and `github_app_client_id` (string + empty rejection), generate happy-path (one-shot plaintext, subsequent redaction, regenerate yields fresh value), generate logs `system_setting_generated`, generate 422 shapes (unknown key, no generator), generate auth gating (401/403), corrupted-ciphertext → `SystemSettingDecryptError`, the global handler turning the exception into 503 + ERROR log, and the lazy-load `SYSTEM_SETTINGS_ENCRYPTION_KEY` RuntimeError contract. An autouse fixture sets the test Fernet key in `os.environ` and clears `_load_key.cache` so the lazy loader picks it up — necessary because pydantic-settings populates `Settings` but not `os.environ`, and `encryption.py` reads env directly (gotcha captured as MEM234).

The verification gate's earlier `uv run alembic downgrade -1` failure was an environmental config drift, not a T03 regression: the local `.env` has `POSTGRES_PORT=55432` while the running `perpetuity-db-1` container exposes 5432. Running with `POSTGRES_PORT=5432` round-trips cleanly through s06↔s05. Captured as MEM235 so future agents do not chase a phantom code bug.

## Verification

All five task-plan verification commands pass: (1) `cd backend && POSTGRES_PORT=5432 uv run pytest tests/api -k 'settings' -v` — 45 passed (26 existing M002/S03 back-compat + 19 new M004/S01); (2) `grep -q 'github_app_private_key' backend/app/api/routes/admin.py` matches; (3) `grep -q 'system_settings_decrypt_failed' backend/app/main.py` matches; (4) `python -c 'from app.api.routes.admin import _VALIDATORS; assert _VALIDATORS["github_app_webhook_secret"].sensitive is True and _VALIDATORS["github_app_webhook_secret"].generator is not None'` succeeds; (5) `python -c 'from app.api.routes.admin import _VALIDATORS; assert _VALIDATORS["workspace_volume_size_gb"].sensitive is False'` succeeds. The 7 pre-existing failures in `test_sessions.py` are unrelated (orchestrator returning 500 from a sibling instance) and reproduce on the prior commit (verified via `git stash`).

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && POSTGRES_PORT=5432 uv run pytest tests/api -k 'settings' -v` | 0 | ✅ pass — 45 passed, 143 deselected (26 M002/S03 back-compat + 19 new M004/S01 sensitive/generate/decrypt-handler tests) | 770ms |
| 2 | `grep -q 'github_app_private_key' backend/app/api/routes/admin.py` | 0 | ✅ pass — registry references the sensitive PEM key | 20ms |
| 3 | `grep -q 'system_settings_decrypt_failed' backend/app/main.py` | 0 | ✅ pass — global exception handler registered with structured detail | 20ms |
| 4 | `uv run python -c 'from app.api.routes.admin import _VALIDATORS; assert _VALIDATORS["github_app_webhook_secret"].sensitive is True and _VALIDATORS["github_app_webhook_secret"].generator is not None'` | 0 | ✅ pass — webhook secret spec is sensitive and has a generator | 800ms |
| 5 | `uv run python -c 'from app.api.routes.admin import _VALIDATORS; assert _VALIDATORS["workspace_volume_size_gb"].sensitive is False'` | 0 | ✅ pass — back-compat: existing M002 key remains non-sensitive | 800ms |
| 6 | `cd backend && POSTGRES_PORT=5432 uv run alembic downgrade -1 && uv run alembic upgrade head` | 0 | ✅ pass — s06 round-trip works with correct port; the gate's failure was env config drift (.env POSTGRES_PORT=55432 vs container 5432), not a T03 regression | 3500ms |

## Deviations

None.

## Known Issues

"Pre-existing test_sessions.py failures (7) reproduce on prior commit via git stash — orchestrator-side 500s unrelated to T03; not in scope for this slice. Verification gate's `alembic downgrade -1` failure was caused by .env POSTGRES_PORT=55432 not matching the running container's host-published 5432; running with POSTGRES_PORT=5432 round-trips s06↔s05 cleanly. Captured as environment memory MEM235."

## Files Created/Modified

- `backend/app/api/routes/admin.py`
- `backend/app/main.py`
- `backend/tests/api/routes/test_admin_settings.py`
