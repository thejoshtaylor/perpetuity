---
id: T04
parent: S06
milestone: M006-ydo2ce
key_files:
  - docs/runbooks/m006-github-oauth-setup.md
  - docs/runbooks/m004-secrets-rotation.md
key_decisions:
  - Runbook grounded in actual setting keys (github_app_client_id, github_app_client_secret, github_app_slug) and route behavior rather than generic GitHub App guidance
  - Verification SQL matches the real system_settings schema (has_value, sensitive columns)
  - Rollback section calls out S17 migration downgrade as destructive (data loss) so operators do not run it casually
  - Cross-reference appended as a new 'Related Runbooks' section at the end of m004 rather than inline — preserves m004's existing structure
duration: 
verification_result: passed
completed_at: 2026-05-12T23:50:16.086Z
blocker_discovered: false
---

# T04: Wrote docs/runbooks/m006-github-oauth-setup.md (197 lines, five named sections) and added cross-reference to m004-secrets-rotation.md

**Wrote docs/runbooks/m006-github-oauth-setup.md (197 lines, five named sections) and added cross-reference to m004-secrets-rotation.md**

## What Happened

Read docs/runbooks/m004-secrets-rotation.md as the style template, then examined the M006 context doc, backend route github.py (install-url endpoint, 409 error shape), admin.py (setting keys: github_app_client_id, github_app_client_secret, github_app_slug), and the S17 migration schema to ground the runbook in real implementation details.

Wrote docs/runbooks/m006-github-oauth-setup.md with five required sections:
- **Why**: explains the personal-install OAuth requirement and what breaks without it (409 github_user_token_required, mis-configured reinstall CTA)
- **What to Change**: five concrete sub-steps — enable OAuth on the App, add repo scope, copy client credentials from GitHub, seed four admin settings (with table of key names), note no restart required
- **How to Verify**: SQL query verifying the three settings rows are present with has_value=true; curl showing the install-url endpoint returning 200; SQL confirming the github_user_oauth_tokens table exists from S17 migration; smoke-test walkthrough with expected log lines
- **Rollback**: three scenarios — scope downgrade, client secret revocation, S17 migration downgrade with data-loss warning
- **When This Changes**: table of triggers (slug rename, client secret rotation, callback URL change, scope downgrade, user off-boarding)

Added a "Related Runbooks" section at the end of m004-secrets-rotation.md with a one-liner cross-reference to m006-github-oauth-setup.md covering OAuth client secret rotation context.

## Verification

Ran: test -f docs/runbooks/m006-github-oauth-setup.md && wc -l < docs/runbooks/m006-github-oauth-setup.md && grep -q m006-github-oauth-setup docs/runbooks/m004-secrets-rotation.md. File exists (197 lines, ≥30 required), cross-reference present in m004.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `test -f docs/runbooks/m006-github-oauth-setup.md && wc -l < docs/runbooks/m006-github-oauth-setup.md && grep -q m006-github-oauth-setup docs/runbooks/m004-secrets-rotation.md && echo CROSS-REF OK` | 0 | pass | 180ms |

## Deviations

none

## Known Issues

none

## Files Created/Modified

- `docs/runbooks/m006-github-oauth-setup.md`
- `docs/runbooks/m004-secrets-rotation.md`
