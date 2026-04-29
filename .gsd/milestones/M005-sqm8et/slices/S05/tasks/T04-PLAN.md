---
estimated_steps: 8
estimated_files: 1
skills_used: []
---

# T04: Frontend run history list page (/runs) with filters

Create frontend/src/routes/_layout/runs.tsx — the team run history list page. It fetches from GET /api/v1/teams/{teamId}/runs (added in T01), renders a table of runs with columns: run ID (truncated), workflow name (from snapshot, not live FK), trigger type badge, status badge, created_at relative timestamp, duration. Filter controls above the table: status multi-select (pending/running/succeeded/failed/cancelled/rejected), trigger_type multi-select, after/before date inputs. Each row links to the existing /runs/{runId} drilldown page. Pagination with limit=50 and offset-based 'Load more' button.

Use existing UI patterns from runs_.$runId.tsx and workflows.tsx for TanStack Query fetching, badge styling, and status color conventions. No new UI components needed — compose from existing ones.

Why/Files/Do/Verify/Done-when:
- Why: R018 mandates run history UI with drilldown. The drilldown exists; only the list view is missing.
- Files: frontend/src/routes/_layout/runs.tsx
- Do: Create the route file. Add a nav link to /runs in the sidebar/nav component (check existing nav for pattern). Add TanStack Router route registration if needed (check routes.ts or equivalent). Add API query function for the runs list endpoint. Implement filter state with URL search params (status, trigger_type, after, before) so filter state survives navigation. TypeScript build must be 0 errors.
- Verify: cd frontend && npm run build 2>&1 | tail -5 (0 errors, 0 type errors)
- Done when: TypeScript build passes; route is reachable; table renders with correct columns; filter params round-trip via URL.

## Inputs

- `frontend/src/routes/_layout/runs_.$runId.tsx`
- `frontend/src/routes/_layout/workflows.tsx`
- `frontend/src/routes/_layout/index.tsx`

## Expected Output

- `frontend/src/routes/_layout/runs.tsx`

## Verification

cd frontend && npm run build 2>&1 | tail -5
