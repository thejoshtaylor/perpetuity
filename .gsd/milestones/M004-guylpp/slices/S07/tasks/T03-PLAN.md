---
estimated_steps: 15
estimated_files: 1
skills_used: []
---

# T03: Add scripts/m004_redaction_sweep.sh as the milestone-wide redaction invariant + run it green against current logs

Create `scripts/m004_redaction_sweep.sh` — an executable bash script that greps the running backend + orchestrator containers' docker logs for the GitHub token-prefix family + PEM headers and exits non-zero on any match. This is the standing programmatic invariant for the M004 redaction discipline (per the milestone's success criteria: "Final redaction grep over backend + orchestrator logs returns zero matches for token prefixes (gho_, ghs_, ghu_, ghr_, github_pat_) and PEM headers"). It extends and centralizes the per-slice redaction sweeps already embedded in S02/S04/S05 e2e tests so an operator can run one command instead of remembering five.

Script shape: bash + set -euo pipefail. Default behavior: run `docker compose ps -q backend orchestrator` to discover the two containers in the current compose stack; for each container, run `docker logs <id> 2>&1` and pipe through a series of greps. Match families:
  - `gho_` — fail loud if found anywhere
  - `ghu_` — fail loud if found anywhere
  - `ghr_` — fail loud if found anywhere
  - `github_pat_` — fail loud if found anywhere
  - `ghs_` — allowed ONLY in lines that ALSO contain `token_prefix=` (the canonical 4-char log shape established in S02/S04/S05); any other occurrence fails
  - `-----BEGIN` — fail loud if found anywhere (PEM armor)
  - `x-access-token` — fail loud if found anywhere (basic-auth userinfo form used in clone)

The `ghs_`-only-with-`token_prefix=` rule mirrors the assertion in `test_m004_s04_two_hop_clone_e2e.py` lines 1407-1416 verbatim. Implementation: use `awk` or a `while read line` loop to handle the conditional grep — a single `grep -v 'token_prefix='` filter works because legitimate uses always co-occur with that substring on the same line.

Behavior on success: print `M004 redaction sweep: clean` to stdout and exit 0. Behavior on any match: print the offending line(s) (preserving which container they came from) to stderr with a `M004 redaction sweep: REGRESSION — <prefix> found in <container>` header, then exit 1. The script also accepts `--container <name>` flags for ad-hoc use against ephemeral test containers (S04/T05's redaction-sweep block sweeps named ephemeral containers — this script's --container flag lets that integration work, even though the default mode targets the compose stack).

Make the script executable (`chmod +x`).

Then RUN the script against the currently-running compose stack. The expected outcome is a clean pass: prior slices have already produced thousands of structured log lines with `token_prefix=ghs_<4>...` and zero plaintext-token leakages. If the script finds something, that's a real regression and must be triaged before this slice is marked complete — a leak in the current logs would block milestone closure.

Note: the script does not require the manual UAT (T01) to have been run — it sweeps whatever logs exist on the host at run time. Operators run it post-UAT to confirm the real-org run did not introduce a regression. CI can run it post-e2e-suite to gate merges. The redaction sweep is the closest thing M004 has to a single-command "is the milestone still safe to ship" gate.

Constraints: the script reads only from `docker logs` — it does not introspect containers' filesystems, so credentials in mounted volumes are out of scope (they're handled by the env-on-exec discipline in clone.py + auto_push.py). The script is hermetic in the sense that a `docker compose ps -q` returning zero containers should produce a clear "no compose stack running — start it with `docker compose up -d backend orchestrator` first" error, not a confusing pass.

## Inputs

- ``backend/tests/integration/test_m004_s04_two_hop_clone_e2e.py``
- ``backend/tests/integration/test_m004_s05_webhook_receiver_e2e.py``
- ``backend/tests/integration/test_m004_s02_github_install_e2e.py``
- ``docker-compose.yml``

## Expected Output

- ``scripts/m004_redaction_sweep.sh``

## Verification

test -x scripts/m004_redaction_sweep.sh && bash -n scripts/m004_redaction_sweep.sh && docker compose up -d backend orchestrator && sleep 2 && bash scripts/m004_redaction_sweep.sh 2>&1 | grep -q 'M004 redaction sweep: clean'

## Observability Impact

Adds a programmatic CI-runnable gate for the M004 redaction discipline. Failure visibility: the script prints the offending log line + container name to stderr and exits 1 — operators see exactly which prefix family matched and where. The gate is the durable companion to the per-slice in-test redaction asserts shipped in S02/S04/S05; those still run inside their respective e2e suites, but this script is the standing one-command sweep an operator runs out of band.
