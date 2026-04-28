#!/usr/bin/env bash
# M005 S05 T01 — redaction sweep over backend + frontend source files.
#
# Checks that no forbidden secrets appear inside logger.*/console.* calls in:
#   backend/app/     — Python source
#   frontend/src/    — TypeScript source
#   frontend/dist/sw.js — built service worker
#
# Patterns checked:
#   1. Raw Grok key prefix   — xai-[A-Za-z0-9] inside a logger.*/console.* call
#   2. Raw VAPID private key — "-----BEGIN EC PRIVATE KEY-----" or a base64url
#                              block >40 chars on a logger.*/console.* line
#   3. Raw multipart boundary strings  — "Content-Disposition: form-data" or
#                              "--WebKit" on a logger.*/console.* line
#   4. Raw push endpoint domains (fcm.googleapis.com,
#                              updates.push.services.mozilla.com) anywhere in
#                              source, AND any https:// URL on a Python logger.*
#                              line that does NOT also contain "endpoint_hash"
#
# Exit 0 → all PASS.  Exit 1 → at least one violation (details printed to stderr).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_SRC="$REPO_ROOT/backend/app"
FRONTEND_SRC="$REPO_ROOT/frontend/src"
SW_DIST="$REPO_ROOT/frontend/dist/sw.js"

FAIL=0
FAIL_MSGS=()

# --------------------------------------------------------------------------
# helper: grep a list of files/dirs with the given extended-regex pattern and
# report any matches; marks FAIL=1 and appends an error message on hit.
# Usage: check_pattern <label> <egrep_pattern> <file_or_dir>...
# --------------------------------------------------------------------------
check_pattern() {
    local label="$1"; shift
    local pattern="$1"; shift
    local targets=("$@")

    local hits
    hits="$(grep -rEn --include="*.py" --include="*.ts" --include="*.tsx" --include="*.js" \
        -- "$pattern" "${targets[@]}" 2>/dev/null || true)"

    if [[ -n "$hits" ]]; then
        FAIL=1
        FAIL_MSGS+=("FAIL: $label")
        FAIL_MSGS+=("$hits")
    fi
}

# Same as check_pattern but uses a SECOND filter to exclude safe lines.
# Usage: check_pattern_exclude <label> <egrep_pattern> <exclude_fixed_str> <file_or_dir>...
check_pattern_exclude() {
    local label="$1"; shift
    local pattern="$1"; shift
    local exclude="$1"; shift
    local targets=("$@")

    local hits
    hits="$(grep -rEn --include="*.py" --include="*.ts" --include="*.tsx" --include="*.js" \
        -- "$pattern" "${targets[@]}" 2>/dev/null \
        | grep -vF "$exclude" || true)"

    if [[ -n "$hits" ]]; then
        FAIL=1
        FAIL_MSGS+=("FAIL: $label")
        FAIL_MSGS+=("$hits")
    fi
}

# --------------------------------------------------------------------------
# Check 1: Grok key prefix  xai-[A-Za-z0-9]  inside a logger.* or console.* line
# Strategy: grep for lines that contain a logger./console. call AND xai-
# --------------------------------------------------------------------------
GROK_PATTERN='(logger\.|console\.)[a-zA-Z]+.*xai-[A-Za-z0-9]'
check_pattern \
    "no Grok key prefix in log paths" \
    "$GROK_PATTERN" \
    "$BACKEND_SRC" "$FRONTEND_SRC" "$SW_DIST"

# --------------------------------------------------------------------------
# Check 2: VAPID private key material inside a logger.* or console.* line
#   2a. PEM armor header
#   2b. Long base64url block (>40 chars) — catches raw key bytes
# --------------------------------------------------------------------------
VAPID_PEM_PATTERN='(logger\.|console\.)[a-zA-Z]+.*-----BEGIN EC PRIVATE KEY-----'
check_pattern \
    "no VAPID private key PEM header in log paths" \
    "$VAPID_PEM_PATTERN" \
    "$BACKEND_SRC" "$FRONTEND_SRC" "$SW_DIST"

