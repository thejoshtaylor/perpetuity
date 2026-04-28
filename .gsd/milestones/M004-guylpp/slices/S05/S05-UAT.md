# S05: Webhook receiver (HMAC verify, persist, dispatch hook) — UAT

**Milestone:** M004-guylpp
**Written:** 2026-04-28T02:43:30.002Z

# S05 UAT — Webhook receiver

**Proof level:** contract + integration. No human/UAT for this slice — S07 covers the real-GitHub round-trip end-to-end. The contract test below is the slice's stopping condition.

## Preconditions

- Compose stack running locally: `docker compose up -d` (db, redis, backend, orchestrator, mock-github sidecar not required for S05).
- `backend:latest` image rebuilt to include the s06e migration: `docker compose build backend` if HEAD has changed since last build (the autouse skip-guard in the e2e enforces this and emits an actionable skip message on miss).
- `app-db-data` volume reachable; alembic head at `s06e_github_webhook_events`.
- FIRST_SUPERUSER credentials configured (`admin@example.com` / changethis or as overridden in `.env`).
- `SYSTEM_SETTINGS_ENCRYPTION_KEY` set (Fernet key) — present in compose env from S01.

## Bundled contract test (single artifact, eight ordered steps)

Run: `cd backend && POSTGRES_PORT=5432 uv run pytest -m e2e tests/integration/test_m004_s05_webhook_receiver_e2e.py -v`

Wall-clock budget: ≤ 30 s. Observed: 9.94 s on first pass, 9.92 s on the back-to-back second pass.

### Step 1 — Login

- POST `/api/v1/login/access-token` as FIRST_SUPERUSER.
- Expected: 200; sets the auth cookie.

### Step 2 — Generate webhook secret (one-time-display)

- POST `/api/v1/admin/settings/github_app_webhook_secret/generate`.
- Expected: 200 with body `{value: "<plaintext>", has_value: true, sensitive: true}`.
- Capture: `secret_plaintext = response.json()["value"]` for use in steps 3–8.
- Verify: subsequent GET `/api/v1/admin/settings/github_app_webhook_secret` returns `{value: null, has_value: true, sensitive: true}` (sanity — proven in S01, asserted here as a precondition).

### Step 3 — Valid signature (happy path)

- Body: `{"action": "push", "repository": {"full_name": "octocat/hello-world"}, "installation": {"id": 12345}}` serialized to fixed bytes.
- Headers: `X-Hub-Signature-256: sha256=<hmac.new(secret_plaintext, body, sha256).hexdigest()>`, `X-GitHub-Event: push`, `X-GitHub-Delivery: <uuid-1>`. **No** `X-GitHub-Hook-Installation-Target-Id` header (see Edge Case A below — MEM298).
- POST `/api/v1/github/webhooks` with the EXACT raw bytes (not via httpx `json=` which re-serializes).
- Expected:
  - 200 with body `{status: "ok", duplicate: false}`.
  - One row in `github_webhook_events` with `delivery_id=<uuid-1>`, `event_type='push'`, `dispatch_status='noop'`.
  - Backend logs contain three contract markers all carrying the same delivery_id:
    - `webhook_received delivery_id=<uuid-1> event_type=push source_ip=`
    - `webhook_verified delivery_id=<uuid-1> event_type=push`
    - `webhook_dispatched delivery_id=<uuid-1> event_type=push dispatch_status=noop`

### Step 4 — Idempotency (duplicate delivery_id)

- Repeat Step 3 with the same body, signature, and `X-GitHub-Delivery: <uuid-1>`.
- Expected:
  - 200 with body `{status: "ok", duplicate: true}`.
  - `github_webhook_events` still has exactly ONE row for `delivery_id=<uuid-1>`.
  - Backend logs gain a `webhook_duplicate_delivery delivery_id=<uuid-1>` INFO line.
  - Dispatch is NOT invoked again (no second `webhook_dispatched` for `<uuid-1>`).

### Step 5 — Invalid signature

- Same body. Build a fresh signature with `_flip_one_hex_char` (flips a hex digit near the midpoint — keeps `sha256=` prefix and 64-char length intact so the receiver progresses past the structural prefix check into `hmac.compare_digest`).
- Headers: corrupted `X-Hub-Signature-256`, `X-GitHub-Delivery: <uuid-2>`.
- POST `/api/v1/github/webhooks`.
- Expected:
  - 401 with body `{detail: "invalid_signature"}`.
  - One row in `webhook_rejections` with `delivery_id=<uuid-2>`, `signature_present=true`, `signature_valid=false`, `source_ip` populated.
  - No new row in `github_webhook_events` (still exactly one — from Step 3).
  - WARNING log `webhook_signature_invalid delivery_id=<uuid-2> source_ip=` with `signature_present=true`.
  - Body is NOT persisted on rejection (verified by inspecting the rejection row — no `payload` column exists).

