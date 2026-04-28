#!/usr/bin/env bash
# M004 milestone-wide redaction invariant.
#
# Greps the docker logs of the backend + orchestrator containers (or any
# explicitly-named --container args) for the GitHub token-prefix families and
# PEM armor headers and exits non-zero on any match. This is the standing
# programmatic gate that backs the M004 success criterion:
#
#   "Final redaction grep over backend + orchestrator logs returns zero
#    matches for token prefixes (gho_, ghs_, ghu_, ghr_, github_pat_) and
#    PEM headers."
#
# It extends and centralizes the per-slice redaction sweeps already embedded
# in the S02/S04/S05 e2e tests so an operator can run one command instead of
# remembering five.
#
# Usage:
#   bash scripts/m004_redaction_sweep.sh
#       Default: discover backend + orchestrator via `docker compose ps -q`
#       and sweep their logs.
#   bash scripts/m004_redaction_sweep.sh --container <name> [--container ...]
#       Explicit mode: sweep one or more named containers (used by S04/T05's
#       ephemeral-container redaction block).
#
# Match families (a match in any of these is a regression):
#   gho_           — never allowed
#   ghu_           — never allowed
#   ghr_           — never allowed
#   github_pat_    — never allowed
#   ghs_           — allowed ONLY in lines that ALSO contain `token_prefix=`
#                    (the canonical 4-char `_token_prefix(token)` log shape
#                     established in S02/S04/S05)
#   -----BEGIN     — PEM armor, never allowed
#   x-access-token — basic-auth userinfo form used in clone, never allowed
#
# Exit:
#   0 — no matches; prints `M004 redaction sweep: clean` to stdout
#   1 — at least one match; prints offending line(s) to stderr with a
#       `M004 redaction sweep: REGRESSION — <prefix> found in <container>`
#       header, preserving which container the line came from
#   2 — operator/usage error (no compose stack, unknown flag, docker missing)

set -euo pipefail

CONTAINERS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --container)
            if [[ $# -lt 2 || -z "${2:-}" ]]; then
                echo "error: --container requires a name argument" >&2
                exit 2
            fi
            CONTAINERS+=("$2")
            shift 2
            ;;
        -h|--help)
            sed -n '2,40p' "$0"
            exit 0
            ;;
        *)
            echo "error: unknown argument: $1" >&2
            echo "usage: $0 [--container <name> ...]" >&2
            exit 2
            ;;
    esac
done

if ! command -v docker >/dev/null 2>&1; then
    echo "error: docker not found on PATH" >&2
    exit 2
fi

# Default mode: discover backend + orchestrator from the current compose stack.
if [[ ${#CONTAINERS[@]} -eq 0 ]]; then
    # `docker compose ps -q backend orchestrator` returns one container ID per
    # line for whichever of the two services are running.
    mapfile -t DISCOVERED < <(docker compose ps -q backend orchestrator 2>/dev/null || true)

    # Strip empty entries (compose can emit blank lines).
    for id in "${DISCOVERED[@]}"; do
        if [[ -n "$id" ]]; then
            CONTAINERS+=("$id")
        fi
    done

    if [[ ${#CONTAINERS[@]} -eq 0 ]]; then
        echo "error: no compose stack running — start it with" >&2
        echo "  docker compose up -d backend orchestrator" >&2
        echo "first, or pass --container <name> for ad-hoc use." >&2
        exit 2
    fi
fi

# Track the failure state. Bash `set -e` aborts on the first failing command,
# so we capture per-container findings into a tmpfile and decide at the end.
FAIL=0
FAIL_LOG="$(mktemp -t m004_redaction_sweep.XXXXXX)"
trap 'rm -f "$FAIL_LOG"' EXIT

sweep_container() {
    local container="$1"
    # Resolve a friendly display name when the caller passed an ID.
    local display
    display="$(docker inspect --format '{{.Name}}' "$container" 2>/dev/null | sed 's|^/||')"
    if [[ -z "$display" ]]; then
        display="$container"
    fi

    local logs
    if ! logs="$(docker logs "$container" 2>&1)"; then
        echo "error: docker logs failed for container $display" >&2
        FAIL=1
        return
    fi

    # Plain-prefix families: any occurrence is a regression.
    local prefix
    for prefix in 'gho_' 'ghu_' 'ghr_' 'github_pat_' '-----BEGIN' 'x-access-token'; do
        # `grep -F` for fixed-string semantics; -n keeps line numbers for the
        # operator's triage; capture into a variable so we can prefix each line
        # with the container name without losing line context.
        local hits
        if hits="$(printf '%s\n' "$logs" | grep -nF -- "$prefix" || true)"; then
            if [[ -n "$hits" ]]; then
                {
                    echo "M004 redaction sweep: REGRESSION — '${prefix}' found in ${display}"
                    printf '%s\n' "$hits" | sed "s|^|  ${display}: |"
                } >> "$FAIL_LOG"
                FAIL=1
            fi
        fi
    done

    # `ghs_` is the installation-token prefix family. The `_token_prefix`
    # helper is allowed to emit it inside the canonical `token_prefix=` log
    # shape (e.g. `token_prefix=ghs_M004...`). Anywhere else is a regression.
    # `grep -v 'token_prefix='` strips the legitimate co-occurrence; whatever
    # remains is the violation set. (Mirrors the assertion at
    # backend/tests/integration/test_m004_s04_two_hop_clone_e2e.py:1410-1415.)
    local ghs_hits
    if ghs_hits="$(printf '%s\n' "$logs" | grep -nF -- 'ghs_' | grep -vF 'token_prefix=' || true)"; then
        if [[ -n "$ghs_hits" ]]; then
            {
                echo "M004 redaction sweep: REGRESSION — 'ghs_' found outside token_prefix= in ${display}"
                printf '%s\n' "$ghs_hits" | sed "s|^|  ${display}: |"
            } >> "$FAIL_LOG"
            FAIL=1
        fi
    fi
}

for c in "${CONTAINERS[@]}"; do
    sweep_container "$c"
done

if [[ "$FAIL" -ne 0 ]]; then
    cat "$FAIL_LOG" >&2
    exit 1
fi

echo "M004 redaction sweep: clean"
exit 0
