---
estimated_steps: 1
estimated_files: 1
skills_used: []
---

# T02: Reinstall CTA JSX + install-url fetch + `window.open`

This is the user-visible substance of the slice. Above the existing submitError JSX (:228-236), add conditional block {tokenRequired && (<ReinstallCta ...>)}. Define ReinstallCta as a colocated component. CTA renders the copy block + button with data-testid=create-repo-reinstall-cta. Button's onClick uses a React Query mutation that fetches /api/v1/teams/${teamId}/github/install-url and on success calls window.open(data.install_url, _blank, noopener,noreferrer). On fetch failure, sets a local installUrlError state shown below the button. In parent dialog's footer JSX, replace existing LoadingButton type=submit with conditional: {!tokenRequired ? <LoadingButton type=submit ...> : null}. Cancel button visible in both branches.

## Inputs

- `T01's tokenRequired state`
- `Existing /install-url endpoint at backend/app/api/routes/github.py:470-518`

## Expected Output

- `ReinstallCta component renders inline error + button with data-testid=create-repo-reinstall-cta`
- `Submit button hidden when tokenRequired !== null`
- `window.open called with (url, _blank, noopener,noreferrer)`
- `Fetch-failure path sets installUrlError state without closing dialog`

## Verification

cd frontend && npx tsc --noEmit
