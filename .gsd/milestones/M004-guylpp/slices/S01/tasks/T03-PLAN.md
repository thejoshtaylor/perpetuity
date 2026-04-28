---
estimated_steps: 12
estimated_files: 2
skills_used: []
---

# T03: Extend admin /settings registry: sensitive validators, redacted GET, generate endpoint, fail-loud decrypt

This is the API-surface task. Edits all live in `backend/app/api/routes/admin.py` plus a new exception handler registration in `backend/app/main.py` (or the admin router include site, whichever is the existing pattern — read the file first).

Refactor `_VALIDATORS: dict[str, Callable]` to `_VALIDATORS: dict[str, _SettingSpec]` where `_SettingSpec` is a small `dataclass(frozen=True)` with fields `validator: Callable[[Any], None] | None`, `sensitive: bool`, `generator: Callable[[], str] | None`. Update the existing `WORKSPACE_VOLUME_SIZE_GB_KEY` and `IDLE_TIMEOUT_SECONDS_KEY` registrations to wrap their validators in the new spec (`sensitive=False, generator=None`). Register four new GitHub-App keys:
  - `github_app_id`: int validator [1, 2**63-1], `sensitive=False`, `generator=None`. Stores into JSONB `value` (back-compat path).
  - `github_app_client_id`: str validator (non-empty, ≤255 chars, ASCII), `sensitive=False`, `generator=None`. Same JSONB storage path.
  - `github_app_private_key`: PEM validator (str startswith `-----BEGIN` and contains `-----END`, length 64..16384 to bound payload size), `sensitive=True`, `generator=None`.
  - `github_app_webhook_secret`: validator is None (sensitive keys with a generator don't need a separate validator — generator output is trusted), `sensitive=True`, `generator=lambda: secrets.token_urlsafe(32)`.

Rework `put_system_setting`: after the validator passes, if `spec.sensitive` is True → call `encrypt_setting(body.value)`, UPSERT with `value=NULL, value_encrypted=:ct, sensitive=true, has_value=true`; if `spec.sensitive` is False → keep the existing JSONB UPSERT path but set `sensitive=false, has_value=true`. The existing shrink-warnings branch is unchanged. Logging: emit `system_setting_updated actor_id=<uuid> key=<name> sensitive=<bool> previous_value_present=<bool>` — never log the value, never log the ciphertext.

Rework `get_system_setting` and `list_system_settings`: when the row's `sensitive=true`, return `value=null` regardless of whether `value_encrypted` is populated. The `has_value` field is the boolean clients use to render `Set` vs `Replace` in the UI. Non-sensitive rows are unchanged.

Add `POST /admin/settings/{key}/generate` → reads the spec, 422 if `spec.generator is None` with detail `{detail: 'no_generator_for_key', key}`, 422 if the key is unregistered (same `unknown_setting_key` shape as PUT), otherwise calls `value = spec.generator()`, encrypts, UPSERTs with `sensitive=true, has_value=true` (generators are sensitive-only by design — assert this at module load to fail-fast on a misregistration), emits `system_setting_generated actor_id=<uuid> key=<name>` and returns `SystemSettingGenerateResponse(key, value, has_value=true, generated=true, updated_at=row.updated_at)`. The plaintext value crosses the API boundary exactly once on this response.

Add a single global-purpose exception handler in `backend/app/main.py` (or admin router include — read the file first to find the canonical registration point): catch `SystemSettingDecryptError` and translate to `JSONResponse(status_code=503, content={'detail': 'system_settings_decrypt_failed', 'key': exc.key})` plus an ERROR-level log `system_settings_decrypt_failed key=<name>`. This handler is the single fan-in for every decrypt failure, no matter which call site raised — the `decrypt_setting` helper from T01 raises this, callers don't catch.

Doc/inline comment: explain that the `generate` endpoint is intentionally destructive on re-call (D025) — re-generating breaks all in-flight webhooks until the upstream (GitHub) is updated. The destructive semantics are an operator safety contract, not a bug.

Assumption documented inline: PEM validation does not parse the key with `cryptography.hazmat.primitives.serialization.load_pem_private_key` because that would require importing the heavy hazmat layer in the API path; the structural validator (begins/ends/length) is sufficient at the API boundary, and S02's first JWT-sign call will fail loudly with a structured error if the bytes happen to be non-PEM. Operator gets a fast PUT response; bad PEM surfaces at the decrypt-and-sign call site in S02.

## Inputs

- ``backend/app/api/routes/admin.py``
- ``backend/app/main.py``
- ``backend/app/core/encryption.py``
- ``backend/app/models.py``

## Expected Output

- ``backend/app/api/routes/admin.py``
- ``backend/app/main.py``

## Verification

From `/Users/josh/code/perpetuity`: (1) `cd backend && uv run pytest tests/api -k 'settings' -v` passes (the existing M002/S03 unit tests still go green — back-compat); (2) `grep -q 'github_app_private_key' backend/app/api/routes/admin.py` matches; (3) `grep -q 'system_settings_decrypt_failed' backend/app/main.py` matches; (4) `cd backend && uv run python -c 'from app.api.routes.admin import _VALIDATORS; assert _VALIDATORS["github_app_webhook_secret"].sensitive is True and _VALIDATORS["github_app_webhook_secret"].generator is not None'` succeeds; (5) `cd backend && uv run python -c 'from app.api.routes.admin import _VALIDATORS; assert _VALIDATORS["workspace_volume_size_gb"].sensitive is False'` succeeds (back-compat).

## Observability Impact

Adds INFO `system_setting_generated`, extends INFO `system_setting_updated` with `sensitive=<bool>` field, and registers the global ERROR `system_settings_decrypt_failed` handler. Inspection: `GET /admin/settings` is the operator surface for verifying which keys are registered, which are sensitive, and which are populated; failure visibility: 503 body and ERROR log both name the failing key (never the value), so an operator triaging a decrypt failure can localize to the registered key in one log search.
