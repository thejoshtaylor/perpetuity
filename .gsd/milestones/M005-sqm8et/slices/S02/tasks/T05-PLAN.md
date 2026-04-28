---
estimated_steps: 6
estimated_files: 10
skills_used: []
---

# T05: Dashboard direct AI buttons + run-detail polled page

Frontend changes that make the dashboard demo-truth statement pass. Two surfaces: the team dashboard (extends the existing `teams_.$teamId.tsx` route which already hosts the S01 secrets panel — that route IS the team dashboard for S02's purposes) and a new `/runs/$runId` route.

(1) `frontend/src/components/dashboard/DirectAIButtons.tsx`: renders two buttons ('Run Claude', 'Run Codex'). On click, opens a `PromptDialog` modal with a Textarea for the prompt and Submit / Cancel. On Submit: looks up the `_direct_claude` (or `_direct_codex`) workflow id via `GET /api/v1/teams/{team_id}/workflows` (added in T04). Posts to `POST /api/v1/workflows/{id}/run` with `{trigger_payload: {prompt}}`. On success: navigates to `/runs/{run_id}`.

(2) `frontend/src/routes/_layout/runs_.$runId.tsx`: new TanStack route. `useQuery` against `GET /api/v1/workflow_runs/{run_id}` with `refetchInterval: 1500` while run.status is 'pending' or 'running'; stops polling when terminal. Renders run header (workflow name, status pill, started/finished timestamps, duration), then ordered step list each with status pill, action label, exit_code, error_class (if any), and a collapsed-by-default `<details>` block with stdout content. When the step transitions to running, show a small spinner; when failed, show the error_class prominently. Empty stdout shows a muted 'no output' note.

(3) Wire `DirectAIButtons` into `frontend/src/routes/_layout/teams_.$teamId.tsx` above the existing TeamSecretsPanel. Only render for users who have at least 'member' role on the team.

(4) Frontend SDK regen via existing tooling so generated types pick up the new endpoints (`frontend/src/client/sdk.gen.ts`, `frontend/src/client/types.gen.ts`, `frontend/openapi.json`).

(5) Playwright tests: dashboard renders both buttons for member; clicking 'Run Claude' opens modal; submitting routes to `/runs/<uuid>`; run page polls and reflects status transitions (T05's Playwright uses Playwright's `page.route()` to mock the trigger and run-detail endpoints and step the response shape through pending → running → succeeded — full end-to-end Celery integration is covered in T06).

## Inputs

- ``frontend/src/components/team/TeamSecretsPanel.tsx``
- ``frontend/src/routes/_layout/teams_.$teamId.tsx``
- ``frontend/src/api/teamSecrets.ts``
- ``frontend/src/components/team/PasteSecretDialog.tsx``
- ``backend/app/api/routes/workflows.py``

## Expected Output

- ``frontend/src/components/dashboard/DirectAIButtons.tsx``
- ``frontend/src/components/dashboard/PromptDialog.tsx``
- ``frontend/src/routes/_layout/runs_.$runId.tsx``
- ``frontend/src/routes/_layout/teams_.$teamId.tsx``
- ``frontend/src/api/workflows.ts``
- ``frontend/src/client/sdk.gen.ts``
- ``frontend/src/client/types.gen.ts``
- ``frontend/openapi.json``
- ``frontend/tests/components/DirectAIButtons.spec.ts``
- ``frontend/tests/components/RunDetailPage.spec.ts``

## Verification

cd frontend && npm run build && npx playwright test tests/components/DirectAIButtons.spec.ts tests/components/RunDetailPage.spec.ts
