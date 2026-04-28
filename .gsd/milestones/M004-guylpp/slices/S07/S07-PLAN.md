# S07: Final integrated acceptance against real GitHub test org + operator runbook

**Goal:** Close M004 with manual UAT against a real GitHub test org covering the four CONTEXT.md "Final Integrated Acceptance" scenarios, ship operator runbooks for SYSTEM_SETTINGS_ENCRYPTION_KEY + webhook-secret rotation, and gate milestone completion on a programmatic milestone-wide redaction sweep that finds zero token-prefix or PEM-header matches across backend + orchestrator logs.
**Demo:** Manual UAT against a real GitHub test org records the four scenarios from CONTEXT.md Final Integrated Acceptance: (1) full install→project→open→commit→push→auto-push→github.com round-trip; (2) external GitHub push delivers a webhook that verifies, persists, and fires the no-op dispatch hook; (3) generate-then-rotate webhook secret breaks old deliveries with 401 until GitHub-side is updated; (4) mirror reap → user click 'Open project' → cold-start → clone proceeds. Each scenario has a recorded timestamp, observed log lines, and screenshots in S07-UAT.md. Final redaction grep over backend + orchestrator logs returns zero matches for token prefixes (gho_, ghs_, ghu_, ghr_, github_pat_) and PEM headers.

## Must-Haves

- `backend/tests/integration/test_m004_guylpp_s07_full_acceptance_e2e.py` exists, is `pytest.mark.skip`-decorated by default with a `RUN_REAL_GITHUB=1` opt-in, and contains executable assertions covering all four CONTEXT.md scenarios (happy path, webhook round-trip, generate-then-rotate, mirror cold-start)
- `.gsd/milestones/M004-guylpp/slices/S07/S07-UAT.md` exists with an entry per scenario carrying timestamp + observed log lines + screenshot/result placeholders ready for an operator to fill in during the real-org run
- `docs/runbooks/m004-secrets-rotation.md` exists and documents both rotations (encryption-key + webhook-secret) end-to-end with operator coordination steps
- `scripts/m004_redaction_sweep.sh` exists, is executable, and exits non-zero when any of `gho_`, `ghu_`, `ghr_`, `github_pat_`, `ghs_` (outside `token_prefix=` context), or `-----BEGIN` is found in backend/orchestrator container logs
- The redaction sweep run in this slice against current backend + orchestrator container logs returns zero matches and prints `M004 redaction sweep: clean`

## Proof Level

- This slice proves: final-assembly. Real runtime required: yes (manual UAT is intentionally human-driven against a real GitHub test org per the slice contract). Human/UAT required: yes — the four-scenario UAT cannot be simulated and must be executed by an operator with access to a GitHub test org. The redaction sweep is programmatically verifiable on this host against the live compose stack.

## Integration Closure

Upstream surfaces consumed: every endpoint and structured log line shipped in S01-S06. New wiring introduced: zero — this slice is documentation + manual verification + a CI-runnable redaction-grep wrapper around prior slices' log output. What remains before the milestone is truly usable end-to-end: the operator must successfully run the four UAT scenarios against a real GitHub test org and record the results in S07-UAT.md. Until that recording is in place, milestone completion claim is unsupported. The redaction sweep script is the standing programmatic invariant.

## Verification

- No new runtime signals are produced by S07 — the slice consumes the observability surfaces shipped in S01-S05 (`webhook_received`, `webhook_verified`, `webhook_dispatched`, `webhook_signature_invalid`, `system_settings_decrypt_failed`, `team_mirror_started/reaped`, `team_mirror_clone_started/completed`, `auto_push_started/completed`, `installation_token_minted/cache_hit`). The new artifact is the `scripts/m004_redaction_sweep.sh` gate, which becomes the standing milestone-wide redaction invariant: any future M004-touching change is expected to pass it. Failure visibility for the manual UAT is captured by the operator in S07-UAT.md (timestamp + observed log line + screenshot per scenario). Redaction grep covers: `gho_`, `ghu_`, `ghr_`, `github_pat_`, `ghs_` (allowed only inside `token_prefix=ghs_<4>...` shape), `-----BEGIN` PEM armor.

