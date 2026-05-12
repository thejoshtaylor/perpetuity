---
estimated_steps: 1
estimated_files: 1
skills_used: []
---

# T01: `GitHubUserTokenRequiredError` + mutation error parsing + `tokenRequired` state

Separating the 409 path from the existing generic error path is the foundation for all the UX work; do it once with a typed error class so the JSX branch is a clean if (tokenRequired) ... else if (submitError) .... Define class GitHubUserTokenRequiredError extends Error with installationId and reason. In mutationFn, branch on (status===409 && body.detail===github_user_token_required) and throw typed error; on (status===502 && body.detail===github_token_refresh_transient) throw new Error('GitHub had a temporary problem. Try again in a moment.'); on (status===503 && body.detail===github_user_token_decrypt_failed) throw new Error('A configuration error prevented repo creation. The operator has been notified.'). Add tokenRequired state. In onError, branch on instanceof.

## Inputs

- `S04's 409/502/503 response shapes`
- `Existing CreateGitHubRepoDialog.tsx structure`

## Expected Output

- `GitHubUserTokenRequiredError typed error class exported or file-local`
- `Three new branches in mutationFn for 409/502/503`
- `tokenRequired state initialized and reset on dialog close`
- `console.warn line emitted on 409 with installationId and reason`

## Verification

cd frontend && npx tsc --noEmit && npm test -- CreateGitHubRepoDialog
