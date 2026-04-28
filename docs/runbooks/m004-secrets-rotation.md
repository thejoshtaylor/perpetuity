# M004 Secrets Rotation Runbook

## Overview

This runbook covers two rotation procedures introduced by milestone M004 (per-team
GitHub App connections). Both rotate values that the running stack actively uses;
both are coordinated, operator-driven actions — there is no online migration path.

1. **`SYSTEM_SETTINGS_ENCRYPTION_KEY` rotation.** The Fernet key wrapping every
   sensitive `system_settings` row (currently `github_app_private_key` and
   `github_app_webhook_secret`). Rotate when the key is suspected of leaking, on a
   policy cadence, or whenever an operator with shell history retention has been
   off-boarded.
2. **`github_app_webhook_secret` rotation.** The HMAC secret GitHub uses to sign
   webhook deliveries. Rotate after suspected secret exposure, on policy cadence,
   or when an admin who could view the one-time-display value has been
   off-boarded. This rotation is *intentionally destructive* per D025: until the
   GitHub-side webhook configuration is updated to match, every incoming delivery
   returns 401.

Run Procedure 1 only during a maintenance window — it requires both backend and
orchestrator processes to be restarted in lockstep. Procedure 2 does not require
a restart but should be coordinated with whoever owns the GitHub App settings UI.

The third subsection ("Inspecting state at rotation time") lists the SQL queries
operators reach for during either procedure.

## Procedure 1: SYSTEM_SETTINGS_ENCRYPTION_KEY rotation

`SYSTEM_SETTINGS_ENCRYPTION_KEY` is a Fernet key (32 url-safe base64 bytes,
44 chars). It MUST stay stable across restarts: the architecture (D020) has no
key-version column on `system_settings`, so rotation is a coordinated
re-encrypt + restart, not an online migration. Rotating the env var without
re-encrypting every `sensitive=true` row first will surface as
`system_settings_decrypt_failed key=<name>` ERROR logs and a 503 response on
every sensitive admin GET.

The backend module `app/core/encryption.py` and the orchestrator mirror at
`orchestrator/orchestrator/encryption.py` BOTH read this env var and BOTH must
agree.

### Steps

1. **Generate a new key.** On any host with the `cryptography` library:

   ```bash
   python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
   ```

   Capture the 44-char output — this is the new key.

2. **Stage both keys.** Set the new key as `SYSTEM_SETTINGS_ENCRYPTION_KEY_NEW`
   and keep the current key as `SYSTEM_SETTINGS_ENCRYPTION_KEY` in the operator
   shell — neither value is read by the running stack yet, this is just
   scratch state for the re-encrypt script.

3. **Re-encrypt every sensitive row.** Shell into a backend container that has
   database access and the `cryptography` library installed (any backend image
   works). Run the following Python snippet, exporting the OLD key as the
   process env var (so `_load_key()` reads it cleanly) and passing the NEW key
   as a CLI arg. The snippet round-trips every `sensitive=true` row through the
   old key (decrypt) and the new key (encrypt) inside a single transaction:

   ```bash
   export SYSTEM_SETTINGS_ENCRYPTION_KEY="<OLD_KEY_VALUE>"
   NEW_KEY="<NEW_KEY_VALUE>" python - <<'PY'
   import os
   from cryptography.fernet import Fernet
   from sqlalchemy import text
   from app.core.db import engine
   from app.core.encryption import _load_key  # reads SYSTEM_SETTINGS_ENCRYPTION_KEY (old)

   old_fernet = _load_key()
   new_fernet = Fernet(os.environ["NEW_KEY"].encode("ascii"))

   with engine.begin() as conn:
       rows = conn.execute(text(
           "SELECT key, value_encrypted FROM system_settings "
           "WHERE sensitive = TRUE AND has_value = TRUE"
       )).all()
       print(f"re-encrypting {len(rows)} sensitive rows")
       for key, ciphertext in rows:
           plaintext = old_fernet.decrypt(bytes(ciphertext))
           new_ct = new_fernet.encrypt(plaintext)
           conn.execute(
               text(
                   "UPDATE system_settings SET value_encrypted = :ct, "
                   "updated_at = NOW() WHERE key = :k"
               ),
               {"ct": new_ct, "k": key},
           )
       print("re-encrypt complete")
   PY
   ```

   Verify the script reports a non-zero row count and exits 0. If it fails
   mid-way, the surrounding transaction rolls back — the database is unchanged
   and you can safely retry.

