---
id: S01
parent: M004-guylpp
milestone: M004-guylpp
provides:
  - ["backend/app/core/encryption.py: encrypt_setting, decrypt_setting, SystemSettingDecryptError(key)", "orchestrator/orchestrator/encryption.py: 1:1 mirror for S02 import", "backend/app/alembic/versions/s06_system_settings_sensitive.py: value_encrypted BYTEA NULL, sensitive BOOL, has_value BOOL; relaxes value JSONB to NULLABLE", "SystemSetting model fields value_encrypted/sensitive/has_value", "SystemSettingPublic shape (key, sensitive, has_value, value: Any | None, updated_at)", "SystemSettingGenerateResponse(key, value, has_value, generated, updated_at)", "_VALIDATORS registry with _SettingSpec(validator, sensitive, generator) and module-load 'generators are sensitive-only' assertion", "Four registered keys: github_app_id (public int), github_app_client_id (public str), github_app_private_key (sensitive PEM, no generator), github_app_webhook_secret (sensitive, generator=secrets.token_urlsafe(32))", "POST /admin/settings/{key}/generate (one-time-display)", "Global FastAPI exception_handler for SystemSettingDecryptError → 503 + ERROR log", "INFO log keys: system_setting_updated (extended with sensitive=<bool>), system_setting_generated. ERROR log key: system_settings_decrypt_failed key=<name>", "SYSTEM_SETTINGS_ENCRYPTION_KEY env var wired through docker-compose.yml (backend + orchestrator), .env.example, .env, conftest.py sibling-backend fixture, and M002 two-key-rotation helper"]
requires:
  - slice: M002/S03
    provides: system_settings table + _VALIDATORS registry pattern in backend/app/api/routes/admin.py
  - slice: M002/S05
    provides: two-key-rotation e2e helper that needed SYSTEM_SETTINGS_ENCRYPTION_KEY threaded through
affects:
  - ["S02 (consumes decrypt_setting + github_app_id/client_id/private_key keys)", "S05 (consumes decrypt_setting + github_app_webhook_secret key)", "S06 (frontend renders sensitive-key UI: lock icon, Set/Generate/Replace, one-time-display modal)", "S07 (operator runbook for key/secret rotation)"]
key_files:
  - ["backend/app/core/encryption.py", "orchestrator/orchestrator/encryption.py", "backend/app/alembic/versions/s06_system_settings_sensitive.py", "backend/app/models.py", "backend/app/api/routes/admin.py", "backend/app/main.py", "backend/tests/api/routes/test_admin_settings.py", "backend/tests/integration/test_m004_s01_sensitive_settings_e2e.py", "backend/tests/integration/conftest.py", "docker-compose.yml", ".env.example", ".env", "backend/pyproject.toml", "orchestrator/pyproject.toml"]
key_decisions:
  - ["Fernet (cryptography library) over raw AES-GCM — library-vetted, automatic nonce, no manual primitive composition", "Backend and orchestrator each carry their own 1:1 mirrored encryption.py (parallel copies, not shared package) — different config surfaces between services would force a packaging boundary M004 doesn't need", "Single global FastAPI exception_handler in main.py for SystemSettingDecryptError — every decrypt site raises and never catches; one place emits 503 + ERROR log, eliminating call-site drift", "_SettingSpec frozen dataclass + module-load 'generators are sensitive-only' assertion — misregistration that would store a generated secret as plaintext JSONB fails at import, not at first leak", "Structural PEM validator at the API boundary (begins/ends/length) rather than load_pem_private_key — keeps hazmat off the PUT hot path; semantic validation deferred to S02's first JWT-sign call", "Plaintext crosses the API boundary EXACTLY ONCE on the generate response — every other read path (GET, list, PUT response) is redacted via _redact()", "decrypt-failure log assertion in e2e routed via PID 1 stderr (open('/proc/1/fd/2','w')) so docker-exec script output lands on the same stream docker logs reads (MEM236)", "Re-calling generate is intentionally destructive on every call (D025) — operator must rotate upstream first to avoid breaking in-flight webhook deliveries"]
