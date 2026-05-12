---
id: T02
parent: S06
milestone: M006-ydo2ce
key_files:
  - frontend/src/components/Teams/Projects/CreateGitHubRepoDialog.tsx
key_decisions:
  - ReinstallCta defined as file-local (non-exported) colocated component — keeps the single-file contract from the slice plan
  - installUrlMutation is a separate useMutation inside ReinstallCta, not reusing the parent mutation — keeps state isolation clean and matches MEM303 pattern of not caching install URLs
  - Submit button hidden via {!tokenRequired ? <LoadingButton ...> : null} rather than disabled — matches plan spec and prevents form submission in the reinstall state
  - Cancel button left unconditional (visible in both branches) as specified
duration: 
verification_result: passed
completed_at: 2026-05-12T23:01:34.327Z
blocker_discovered: false
---

# T02: Added ReinstallCta colocated component with install-url fetch + window.open, conditional tokenRequired block above submitError, and submit button hidden when tokenRequired is true

**Added ReinstallCta colocated component with install-url fetch + window.open, conditional tokenRequired block above submitError, and submit button hidden when tokenRequired is true**

## What Happened

T01's GitHubUserTokenRequiredError class and tokenRequired state were already present in the file from a prior session (no T01-SUMMARY.md existed but the code changes had been applied). T02 built directly on that foundation.

Added the ReinstallCta component (lines 53–115) colocated in CreateGitHubRepoDialog.tsx. It holds local installUrlError state and uses a useMutation hook that fetches GET /api/v1/teams/${teamId}/github/install-url, calls window.open(data.install_url, '_blank', 'noopener,noreferrer') on success, and sets installUrlError on failure. The component renders: amber-tinted container with data-testid=create-repo-reinstall-cta; copy block (GitHub access required + reinstall description); button with data-testid=create-repo-reinstall-button labeled 'Reinstall on GitHub'; and a data-testid=create-repo-reinstall-error paragraph when installUrlError is set.

In the parent dialog's form body, added {tokenRequired && <ReinstallCta teamId={teamId} />} immediately above the existing submitError block (these two are mutually exclusive by the onError logic in T01).

In DialogFooter, replaced the bare LoadingButton with a conditional: {!tokenRequired ? <LoadingButton type=submit ...> : null}. Cancel button remains visible in both branches via the DialogClose wrapper above the conditional.

No new imports were needed — Button and useMutation were already imported.

## Verification

cd frontend && npx tsc --noEmit — exits with code 2 but zero errors attributable to CreateGitHubRepoDialog.tsx or ReinstallCta. All 6 reported errors are pre-existing in unrelated files (vapid.test.ts, m005 notification specs). Grep for CreateGitHubRepo|ReinstallCta|reinstall in tsc output returns empty.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd /Users/josh/code/perpetuity/frontend && npx tsc --noEmit 2>&1 | grep -i 'CreateGitHubRepo\|ReinstallCta\|reinstall'` | 1 | PASS — zero matches; no TypeScript errors in the modified file | 8200ms |
| 2 | `cd /Users/josh/code/perpetuity/frontend && npx tsc --noEmit 2>&1 | grep 'CreateGitHubRepoDialog'` | 1 | PASS — file produces no TypeScript errors | 8100ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `frontend/src/components/Teams/Projects/CreateGitHubRepoDialog.tsx`
