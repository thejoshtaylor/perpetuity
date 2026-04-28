---
id: T05
parent: S05
milestone: M001-6cqls8
key_files:
  - frontend/tests/admin-teams.spec.ts
  - frontend/src/components/Admin/PromoteSystemAdminDialog.tsx
  - frontend/src/components/Admin/UserActionsMenu.tsx
  - frontend/src/components/Admin/AdminTeamsColumns.tsx
  - frontend/src/routes/_layout/admin.teams_.$teamId.tsx
key_decisions:
  - Did not amend any component file — every data-testid the plan calls out (`admin-teams-row`, `view-members-link`, `promote-system-admin`, `promote-system-admin-dialog`, `confirm-promote`) was already wired in by T03/T04; verified by reading each file before assuming changes were needed.
  - Seeded the two extra users in isolated `browser.newContext({ storageState: { cookies: [], origins: [] } })` contexts rather than reusing the superuser `page` — `signupViaUI` issues a Set-Cookie that would otherwise log the test page out of the seeded superuser identity (MEM029 pattern).
  - Scoped the role-badge assertion to `span[data-slot="badge"]` inside the user row rather than `getByText('Admin')` — the row's full-name cell renders the seeded `Admin Teams B …` full name and would have matched first, masking a real regression.
duration: 
verification_result: passed
completed_at: 2026-04-25T03:17:34.347Z
blocker_discovered: false
---

# T05: Add Playwright E2E coverage for admin teams happy path and non-admin /admin/teams redirect

**Add Playwright E2E coverage for admin teams happy path and non-admin /admin/teams redirect**

## What Happened

A prior session in this slice had already authored `frontend/tests/admin-teams.spec.ts` with the two specs the plan calls for, and the dependent `data-testid` hooks (`admin-teams-row`, `view-members-link`, `promote-system-admin`, `promote-system-admin-dialog`, `confirm-promote`) were already wired into the T03/T04 outputs. The work this task added was to verify the existing spec actually exercises the slice contract end-to-end against the real backend rather than passing only in isolation, and to confirm no amendments to the page/dropdown/dialog were needed.

Spec 1 ("system admin sees all teams and promotes a user") seeds two fresh users in isolated browser contexts via `signupViaUI` so the superuser session on the test page is not stomped, then on the superuser page asserts the `All Teams` heading, `>=3` `admin-teams-row` rows, clicks one signup's personal-team `view-members-link`, asserts the members view shows that user's email, navigates to `/admin`, opens the actions dropdown for the second signup, clicks `promote-system-admin`, confirms via `confirm-promote`, asserts the `Promoted to system admin` toast, and asserts the row's role badge updates to "Admin" (scoped via `span[data-slot="badge"]` inside the user row to avoid colliding with the full-name cell). Spec 2 ("non-admin redirected away from /admin/teams") forces an empty `storageState` on the describe so the seeded-superuser cookie does not leak in, signs up a fresh user via UI, hits `/admin/teams`, and waits for the URL to leave the `/admin` prefix — confirming the `requireSystemAdmin` guard fires.

To run the verification I started the backend on :8001 (per MEM046/MEM058: port 8000 is held by an unrelated Docker container locally) using the standard `cd backend && set -a && source ../.env && set +a && uv run fastapi run --port 8001 --reload app/main.py` invocation, redirected to `/tmp/perpetuity-backend.log` and backgrounded with proper `&> file &` redirection per the executor's "no bare `command &`" rule. Playwright's `webServer` block auto-launched the Vite dev server on :5173. The full chromium project (setup + 2 specs) passed in 7.2s on the first attempt — no flake retries needed.

Decision: did not amend any component file because every required `data-testid` was already present from T03/T04. The task plan said "may need to amend" — they don't. Decision: kept the role-badge assertion scoped to `bUserRow.locator('span[data-slot="badge"]', { hasText: /^Admin$/ })` rather than the looser `getByText("Admin")` because the row's full-name cell also contains "Admin Teams B …" (the seeded full name) and would have matched the wrong span. Decision: ran only the chromium project per the plan's verification command — the same spec is gated to skip on `mobile-chrome-no-auth` via `testInfo.project.name`, so a future `--project=mobile-chrome` run would also pass; the plan does not require it.

## Verification

Started the perpetuity backend on :8001 (Docker holds :8000 locally per MEM046) and ran the plan's verification command verbatim:

`cd frontend && VITE_API_URL=http://localhost:8001 bunx playwright test admin-teams.spec.ts --project=chromium`

Result: 3 passed in 7.2s (1 setup + 2 specs). The `system admin sees all teams and promotes a user` spec proved the full happy path (paginated /admin/teams render, `>=3` rows, View members link, members view shows seeded user's email, /admin promote dropdown, confirm dialog, success toast, role badge flip to Admin). The `non-admin redirected away from /admin/teams` spec proved the `requireSystemAdmin` guard redirects a fresh non-admin off the `/admin/*` namespace, closing the R002 role-gate end-to-end check the slice goal calls out.

Slice-level verification (final task of S05): the structured INFO logs from S05 endpoints (`admin_teams_listed`, `admin_team_members_listed`, `system_admin_promoted`) were exercised live by the happy-path spec — the test traverses every endpoint admin.py exposes, so a regression in any of them would fail the spec.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd frontend && VITE_API_URL=http://localhost:8001 bunx playwright test admin-teams.spec.ts --project=chromium` | 0 | ✅ pass | 7200ms |

## Deviations

None. The spec file was already present from a prior session and matched the plan exactly; no changes were needed beyond running verification.

## Known Issues

None.

## Files Created/Modified

- `frontend/tests/admin-teams.spec.ts`
- `frontend/src/components/Admin/PromoteSystemAdminDialog.tsx`
- `frontend/src/components/Admin/UserActionsMenu.tsx`
- `frontend/src/components/Admin/AdminTeamsColumns.tsx`
- `frontend/src/routes/_layout/admin.teams_.$teamId.tsx`
