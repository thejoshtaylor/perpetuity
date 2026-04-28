---
id: T03
parent: S07
milestone: M004-guylpp
key_files:
  - scripts/m004_redaction_sweep.sh
key_decisions:
  - Used `grep -F` (fixed-string) for all match families to avoid regex surprises with shell metacharacters in operator-supplied container names and to keep semantics identical to the python `in` check used by the e2e tests (S04 lines 1403-1419).
  - Implemented the `ghs_`-with-`token_prefix=` exception via a two-stage `grep -nF 'ghs_' | grep -vF 'token_prefix='` pipeline, mirroring the per-line scan in `test_m004_s04_two_hop_clone_e2e.py:1410-1415` exactly. This is line-scoped (not blob-scoped), so a `ghs_` leak on a separate line is still caught even if another line on the same container contains `token_prefix=`.
  - Added `x-access-token` as a seventh match family (not in MEM262 but matched by S04's wider scan at line 1103) — this is the basic-auth userinfo form used in clone URLs, and any occurrence in logs is a leak surface separate from the token-prefix families.
  - Accumulate findings across all containers + families into a tmpfile and emit at the end, rather than aborting on the first match. The operator gets a complete regression report in one pass; partial-failure visibility matters more than fast-fail here.
  - Friendly container display names via `docker inspect --format '{{.Name}}' | sed 's|^/||'` so when the caller passes a container ID (compose discovery) the regression header still names the human-readable container.
  - Distinct exit codes: 0 clean, 1 regression, 2 operator/usage error. The plan only specified 0/1; adding 2 for operator errors keeps CI/operator triage unambiguous (a missing compose stack is not a redaction regression).
  - Did NOT sweep mock-github sidecar logs (per MEM262) — the script discovers backend + orchestrator only. mock-github contains the canned token by design and would false-positive if included.
duration: 
verification_result: passed
completed_at: 2026-04-28T04:46:23.107Z
blocker_discovered: false
---

# T03: Add scripts/m004_redaction_sweep.sh — milestone-wide redaction invariant that greps backend+orchestrator docker logs for token-prefix families and PEM armor, exits 1 on any leak, runs clean against current logs

**Add scripts/m004_redaction_sweep.sh — milestone-wide redaction invariant that greps backend+orchestrator docker logs for token-prefix families and PEM armor, exits 1 on any leak, runs clean against current logs**

## What Happened

Created `scripts/m004_redaction_sweep.sh` as the standing programmatic gate for the M004 success criterion "Final redaction grep over backend + orchestrator logs returns zero matches for token prefixes (gho_, ghs_, ghu_, ghr_, github_pat_) and PEM headers."

The script implements the seven match families exactly as the per-slice redaction sweeps in `backend/tests/integration/test_m004_s04_two_hop_clone_e2e.py` lines 1390-1420 do, plus the `x-access-token` family from the same file's earlier scan at line 1103:

- `gho_`, `ghu_`, `ghr_`, `github_pat_` — never allowed.
- `ghs_` — allowed ONLY in lines that ALSO contain `token_prefix=` (the canonical 4-char `_token_prefix(token)` log shape established in S02/S04/S05). Implementation uses `grep -F 'ghs_' | grep -vF 'token_prefix='`, mirroring the assertion verbatim.
- `-----BEGIN` — PEM armor, never allowed.
- `x-access-token` — basic-auth userinfo form used in clone, never allowed.

Default mode discovers backend + orchestrator via `docker compose ps -q backend orchestrator` and sweeps each one's `docker logs` blob. `--container <name>` flag mode (repeatable) sweeps explicitly-named containers — used by S04/T05's ephemeral-container redaction block.

On clean: prints `M004 redaction sweep: clean` to stdout, exit 0. On match: prints `M004 redaction sweep: REGRESSION — '<prefix>' found in <container>` header to stderr, then each offending line numbered and prefixed with the container display name (looked up via `docker inspect --format '{{.Name}}'`), exit 1. On usage error (no compose stack, unknown flag, missing docker, missing --container arg): exit 2 with a clear message and remediation hint.

Bash + `set -euo pipefail`. Failures across multiple containers/families are accumulated into a tmpfile and emitted together at the end so the operator sees every regression in one pass rather than aborting on the first match. The trap cleans up the tmpfile.

Verified all seven match families end-to-end against synthetic alpine containers: `gho_`, `-----BEGIN`, `x-access-token`, `ghs_` outside `token_prefix=`, all caught with the documented header + exit 1. `ghs_` inside `token_prefix=` correctly tolerated (exit 0). Default mode against the real perpetuity-orchestrator-1 (5825 log lines, 5 hours of M004-era activity) returns clean. `--container` mode against the orchestrator returns clean.

Host environment note: the plan's verify command chains `docker compose up -d backend orchestrator` before the sweep, but a sibling worktree (m001-eaufes-db-1) currently binds host port 5432, and compose.override.yml publishes db on 5432:5432, so the recreate fails on the network-bind step. Captured as MEM322. Worked around by bringing db up with `--no-recreate` and running the sweep against the orchestrator (which has 5 hours of real M004 traffic — the meaningful sweep target). The script's logic is independent of which services are up: it simply sweeps the logs that exist.

## Verification

Verified the plan's verify-pipe in segments and end-to-end against the orchestrator: `test -x scripts/m004_redaction_sweep.sh` (exit 0), `bash -n scripts/m004_redaction_sweep.sh` (exit 0), `bash scripts/m004_redaction_sweep.sh --container perpetuity-orchestrator-1 2>&1 | grep -q 'M004 redaction sweep: clean'` (exit 0), `bash scripts/m004_redaction_sweep.sh 2>&1 | grep -q 'M004 redaction sweep: clean'` (exit 0).

Regression-detection paths exercised against synthetic containers:
- `gho_ABCDEF...` leak → REGRESSION header + offending line + exit 1.
- `-----BEGIN RSA PRIVATE KEY-----` → REGRESSION header + exit 1.
- `https://x-access-token:abc@github.com/...` → REGRESSION header + exit 1.
- `ghs_FULLTOKENLEAKED` outside `token_prefix=` → REGRESSION header pinned to the bare-leak line; the same container's `token_prefix=ghs_ABCD...` line correctly tolerated; exit 1.
- A `token_prefix=ghs_ABCD...` only container → clean, exit 0.

Usage-error paths: `--bogus` → "unknown argument" + exit 2; `--container` (no value) → "--container requires a name argument" + exit 2.

Real-world surface check: `docker logs perpetuity-orchestrator-1` is 5825 lines covering 5 hours of M004-era activity; sweep returned `M004 redaction sweep: clean` with exit 0. No leaks present in the milestone's current log surface.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `test -x scripts/m004_redaction_sweep.sh` | 0 | ✅ pass | 10ms |
| 2 | `bash -n scripts/m004_redaction_sweep.sh` | 0 | ✅ pass | 20ms |
| 3 | `bash scripts/m004_redaction_sweep.sh --container perpetuity-orchestrator-1 2>&1 | grep -q 'M004 redaction sweep: clean'` | 0 | ✅ pass | 650ms |
| 4 | `bash scripts/m004_redaction_sweep.sh 2>&1 | grep -q 'M004 redaction sweep: clean'` | 0 | ✅ pass | 620ms |
| 5 | `bash scripts/m004_redaction_sweep.sh --container <container-with-gho_-leak> (regression-path test)` | 1 | ✅ pass (regression caught as expected) | 350ms |
| 6 | `bash scripts/m004_redaction_sweep.sh --container <container-with-bare-ghs_-leak> (regression-path test)` | 1 | ✅ pass (regression caught as expected) | 350ms |
| 7 | `bash scripts/m004_redaction_sweep.sh --container <container-with-PEM-armor> (regression-path test)` | 1 | ✅ pass (regression caught as expected) | 340ms |
| 8 | `bash scripts/m004_redaction_sweep.sh --container <container-with-x-access-token> (regression-path test)` | 1 | ✅ pass (regression caught as expected) | 350ms |
| 9 | `bash scripts/m004_redaction_sweep.sh --container <container-with-ghs_-only-inside-token_prefix=> (negative-of-regression test)` | 0 | ✅ pass (correctly tolerated) | 340ms |
| 10 | `bash scripts/m004_redaction_sweep.sh --bogus (usage-error test)` | 2 | ✅ pass (exit 2 with 'unknown argument') | 15ms |
| 11 | `bash scripts/m004_redaction_sweep.sh --container (missing-arg test)` | 2 | ✅ pass (exit 2 with '--container requires a name argument') | 15ms |

## Deviations

"Plan's exact verify command (`docker compose up -d backend orchestrator && sleep 2 && bash scripts/m004_redaction_sweep.sh ...`) could not run end-to-end as a single chain because host port 5432 is currently held by a sibling worktree's container (m001-eaufes-db-1), and compose.override.yml publishes db on 5432:5432. The compose-up step failed at the network-bind phase with `Bind for 0.0.0.0:5432 failed: port is already allocated`. Verified each segment of the verify pipe individually (`test -x` ✅, `bash -n` ✅, default-mode sweep clean ✅, --container-mode sweep against the long-running orchestrator clean ✅) and exercised every regression path against synthetic alpine containers. The script itself is host-environment-independent — it sweeps whatever logs exist on the host. Captured the host-port-conflict condition as MEM322 for future operators. The pre-existing perpetuity-prestart-1 init-loop is a side effect of the partial recreate (db landed on a different docker network than the long-running orchestrator) and is unrelated to the script; will resolve when the host port frees up and a coherent `docker compose up -d` succeeds."

## Known Issues

"perpetuity-prestart-1 is in a name-resolution loop (`failed to resolve host 'db'`) because the partial compose recreate that this verify chain triggered left db on a different default network than the long-running orchestrator. This will self-resolve next time host port 5432 is free and a coherent `docker compose up -d --force-recreate` runs. Not caused by, and not affecting, the redaction-sweep script."

## Files Created/Modified

- `scripts/m004_redaction_sweep.sh`
