---
estimated_steps: 1
estimated_files: 2
skills_used: []
---

# T04: Write runbook `docs/runbooks/m006-github-oauth-setup.md` + cross-reference from m004

The milestone is not deployable without an operator changing the GitHub App config; the runbook is how that knowledge persists. Write the runbook with sections Why / What to change / How to verify / Rollback / When this changes per must-have (9). Reference specific GitHub App settings page navigation. Include verification SQL query verbatim. Cross-reference m004 runbook. Add one-liner to m004.

## Inputs

- `docs/runbooks/m004-secrets-rotation.md (template)`

## Expected Output

- `docs/runbooks/m006-github-oauth-setup.md at least 30 lines with five named sections (Why, What to change, How to verify, Rollback, When this changes)`
- `docs/runbooks/m004-secrets-rotation.md gains one new line cross-referencing m006-github-oauth-setup.md`

## Verification

test -f docs/runbooks/m006-github-oauth-setup.md && [ $(wc -l < docs/runbooks/m006-github-oauth-setup.md) -ge 30 ] && grep -q m006-github-oauth-setup docs/runbooks/m004-secrets-rotation.md