## Tasks

- [x] **T01: Author manual-UAT integration test scaffold + S07-UAT.md recording template for the four real-GitHub acceptance scenarios** `est:2h`
  Create the test file `backend/tests/integration/test_m004_guylpp_s07_full_acceptance_e2e.py` that codifies the four CONTEXT.md "Final Integrated Acceptance" scenarios as a manual-mode pytest module. The file is the durable recipe operators run against a real GitHub test org; it must skip cleanly in CI and on dev hosts that lack the test-org credentials.

The scenarios, exactly as stated in M004-guylpp-CONTEXT.md §"Final Integrated Acceptance":
  1. End-to-end happy path: install GitHub App → see connection in team settings → create project linked to real repo → click open → repo materializes at `/workspaces/<u>/<t>/<project_name>` with no credentials in `.git/config` → user commits + pushes → mirror receives → auto-push pushes to GitHub → github.com shows the commit
  2. Webhook round-trip: external push to GitHub repo → GitHub delivers webhook → HMAC verifies → row in github_webhook_events → no-op dispatch_github_event invoked (assert via `webhook_dispatched` log line)
  3. Generate-then-rotate webhook secret: admin generates secret → pastes into GitHub → webhook from GitHub verifies clean → admin re-generates → next external GitHub delivery returns 401 + audit row in webhook_rejections until GitHub side updated
  4. Mirror lifecycle cold-start: mirror reaped (idle or admin force-reap) → user clicks open → mirror cold-starts → clone proceeds → mirror reachable via compose-network DNS

