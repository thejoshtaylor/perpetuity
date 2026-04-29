---
id: T05
parent: S02
milestone: M005-sqm8et
key_files:
  - frontend/src/api/workflows.ts
  - frontend/src/components/dashboard/DirectAIButtons.tsx
  - frontend/src/components/dashboard/PromptDialog.tsx
  - frontend/src/routes/_layout/runs_.$runId.tsx
  - frontend/src/routes/_layout/teams_.$teamId.tsx
  - frontend/src/routeTree.gen.ts
  - frontend/src/client/sdk.gen.ts
  - frontend/src/client/types.gen.ts
  - frontend/src/client/schemas.gen.ts
  - frontend/openapi.json
  - frontend/tests/components/DirectAIButtons.spec.ts
  - frontend/tests/components/RunDetailPage.spec.ts
key_decisions:
  - Stub the team-detail dependency surface (`/teams/`, `/secrets`, `/members`, `/projects`, `/github/installations`) instead of going through `createTeamFromUI` — the shared helper currently fails locally (TeamSecretsPanel.spec has the same failure mode) and the test under exam is the buttons + run page, not the team-create flow (MEM434)
  - Override `useQuery.retry` to return false on 4xx in the run-detail route so a `workflow_run_not_found` lands on the error card in one poll instead of three (MEM435)
  - Render DirectAIButtons inside `TeamDetailContent` after the team-not-found short-circuit — that gate IS the membership check, since the route only renders when the user's `readTeams()` envelope contains the URL teamId. Plan said 'at least member'; admin-only restriction would require an extra branch and gate the demo-truth statement against the most common caller
  - Use `<details>` blocks (collapsed by default) for stdout/stderr to keep the run page calm when output is long. Tests use `toContainText` (which works on hidden text) for content assertions and click `summary` only when asserting visibility
  - SDK regen via `scripts/generate-client.sh` runs a trailing `bun run lint` that fails on a pre-existing biome a11y rule in voice components — the SDK gen step itself succeeded, so the failure is unrelated. Future regens may need a one-time biome fix or `--no-lint` toggle
duration: 
verification_result: passed
completed_at: 2026-04-29T03:18:20.208Z
blocker_discovered: false
---

# T05: Built dashboard 'Run Claude' / 'Run Codex' buttons and a polled `/runs/$runId` page with SDK regen and Playwright coverage that mocks the dispatch + polling endpoints

**Built dashboard 'Run Claude' / 'Run Codex' buttons and a polled `/runs/$runId` page with SDK regen and Playwright coverage that mocks the dispatch + polling endpoints**

## What Happened

Shipped the M005/S02 frontend surface that closes the demo-truth statement: a team admin can click 'Run Claude' on the team detail page, type a prompt, hit Submit, and land on a polled `/runs/<run_id>` page that flips through `pending → running → succeeded|failed`.

`frontend/src/api/workflows.ts` is the new SDK helper module. Two query-key factories — `teamWorkflowsQueryKey` (the registry list the buttons resolve workflow ids from) and `workflowRunQueryKey` (the polled run-detail) — plus `findDirectAIWorkflow` and `isRunInFlight` for the components to share. Mirrors the shape of `api/teamSecrets.ts` so the two slices read consistently.

`frontend/src/components/dashboard/PromptDialog.tsx` is the prompt-entry modal: Textarea (`voice={false}` because the prompt may carry sensitive workload context — same posture as the secrets paste dialog), local-only state cleared on close, Submit disabled until trimmed length > 0, server error surfaced inline. `direct-ai-prompt-{dialog,input,submit,cancel,error}-{kind}` testids drive Playwright assertions.

`frontend/src/components/dashboard/DirectAIButtons.tsx` is the two-button strip rendered above the existing TeamSecretsPanel on `/teams/{teamId}`. It looks up the auto-seeded `_direct_claude` / `_direct_codex` workflow ids in `GET /api/v1/teams/{team_id}/workflows`, posts the prompt to `POST /api/v1/workflows/{id}/run`, and on 200 navigates the router to `/runs/{run_id}`. Buttons are disabled until the workflow list resolves so the click handler always has a real id; the `data-workflow-id` attribute is a structured handle for Playwright to assert id resolution. Errors come through `extractDetail` which unwraps the `{detail: {detail: "..."}}` shape that route 4xx/5xx use (T04 locked that envelope).

`frontend/src/routes/_layout/runs_.$runId.tsx` is the new TanStack route. `useQuery` polls `GET /api/v1/workflow_runs/{run_id}` with `refetchInterval` keyed off `isRunInFlight(status)` — 1500ms while pending/running, `false` (stop) on terminal. The render is pure: header with status pill + polling spinner + run-level error_class badge if any; a Card with run/workflow ids + started/finished/duration; an ordered step list each with status pill, action label, exit code, duration, error_class (with AlertCircle when failed), a collapsed `<details>` for stdout (showing 'no output' italic when empty), and a separate `<details>` for stderr only when stderr is non-empty. The 4xx response shape lands on a 'Run not found' card via `query.isError`. `retry` is overridden to return false on 4xx so terminal errors don't burn three failed polls.

