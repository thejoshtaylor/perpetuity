---
estimated_steps: 1
estimated_files: 3
skills_used: []
---

# T04: Scenario 3 — Org-install regression + evidence capture + cleanup

Scenario 3 is the explicit regression-protection check. If it fails, M005-sqm8et's org path has been broken by this milestone — must reopen the affected slice. Execute steps in must-have (6): install App on real GitHub organization, confirm install lands in github_app_installations with account_type=Organization. Open create-repo modal for team that owns org install. Enter repo_name = m006-acceptance-org-<timestamp>. Expect success within ~2s. Capture screenshot + orchestrator log. After all three scenarios pass: cleanup per must-have (8) — delete three test repos via gh, leave personal install in place for future regression checks.

## Inputs

- `T02 + T03 completed`
- `Real GitHub organization with operator as admin`

## Expected Output

- `Screenshot of org-install success state`
- `Orchestrator log line github_repository_created with NO token_class=user_token field (proves install-token path was taken)`
- `gh repo view returns 404 for all three test repos after cleanup`
- `SUMMARY appended with Scenario 3 evidence subsection`

## Verification

! grep -q token_class=user_token .gsd/milestones/M006-ydo2ce/evidence/scenario3-orchestrator.log && gh repo view <personal>/m006-acceptance-personal-<ts> 2>&1 | grep -q 'Could not resolve'