patterns_established:
  - ["decrypt_setting() import target stable for S02 (orchestrator) and S05 (backend webhook receiver)", "Single-fan-in SystemSettingDecryptError handler in main.py — call sites raise, handler emits 503 + ERROR log", "_SettingSpec(sensitive=True, generator=...) registry pattern with module-load 'generators are sensitive-only' invariant", "One-shot plaintext discipline: plaintext crosses API boundary only on generate response, never on PUT/GET/list", "e2e skip-guard probing baked image for new alembic revision (mirror of MEM147/MEM162)", "Autouse cleanup fixture DELETEing system_settings rows before AND after each e2e (MEM161 — app-db-data persistence)", "docker-exec log-line emission via PID 1 stderr redirect for log-shape assertions"]
observability_surfaces:
  - ["INFO system_setting_updated actor_id=<uuid> key=<name> sensitive=<bool> previous_value_present=<bool>", "INFO system_setting_generated actor_id=<uuid> key=<name>", "ERROR system_settings_decrypt_failed key=<name>", "INFO system_settings_encryption_loaded key_prefix=<first_4>... (one-shot at first decrypt site)", "GET /api/v1/admin/settings (list with sensitive + has_value flags)", "psql operator-side: SELECT key, sensitive, has_value, length(value_encrypted) FROM system_settings"]
drill_down_paths:
  - [".gsd/milestones/M004-guylpp/slices/S01/tasks/T01-SUMMARY.md", ".gsd/milestones/M004-guylpp/slices/S01/tasks/T02-SUMMARY.md", ".gsd/milestones/M004-guylpp/slices/S01/tasks/T03-SUMMARY.md", ".gsd/milestones/M004-guylpp/slices/S01/tasks/T04-SUMMARY.md"]
duration: ""
verification_result: passed
completed_at: 2026-04-26T00:29:51.308Z
blocker_discovered: false
---

# S01: Sensitive system_settings + GitHub App credentials registered

**Sensitive system_settings storage is end-to-end: Fernet encryption substrate, four registered GitHub App keys, redacted GETs, one-shot generate semantics, and a single global decrypt-failure 503 handler — all proven by an e2e that round-trips a synthetic PEM, generates a webhook secret twice, corrupts ciphertext, and sweeps logs for redaction.**

## What Happened

## Outcome

S01 establishes the encryption substrate that every later slice in M004 builds on. After this slice an admin can paste the GitHub App private key (PEM) once via PUT and subsequent GETs return `{value: null, has_value: true, sensitive: true}`; an admin can POST `/admin/settings/github_app_webhook_secret/generate` to seed a server-side `secrets.token_urlsafe(32)` and receive the value exactly once in the response with all subsequent reads redacted; the orchestrator-side `decrypt_setting()` is import-clean for S02 to consume; corrupted Fernet ciphertext at any decrypt site surfaces as 503 with a `system_settings_decrypt_failed key=<name>` ERROR log and never silently falls back.

## Architecture Established

**Encryption substrate (T01).** `backend/app/core/encryption.py` and `orchestrator/orchestrator/encryption.py` are 1:1 mirrored Fernet helpers — same exports (`encrypt_setting`, `decrypt_setting`, `SystemSettingDecryptError`), same lazy `@functools.cache _load_key()` reading `SYSTEM_SETTINGS_ENCRYPTION_KEY` from os.environ, same exception class. Fernet was chosen over raw AES-GCM (D020/MEM224) because it's library-vetted with automatic nonce management. The two modules are intentionally parallel copies rather than a shared package — the services have different config surfaces (pydantic-settings BaseSettings in orchestrator vs. Settings + os.environ in backend) and a shared package would force a new packaging boundary M004 doesn't need (MEM230). `cryptography>=43,<46` resolved to 45.0.7 in both pyproject.toml files. `SYSTEM_SETTINGS_ENCRYPTION_KEY` is wired through docker-compose.yml under both services with `?Variable not set` so missing keys fail at compose-up, plus a placeholder in .env.example, a working dev key in .env, and a stable test constant injected into the e2e conftest's sibling-backend env_args and the M002 two-key-rotation helper.

