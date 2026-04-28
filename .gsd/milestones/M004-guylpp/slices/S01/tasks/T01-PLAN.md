---
estimated_steps: 3
estimated_files: 7
skills_used: []
---

# T01: Add cryptography dep + Fernet encryption module + env wiring (backend + orchestrator)

Land the encryption substrate that every later task in this slice depends on. Add `cryptography` to `backend/pyproject.toml` and `orchestrator/pyproject.toml`. Create `backend/app/core/encryption.py` exposing `encrypt_setting(plaintext: str) -> bytes` and `decrypt_setting(ciphertext: bytes) -> str` plus a private `_load_key()` that reads `SYSTEM_SETTINGS_ENCRYPTION_KEY` from env, validates it is 32 url-safe base64 bytes, and constructs a `cryptography.fernet.Fernet` instance. The loader is module-level and lazy (`@functools.cache`), so import does NOT crash when no sensitive key is registered yet; the first encrypt/decrypt call triggers validation. Decrypt wraps `cryptography.fernet.InvalidToken` and re-raises as a custom `SystemSettingDecryptError(key: str | None)` whose handler sites translate to a 503 with the structured ERROR log. Mirror the same module shape in `orchestrator/orchestrator/encryption.py` (Fernet, same env var name, same exceptions) so S02 has a stable import target — the orchestrator does NOT need the cache yet but the file shape must match. Wire `SYSTEM_SETTINGS_ENCRYPTION_KEY` through `docker-compose.yml` (backend service env + orchestrator service env, both read from the root `.env`), append a placeholder line to `.env.example` if one exists in the repo, and thread the same env var into the e2e conftest's sibling-backend fixture (`backend/tests/integration/conftest.py`) and the two-key-rotation `_boot_sibling_backend` helper if it currently exists with its own env list. Generate a stable test key once and bake it into the fixture (the same value across tests is fine — it's a test-only secret). Add a one-line comment in compose explaining that this key MUST stay stable across restarts because rotating it without re-encrypting all sensitive rows breaks every sensitive-key read.

Failure modes: if `cryptography` is missing in the runtime image, `import` time will raise — that is the desired failure shape (loud at boot, not silent at first call). If `SYSTEM_SETTINGS_ENCRYPTION_KEY` is set but malformed, `_load_key()` raises `RuntimeError` with a message naming the env var; this propagates out of the first encrypt/decrypt call.

Assumptions documented inline in the module docstring: (1) we use Fernet rather than raw AES-GCM because Fernet is library-vetted with no manual nonce management (D020); (2) key rotation is out of scope for M004 and is tracked in the M004 operator runbook landing in S07; (3) the orchestrator-side mirror module is intentionally a copy rather than a shared package import because the two services have different config surfaces (`pydantic_settings.BaseSettings` in orchestrator vs. `os.environ` plus `Settings` in backend) and a shared package would force a new packaging boundary in M004 that nothing else in the milestone needs.

## Inputs

- ``backend/pyproject.toml``
- ``orchestrator/pyproject.toml``
- ``docker-compose.yml``
- ``backend/tests/integration/conftest.py``
- ``backend/tests/integration/test_m002_s05_two_key_rotation_e2e.py``

## Expected Output

- ``backend/app/core/encryption.py``
- ``orchestrator/orchestrator/encryption.py``
- ``backend/pyproject.toml``
- ``orchestrator/pyproject.toml``
- ``docker-compose.yml``
- ``backend/tests/integration/conftest.py``

## Verification

From `/Users/josh/code/perpetuity`: (1) `cd backend && uv sync` succeeds and `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key())'` prints a key; (2) `cd backend && uv run python -c 'import os; os.environ["SYSTEM_SETTINGS_ENCRYPTION_KEY"]="<test_key>"; from app.core.encryption import encrypt_setting, decrypt_setting; ct=encrypt_setting("hello"); assert decrypt_setting(ct)=="hello"'` round-trips; (3) `cd orchestrator && uv run python -c 'import os; os.environ["SYSTEM_SETTINGS_ENCRYPTION_KEY"]="<test_key>"; from orchestrator.encryption import encrypt_setting, decrypt_setting; ct=encrypt_setting("hello"); assert decrypt_setting(ct)=="hello"'` round-trips; (4) `grep -q SYSTEM_SETTINGS_ENCRYPTION_KEY docker-compose.yml` matches under both `backend:` and `orchestrator:` service blocks; (5) `grep -q SYSTEM_SETTINGS_ENCRYPTION_KEY backend/tests/integration/conftest.py` matches.

## Observability Impact

ERROR taxonomy `system_settings_decrypt_failed key=<name>` is the single source of truth for decrypt failures — raised from `decrypt_setting`'s `InvalidToken` handler with `key` attached by the caller. INFO `system_settings_encryption_loaded key_prefix=<first_4_chars>` at first encrypt/decrypt call so operators can see the loader fired. The `key_prefix` is intentionally truncated to 4 chars so the log proves the key changed without leaking the full secret.