Structure: a single test module guarded by `pytestmark = [pytest.mark.skip(reason="manual UAT — run with RUN_REAL_GITHUB=1 against a real GitHub test org"), pytest.mark.e2e, pytest.mark.serial]` at module scope, plus an explicit `if not os.environ.get("RUN_REAL_GITHUB"): pytest.skip(...)` guard inside each test for belt-and-suspenders. Four `def test_scenario_<n>_*` functions, each with a docstring naming the CONTEXT.md scenario it implements and inline assertions / observable steps. Credentials sourced from a tracked-but-empty `backend/tests/integration/.env.test-org.example` file — the operator copies it to `.env.test-org` (gitignored) and fills in `GITHUB_TEST_ORG`, `GITHUB_TEST_REPO_FULL_NAME`, `GITHUB_APP_ID`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_PRIVATE_KEY_PATH` (filesystem path to a PEM, not the PEM body — keeps secrets off shell history), `GITHUB_TEST_USER_PAT` (for triggering the external push in scenario 2), and `BACKEND_BASE_URL` / `ORCHESTRATOR_BASE_URL`.

The four scenario functions run real HTTP against the live stack (no TestClient, no mock-github sidecar) — they're the contract proof that prior slices' mock-github-backed e2es were faithful approximations. Each function asserts the observable surfaces from CONTEXT.md: scenario 1 asserts `git rev-parse HEAD` from a fresh github.com fetch matches the local commit SHA after auto-push; scenario 2 asserts a row appears in `github_webhook_events` with the `delivery_id` GitHub returns in the `X-GitHub-Delivery` header AND that the backend container logs contain `webhook_dispatched delivery_id=<id>`; scenario 3 asserts the post-rotate webhook returns HTTP 401 with `webhook_rejections.signature_valid=false` AND the WARNING `webhook_signature_invalid` log line; scenario 4 asserts `team_mirror_reaped reason=admin` precedes `team_mirror_started trigger=ensure` and the user's open returns 200 within 30s.

Also write `.gsd/milestones/M004-guylpp/slices/S07/S07-UAT.md` as the recording template. Five sections: a header with operator/date/test-org-name fields, then one section per scenario carrying `Started at: <timestamp>`, `Observed log lines:` (bulleted, prefilled with the expected log line names from the scenario's contract), `Result: PASS|FAIL`, `Screenshots: <list>`, `Notes:`. The operator fills these in during the real run; the file is the durable artifact.

Rationale for the manual-skip-by-default shape: this slice's contract is that the four scenarios MUST be exercised against a real GitHub test org — that's not simulatable. The test file's job is to (a) be a durable, code-reviewable recipe so the next operator who runs UAT doesn't have to reverse-engineer the steps, (b) carry executable assertions so a future operator can flip RUN_REAL_GITHUB=1 in CI when the project gets a dedicated test-org account and have a green/red signal, and (c) not run by default — running it without a real org would either no-op or error noisily.

Constraints: the test file's only test fixtures it imports MUST be tracked in git. Do not import from `.gsd/`, `.planning/`, or `.audits/`. The `.env.test-org.example` file is the inline tracked fixture. The skip-decorator covers the case where the file is run on a dev box without the env. The test file imports nothing from `backend/tests/integration/fixtures/mock_github_app.py` — this is the real-org branch. Wall-clock budget when run with RUN_REAL_GITHUB=1: ≤5 minutes for scenarios 1-4 combined.
  - Files: `backend/tests/integration/test_m004_guylpp_s07_full_acceptance_e2e.py`, `backend/tests/integration/.env.test-org.example`, `.gsd/milestones/M004-guylpp/slices/S07/S07-UAT.md`
  - Verify: test -f backend/tests/integration/test_m004_guylpp_s07_full_acceptance_e2e.py && test -f backend/tests/integration/.env.test-org.example && test -f .gsd/milestones/M004-guylpp/slices/S07/S07-UAT.md && cd backend && POSTGRES_PORT=5432 uv run pytest tests/integration/test_m004_guylpp_s07_full_acceptance_e2e.py -v 2>&1 | grep -qE 'skipped|deselected' && grep -c '^## Scenario ' /Users/josh/code/perpetuity/.gsd/milestones/M004-guylpp/slices/S07/S07-UAT.md | awk '$1 >= 4 {exit 0} {exit 1}'

- [x] **T02: Write operator runbook for SYSTEM_SETTINGS_ENCRYPTION_KEY + webhook-secret rotation** `est:1h`
  Create `docs/runbooks/m004-secrets-rotation.md` documenting both rotation procedures end-to-end. Create the `docs/runbooks/` directory in the same task (does not exist yet — verified by `ls docs/runbooks/` returning No such file or directory).

The runbook must cover two procedures:

**Procedure 1 — SYSTEM_SETTINGS_ENCRYPTION_KEY rotation.** This is the Fernet key wrapping every sensitive system_settings row. The current architecture has no key-versioning column on system_settings (D020) — rotation is a coordinated re-encrypt + restart, not an online migration. Steps: (1) generate a new Fernet key with `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`; (2) shell into a backend container with both old and new keys available, walk every row WHERE sensitive=true, decrypt with the old key, re-encrypt with the new key, write back; (3) update `SYSTEM_SETTINGS_ENCRYPTION_KEY` in the deployment environment; (4) restart backend + orchestrator together (both processes call `decrypt_setting`); (5) verify by hitting any sensitive admin GET endpoint — `200 has_value=true value=null` means decrypt round-tripped; if instead a 503 `system_settings_decrypt_failed` surfaces, the re-encrypt step missed a row and the operator must re-run with the old key still present in env. Include a copy-pasteable Python re-encrypt snippet that uses `app.core.encryption._load_key` from both keys (operator sets `OLD_SYSTEM_SETTINGS_ENCRYPTION_KEY` env temporarily). Call out the failure mode: if the operator updates the env BEFORE re-encrypting, every sensitive read fails with 503 and the only recovery is to revert the env. Document the inverse operation as the recovery procedure.

**Procedure 2 — github_app_webhook_secret rotation.** This is the secret GitHub uses to sign webhook deliveries. Re-generate is intentionally destructive (D025) — old GitHub deliveries return 401 until the operator updates the GitHub-side webhook configuration to use the new secret. Steps: (1) operator decides on a coordination window; (2) admin clicks Generate webhook secret in /admin/settings, captures the one-time-display value within the modal lifetime (NOT screenshot — value lives only in modal closure per S06 invariant; copy-paste into clipboard then immediately into the GitHub App settings UI); (3) GitHub Settings → Apps → <our-app> → Edit → Webhook secret field → paste → Save; (4) verify by triggering an external push to a test repo; HMAC must verify cleanly with the new secret. Document the recovery: if the admin closes the modal before pasting into GitHub, the secret is unrecoverable — operator must Generate again. Document the visibility surface during the rotation window: `webhook_signature_invalid` WARNING lines with `delivery_id` from GitHub will accumulate until the GitHub-side update lands; that's expected and audit rows in `webhook_rejections` are the durable evidence the rotation was in flight.

Also include a short third subsection "Inspecting state at rotation time" listing the SQL queries operators reach for: `SELECT key, has_value, sensitive, has_encrypted FROM system_settings WHERE sensitive=true`; `SELECT delivery_id, signature_valid, source_ip, received_at FROM webhook_rejections WHERE received_at > NOW() - INTERVAL '1 hour' ORDER BY received_at DESC LIMIT 50`. These mirror the surfaces named in CONTEXT.md §"Open Questions" and the operator-readiness sections of S04-S06 summaries.

File location: `docs/runbooks/m004-secrets-rotation.md` per CONTEXT.md §"Open Questions" ("the runbook (S07) should call out the operator coordination needed") and the slice plan's boundary map (S07 produces an operator runbook "likely `deployment.md` extension or new `docs/runbooks/m004-secrets-rotation.md`"). Choose `docs/runbooks/` because (a) `deployment.md` is currently a Traefik/Docker setup doc — extending it would muddy concerns; (b) future M005+ runbooks will want the same parent directory.

File structure: 4 H2 sections — "Overview" (1 paragraph naming both procedures and when to run each), "Procedure 1: SYSTEM_SETTINGS_ENCRYPTION_KEY rotation", "Procedure 2: github_app_webhook_secret rotation", "Inspecting state at rotation time". Each procedure section carries numbered steps, copy-pasteable commands in code blocks, and an explicit "Recovery" subsection.
  - Files: `docs/runbooks/m004-secrets-rotation.md`
  - Verify: test -f docs/runbooks/m004-secrets-rotation.md && grep -qE '^## Procedure 1: SYSTEM_SETTINGS_ENCRYPTION_KEY rotation' docs/runbooks/m004-secrets-rotation.md && grep -qE '^## Procedure 2: github_app_webhook_secret rotation' docs/runbooks/m004-secrets-rotation.md && grep -qE '^## Inspecting state at rotation time' docs/runbooks/m004-secrets-rotation.md && grep -qE 'Fernet|fernet' docs/runbooks/m004-secrets-rotation.md && grep -qE 'webhook_signature_invalid' docs/runbooks/m004-secrets-rotation.md && grep -qE '### Recovery' docs/runbooks/m004-secrets-rotation.md && [ $(wc -l < docs/runbooks/m004-secrets-rotation.md) -gt 50 ]

- [x] **T03: Add scripts/m004_redaction_sweep.sh as the milestone-wide redaction invariant + run it green against current logs** `est:1h`
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
  - Files: `scripts/m004_redaction_sweep.sh`
  - Verify: test -x scripts/m004_redaction_sweep.sh && bash -n scripts/m004_redaction_sweep.sh && docker compose up -d backend orchestrator && sleep 2 && bash scripts/m004_redaction_sweep.sh 2>&1 | grep -q 'M004 redaction sweep: clean'

## Files Likely Touched

- backend/tests/integration/test_m004_guylpp_s07_full_acceptance_e2e.py
- backend/tests/integration/.env.test-org.example
- .gsd/milestones/M004-guylpp/slices/S07/S07-UAT.md
- docs/runbooks/m004-secrets-rotation.md
- scripts/m004_redaction_sweep.sh