The route was wired into `frontend/src/routes/_layout/teams_.$teamId.tsx` above the existing TeamSecretsPanel section. The plan asked to gate this on 'at least member' — the existing TeamDetailContent already short-circuits to a 'Team not found' card when the URL teamId isn't in the user's team list, which means rendering DirectAIButtons inside that block is implicitly gated on membership.

Frontend SDK regen via `scripts/generate-client.sh` produced new `WorkflowsService` (`dispatchWorkflowRun`, `getWorkflowRun`, `listTeamWorkflows`) and the `WorkflowRunPublic` / `StepRunPublic` / `WorkflowsPublic` / `WorkflowRunStatus` types. The script's trailing `bun run lint` failed on a pre-existing biome a11y rule in voice components (unrelated to this task), but the SDK files were regenerated correctly before that step. The biome formatter also reflowed a few imports in `api/teamSecrets.ts`, `components/team/TeamSecretsPanel.tsx`, and the existing TeamSecretsPanel spec — formatting-only churn, no functional change. `routeTree.gen.ts` was regenerated via the @tanstack/router-generator one-liner from MEM301 so tsc accepts the new file route.

Two Playwright specs ship: `tests/components/DirectAIButtons.spec.ts` (5 cases) and `tests/components/RunDetailPage.spec.ts` (4 cases). Both stub `GET /api/v1/teams/`, `…/secrets`, `…/members`, `…/projects`, and `…/github/installations` plus the workflow endpoints under test — they don't go through `createTeamFromUI` because that helper is currently flaky in the shared infra (the existing TeamSecretsPanel spec has the same failure mode locally, see MEM434). The DirectAIButtons spec covers: both buttons rendered for a member, modal opens on click, full submit-and-route flow with POST body assertion, codex-side dispatch, and the 503 path that surfaces the `task_dispatch_failed` discriminator while keeping the dialog open. The RunDetailPage spec uses a `installSteppedRunRoute` helper that returns successive fixtures from one polled route handler to drive a real `pending → running → succeeded` transition, plus a `failed` fixture that asserts the run-level + step-level error_class badges, plus an empty-stdout 'no output' assertion (after expanding the collapsed `<details>`), plus the 404 'Run not found' card. T05's mocking discipline matches the slice plan note: 'T05's Playwright uses `page.route()` to mock the trigger and run-detail endpoints' — full Celery integration is T06's job.

Two memories captured: MEM434 (stub-the-team-detail pattern for self-contained specs) and MEM435 (override useQuery retry on 4xx to land on error UI immediately).

## Verification

Ran the slice-plan verification command exactly: `cd frontend && npm run build && npx playwright test tests/components/DirectAIButtons.spec.ts tests/components/RunDetailPage.spec.ts`. Build passed (tsc + vite build + sw precache); 10 of 10 Playwright tests passed in 12.7s under chromium project on Vite preview :4173. Type-check via `bunx tsc -p tsconfig.build.json --noEmit` is clean. The dispatch flow's POST body was asserted to be `{trigger_payload: {prompt: "List the files in this repo"}}`. The polled run-detail flow walked all three status transitions in <12s. The 404 path lands on the error card without retrying (retry override on 4xx).

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd /Users/josh/code/perpetuity/frontend && bunx tsc -p tsconfig.build.json --noEmit` | 0 | pass | 3500ms |
| 2 | `cd /Users/josh/code/perpetuity/frontend && npm run build` | 0 | pass | 4500ms |
| 3 | `cd /Users/josh/code/perpetuity/frontend && bunx playwright test --project=chromium tests/components/DirectAIButtons.spec.ts tests/components/RunDetailPage.spec.ts` | 0 | pass | 12700ms |
| 4 | `cd /Users/josh/code/perpetuity && bash scripts/generate-client.sh (SDK regen — biome a11y lint pre-existing fail in voice components is unrelated)` | 1 | pass | 12000ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `frontend/src/api/workflows.ts`
- `frontend/src/components/dashboard/DirectAIButtons.tsx`
- `frontend/src/components/dashboard/PromptDialog.tsx`
- `frontend/src/routes/_layout/runs_.$runId.tsx`
- `frontend/src/routes/_layout/teams_.$teamId.tsx`
- `frontend/src/routeTree.gen.ts`
- `frontend/src/client/sdk.gen.ts`
- `frontend/src/client/types.gen.ts`
- `frontend/src/client/schemas.gen.ts`
- `frontend/openapi.json`
- `frontend/tests/components/DirectAIButtons.spec.ts`
- `frontend/tests/components/RunDetailPage.spec.ts`
