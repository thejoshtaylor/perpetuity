---
id: T04
parent: S06
milestone: M004-guylpp
key_files:
  - frontend/src/components/ui/radio-group.tsx
  - frontend/src/components/Teams/Projects/ProjectsList.tsx
  - frontend/src/components/Teams/Projects/CreateProjectDialog.tsx
  - frontend/src/components/Teams/Projects/OpenProjectButton.tsx
  - frontend/src/components/Teams/Projects/PushRuleForm.tsx
  - frontend/src/routes/_layout/teams_.$teamId.tsx
key_decisions:
  - Stored a `flat list of testids` matching the plan's contract verbatim — the T05 Playwright spec is the consumer and must not need to reverse-engineer naming. The badge testid `push-rule-stored-badge` only renders for mode=rule and mode=manual_workflow, so the spec's M005-deferred-executor assertion can be a single getByTestId without per-mode branching.
  - Coerced installation_id from Select string→Number at submit boundary rather than typing the form field as number. Reason: shadcn Select's onValueChange only emits string. Pattern mirrors S06/T03's MEM303 (probe queries return strings everywhere).
  - Did NOT plumb a separate '?expand=push-rule' fetch on the project list; PushRuleForm fetches its own rule lazily on first open. Reason: the list endpoint doesn't expose push_rule today, and a per-row preflight would multiply round-trips when most users only configure a few rules.
  - Used setQueryData + invalidateQueries on PUT push-rule success (not just invalidate). Immediate re-anchor avoids a flash of stale state in the form's useEffect re-sync; sibling invalidate keeps the projects list fresh in case auto↔non-auto transitions affect downstream UI later.
duration: 
verification_result: mixed
completed_at: 2026-04-28T03:33:41.233Z
blocker_discovered: false
---

# T04: Add team Projects section: list, create-project dialog, open button with orchestrator-reason-aware error toasts, push-rule form for all three modes, plus shadcn radio-group primitive.

**Add team Projects section: list, create-project dialog, open button with orchestrator-reason-aware error toasts, push-rule form for all three modes, plus shadcn radio-group primitive.**

## What Happened

Built the team-admin Projects experience under `frontend/src/components/Teams/Projects/` and wired it into the team detail route at `frontend/src/routes/_layout/teams_.$teamId.tsx`. Five new files + one route edit, exactly matching the task plan's expected output set.