4. **Update the deployment env.** Replace `SYSTEM_SETTINGS_ENCRYPTION_KEY` with
   the new key value in the deployment environment (compose `.env`, secrets
   manager, etc.) for BOTH the backend and orchestrator services.

5. **Restart backend + orchestrator together.** Both processes cache the Fernet
   instance via `functools.cache` on `_load_key()` — they must be restarted to
   pick up the new key. Restart in any order, but do not let either run
   against rows encrypted with a key the other does not have.

6. **Verify.** Hit any sensitive admin GET endpoint as an admin user, e.g.:

   ```bash
   curl -sS -b cookies.txt https://api.example.com/api/v1/admin/settings/github_app_webhook_secret
   ```

   A `200` with body `{"key":"github_app_webhook_secret","sensitive":true,"has_value":true,"value":null,...}`
   means the new key successfully decrypted the row (and the plaintext is
   intentionally redacted in the response per the sensitive-key contract).

   On startup, both backend and orchestrator emit a one-shot
   `system_settings_encryption_loaded key_prefix=<first_4_chars>...` INFO log
   line — the prefix should match the first 4 chars of the NEW key. If you see
   the OLD prefix, the env update did not take.

### Recovery

**If the env var was updated BEFORE re-encrypting:** every sensitive admin GET
will return 503 with `system_settings_decrypt_failed key=<name>` in the ERROR
log. Recovery is to revert `SYSTEM_SETTINGS_ENCRYPTION_KEY` to the OLD value,
restart backend + orchestrator, and re-run the procedure from step 3.

**If the re-encrypt script missed a row** (e.g. a new sensitive row was added
mid-rotation): symptom is the same — 503 + `system_settings_decrypt_failed`
on the specific key. Recovery: with the OLD key still available in your
operator shell, run the snippet again. It is idempotent under the new key
(rows already re-encrypted decrypt cleanly with the new key, so the old-key
decrypt step would fail). Safer recovery is to revert the deployment env to
the OLD key, restart, and re-run the full procedure.

**If both keys are lost:** sensitive rows are unrecoverable. Operator must
clear `value_encrypted` and `has_value` on every `sensitive=true` row
(`UPDATE system_settings SET value_encrypted = NULL, has_value = FALSE WHERE
sensitive = TRUE`) and re-seed each value via the admin UI (`Generate` for
keys with a server-side generator, paste-in for `github_app_private_key`).
This invalidates every existing GitHub App installation token and breaks
inbound webhook verification until rotation 2 lands a new webhook secret.

## Procedure 2: github_app_webhook_secret rotation

`github_app_webhook_secret` is the HMAC secret GitHub uses to sign every
webhook delivery. The receiver (`POST /api/v1/github/webhooks`) decrypts the
current value at the call site and compares the GitHub-supplied
`X-Hub-Signature-256` against `hmac.compare_digest`. Rotation is intentionally
destructive per D025: rolling it on our side IMMEDIATELY breaks every
in-flight delivery from GitHub until the GitHub App configuration is updated
to match.

The new value is shown ONCE — the `OneTimeValueModal` component holds it in
React state for the lifetime of the modal. Closing the modal unmounts the
component and the plaintext is unrecoverable. The server stores only the
encrypted ciphertext; there is no read endpoint for the plaintext.

### Steps

