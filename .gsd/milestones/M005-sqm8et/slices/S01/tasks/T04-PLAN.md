---
estimated_steps: 1
estimated_files: 4
skills_used: []
---

# T04: Frontend AI Credentials panel + paste-once modal

Add `frontend/src/components/team/TeamSecretsPanel.tsx` rendered inside the existing team settings route (`frontend/src/routes/_layout/teams_.$teamId.tsx` or whichever route holds team settings — inspect the M002+M004 layout to confirm). Panel fetches `GET /api/v1/teams/{team_id}/secrets` via React Query; displays both registered keys with a `has_value` badge ('Set' green / 'Not set' gray) plus `updated_at` timestamp when set. Team admin sees Replace + Delete buttons; non-admin sees read-only badges (use existing role-check hook from `frontend/src/hooks/useTeamRole.ts` or similar). Replace button opens a paste-once modal with a password-type input + show/hide toggle, validates non-empty client-side, submits via React Query mutation that calls PUT, on success invalidates the list query and closes the modal. Delete button confirms then issues DELETE. Add Playwright/Vitest component test covering: panel renders both keys, admin sees buttons, non-admin sees read-only, paste-once modal submits + closes + refreshes list.

## Inputs

- `Existing team settings route layout in `frontend/src/routes/_layout/``
- `Existing role-check hook (look for `useTeamRole` or similar)`
- `Existing modal component (look in `frontend/src/components/ui/`)`
- `T03's API contract`

## Expected Output

- `Team admin browsing the team page sees the AI Credentials panel above or below the GitHub connections panel`
- `Both keys render with correct has_value state on initial load`
- `Replace modal accepts a paste, submits, and the panel re-renders with has_value=true`
- `Delete button removes the row and the panel re-renders with has_value=false`
- `Non-admin sees read-only badges with no Replace/Delete buttons`

## Verification

cd frontend && npm test -- TeamSecretsPanel

## Observability Impact

No new log lines from frontend; relies on T03's backend logs.
