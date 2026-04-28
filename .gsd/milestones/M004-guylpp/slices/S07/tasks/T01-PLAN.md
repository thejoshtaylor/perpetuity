---
estimated_steps: 11
estimated_files: 3
skills_used: []
---

# T01: Author manual-UAT integration test scaffold + S07-UAT.md recording template for the four real-GitHub acceptance scenarios

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

## Inputs

- ``.gsd/milestones/M004-guylpp/M004-guylpp-CONTEXT.md``
- ``backend/tests/integration/test_m004_s04_two_hop_clone_e2e.py``
- ``backend/tests/integration/test_m004_s05_webhook_receiver_e2e.py``
- ``.gsd/milestones/M004-guylpp/slices/S07/S07-PLAN.md``

## Expected Output

- ``backend/tests/integration/test_m004_guylpp_s07_full_acceptance_e2e.py``
- ``backend/tests/integration/.env.test-org.example``
- ``.gsd/milestones/M004-guylpp/slices/S07/S07-UAT.md``

## Verification

test -f backend/tests/integration/test_m004_guylpp_s07_full_acceptance_e2e.py && test -f backend/tests/integration/.env.test-org.example && test -f .gsd/milestones/M004-guylpp/slices/S07/S07-UAT.md && cd backend && POSTGRES_PORT=5432 uv run pytest tests/integration/test_m004_guylpp_s07_full_acceptance_e2e.py -v 2>&1 | grep -qE 'skipped|deselected' && grep -c '^## Scenario ' /Users/josh/code/perpetuity/.gsd/milestones/M004-guylpp/slices/S07/S07-UAT.md | awk '$1 >= 4 {exit 0} {exit 1}'

## Observability Impact

No new runtime signals introduced. The test asserts on the observability surfaces shipped in S02-S05: `webhook_dispatched`, `webhook_signature_invalid`, `team_mirror_reaped reason=admin`, `team_mirror_started trigger=ensure`. The S07-UAT.md recording template prefills the expected log-line names per scenario so the operator captures the diagnostic state of the real-org run.