**Schema (T02).** Migration `s06_system_settings_sensitive` (down_revision `s05_system_settings`) adds three columns — `value_encrypted BYTEA NULL`, `sensitive BOOLEAN NOT NULL DEFAULT FALSE`, `has_value BOOLEAN NOT NULL DEFAULT FALSE` — and relaxes `value JSONB` to NULLABLE so sensitive rows can store NULL there. Existing M002 rows (`workspace_volume_size_gb`, `idle_timeout_seconds`) keep their JSONB `value` and inherit `sensitive=false`. The downgrade re-tightens `value` to NOT NULL safely because all M002 rows have non-null values today. SQLModel `SystemSetting` gained the three fields; `SystemSettingPublic` always carries `key, sensitive, has_value, value: Any | None, updated_at` (no per-key shape branching); `SystemSettingGenerateResponse(key, value: str, has_value=True, generated=True, updated_at)` was added for the one-time-display POST. The model layer stays purely declarative — encryption logic lives only in T01's helpers and the T03 API layer.

**API layer (T03).** `_VALIDATORS` is now `dict[str, _SettingSpec]` where `_SettingSpec` is a `dataclass(frozen=True)` with `validator | None`, `sensitive: bool`, `generator | None`. Four registered keys: `github_app_id` (int 1..2**63-1, JSONB), `github_app_client_id` (non-empty ASCII ≤255, JSONB), `github_app_private_key` (structural PEM 64..16384, sensitive, no generator), `github_app_webhook_secret` (no validator, sensitive, generator=`secrets.token_urlsafe(32)`). A module-load assertion enforces "generators are sensitive-only" so a future misregistration that would store a generated secret as plaintext JSONB fails at import, not at first leak (MEM232). `put_system_setting` branches on `spec.sensitive`: sensitive paths run through `_upsert_encrypted` writing BYTEA `value_encrypted` with `value=NULL`; non-sensitive paths take the existing JSONB `_upsert_jsonb` path. The PUT response always returns `value=None` for sensitive keys — plaintext crosses the API boundary EXACTLY ONCE, on the generate response. `get_system_setting` and `list_system_settings` route through a single `_redact()` helper that returns `value=None` for any sensitive row regardless of whether `value_encrypted` is populated; `has_value` is the FE's source of truth for Set vs Replace UI decisions.

**Generate endpoint (T03).** `POST /admin/settings/{key}/generate` returns 422 `unknown_setting_key` for unregistered keys, 422 `no_generator_for_key` for registered keys without a generator (e.g. `github_app_private_key` whose seed must come from the operator), and otherwise calls `spec.generator()`, encrypts, UPSERTs, emits INFO `system_setting_generated actor_id=<uuid> key=<name>` (plaintext never logged), and returns `SystemSettingGenerateResponse`. Re-calling generate is intentionally destructive (D025) — operators must rotate upstream first to avoid breaking in-flight webhook deliveries. Documented inline in the route handler.

**Decrypt-failure observability (T03).** Single global FastAPI `exception_handler` in `backend/app/main.py` for `SystemSettingDecryptError`: emits ERROR `system_settings_decrypt_failed key=<name>` and returns `JSONResponse(status_code=503, content={'detail': 'system_settings_decrypt_failed', 'key': exc.key})`. Every decrypt call site raises and never catches — the handler is the single fan-in (MEM233). The exception class never carries plaintext (only the row key), so no leak is possible through this path. The PEM validator at the API boundary is intentionally structural rather than `load_pem_private_key` — keeps hazmat off the PUT hot path; semantic validation is deferred to S02's first JWT-sign call where bad bytes will surface with the same fail-loud structured error.

## Verification Coverage

