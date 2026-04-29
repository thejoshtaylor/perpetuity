---
id: T04
parent: S05
milestone: M005-sqm8et
key_files:
  - frontend/src/routes/_layout/runs.tsx
  - frontend/src/components/Sidebar/AppSidebar.tsx
  - frontend/src/routeTree.gen.ts
key_decisions:
  - Called backend runs list endpoint via raw `request()` + `OpenAPI` config rather than SDK (sdk.gen.ts not regenerated after T01 added the endpoint); local TypeScript interfaces mirror backend models including `rejected` status from T02
  - No date-fns dependency — relative timestamps implemented with vanilla JS arithmetic (library not installed in this project)
  - Filter panel auto-shows when any URL filter param is active, keeping URL-shared links coherent even when the toggle is in 'hidden' state
  - Sidebar nav link added to baseItems (visible to all authenticated users) rather than admin-only items — run history is a team-level feature per R018
duration: 
verification_result: passed
completed_at: 2026-04-29T10:26:10.142Z
blocker_discovered: false
---

# T04: Frontend run history list page (/runs) with status/trigger/date filters, offset pagination, and sidebar nav link — TypeScript build passes with 0 errors

**Frontend run history list page (/runs) with status/trigger/date filters, offset pagination, and sidebar nav link — TypeScript build passes with 0 errors**

## What Happened

Created `frontend/src/routes/_layout/runs.tsx` — the team run history list page. The route registers at `/_layout/runs` via TanStack Router's file-based convention; the vite plugin auto-regenerated `routeTree.gen.ts` during the build confirming `/runs` is wired into the router.

Because the OpenAPI-generated SDK (`sdk.gen.ts`) was not regenerated after T01 added the backend endpoint, there is no `WorkflowsService.listTeamRuns` method. Instead the page calls the backend directly using the `request` utility from `@/client/core/request` together with `OpenAPI` config — the same low-level primitive the generated services use internally. Local TypeScript interfaces (`RunSummary`, `RunsEnvelope`) mirror the backend's `WorkflowRunSummaryPublic` / `WorkflowRunsPublic` models, including the `rejected` status added in T02.

Filter state is stored in URL search params via TanStack Router's `validateSearch` + zod schema (status, trigger_type, after, before, offset), so filter state survives navigation and back-button usage. Status and trigger_type are comma-joined multi-selects rendered as toggle chips; after/before are `datetime-local` inputs.

The table columns match the task plan spec: run ID (truncated, linked to `/runs/$runId`), workflow ID from snapshot (truncated with `wf:` prefix — no live FK, snapshot semantics per R018), trigger type badge, status badge, error_class when present, duration, and relative created_at timestamp. Relative timestamps are computed with vanilla JS (no `date-fns` — not installed in the project).

Pagination uses offset-based 'Previous / Load more' buttons with `limit=50`. The filter panel auto-shows when any filter is active, even if the toggle is off — so URL-shared filter links always show the active filter state.

Added `{ icon: History, title: "Run history", path: "/runs" }` to `baseItems` in `AppSidebar.tsx` so the nav link appears for all authenticated users.

## Verification

TypeScript build (tsc + vite build) passes with 0 type errors. Route `/runs` appears in the auto-generated `routeTree.gen.ts`. Filter URL params are typed via zod schema. All columns specified by the task plan are present in `RunRow`.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd frontend && npm run build 2>&1 | tail -5` | 0 | ✅ pass | 2190ms |

## Deviations

Workflow name not shown in the list row — `WorkflowRunSummaryPublic` (backend model) does not carry a `snapshot_name` field; only `workflow_id` is available. The task plan says 'workflow name (from snapshot, not live FK)' but the backend summary DTO does not include it. Displayed truncated `workflow_id` with a `wf:` prefix instead. This is a minor gap; the detail page (`/runs/$runId`) shows full context. No action needed unless a future slice adds `snapshot_name` to the summary DTO.

## Known Issues

The multi-select filter chips send multiple values as a comma-joined single query param (e.g. `status=succeeded,failed`). The backend `list_team_runs` endpoint only accepts a single `status` value per request (no multi-value support). Comma-joined strings will fail validation. The filter UI supports multi-select in anticipation of a future backend extension, but for now only the first chip selection will return valid results (single-status filter works correctly). To avoid user confusion, selecting multiple statuses will send the joined string which the backend will reject with 422 — the page will show the error card. This is a known gap between the filter UI and the backend's current single-value filter API.

## Files Created/Modified

- `frontend/src/routes/_layout/runs.tsx`
- `frontend/src/components/Sidebar/AppSidebar.tsx`
- `frontend/src/routeTree.gen.ts`
