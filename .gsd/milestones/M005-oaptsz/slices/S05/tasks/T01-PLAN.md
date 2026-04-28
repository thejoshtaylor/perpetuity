---
estimated_steps: 3
estimated_files: 8
skills_used: []
---

# T01: Redaction sweep: write and run grep assertions over all log call paths

Write a bash script `scripts/redaction-sweep.sh` that greps all backend Python source (app/ directory), all frontend TypeScript source (src/), and the built service worker dist/sw.js for forbidden patterns, then asserts zero matches for each pattern. Patterns to check: (1) raw Grok key prefix — any string matching `xai-[A-Za-z0-9]` appearing inside a logger.* or console.* call; (2) raw VAPID private key material — `-----BEGIN EC PRIVATE KEY-----` or a base64url block > 40 chars in a logger/console call; (3) raw multipart boundary strings — `Content-Disposition: form-data` or `--WebKit` appearing in logger/console calls; (4) raw push endpoint URLs — `https://fcm.googleapis.com` or `https://updates.push.services.mozilla.com` or any `https://` URL appearing on a logger.* line that does NOT contain `endpoint_hash`.

Also verify the existing test-level redaction assertions remain in place: `test_voice_transcribe_happy_path_returns_text_and_redacts_logs` asserts `TRANSCRIPT_VALUE not in logs` and `SECRET_VALUE not in logs`; `test_grok_key_stored_encrypted_and_transcribe_never_logs_key_or_text` asserts the same.

The script exits 0 if all checks pass, non-zero with a clear failure message if any pattern is found. Run the script and capture its output as the verification evidence for this task.

## Inputs

- `backend/app/core/grok_stt.py`
- `backend/app/core/push_dispatch.py`
- `backend/app/api/routes/voice.py`
- `backend/app/core/notify.py`
- `frontend/src/sw.ts`
- `frontend/src/components/voice/useVoiceRecorder.ts`
- `frontend/src/components/notifications/PushPermissionPrompt.tsx`
- `frontend/dist/sw.js`

## Expected Output

- `scripts/redaction-sweep.sh`

## Verification

bash scripts/redaction-sweep.sh exits 0 with output: 'PASS: no Grok key prefix in log paths', 'PASS: no VAPID private key material in log paths', 'PASS: no multipart boundary in log paths', 'PASS: no raw push endpoint URLs in log paths'. Each PASS line printed. Script exit code 0.
