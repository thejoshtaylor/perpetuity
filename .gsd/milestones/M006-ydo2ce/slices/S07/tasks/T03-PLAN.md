---
estimated_steps: 1
estimated_files: 4
skills_used: []
---

# T03: Scenario 2 — Pre-M006 reinstall flow + evidence capture

Scenario 2 proves the CTA flow works for the migration-edge case (users who installed before this milestone deployed). This is the most-likely path for the first weeks after deploy. Execute steps in must-have (5): DELETE FROM github_user_oauth_tokens WHERE user_id = <operator-uuid> to simulate pre-M006 state. Open modal, attempt create. Expect inline error with reinstall CTA, submit button hidden, Cancel visible. Click Reinstall on GitHub. Complete install in new tab. Re-open modal, retry. Expect success. Capture CTA screenshot, post-retry success screenshot, then backend log excerpts.

## Inputs

- `T02 completed`
- `Same personal GitHub account`

## Expected Output

- `Screenshot of inline error + reinstall CTA`
- `Screenshot of post-reinstall success state`
- `Backend log line github_user_token_required user_id=<uuid> installation_id=<int> reason=row_missing from first attempt`
- `Backend log line github_user_token_persisted from install callback after reinstall`
- `Backend log line github_repository_created token_class=user_token from second attempt`
- `SUMMARY appended with Scenario 2 evidence subsection`

## Verification

grep -q "github_user_token_required.*reason=row_missing" .gsd/milestones/M006-ydo2ce/evidence/scenario2-backend.log && grep -q github_user_token_persisted .gsd/milestones/M006-ydo2ce/evidence/scenario2-backend.log
