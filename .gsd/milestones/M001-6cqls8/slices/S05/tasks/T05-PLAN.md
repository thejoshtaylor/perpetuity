---
estimated_steps: 1
estimated_files: 4
skills_used: []
---

# T05: Add Playwright E2E coverage for admin happy path + non-admin redirect

Add `frontend/tests/admin-teams.spec.ts` with two specs. (1) `'system admin sees all teams and promotes a user'`: log in as the seeded `firstSuperuser` (use the existing `auth.setup.ts`-derived storage state — admin is already authenticated for the chromium project). Sign up two new users via the existing `signupViaUI` helper from `tests/utils/teams.ts` so multiple non-personal teams exist. Then navigate as the superuser to `/admin/teams`, assert at least 3 team rows are visible (the two signups' personal teams plus the seeded admin's personal), assert the heading 'All Teams' is visible. Click into one of the new users' personal-team row, assert the members view shows that user's email. Navigate to `/admin`, find the second new user in the users table, open the actions menu, click 'Promote to system admin', confirm in the dialog, assert toast 'Promoted to system admin' is visible, and assert the user's role badge updates to indicate system admin (data-role attribute or text). (2) `'non-admin redirected away from /admin/teams'`: sign up a fresh user via UI in a clean context, navigate to `/admin/teams`, assert the URL ends up at `/` (root) — proves the `requireSystemAdmin` guard fires. Use the existing `mobile-chrome-no-auth` project ergonomics (skip irrelevant projects via `testInfo.project.name`). Selectors: prefer `getByTestId` — add `data-testid='admin-teams-row'` on each row, `data-testid='promote-system-admin'` on the dropdown item, `data-testid='confirm-promote'` on the dialog action button (update T03/T04 outputs accordingly if not already present — the executor of T05 may need to amend those files).

## Inputs

- ``frontend/tests/admin.spec.ts` — reference for admin-page test patterns`
- ``frontend/tests/teams.spec.ts` — reference for signupViaUI helper invocation and multi-user setup`
- ``frontend/tests/utils/teams.ts` — `signupViaUI`, `loginViaUI` helpers`
- ``frontend/tests/auth.setup.ts` — provides the seeded-superuser storage state for chromium project`
- ``frontend/tests/config.ts` — `firstSuperuser`/`firstSuperuserPassword``
- ``frontend/src/routes/_layout/admin.teams.tsx` — page-under-test (T03 output)`
- ``frontend/src/components/Admin/UserActionsMenu.tsx` — dropdown-under-test (T04 output)`

## Expected Output

- ``frontend/tests/admin-teams.spec.ts` — two Playwright specs covering happy path + 403 redirect`
- ``frontend/src/components/Admin/PromoteSystemAdminDialog.tsx` — amended to include `data-testid` hooks if missing`
- ``frontend/src/components/Admin/UserActionsMenu.tsx` — amended with `data-testid='promote-system-admin'` on the dropdown item`
- ``frontend/src/routes/_layout/admin.teams.tsx` — amended with `data-testid='admin-teams-row'` on rows`

## Verification

cd frontend && VITE_API_URL=http://localhost:8001 bunx playwright test admin-teams.spec.ts --project=chromium

## Observability Impact

Playwright traces serve as the failure-diagnosis surface for E2E regressions; failures land in `frontend/test-results/`. No new runtime signals.
