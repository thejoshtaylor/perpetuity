---
id: T01
parent: S01
milestone: M004-guylpp
key_files:
  - backend/app/core/encryption.py
  - orchestrator/orchestrator/encryption.py
  - backend/pyproject.toml
  - orchestrator/pyproject.toml
  - docker-compose.yml
  - .env.example
  - .env
  - backend/tests/integration/conftest.py
  - backend/tests/integration/test_m002_s05_two_key_rotation_e2e.py
key_decisions:
  - Fernet (cryptography library) over raw AES-GCM — library-vetted, automatic nonce, no manual primitive composition (D020 / MEM224)
  - Module-level @functools.cache loader; lazy validation fires on first encrypt/decrypt rather than at import — keeps dev ergonomics while preserving fail-loud production semantics
  - Orchestrator-side encryption.py is a 1:1 mirror of the backend module (parallel copies, not shared package) — different config surfaces between services would force a new packaging boundary M004 doesn't need (MEM230)
  - Single Fernet key for the entire e2e suite (test-only constant in conftest, mirrored in two-key-rotation test) — sensitive rows must remain decryptable across sibling-backend boots within one test run
  - Custom SystemSettingDecryptError(key: str | None) raised with key=None from decrypt_setting; call site attaches the row key — keeps the encryption module ignorant of which row a ciphertext belongs to
duration: 
verification_result: passed
completed_at: 2026-04-25T23:56:03.139Z
blocker_discovered: false
---

# T01: Add Fernet-backed encryption substrate (backend + orchestrator) and wire SYSTEM_SETTINGS_ENCRYPTION_KEY through compose, env, and the e2e fixture

**Add Fernet-backed encryption substrate (backend + orchestrator) and wire SYSTEM_SETTINGS_ENCRYPTION_KEY through compose, env, and the e2e fixture**

## What Happened

Landed the encryption substrate every later S01 task depends on. Added `cryptography>=43,<46` to both `backend/pyproject.toml` and `orchestrator/pyproject.toml` (resolved to 45.0.7 via uv). Created `backend/app/core/encryption.py` exposing `encrypt_setting(plaintext: str) -> bytes`, `decrypt_setting(ciphertext: bytes) -> str`, and a custom `SystemSettingDecryptError(key: str | None)`; the loader is a module-level `@functools.cache _load_key()` that reads `SYSTEM_SETTINGS_ENCRYPTION_KEY`, decodes it as url-safe base64, asserts a 32-byte length, and constructs the `Fernet` instance. Decode/length/missing failures all raise `RuntimeError` naming the env var; first successful load logs `system_settings_encryption_loaded key_prefix=<first_4>...`. Decrypt wraps `cryptography.fernet.InvalidToken` and re-raises `SystemSettingDecryptError(key=None)` so the call site can attach the row key and translate to a 503 + `system_settings_decrypt_failed key=<name>` ERROR log per the slice observability contract.

Mirrored the module 1:1 in `orchestrator/orchestrator/encryption.py` (same exports, same loader shape, same exception class) so S02 has a stable import target. The orchestrator-side mirror is intentionally a copy rather than a shared package import — the two services have different config surfaces (pydantic_settings.BaseSettings vs Settings + os.environ) and a shared package would force a new packaging boundary nothing else in M004 needs. Captured this in MEM230.

Wired `SYSTEM_SETTINGS_ENCRYPTION_KEY` into `docker-compose.yml` under both the `orchestrator:` (line 137) and `backend:` (line 220) `environment:` blocks with `?Variable not set` so a missing key fails at compose-up time. Added a one-line comment on each declaration explaining the stay-stable invariant. Appended a placeholder + generation command to `.env.example` (line 64) and a working Fernet key to the local `.env` so `docker compose up` works in dev. Threaded the same key into `backend/tests/integration/conftest.py` via a module-level `SYSTEM_SETTINGS_ENCRYPTION_KEY_TEST` constant injected into the sibling-backend `env_args`, and added the matching constant to `test_m002_s05_two_key_rotation_e2e.py` for both the ephemeral orchestrator boot and `_boot_sibling_backend`.