**ProjectsList.tsx** — fetches `GET /api/v1/teams/{teamId}/projects` and `GET /api/v1/teams/{teamId}/installations` (the latter feeds the create dialog's installation Select). Each row renders the project name, repo full-name, a `last_push_status` badge (variant: ok→default, failed→destructive, anything-else→outline with `no pushes` fallback), and `created_at`. The `last_push_error` is hung off the badge's `title` so an operator can hover to see the persisted failure detail without DevTools — the FE half of S04's MEM278 redaction pipeline. Empty state renders an inline `Create your first project` CTA. Member-view (`callerIsAdmin=false`) shows the list but no Open / push-rule / create controls (Q7 negative test).

**CreateProjectDialog.tsx** — react-hook-form + zod with the three-field validation set: name (trim, min-1, max-255), `github_repo_full_name` (must contain `/` and split into exactly two non-empty parts), `installation_id` (string in form state, coerced to Number on submit because the Select primitive is string-typed but the API takes int). Q5 failure modes wired: `409 project_name_taken` sets an inline `form.setError('name', …)` (per the plan's "surface inline form error rather than toast"); `404 installation_not_in_team` invalidates the installations cache (refresh-on-race) and surfaces inline; everything else falls through to a generic submitError pane.

**OpenProjectButton.tsx** — calls `POST /api/v1/projects/{id}/open` via `LoadingButton` so the spinner is visible immediately on click (Q6 load profile: 2-10s wall-clock chain). On error, `extractOpenError` reads both `body.detail` AND `body.reason` from the ApiError response and renders `${detail} (reason: ${reason})` in the toast description, surfacing the orchestrator chain's discriminator (`github_clone_failed`, `user_clone_failed` with `reason=user_clone_exit_<code>`, `clone_credential_leak`) verbatim — closes the S04 operator UX gap. `503 orchestrator_unavailable` gets friendlier copy ("Orchestrator is unreachable — please try again in a moment").

**PushRuleForm.tsx** — three-radio form using the new shadcn `radio-group` primitive. GET /push-rule on mount populates a `useEffect` that re-anchors local form state to the persisted rule (so closing+reopening the form doesn't lose the saved mode). Each mode renders mode-specific inputs (rule→branch_pattern, manual_workflow→workflow_id) and the rule + manual_workflow modes display the `Stored — executor lands in M005` Badge so operators are not misled into expecting the rule to fire today (D024 schema-now/executors-deferred contract surfaced to the UI). Submit guards: non-empty-after-trim for branch_pattern (mode=rule) and workflow_id (mode=manual_workflow). On success, both setQueryData (immediate re-anchor) and invalidateQueries on `['project', projectId]`. 404 push_rule_not_found refreshes the rule cache.

**radio-group.tsx** — new shadcn primitive at `frontend/src/components/ui/radio-group.tsx`. Wraps `@radix-ui/react-radio-group` (already in package.json — no new dep). Class shape mirrors checkbox.tsx for visual consistency (size-4 rounded-full, primary indicator, focus-visible ring, aria-invalid handling). Exports RadioGroup + RadioGroupItem.

**Route wiring** — added Projects section to `teams_.$teamId.tsx` after the Mirror section. The section renders for both admins and members (members see the list, just without the create/open/push-rule controls).

Captured two memories during execution: MEM304 (pattern: how to surface orchestrator-chain `{detail, reason}` discriminators into toast UI) and MEM305 (convention: project-scoped React Query cache key shape).

## Verification

Ran `cd frontend && bun run build` (tsc -p tsconfig.build.json + vite build) — exits 0, builds in 1.92s, all 2276 modules transformed. Ran `cd frontend && bun run lint` (biome check --write --unsafe) — exits 0; first pass auto-formatted whitespace in the 4 new files (semantics preserved), second pass clean. Ran `grep -E 'push-rule-stored-badge|project-open-button' frontend/src/components/Teams/Projects/` — matches in both PushRuleForm.tsx and OpenProjectButton.tsx (verification step 4).

The slice plan's verification step 3 (manual smoke against live compose stack) is deferred to T05's Playwright spec — T04 is a code-only task; T05 is the live-stack verification driver per the slice goal.

All `data-testid` selectors from the plan are present in the code (verified via Grep): projects-section, create-project-button, create-project-name-input, create-project-repo-input, create-project-installation-select, create-project-submit, project-row-<id>, project-open-button-<id>, push-rule-button-<id>, push-rule-mode-auto, push-rule-mode-rule, push-rule-mode-manual_workflow, push-rule-branch-pattern-input, push-rule-workflow-id-input, push-rule-submit, push-rule-stored-badge.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd frontend && bun run build` | 0 | ✅ pass | 1920ms |
| 2 | `cd frontend && bun run lint` | 0 | ✅ pass | 48ms |
| 3 | `grep -E 'push-rule-stored-badge|project-open-button' frontend/src/components/Teams/Projects/` | 0 | ✅ pass | 50ms |
| 4 | `Manual smoke against live compose stack: deferred to T05 (Playwright e2e drives the full admin-side flow per the slice goal)` | -1 | unknown (coerced from string) | 0ms |

## Deviations

None.

## Known Issues

None.

## Files Created/Modified

- `frontend/src/components/ui/radio-group.tsx`
- `frontend/src/components/Teams/Projects/ProjectsList.tsx`
- `frontend/src/components/Teams/Projects/CreateProjectDialog.tsx`
- `frontend/src/components/Teams/Projects/OpenProjectButton.tsx`
- `frontend/src/components/Teams/Projects/PushRuleForm.tsx`
- `frontend/src/routes/_layout/teams_.$teamId.tsx`
