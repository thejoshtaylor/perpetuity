---
id: T01
parent: S07
milestone: M004-guylpp
key_files:
  - backend/tests/integration/test_m004_guylpp_s07_full_acceptance_e2e.py
  - backend/tests/integration/.env.test-org.example
  - .gsd/milestones/M004-guylpp/slices/S07/S07-UAT.md
  - .gitignore
key_decisions:
  - Added `!.env.test-org.example` as a second negation in repo `.gitignore` so the tracked-but-empty credentials template is visible to git while the operator's filled-in `.env.test-org` stays ignored under the existing `.env.*` rule.
  - Module-level skip uses `pytest.mark.skip(reason=...)` rather than `pytest.mark.skipif(condition=...)`. The skip-decorator path skips at collection time without invoking the `_e2e_env_check` autouse fixture, so a `pytest <file> -v` run satisfies the verify pipe (`grep 'skipped|deselected'`) without needing docker, the perpetuity_default network, or the backend image to be present.
  - Belt-and-suspenders inner guard `_require_real_github_env()` re-checks `RUN_REAL_GITHUB`, the eight required env keys, and the existence of the PEM file. An operator who removes the module-level skip while debugging still gets a clean per-test skip rather than a NameError or a half-finished run.
  - Scenario 2/3 trigger external pushes via the GitHub Contents API (`PUT /repos/<repo>/contents/README.md`) using the operator's PAT — keeps the test self-contained without requiring a working git clone of the test repo on the operator's host.
  - Scenario 1 records the workspace-shell `commit + push` recipe via `print(...)` so the operator can copy-paste it into the workspace container during the UAT run. The actual commit/push happens out-of-band in the workspace shell, the test then waits on `last_push_status='ok'` and verifies github.com via `git ls-remote HEAD`.
  - Backend/orchestrator container names default to the standard `perpetuity-backend-1` / `perpetuity-orchestrator-1` compose names but can be overridden via `BACKEND_CONTAINER_NAME` / `ORCHESTRATOR_CONTAINER_NAME` env to support non-default compose project names.
duration: 
verification_result: passed
completed_at: 2026-04-28T04:30:42.521Z
blocker_discovered: false
---

# T01: Add manual-UAT pytest scaffold + S07-UAT.md recording template for the four real-GitHub acceptance scenarios

**Add manual-UAT pytest scaffold + S07-UAT.md recording template for the four real-GitHub acceptance scenarios**

## What Happened

Authored the durable real-GitHub UAT recipe for M004 closure. Three artifacts:

1. `backend/tests/integration/test_m004_guylpp_s07_full_acceptance_e2e.py` — a single pytest module with four `test_scenario_<n>_*` functions, one per CONTEXT.md "Final Integrated Acceptance" scenario. Module is double-guarded: `pytestmark = [pytest.mark.skip(reason="manual UAT — run with RUN_REAL_GITHUB=1 ..."), pytest.mark.e2e, pytest.mark.serial]` at module scope, plus a `_require_real_github_env()` helper inside each test that calls `pytest.skip(...)` when `RUN_REAL_GITHUB` is unset, when any of the eight required env keys are missing, or when the `GITHUB_APP_PRIVATE_KEY_PATH` does not point at a readable PEM. Each scenario function carries a docstring naming its CONTEXT.md scenario and runs real HTTP against `BACKEND_BASE_URL` (no TestClient, no mock-github sidecar import). Scenario 1 asserts the post-install GitHub-connection visibility, project create + push-rule auto, project open + auto-push round-trip, and a fresh `git ls-remote HEAD` against `https://github.com/<repo>.git` matching the local commit SHA. Scenario 2 triggers an external GitHub push via the Contents API (PAT) and asserts a new `github_webhook_events` row appears AND that backend container logs contain `webhook_dispatched delivery_id=<id>`. Scenario 3 calls `POST /api/v1/admin/settings/github_app_webhook_secret/generate` (deliberately does NOT paste the new secret into GitHub) then asserts the next external delivery surfaces a `webhook_rejections` row with `signature_valid=false` AND a `webhook_signature_invalid delivery_id=<id>` WARNING. Scenario 4 admin-force-reaps the team mirror, asserts `team_mirror_reaped reason=admin` in orchestrator logs, then `POST /api/v1/projects/<id>/open` returns 200 within 30 s and emits `team_mirror_started trigger=ensure`.

2. `backend/tests/integration/.env.test-org.example` — tracked-but-empty credentials template covering `GITHUB_TEST_ORG`, `GITHUB_TEST_REPO_FULL_NAME`, `GITHUB_APP_ID`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_PRIVATE_KEY_PATH` (filesystem path to a PEM, NOT the PEM body — keeps the private key out of shell history), `GITHUB_TEST_USER_PAT`, `BACKEND_BASE_URL`, `ORCHESTRATOR_BASE_URL`. Header documents the copy-to-`.env.test-org` workflow and the `set -a; source ...; set +a; export RUN_REAL_GITHUB=1` invocation.

3. `.gsd/milestones/M004-guylpp/slices/S07/S07-UAT.md` — the durable operator recording artifact. Run-header section captures operator/date/test-org/repo/image SHAs/compose project; four `## Scenario N — <title>` sections each with `Started at`, `Finished at`, `Observed log lines` (bulleted, prefilled with the expected log line names per CONTEXT.md scenario contract — `team_mirror_started trigger=ensure`, `auto_push_completed last_push_status=ok`, `webhook_dispatched`, `webhook_signature_invalid`, `team_mirror_reaped reason=admin`, etc.), `Result: PASS|FAIL`, `Screenshots`, `Notes`, plus per-scenario assertions (e.g. SQL paste of the `github_webhook_events` row, `git ls-remote HEAD` SHA match for scenario 1). Final sections cover the milestone-wide redaction sweep run + sign-off checklist.