1. **Pick a coordination window.** During the window, every external GitHub
   delivery will return 401 and accumulate `webhook_signature_invalid` WARNING
   lines + `webhook_rejections` audit rows. GitHub retries failed deliveries
   for 24 hours, so the window can be tens of seconds and still have GitHub
   re-deliver successfully on the post-update side. Coordinate with whoever
   owns the GitHub App configuration so both sides switch in close succession.

2. **Generate the new secret in the admin UI.**
   - Navigate to `/admin/settings`.
   - Find the `github_app_webhook_secret` row.
   - Click `Generate webhook secret`.
   - The new value appears ONCE inside a modal. **Copy the value to the
     clipboard within the modal lifetime** — do NOT take a screenshot,
     do NOT paste it into a chat, log, or ticket. The plaintext lives only
     in the modal closure (per the FE one-shot discipline of D025).

3. **Paste into the GitHub App settings UI.**
   - GitHub → Settings → Developer settings → GitHub Apps → `<our-app>`
     → Edit → General → Webhook secret.
   - Paste the new value into the `Webhook secret` field.
   - Click `Save changes`.
   - Immediately clear the clipboard (or copy something innocuous over it).

4. **Verify.** Trigger a known-good external event: push a no-op commit to a
   test repository whose installation is connected to a team in our system.
   Within seconds:
   - The receiver should log
     `webhook_received delivery_id=<id> event_type=push source_ip=<ip>`,
     followed by `webhook_verified delivery_id=<id> event_type=push` and
     `webhook_dispatched delivery_id=<id> event_type=push dispatch_status=noop`.
   - GitHub's "Recent Deliveries" panel for the App webhook should show the
     same `delivery_id` with a `200` response.

   If you see `webhook_signature_invalid delivery_id=<id> source_ip=<ip>`
   instead, the GitHub-side update has not landed yet (or did not save) —
   re-check the GitHub App configuration.

### Recovery

**If the admin closes the modal before pasting into GitHub:** the plaintext
is unrecoverable — the server holds only the ciphertext and the FE never
persists the value. Recovery: click `Generate webhook secret` again. This
issues a NEW secret and the previous one is now dead-on-arrival anyway. The
operator coordination window starts over.

**If the GitHub-side update saves the wrong value:** symptom is
`webhook_signature_invalid` accumulating on every delivery. Recovery: open
the GitHub App webhook secret field, paste the correct value, save. There
is no retrieval path on our side — if the operator lost the just-generated
plaintext, they must `Generate` again and re-paste both sides.

**Visibility surface during the rotation window.** Until the GitHub-side
update lands, every external delivery emits a
`webhook_signature_invalid delivery_id=<id> source_ip=<ip>` WARNING and
inserts one row into `webhook_rejections` (`signature_present=true,
signature_valid=false`). This is expected. Audit rows in `webhook_rejections`
are the durable evidence the rotation was in flight; clear or annotate them
if your runbook policy requires.

## Inspecting state at rotation time

These SQL queries give the operator visibility into both procedures while
they are mid-flight. Run them as a database-read user against the live DB.

**Which sensitive system_settings rows exist and have a value?** Useful
before Procedure 1 to know how many rows the re-encrypt script will touch,
and after Procedure 1 to confirm `has_value=true` survived the round-trip.

```sql
SELECT key, has_value, sensitive, value_encrypted IS NOT NULL AS has_encrypted
FROM system_settings
WHERE sensitive = TRUE
ORDER BY key;
```

**Recent webhook rejections.** Useful during Procedure 2 to confirm the
rotation window is closing (rejections should stop within seconds of the
GitHub-side save) and to capture audit evidence the rotation happened.

```sql
SELECT delivery_id, signature_present, signature_valid, source_ip, received_at
FROM webhook_rejections
WHERE received_at > NOW() - INTERVAL '1 hour'
ORDER BY received_at DESC
LIMIT 50;
```

**Decrypt-failure detection from logs.** If the structured logger is shipping
to a log aggregator, search for `system_settings_decrypt_failed` over the
maintenance window — exactly zero matches is the success signal for
Procedure 1. Any non-zero count means a row missed the re-encrypt step (see
Recovery above).
