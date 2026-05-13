# S07: Final integrated acceptance against real GitHub.com

**Goal:** Prove the three Final Integrated Acceptance scenarios from M006-ydo2ce-CONTEXT.md against the real GitHub.com API on a real personal GitHub account and a real org GitHub account. Capture screenshots and log excerpts as the operational proof artifact. This slice ships no production code; it ships proof.
**Demo:** A human operator performs each of the three CONTEXT scenarios end-to-end against a real GitHub App installation pointed at real GitHub accounts, captures evidence (screenshot of UI success state, docker compose logs orchestrator backend excerpt showing the correct branch was taken), and pastes those artifacts into M006-ydo2ce-SUMMARY.md under a 'Final Integrated Acceptance Evidence' section.

## Must-Haves

- All three CONTEXT scenarios pass against real GitHub.com (personal happy-path, pre-M006 reinstall flow, org-install regression check); evidence saved at .gsd/milestones/M006-ydo2ce/evidence/ with at least 3 screenshots; M006-ydo2ce-SUMMARY.md contains a Final Integrated Acceptance Evidence section with three subsections; orchestrator log shows token_class=user_token for scenarios 1 and 2 and NO token_class field for scenario 3; all M005-sqm8et and M006-ydo2ce S01-S06 tests still pass; test repos cleaned up after evidence captured.

## Proof Level

- This slice proves: Operational + UAT — the milestone's end-to-end claim against real GitHub.com. No further proof level is achievable; this is the highest-confidence verification the project can produce. Real GitHub.com, real personal account, real organization. Human/UAT required: operator is the auditor.

## Integration Closure

Upstream surfaces consumed: every slice in M006-ydo2ce. New wiring: none — S07 only exercises what S01-S06 built. What remains: nothing. S07 IS the closure step.

## Verification

- All logs introduced in S02–S05. S07 is the first time these logs are exercised against real GitHub responses; pay attention to any field formats that drift from the mocked expectations. Any scenario failure produces evidence — paste the failing screenshot and log excerpt directly into the bug ticket. NEVER paste raw access or refresh tokens into evidence.

## Tasks

- [x] **T01: Pre-flight — confirm GitHub App config + compose stack health** `est:30m`
  Any unfinished prerequisite turns a 'find a real-runtime bug' exercise into 'debug environment misconfiguration' — eliminate the latter first. Visit https://github.com/settings/apps/<app-slug>/permissions; take a screenshot showing OAuth enabled + Contents: R/W (or equivalent); save to evidence dir. Run docker compose ps and confirm all five services healthy. Hit GET /api/v1/health on backend; hit GET /v1/health on orchestrator. Verify head migration is s17_github_user_oauth_tokens via cd backend && uv run alembic current. Write 00-preflight.md listing each check + result.
  - Files: `.gsd/milestones/M006-ydo2ce/evidence/00-preflight.md`, `.gsd/milestones/M006-ydo2ce/evidence/00-preflight-github-app.png`
  - Verify: test -f .gsd/milestones/M006-ydo2ce/evidence/00-preflight.md && grep -q s17_github_user_oauth_tokens .gsd/milestones/M006-ydo2ce/evidence/00-preflight.md

- [x] **T02: Scenario 1 — Personal-install happy path + evidence capture** `est:30m`
  Scenario 1 is the milestone's primary user story (solo dev creates a repo on their personal GitHub account). If this fails, the milestone has nothing. Execute steps in must-have (4): from team's project setup, open Create new repo modal (pre-condition: personal-account install AFTER S02 deploy). Enter repo_name = m006-acceptance-personal-<timestamp>, blank description, Private. Click Create. Expect success within ~2s, dialog closes, next step shown with new repo URL prefilled. Capture all four evidence artifacts.
  - Files: `.gsd/milestones/M006-ydo2ce/evidence/scenario1-personal-happy.png`, `.gsd/milestones/M006-ydo2ce/evidence/scenario1-orchestrator.log`, `.gsd/milestones/M006-ydo2ce/M006-ydo2ce-SUMMARY.md`
  - Verify: gh repo view <personal>/m006-acceptance-personal-<ts> && grep -q token_class=user_token .gsd/milestones/M006-ydo2ce/evidence/scenario1-orchestrator.log

- [x] **T03: Scenario 2 — Pre-M006 reinstall flow + evidence capture** `est:45m`
  Scenario 2 proves the CTA flow works for the migration-edge case (users who installed before this milestone deployed). This is the most-likely path for the first weeks after deploy. Execute steps in must-have (5): DELETE FROM github_user_oauth_tokens WHERE user_id = <operator-uuid> to simulate pre-M006 state. Open modal, attempt create. Expect inline error with reinstall CTA, submit button hidden, Cancel visible. Click Reinstall on GitHub. Complete install in new tab. Re-open modal, retry. Expect success. Capture CTA screenshot, post-retry success screenshot, then backend log excerpts.
  - Files: `.gsd/milestones/M006-ydo2ce/evidence/scenario2-cta.png`, `.gsd/milestones/M006-ydo2ce/evidence/scenario2-success.png`, `.gsd/milestones/M006-ydo2ce/evidence/scenario2-backend.log`, `.gsd/milestones/M006-ydo2ce/M006-ydo2ce-SUMMARY.md`
  - Verify: grep -q "github_user_token_required.*reason=row_missing" .gsd/milestones/M006-ydo2ce/evidence/scenario2-backend.log && grep -q github_user_token_persisted .gsd/milestones/M006-ydo2ce/evidence/scenario2-backend.log

- [ ] **T04: Scenario 3 — Org-install regression + evidence capture + cleanup** `est:30m`
  Scenario 3 is the explicit regression-protection check. If it fails, M005-sqm8et's org path has been broken by this milestone — must reopen the affected slice. Execute steps in must-have (6): install App on real GitHub organization, confirm install lands in github_app_installations with account_type=Organization. Open create-repo modal for team that owns org install. Enter repo_name = m006-acceptance-org-<timestamp>. Expect success within ~2s. Capture screenshot + orchestrator log. After all three scenarios pass: cleanup per must-have (8) — delete three test repos via gh, leave personal install in place for future regression checks.
  - Files: `.gsd/milestones/M006-ydo2ce/evidence/scenario3-org-success.png`, `.gsd/milestones/M006-ydo2ce/evidence/scenario3-orchestrator.log`, `.gsd/milestones/M006-ydo2ce/M006-ydo2ce-SUMMARY.md`
  - Verify: ! grep -q token_class=user_token .gsd/milestones/M006-ydo2ce/evidence/scenario3-orchestrator.log && gh repo view <personal>/m006-acceptance-personal-<ts> 2>&1 | grep -q 'Could not resolve'

## Files Likely Touched

- .gsd/milestones/M006-ydo2ce/evidence/00-preflight.md
- .gsd/milestones/M006-ydo2ce/evidence/00-preflight-github-app.png
- .gsd/milestones/M006-ydo2ce/evidence/scenario1-personal-happy.png
- .gsd/milestones/M006-ydo2ce/evidence/scenario1-orchestrator.log
- .gsd/milestones/M006-ydo2ce/M006-ydo2ce-SUMMARY.md
- .gsd/milestones/M006-ydo2ce/evidence/scenario2-cta.png
- .gsd/milestones/M006-ydo2ce/evidence/scenario2-success.png
- .gsd/milestones/M006-ydo2ce/evidence/scenario2-backend.log
- .gsd/milestones/M006-ydo2ce/evidence/scenario3-org-success.png
- .gsd/milestones/M006-ydo2ce/evidence/scenario3-orchestrator.log
