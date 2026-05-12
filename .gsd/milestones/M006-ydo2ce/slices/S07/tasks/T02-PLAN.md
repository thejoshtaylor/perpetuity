---
estimated_steps: 1
estimated_files: 3
skills_used: []
---

# T02: Scenario 1 — Personal-install happy path + evidence capture

Scenario 1 is the milestone's primary user story (solo dev creates a repo on their personal GitHub account). If this fails, the milestone has nothing. Execute steps in must-have (4): from team's project setup, open Create new repo modal (pre-condition: personal-account install AFTER S02 deploy). Enter repo_name = m006-acceptance-personal-<timestamp>, blank description, Private. Click Create. Expect success within ~2s, dialog closes, next step shown with new repo URL prefilled. Capture all four evidence artifacts.

## Inputs

- `T01's preflight green`
- `Personal GitHub account with App installed AFTER S02 deploy`

## Expected Output

- `Screenshot of success state`
- `Orchestrator log excerpt showing token_class=user_token line with correct installation_id`
- `gh repo view confirms repo exists on GitHub`
- `DB query confirms github_user_oauth_tokens row updated within last 10 minutes`
- `SUMMARY appended with Scenario 1 evidence subsection`

## Verification

gh repo view <personal>/m006-acceptance-personal-<ts> && grep -q token_class=user_token .gsd/milestones/M006-ydo2ce/evidence/scenario1-orchestrator.log