- **Unit (T03):** 19 new tests in `backend/tests/api/routes/test_admin_settings.py` covering sensitive PUT redaction (response + GET + list), the new `system_setting_updated sensitive=true` log line + plaintext-not-leaked invariant, PEM validator (happy/malformed/too-short), back-compat for non-sensitive keys, generate happy-path with one-shot plaintext + subsequent redaction + destructive re-generate, generate 422 shapes, generate auth gating (401/403), corrupted-ciphertext → SystemSettingDecryptError, the global handler turning the exception into 503 + ERROR log, and the lazy-load RuntimeError contract. Combined unit suite: 45 passed, 143 deselected (26 M002/S03 back-compat + 19 new).
- **Integration (T04):** `backend/tests/integration/test_m004_s01_sensitive_settings_e2e.py` exercises the full sensitive-storage contract end-to-end against the live compose stack via the sibling `backend_url` fixture: skip-guard probes for the `s06` migration in `backend:latest`; autouse fixture DELETEs all four `github_app_*` rows before/after; PEM PUT → redacted shape + log + DB inspection (length(value_encrypted)>0, value IS NULL, sensitive=true, has_value=true); GET redacted; generate → one-shot plaintext + log without leak + DB-side cipher inspection; second GET redacted; re-generate yields fresh value (D025); 422 negative shapes (non-PEM, no_generator_for_key, unknown_setting_key); ciphertext corruption via psql + docker-exec script that catches `SystemSettingDecryptError` and emits the structured ERROR line on PID 1 stderr (MEM236); redaction sweep across `docker logs <sibling>` confirms neither the synthetic PEM sentinel nor either generated webhook secret appears, and all three observability markers (`system_setting_updated`, `system_setting_generated`, `system_settings_decrypt_failed`) are present. 8.33 s first run, 7.79 s back-to-back (well under the 30 s budget; autouse cleanup proven idempotent).

## Patterns Established for Downstream Slices

- **decrypt_setting() import target.** S02 imports `from orchestrator.encryption import decrypt_setting` to pull `github_app_private_key` plaintext at JWT-sign call site only. The mirror module's shape and exception class are stable.
- **Single-fan-in handler.** S05's webhook receiver MUST raise `SystemSettingDecryptError` from `decrypt_setting('github_app_webhook_secret')` and let the global handler in main.py translate to 503 + ERROR log. Do NOT add a per-route try/except.
- **One-shot plaintext discipline.** Plaintext crosses the API boundary exactly once on `POST /admin/settings/{key}/generate`. Every other read path (GET, list, PUT response) is redacted via `_redact()`. New sensitive keys added in future slices MUST follow this contract — register through `_SettingSpec(sensitive=True, ...)` and the module-load assertion will enforce "generators are sensitive-only".
- **e2e skip-guard + autouse cleanup.** Future slice e2e tests touching system_settings should follow MEM246 (autouse DELETE before/after) and MEM247 (skip-guard probing the baked image for the relevant alembic revision). Without these, app-db-data persistence and lagging backend:latest produce confusing failures.

## Deviations from Plan

- **Plan said the PUT response should carry `has_value` and `sensitive`.** The actual `SystemSettingPutResponse` model only has `{key, value, updated_at, warnings}` so the e2e verifies those flags via the immediately-following GET on the same key. Documented inline in the test.
- **Plan was ambiguous about how the docker-exec decrypt-failure log line reaches `docker logs`.** Resolved by redirecting the exec'd script's logger to PID 1's stderr (`open('/proc/1/fd/2','w')`) so the line lands on the same stream the FastAPI handler writes to under HTTP. Captured as MEM236.
- **T01 added SYSTEM_SETTINGS_ENCRYPTION_KEY to the local .env (in addition to .env.example) with a working Fernet key.** Required because compose declares the var as `?Variable not set`, making it a hard requirement at compose-up; otherwise local dev breaks.

## Known Limitations

- The decrypt-failure step in T04 exercises the log-shape contract but not a true 503-via-HTTP — there is no S01 HTTP endpoint that calls `decrypt_setting` on a sensitive row (sensitive GETs are always redacted, so they never decrypt). S02's first real consumer (orchestrator JWT-sign) provides the HTTP path; that test will upgrade this assertion to a true 503-status assertion.
- Pre-existing `test_sessions.py` failures (7) reproduce on prior commits — orchestrator-side 500s unrelated to S01. Out of scope.
- Local `.env` has `POSTGRES_PORT=55432` while compose publishes 5432; alembic and pytest commands from the host need `POSTGRES_PORT=5432 uv run ...`. Captured as MEM245.

