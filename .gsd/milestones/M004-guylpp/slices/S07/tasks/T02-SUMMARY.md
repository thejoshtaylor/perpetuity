---
id: T02
parent: S07
milestone: M004-guylpp
key_files:
  - docs/runbooks/m004-secrets-rotation.md
key_decisions:
  - Placed runbook at docs/runbooks/m004-secrets-rotation.md (created the docs/runbooks/ directory) rather than extending deployment.md, so future M005+ runbooks share a parent and Traefik setup concerns stay separate.
  - Re-encrypt snippet uses _load_key() for the OLD key (read from SYSTEM_SETTINGS_ENCRYPTION_KEY env, matching every other backend tool) and a direct Fernet(NEW_KEY) for the new key — necessary because @functools.cache on _load_key() pins the first key for the process lifetime.
duration: 
verification_result: passed
completed_at: 2026-04-28T04:38:43.315Z
blocker_discovered: false
---

# T02: Add docs/runbooks/m004-secrets-rotation.md operator runbook covering SYSTEM_SETTINGS_ENCRYPTION_KEY re-encrypt rotation and github_app_webhook_secret one-shot regeneration, with recovery paths and DB inspection queries

**Add docs/runbooks/m004-secrets-rotation.md operator runbook covering SYSTEM_SETTINGS_ENCRYPTION_KEY re-encrypt rotation and github_app_webhook_secret one-shot regeneration, with recovery paths and DB inspection queries**

## What Happened

Created the docs/runbooks/ directory and wrote m004-secrets-rotation.md (255 lines, 4 H2 sections). The runbook documents two coordinated, operator-driven rotation procedures introduced by M004.

Procedure 1 (SYSTEM_SETTINGS_ENCRYPTION_KEY) anchors on the architecture constraint that there is no key-version column on system_settings (D020/MEM231/MEM244): rotation is a coordinated re-encrypt + restart, not an online migration. The runbook walks through generating a Fernet key, staging old + new keys side-by-side, running a copy-pasteable Python snippet that re-encrypts every sensitive=true row inside a single transaction (using app.core.encryption._load_key for the OLD key + a fresh Fernet(NEW_KEY) for the new key), updating the deployment env on BOTH backend and orchestrator, restarting in lockstep, and verifying via the system_settings_encryption_loaded key_prefix=... INFO log + a sensitive admin GET returning 200 has_value=true value=null. The Recovery subsection covers the three failure modes: (a) env updated before re-encrypt → revert env, restart, retry; (b) re-encrypt missed a row → revert env, retry; (c) both keys lost → unrecoverable, must clear sensitive rows and re-seed.

Procedure 2 (github_app_webhook_secret) anchors on D025/MEM229/MEM308/MEM314: rotation is intentionally destructive — old GitHub deliveries return 401 until the GitHub-side webhook configuration is updated. The runbook walks through picking a coordination window, clicking Generate webhook secret in /admin/settings, copying the modal-displayed plaintext within the modal lifetime (no screenshot, no console.log — the FE one-shot plaintext discipline lives only in modal closure state), pasting into the GitHub App settings UI Webhook secret field, and verifying via the three-line INFO log sequence (webhook_received → webhook_verified → webhook_dispatched dispatch_status=noop) on a known-good external push. The Recovery subsection covers (a) admin closed modal before pasting → Generate again, the previous secret is dead anyway; (b) wrong value saved on GitHub side → re-paste; (c) the visibility surface during the window — webhook_signature_invalid WARNING lines + webhook_rejections rows with signature_present=true, signature_valid=false are EXPECTED until the GitHub-side update lands and serve as durable audit evidence.

The third subsection (Inspecting state at rotation time) provides three operator queries: a SELECT against system_settings for sensitive rows + has_value/has_encrypted state (used before Procedure 1 to size the re-encrypt and after to confirm round-trip), a SELECT against webhook_rejections for the last hour (used during Procedure 2 to confirm the window is closing), and the log-aggregator pattern for system_settings_decrypt_failed (zero matches = Procedure 1 success).

Decisions: (1) New file at docs/runbooks/m004-secrets-rotation.md (created docs/runbooks/ in this task — neither docs/ nor docs/runbooks/ existed). Chose this over extending deployment.md per the slice plan boundary map: deployment.md is currently a Traefik/Docker setup doc and extending it would muddy concerns; future M005+ runbooks will share the same parent directory. (2) Sourced exact log-line names and column names from backend/app/api/routes/github_webhooks.py and backend/app/api/routes/admin.py rather than the planner snapshot — the receiver emits webhook_received, webhook_verified, webhook_dispatched dispatch_status=noop on the success path and webhook_signature_invalid on HMAC fail, with audit rows persisted into webhook_rejections (id, delivery_id, signature_present, signature_valid, source_ip, received_at). (3) The re-encrypt snippet uses functools.cache-backed _load_key() for the OLD key (so the operator only has to set SYSTEM_SETTINGS_ENCRYPTION_KEY in their shell, matching how every other backend tool reads it) and a fresh Fernet(os.environ["NEW_KEY"]) for the new key — calling _load_key() twice with different env values would not work because @functools.cache pins the first result.

No code changes — this is a docs-only task. No DECISIONS.md update needed; the runbook surfaces existing decisions D020/D024/D025 without introducing new structural choices.

## Verification

Ran the task plan's verification command end-to-end: file exists, all four H2 sections present (Overview, Procedure 1, Procedure 2, Inspecting state at rotation time), 'Fernet'/'fernet' present, 'webhook_signature_invalid' present, '### Recovery' present, line count 255 > 50. Cross-checked exact log-line names and table columns against backend/app/api/routes/github_webhooks.py (lines 51-56, 168-180, 300, 314, 388) and backend/app/api/routes/admin.py (sensitive=True keys at 453/458) before writing the runbook so the operator-facing log/SQL strings match what the running stack actually produces.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `test -f docs/runbooks/m004-secrets-rotation.md && grep -qE '^## Procedure 1: SYSTEM_SETTINGS_ENCRYPTION_KEY rotation' docs/runbooks/m004-secrets-rotation.md && grep -qE '^## Procedure 2: github_app_webhook_secret rotation' docs/runbooks/m004-secrets-rotation.md && grep -qE '^## Inspecting state at rotation time' docs/runbooks/m004-secrets-rotation.md && grep -qE 'Fernet|fernet' docs/runbooks/m004-secrets-rotation.md && grep -qE 'webhook_signature_invalid' docs/runbooks/m004-secrets-rotation.md && grep -qE '### Recovery' docs/runbooks/m004-secrets-rotation.md && [ $(wc -l < docs/runbooks/m004-secrets-rotation.md) -gt 50 ]` | 0 | ✅ pass | 80ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `docs/runbooks/m004-secrets-rotation.md`