Captured MEM230 (parallel-encryption-modules pattern) and MEM231 (key-stability convention) so future agents understand the rotation gotcha without re-deriving it. No blocker discovered — every step in the inlined task plan landed cleanly.

## Verification

Ran every command from the task plan's Verification section plus the failure-mode probes. (1) `cd backend && uv sync` resolved 88 packages and installed cryptography 45.0.7. (2) `uv run python -c '...generate_key()...'` printed a valid 44-char key. (3) Backend round-trip: `encrypt_setting('hello')` → ciphertext → `decrypt_setting(ct) == 'hello'` ✅. (4) Orchestrator round-trip: same shape against `orchestrator.encryption` ✅. (5) `grep -c SYSTEM_SETTINGS_ENCRYPTION_KEY docker-compose.yml` → 2 (one per service block). (6) `grep -c SYSTEM_SETTINGS_ENCRYPTION_KEY backend/tests/integration/conftest.py` → 3 (constant decl + comment + env injection). (7) `docker compose config --quiet` validates and `docker compose config | grep SYSTEM_SETTINGS` shows the key resolved across orchestrator/prestart/backend service blocks. (8) Failure modes verified: missing key → RuntimeError naming env var; non-base64 → RuntimeError; 16-byte (wrong-length) decoded → RuntimeError mentioning 32-byte requirement; corrupted Fernet ciphertext → `SystemSettingDecryptError`. (9) `ruff check` and strict `mypy` both clean on both new modules.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd backend && uv sync` | 0 | ✅ pass | 800ms |
| 2 | `uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key())'` | 0 | ✅ pass | 250ms |
| 3 | `uv run python -c 'os.environ[...]=test_key; from app.core.encryption import encrypt_setting, decrypt_setting; assert decrypt_setting(encrypt_setting("hello"))=="hello"'` | 0 | ✅ pass | 350ms |
| 4 | `cd orchestrator && uv run python -c '...; from orchestrator.encryption import encrypt_setting, decrypt_setting; assert decrypt_setting(encrypt_setting("hello"))=="hello"'` | 0 | ✅ pass | 350ms |
| 5 | `grep -c SYSTEM_SETTINGS_ENCRYPTION_KEY docker-compose.yml  # expects 2 (orch + backend service blocks)` | 0 | ✅ pass (got 2) | 30ms |
| 6 | `grep -c SYSTEM_SETTINGS_ENCRYPTION_KEY backend/tests/integration/conftest.py  # expects ≥1 (got 3)` | 0 | ✅ pass (got 3) | 30ms |
| 7 | `docker compose config --quiet` | 0 | ✅ pass | 600ms |
| 8 | `uv run ruff check app/core/encryption.py` | 0 | ✅ pass | 200ms |
| 9 | `uv run mypy app/core/encryption.py (strict)` | 0 | ✅ pass | 1500ms |
| 10 | `uv run mypy orchestrator/encryption.py (strict)` | 0 | ✅ pass | 1500ms |
| 11 | `Failure-mode probe: missing/malformed/wrong-length key → RuntimeError; corrupted ciphertext → SystemSettingDecryptError` | 0 | ✅ pass (all 4 cases) | 400ms |

## Deviations

Added SYSTEM_SETTINGS_ENCRYPTION_KEY to the local .env (with a freshly-generated Fernet key) in addition to .env.example. The task plan only mentioned .env.example, but declaring the var as `?Variable not set` in compose makes it a hard requirement at compose-up time, so a stable .env value is needed for local dev to keep working. Threaded the same env var into the ephemeral orchestrator boot in test_m002_s05_two_key_rotation_e2e.py too (the plan only called out the sibling-backend boot) — defensive: keeps both services in sync now so the S02 read path doesn't have to revisit this file.

## Known Issues

none

## Files Created/Modified

- `backend/app/core/encryption.py`
- `orchestrator/orchestrator/encryption.py`
- `backend/pyproject.toml`
- `orchestrator/pyproject.toml`
- `docker-compose.yml`
- `.env.example`
- `.env`
- `backend/tests/integration/conftest.py`
- `backend/tests/integration/test_m002_s05_two_key_rotation_e2e.py`