## Verification

## Verification Run

All slice-level verification ran against the live compose stack from `/Users/josh/code/perpetuity` after `docker compose build backend orchestrator` and `docker compose up -d db redis orchestrator`.

| # | Command | Result |
|---|---------|--------|
| 1 | `cd backend && uv run alembic heads` | ✅ Single head: `s06_system_settings_sensitive (head)` |
| 2 | Round-trip `encrypt_setting('hello') → decrypt_setting(...) == 'hello'` in **both** backend and orchestrator modules | ✅ Both pass |
| 3 | `_VALIDATORS` invariants (private_key sensitive=true generator=None; webhook_secret sensitive=true generator!=None; workspace_volume_size_gb sensitive=false) | ✅ All assertions pass |
| 4 | `grep -q 'github_app_private_key' backend/app/api/routes/admin.py` | ✅ Match |
| 5 | `grep -q 'system_settings_decrypt_failed' backend/app/main.py` | ✅ Match (global exception handler registered) |
| 6 | `grep -c SYSTEM_SETTINGS_ENCRYPTION_KEY docker-compose.yml` | ✅ 2 (one per service block, both with `?Variable not set` guard) |
| 7 | `cd backend && POSTGRES_PORT=5432 uv run pytest tests/api -k 'settings' -v` | ✅ **45 passed**, 143 deselected (26 M002/S03 back-compat + 19 new M004/S01 unit tests) |
| 8 | `cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m004_s01_sensitive_settings_e2e.py -v` | ✅ **1 passed in 7.91s** — full contract: PEM PUT, redacted GET, generate one-shot, D025 destructive re-generate, 422 negative shapes, decrypt-failure ERROR log, redaction sweep |
| 9 | Failure-mode probe: missing `SYSTEM_SETTINGS_ENCRYPTION_KEY` → RuntimeError; non-base64 → RuntimeError; 16-byte decoded → RuntimeError; corrupted ciphertext → `SystemSettingDecryptError` | ✅ All 4 cases fail-loud as designed |
| 10 | Redaction sweep inside the e2e: `docker logs <sibling-backend>` scanned for the synthetic PEM sentinel + both generated webhook secrets | ✅ Zero matches; all three observability markers (`system_setting_updated`, `system_setting_generated`, `system_settings_decrypt_failed`) present |

## Health Signal

`GET /api/v1/admin/settings` lists all four GitHub App keys with `sensitive` and `has_value` flags but never plaintext for sensitive rows. Operator-side state inspector: `SELECT key, sensitive, has_value, length(value_encrypted) FROM system_settings`.

## Failure Signal

503 response body and ERROR log both name the key but never the plaintext. Boot-time validation of `SYSTEM_SETTINGS_ENCRYPTION_KEY` happens at first encrypt/decrypt call (lazy `_load_key`). Compose declares the var with `?Variable not set` so missing keys fail at compose-up, not silently at first read.

## Recovery Procedure