### Step 6 — Absent signature header

- Same body. NO `X-Hub-Signature-256` header at all. `X-GitHub-Delivery: <uuid-3>`.
- POST `/api/v1/github/webhooks`.
- Expected:
  - 401 with body `{detail: "invalid_signature"}`.
  - One additional row in `webhook_rejections` with `delivery_id=<uuid-3>`, `signature_present=false`, `signature_valid=false`.
  - WARNING log `webhook_signature_invalid` with `signature_present=false`.

### Step 7 — Decrypt failure (corrupted ciphertext) — first 503-via-HTTP test of the global handler

- Corrupt the stored ciphertext: `psql -c "UPDATE system_settings SET value_encrypted = '\\xdeadbeef' WHERE key = 'github_app_webhook_secret'"`.
- Build a payload signed with the captured `secret_plaintext` (would be valid against the OLD secret) and POST. `X-GitHub-Delivery: <uuid-4>`.
- Expected:
  - 503 with body `{detail: "system_settings_decrypt_failed", key: "github_app_webhook_secret"}`.
  - ERROR log line `system_settings_decrypt_failed key=github_app_webhook_secret` (the global `SystemSettingDecryptError` handler in main.py is the path under test — S01 T04 proved the log shape via docker-exec; this is the first end-to-end HTTP proof).
  - No new row in `github_webhook_events`.
  - No new row in `webhook_rejections` (decrypt-failure is operator misconfiguration, not a bad-actor probe).

### Step 8 — Redaction sweep

- After all preceding steps, capture the full sibling-backend stderr via `docker logs <backend-container>`.
- Assert: `secret_plaintext` (captured in Step 2) does NOT appear ANYWHERE in the log dump.
- Assert: all five contract markers fired across the run (`webhook_received`, `webhook_verified`, `webhook_dispatched`, `webhook_signature_invalid`, `system_settings_decrypt_failed`).

## Edge cases covered by unit tests (TestClient, no compose stack required)

Run: `cd backend && POSTGRES_PORT=5432 uv run pytest tests/api/routes/test_github_webhooks.py -v`

- **A. Valid signature → 200 + event row + dispatch invoked + three INFO logs** (mirrors Step 3).
- **B. Invalid signature → 401 + rejection row + WARNING + no event row + no dispatch** (mirrors Step 5).
- **C. Absent X-Hub-Signature-256 → 401 + rejection row with signature_present=false** (mirrors Step 6).
- **D. Duplicate delivery_id → 200 + only one event row + dispatch invoked exactly once** (mirrors Step 4).
- **E. Valid signature + malformed JSON body → 400 + no event row + no rejection** (HMAC was fine, body is the contract break — not a probe surface).
- **F. Decrypt failure (mocked) → 503 via global handler with key=github_app_webhook_secret in response and log** (mirrors Step 7).
- **G. Unconfigured webhook secret (missing row) → 503 webhook_secret_not_configured** + WARNING; no rejection row.
- **G'. Webhook secret row present but has_value=false → same 503 webhook_secret_not_configured.**
- **H. Real (non-mocked) dispatch_github_event emits the contract log line** — confirms the stub itself is wired, not just the spy used in (A).

## Schema invariants (proven by `tests/api/routes/test_github_webhooks_schema.py`)

- Duplicate `delivery_id` INSERT raises `IntegrityError` (UNIQUE constraint, not just an index — required for the route's `INSERT ... ON CONFLICT DO NOTHING` semantics).
- DELETE of parent `github_app_installations` row NULLs `github_webhook_events.installation_id` (audit-trail preservation per ON DELETE SET NULL).
- Alembic `upgrade head → downgrade -1 → upgrade head` round-trip leaves the schema byte-identical (catches SQLModel/migration drift).

## Known limitations carried into S07/M005

- **MEM298 / route-hardening**: sending `X-GitHub-Hook-Installation-Target-Id` referencing an installation_id NOT in `github_app_installations` triggers FK violation → 500. T01 schema chose ON DELETE SET NULL for exactly this case; the route should NULL the column on missing FK target. The S05 e2e omits the header; M005 owns the fix during real dispatch + install-discovery.
- **S07 deliverable**: real-GitHub round-trip against a test org, operator runbook for webhook-secret rotation, and the milestone-wide redaction sweep across backend + orchestrator logs.

## Pass criteria

- All 1 + 9 + 3 = 13 tests above pass on a fresh `docker compose up -d` + alembic head + first-superuser seed.
- E2E wall-clock ≤ 30 s.
- Final `docker compose logs backend` grep of the captured `secret_plaintext` returns zero matches.
- All five contract log markers (webhook_received, webhook_verified, webhook_dispatched, webhook_signature_invalid, system_settings_decrypt_failed) fire on their respective branches.
