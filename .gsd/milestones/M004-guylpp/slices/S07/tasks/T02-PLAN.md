---
estimated_steps: 7
estimated_files: 1
skills_used: []
---

# T02: Write operator runbook for SYSTEM_SETTINGS_ENCRYPTION_KEY + webhook-secret rotation

Create `docs/runbooks/m004-secrets-rotation.md` documenting both rotation procedures end-to-end. Create the `docs/runbooks/` directory in the same task (does not exist yet — verified by `ls docs/runbooks/` returning No such file or directory).

The runbook must cover two procedures:

**Procedure 1 — SYSTEM_SETTINGS_ENCRYPTION_KEY rotation.** This is the Fernet key wrapping every sensitive system_settings row. The current architecture has no key-versioning column on system_settings (D020) — rotation is a coordinated re-encrypt + restart, not an online migration. Steps: (1) generate a new Fernet key with `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`; (2) shell into a backend container with both old and new keys available, walk every row WHERE sensitive=true, decrypt with the old key, re-encrypt with the new key, write back; (3) update `SYSTEM_SETTINGS_ENCRYPTION_KEY` in the deployment environment; (4) restart backend + orchestrator together (both processes call `decrypt_setting`); (5) verify by hitting any sensitive admin GET endpoint — `200 has_value=true value=null` means decrypt round-tripped; if instead a 503 `system_settings_decrypt_failed` surfaces, the re-encrypt step missed a row and the operator must re-run with the old key still present in env. Include a copy-pasteable Python re-encrypt snippet that uses `app.core.encryption._load_key` from both keys (operator sets `OLD_SYSTEM_SETTINGS_ENCRYPTION_KEY` env temporarily). Call out the failure mode: if the operator updates the env BEFORE re-encrypting, every sensitive read fails with 503 and the only recovery is to revert the env. Document the inverse operation as the recovery procedure.

**Procedure 2 — github_app_webhook_secret rotation.** This is the secret GitHub uses to sign webhook deliveries. Re-generate is intentionally destructive (D025) — old GitHub deliveries return 401 until the operator updates the GitHub-side webhook configuration to use the new secret. Steps: (1) operator decides on a coordination window; (2) admin clicks Generate webhook secret in /admin/settings, captures the one-time-display value within the modal lifetime (NOT screenshot — value lives only in modal closure per S06 invariant; copy-paste into clipboard then immediately into the GitHub App settings UI); (3) GitHub Settings → Apps → <our-app> → Edit → Webhook secret field → paste → Save; (4) verify by triggering an external push to a test repo; HMAC must verify cleanly with the new secret. Document the recovery: if the admin closes the modal before pasting into GitHub, the secret is unrecoverable — operator must Generate again. Document the visibility surface during the rotation window: `webhook_signature_invalid` WARNING lines with `delivery_id` from GitHub will accumulate until the GitHub-side update lands; that's expected and audit rows in `webhook_rejections` are the durable evidence the rotation was in flight.

Also include a short third subsection "Inspecting state at rotation time" listing the SQL queries operators reach for: `SELECT key, has_value, sensitive, has_encrypted FROM system_settings WHERE sensitive=true`; `SELECT delivery_id, signature_valid, source_ip, received_at FROM webhook_rejections WHERE received_at > NOW() - INTERVAL '1 hour' ORDER BY received_at DESC LIMIT 50`. These mirror the surfaces named in CONTEXT.md §"Open Questions" and the operator-readiness sections of S04-S06 summaries.

File location: `docs/runbooks/m004-secrets-rotation.md` per CONTEXT.md §"Open Questions" ("the runbook (S07) should call out the operator coordination needed") and the slice plan's boundary map (S07 produces an operator runbook "likely `deployment.md` extension or new `docs/runbooks/m004-secrets-rotation.md`"). Choose `docs/runbooks/` because (a) `deployment.md` is currently a Traefik/Docker setup doc — extending it would muddy concerns; (b) future M005+ runbooks will want the same parent directory.

File structure: 4 H2 sections — "Overview" (1 paragraph naming both procedures and when to run each), "Procedure 1: SYSTEM_SETTINGS_ENCRYPTION_KEY rotation", "Procedure 2: github_app_webhook_secret rotation", "Inspecting state at rotation time". Each procedure section carries numbered steps, copy-pasteable commands in code blocks, and an explicit "Recovery" subsection.

## Inputs

- ``.gsd/milestones/M004-guylpp/M004-guylpp-CONTEXT.md``
- ``.gsd/DECISIONS.md``
- ``backend/app/core/encryption.py``
- ``backend/app/api/routes/admin.py``
- ``backend/app/api/routes/github_webhooks.py``
- ``deployment.md``

## Expected Output

- ``docs/runbooks/m004-secrets-rotation.md``

## Verification

test -f docs/runbooks/m004-secrets-rotation.md && grep -qE '^## Procedure 1: SYSTEM_SETTINGS_ENCRYPTION_KEY rotation' docs/runbooks/m004-secrets-rotation.md && grep -qE '^## Procedure 2: github_app_webhook_secret rotation' docs/runbooks/m004-secrets-rotation.md && grep -qE '^## Inspecting state at rotation time' docs/runbooks/m004-secrets-rotation.md && grep -qE 'Fernet|fernet' docs/runbooks/m004-secrets-rotation.md && grep -qE 'webhook_signature_invalid' docs/runbooks/m004-secrets-rotation.md && grep -qE '### Recovery' docs/runbooks/m004-secrets-rotation.md && [ $(wc -l < docs/runbooks/m004-secrets-rotation.md) -gt 50 ]