1. `system_settings_decrypt_failed key=<name>` ERROR log → check that `SYSTEM_SETTINGS_ENCRYPTION_KEY` matches the value used when the row was written.
2. If the key was rotated without re-encrypting rows: roll the env var back; the M004/S07 operator runbook will codify the rotation procedure.
3. If a single row is corrupt: re-PUT (for operator-seeded keys like `github_app_private_key`) or POST generate (for `github_app_webhook_secret`, accepting that it's destructive — upstream must be re-rotated).

## Monitoring Gaps (carried forward to S07)

- No automated alert on repeated `system_settings_decrypt_failed` log lines yet — S07's operator runbook will define alerting thresholds.
- No metrics on cache hit/miss for `_load_key` (acceptable: it loads once per process lifetime).

## Requirements Advanced

None.

## Requirements Validated

None.

## New Requirements Surfaced

None.

## Requirements Invalidated or Re-scoped

None.

## Operational Readiness

None.

## Deviations

"Plan said the PUT response should carry has_value and sensitive, but SystemSettingPutResponse only has {key, value, updated_at, warnings} — verified via the immediately-following GET on the same key. Plan was ambiguous about how the docker-exec decrypt-failure log line reaches docker logs — resolved by redirecting the exec'd script's logger to PID 1's stderr so the line lands on the same stream the FastAPI handler writes to under HTTP (MEM236). T01 added SYSTEM_SETTINGS_ENCRYPTION_KEY to the local .env (in addition to .env.example) with a working Fernet key — required because compose declares the var as ?Variable not set."

## Known Limitations

"Decrypt-failure step exercises the log-shape contract but not a true 503-via-HTTP — there is no S01 HTTP endpoint that calls decrypt_setting on a sensitive row (sensitive GETs are always redacted, so they never decrypt). S02's first real consumer (orchestrator JWT-sign endpoint) provides the HTTP path; that test will upgrade this assertion to a true 503-status assertion. Pre-existing test_sessions.py failures (7) reproduce on the prior commit — orchestrator-side 500s unrelated to S01; out of scope. Local .env has POSTGRES_PORT=55432 while compose publishes 5432 — alembic and pytest commands from the host need POSTGRES_PORT=5432 uv run ... (captured as MEM245)."

## Follow-ups

"S02: import decrypt_setting from orchestrator.encryption to pull github_app_private_key plaintext at JWT-sign call site only; first real HTTP consumer that round-trips through the global SystemSettingDecryptError handler. S05: HMAC compare against decrypted github_app_webhook_secret in the webhook receiver. S07: operator runbook for SYSTEM_SETTINGS_ENCRYPTION_KEY rotation procedure (re-encrypt all sensitive rows under new key) and webhook secret rotation (operator coordinates with GitHub before re-generating). S07: alerting threshold for repeated system_settings_decrypt_failed log lines."

## Files Created/Modified

- `backend/app/core/encryption.py` — NEW — Fernet helpers encrypt_setting/decrypt_setting + lazy _load_key + SystemSettingDecryptError
- `orchestrator/orchestrator/encryption.py` — NEW — 1:1 mirror of backend module for S02 to import
- `backend/app/alembic/versions/s06_system_settings_sensitive.py` — NEW — adds value_encrypted/sensitive/has_value, relaxes value JSONB to nullable
- `backend/app/models.py` — EXTEND — SystemSetting fields, SystemSettingPublic shape, new SystemSettingGenerateResponse
- `backend/app/api/routes/admin.py` — REWORK — _VALIDATORS → dict[str, _SettingSpec]; four GitHub App keys registered; sensitive PUT/GET/list redaction; new POST /generate endpoint
- `backend/app/main.py` — ADD — global exception_handler for SystemSettingDecryptError
- `backend/tests/api/routes/test_admin_settings.py` — EXTEND — 19 new unit tests covering sensitive PUT/GET/list, generate, decrypt-failure handler, lazy-key contract
- `backend/tests/integration/test_m004_s01_sensitive_settings_e2e.py` — NEW — full sensitive-storage e2e (PEM PUT, generate one-shot, destructive re-generate, 422 negative shapes, decrypt-failure log shape, redaction sweep)
- `backend/tests/integration/conftest.py` — ADD — SYSTEM_SETTINGS_ENCRYPTION_KEY_TEST constant injected into sibling-backend env_args
- `backend/tests/integration/test_m002_s05_two_key_rotation_e2e.py` — EXTEND — same env var threaded into ephemeral orchestrator boot + _boot_sibling_backend
- `docker-compose.yml` — ADD — SYSTEM_SETTINGS_ENCRYPTION_KEY?Variable_not_set under both backend: and orchestrator: environment blocks
- `.env.example` — ADD — placeholder + Fernet.generate_key() command
- `.env` — ADD — working Fernet key for local dev
- `backend/pyproject.toml` — ADD — cryptography>=43,<46
- `orchestrator/pyproject.toml` — ADD — cryptography>=43,<46