VAPID_B64_PATTERN='(logger\.|console\.)[a-zA-Z]+.*[A-Za-z0-9_-]{41,}'
# Apply base64url check only to source files, not the minified bundle.
# The minified sw.js is a single line — applying a whole-line regex to it
# would produce false positives from long Workbox identifiers/URLs on the
# same line as legitimate console.warn calls in library code we don't own.
check_pattern \
    "no VAPID private key base64url material in log paths" \
    "$VAPID_B64_PATTERN" \
    "$BACKEND_SRC" "$FRONTEND_SRC"

# --------------------------------------------------------------------------
# Check 3: Multipart boundary strings inside a logger.* or console.* line
# --------------------------------------------------------------------------
MULTIPART_PATTERN='(logger\.|console\.)[a-zA-Z]+.*(Content-Disposition: form-data|--WebKit)'
check_pattern \
    "no multipart boundary in log paths" \
    "$MULTIPART_PATTERN" \
    "$BACKEND_SRC" "$FRONTEND_SRC" "$SW_DIST"

# --------------------------------------------------------------------------
# Check 4a: Known push-endpoint domains appearing anywhere in source
# --------------------------------------------------------------------------
ENDPOINT_DOMAIN_PATTERN='(fcm\.googleapis\.com|updates\.push\.services\.mozilla\.com)'
check_pattern \
    "no raw push endpoint domains in source" \
    "$ENDPOINT_DOMAIN_PATTERN" \
    "$BACKEND_SRC" "$FRONTEND_SRC" "$SW_DIST"

# --------------------------------------------------------------------------
# Check 4b: Any https:// URL on a Python logger.* line without endpoint_hash
# (Frontend console.* lines are excluded — the sw.js Workbox bundle contains
# https:// URLs in string literals that are not push endpoints and are not
# inside the application's own console.* calls.)
# --------------------------------------------------------------------------
LOGGER_HTTPS_PATTERN='logger\.[a-zA-Z]+.*https://'
check_pattern_exclude \
    "no raw push endpoint URLs in logger paths (Python)" \
    "$LOGGER_HTTPS_PATTERN" \
    "endpoint_hash" \
    "$BACKEND_SRC"

# --------------------------------------------------------------------------
# Check 5: Verify test-level redaction assertions are still in place
# --------------------------------------------------------------------------
TEST_FILE="$REPO_ROOT/backend/tests/api/routes/test_voice.py"

if [[ ! -f "$TEST_FILE" ]]; then
    FAIL=1
    FAIL_MSGS+=("FAIL: test file not found: $TEST_FILE")
else
    # happy-path test must assert TRANSCRIPT_VALUE not in logs
    if ! grep -q "TRANSCRIPT_VALUE not in logs" "$TEST_FILE"; then
        FAIL=1
        FAIL_MSGS+=("FAIL: test_voice_transcribe_happy_path_returns_text_and_redacts_logs missing 'TRANSCRIPT_VALUE not in logs' assertion")
    fi

    # happy-path test must assert SECRET_VALUE not in logs
    if ! grep -q "SECRET_VALUE not in logs" "$TEST_FILE"; then
        FAIL=1
        FAIL_MSGS+=("FAIL: test_voice_transcribe_happy_path_returns_text_and_redacts_logs missing 'SECRET_VALUE not in logs' assertion")
    fi

    # encrypted-key test must assert both values absent
    if ! grep -q "SECRET_VALUE not in combined\|SECRET_VALUE not in logs" "$TEST_FILE"; then
        FAIL=1
        FAIL_MSGS+=("FAIL: test_grok_key_stored_encrypted_and_transcribe_never_logs_key_or_text missing SECRET_VALUE redaction assertion")
    fi

    if ! grep -q "TRANSCRIPT_VALUE not in combined\|TRANSCRIPT_VALUE not in logs" "$TEST_FILE"; then
        FAIL=1
        FAIL_MSGS+=("FAIL: test_grok_key_stored_encrypted_and_transcribe_never_logs_key_or_text missing TRANSCRIPT_VALUE redaction assertion")
    fi
fi

# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
if [[ "$FAIL" -ne 0 ]]; then
    printf '%s\n' "${FAIL_MSGS[@]}" >&2
    exit 1
fi

echo "PASS: no Grok key prefix in log paths"
echo "PASS: no VAPID private key material in log paths"
echo "PASS: no multipart boundary in log paths"
echo "PASS: no raw push endpoint URLs in log paths"
echo "PASS: test-level redaction assertions present"
exit 0