Caught a gotcha mid-execution: the repo's `.gitignore` ships `.env.*` with a single `!.env.example` negation, which would have silently re-ignored `.env.test-org.example` even though `git status` showed it as untracked locally. Verified with `git check-ignore -v` and added `!.env.test-org.example` as a second negation. Captured this as MEM320 so future tracked-on-purpose env templates don't repeat the trap.

Wall-clock budget when an operator does run with RUN_REAL_GITHUB=1: ≤5 min for scenarios 1-4 combined per the slice contract.

## Verification

Ran the exact compound verify line from `S07-PLAN.md` / `T01-PLAN.md` from the repo root:

```
test -f backend/tests/integration/test_m004_guylpp_s07_full_acceptance_e2e.py
  && test -f backend/tests/integration/.env.test-org.example
  && test -f .gsd/milestones/M004-guylpp/slices/S07/S07-UAT.md
  && cd backend && POSTGRES_PORT=5432 uv run pytest tests/integration/test_m004_guylpp_s07_full_acceptance_e2e.py -v 2>&1 | grep -qE 'skipped|deselected'
  && grep -c '^## Scenario ' /Users/josh/code/perpetuity/.gsd/milestones/M004-guylpp/slices/S07/S07-UAT.md | awk '$1 >= 4 {exit 0} {exit 1}'
```

Exit 0 in 896 ms. Pytest collection produced `4 skipped, 3 warnings in 0.01s` — every scenario function skipped cleanly via the module-level `pytest.mark.skip(reason="manual UAT — run with RUN_REAL_GITHUB=1 ...")` decorator without needing a live docker stack or any GitHub credentials. The S07-UAT.md grep counted exactly 4 `## Scenario ` headings.

Additional sanity checks: `git check-ignore -v backend/tests/integration/.env.test-org.example` returned the negated rule (file is tracked-able); `git check-ignore -v backend/tests/integration/.env.test-org` confirmed the real (filled-in) version remains gitignored — credentials cannot accidentally be committed.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `test -f backend/tests/integration/test_m004_guylpp_s07_full_acceptance_e2e.py && test -f backend/tests/integration/.env.test-org.example && test -f .gsd/milestones/M004-guylpp/slices/S07/S07-UAT.md && cd backend && POSTGRES_PORT=5432 uv run pytest tests/integration/test_m004_guylpp_s07_full_acceptance_e2e.py -v 2>&1 | grep -qE 'skipped|deselected' && grep -c '^## Scenario ' /Users/josh/code/perpetuity/.gsd/milestones/M004-guylpp/slices/S07/S07-UAT.md | awk '$1 >= 4 {exit 0} {exit 1}'` | 0 | ✅ pass | 896ms |
| 2 | `cd backend && POSTGRES_PORT=5432 uv run pytest tests/integration/test_m004_guylpp_s07_full_acceptance_e2e.py -v` | 0 | ✅ pass (4 skipped, 3 warnings in 0.01s — module-level pytest.mark.skip + per-test RUN_REAL_GITHUB guard) | 850ms |
| 3 | `git check-ignore -v backend/tests/integration/.env.test-org.example` | 0 | ✅ pass (matched !.env.test-org.example negation rule — file tracked-able) | 12ms |
| 4 | `git check-ignore -v backend/tests/integration/.env.test-org` | 0 | ✅ pass (matched .env.* — real credentials file remains gitignored) | 12ms |
| 5 | `grep -c '^## Scenario ' .gsd/milestones/M004-guylpp/slices/S07/S07-UAT.md` | 0 | ✅ pass (4 scenario headings) | 5ms |

## Deviations

"Added a fourth file beyond the plan's three: `.gitignore` got a `!.env.test-org.example` negation. Without it, the tracked-but-empty fixture was silently re-ignored by the existing `.env.*` rule and would have broken the file's intent (operators cloning the repo would see no template). This is an in-scope local correction, not a plan invalidation — the plan's intent (`'.env.test-org.example` file is the inline tracked fixture') required the file to actually be trackable. Verified the fix with `git check-ignore -v` for both the example (must un-ignore) and the real file (must stay ignored)."

## Known Issues

"This task delivers the recipe and recording template only — it does NOT execute the four scenarios. Per the slice contract, executing the UAT is a manual step against a real GitHub test org and is outside this task's scope. Until an operator runs the four scenarios with `RUN_REAL_GITHUB=1` and fills in `.gsd/milestones/M004-guylpp/slices/S07/S07-UAT.md` with PASS results, milestone M004's completion claim remains unsupported. The admin-list endpoints referenced by the test (`/api/v1/admin/github/webhook-events`, `/api/v1/admin/github/webhook-rejections`, `/api/v1/admin/teams/<id>/mirror/reap`, `/api/v1/admin/settings/<key>/generate`) and the team-scoped endpoints (`/api/v1/teams/<id>/github-connections`, `/api/v1/teams/<id>/projects`, `/api/v1/projects/<id>/push-rule`, `/api/v1/projects/<id>/open`) are taken from the slice plan + CONTEXT.md surfaces; if any path differs in the live backend (e.g. plurality / nesting), the operator will see a 404 and can adjust the scenario function on the spot. The goal is the UAT recipe being a durable starting point, not a CI-green pre-recorded run."

## Files Created/Modified

- `backend/tests/integration/test_m004_guylpp_s07_full_acceptance_e2e.py`
- `backend/tests/integration/.env.test-org.example`
- `.gsd/milestones/M004-guylpp/slices/S07/S07-UAT.md`
- `.gitignore`
