---
id: T01
parent: S05
milestone: M005-oaptsz
key_files:
  - scripts/redaction-sweep.sh
key_decisions:
  - Excluded frontend/dist/sw.js from the base64url VAPID check because the minified bundle is a single line — the Workbox library's console.warn call would trigger false positives from long internal identifiers. The TypeScript source (frontend/src/) is the correct gate for application-authored code.
duration: 
verification_result: passed
completed_at: 2026-04-28T19:27:24.489Z
blocker_discovered: false
---

# T01: Added scripts/redaction-sweep.sh: source-file grep gate asserting zero secret leaks across all backend + frontend log call paths

**Added scripts/redaction-sweep.sh: source-file grep gate asserting zero secret leaks across all backend + frontend log call paths**

## What Happened

Wrote `scripts/redaction-sweep.sh`, a standalone bash script that greps backend Python source (`backend/app/`), frontend TypeScript source (`frontend/src/`), and the built service worker (`frontend/dist/sw.js`) for five classes of forbidden patterns inside logger.*/console.* calls:

1. **Grok key prefix** — `xai-[A-Za-z0-9]` co-occurring with a logger/console call
2. **VAPID PEM header** — `-----BEGIN EC PRIVATE KEY-----` on a logger/console line
3. **VAPID base64url material** — base64url blocks >40 chars on a logger/console line (applied to `.py`/`.ts`/`.tsx` source only, not the minified `dist/sw.js` — rationale below)
4. **Multipart boundary strings** — `Content-Disposition: form-data` or `--WebKit` on a logger/console line
5. **Raw push endpoint URLs** — specific FCM/Mozilla push domains anywhere in source, plus any `https://` on a Python `logger.*` line without `endpoint_hash`

Additionally verifies that `backend/tests/api/routes/test_voice.py` still contains all four required redaction assertions (`TRANSCRIPT_VALUE not in logs`, `SECRET_VALUE not in logs`, `SECRET_VALUE not in combined`, `TRANSCRIPT_VALUE not in combined`).

**Key deviation from plan:** The base64url VAPID check (check 2b) is intentionally scoped to source files only, excluding `frontend/dist/sw.js`. The built bundle is a single minified line containing Workbox library code; applying a whole-line regex to it produces false positives because `console.warn(c)` in the Workbox safety-check string precedes long base64url-like identifiers belonging to library internals, not application secrets. Checking the TypeScript source `frontend/src/` is the correct security gate — the dist build derives from it. All five checks passed cleanly with exit code 0.

## Verification

Ran `bash scripts/redaction-sweep.sh` — all five checks produced PASS lines and the script exited 0. Confirmed zero matches for Grok key prefix, VAPID key material, multipart boundary strings, raw push endpoint URLs, and all four test-level redaction assertions remain present in `test_voice.py`.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `bash scripts/redaction-sweep.sh` | 0 | ✅ pass — PASS: no Grok key prefix in log paths; PASS: no VAPID private key material in log paths; PASS: no multipart boundary in log paths; PASS: no raw push endpoint URLs in log paths; PASS: test-level redaction assertions present | 850ms |

## Deviations

Base64url VAPID check scoped to .py/.ts/.tsx source files only, not dist/sw.js. The plan did not anticipate that sw.js is a minified single-line file where whole-line regex matching is unsafe. The security intent is preserved by checking the TypeScript source that compiles to sw.js.

## Known Issues

none

## Files Created/Modified

- `scripts/redaction-sweep.sh`
