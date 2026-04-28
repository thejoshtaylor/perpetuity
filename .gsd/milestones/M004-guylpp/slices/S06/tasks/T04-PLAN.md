---
estimated_steps: 10
estimated_files: 6
skills_used: []
---

# T04: Projects list + create-project dialog + Open button + push-rule form (all three modes)

Extend `frontend/src/routes/_layout/teams_.$teamId.tsx` with a Projects section gated on team-admin role (members see the list but cannot create/configure).

**ProjectsList.tsx**: GET /api/v1/teams/{teamId}/projects → renders rows (name, github_repo_full_name, last_push_status badge, last action timestamp). Empty state CTA `Create your first project`. Admin-only `+ New Project` button opens `CreateProjectDialog`.

**CreateProjectDialog.tsx**: form with `name` (text) + `github_repo_full_name` (text, placeholder `owner/repo`) + `installation_id` (Select populated from the existing connections list — surface `account_login (account_type)` as label). Submit calls POST /api/v1/teams/{teamId}/projects, invalidates project list cache, success toast. Reject empty/whitespace-only name and repo client-side; reject repo without `/` separator.

**OpenProjectButton.tsx**: per-row button calling POST /api/v1/projects/{id}/open. Loading state during the chain (`mirror/ensure → materialize-mirror → materialize-user` can take seconds — show a spinner inside the button). On success, success toast `Project opened in your workspace`. On error, surface the backend's `{detail, reason}` body verbatim in the error toast (so operator sees `github_clone_failed` vs `user_clone_exit_<code>` vs `clone_credential_leak` discriminators from S04). Use the existing `loading-button.tsx` primitive.

**PushRuleForm.tsx**: rendered inline when a project row is expanded (or via a `Configure push rule` button). Three radio modes — install `@radix-ui/react-radio-group` (already in package.json) and add a shadcn `radio-group.tsx` primitive at `frontend/src/components/ui/radio-group.tsx` if not yet present. `auto` (live executor, default suggestion text), `rule` (with a `branch_pattern` text input, badge `Stored — executor lands in M005`), `manual_workflow` (with a `workflow_id` text input, same badge). PUT /api/v1/projects/{id}/push-rule on submit; invalidate project + push-rule cache.

Must not block on `mode=rule`/`mode=manual_workflow` workflow_id/branch_pattern validation beyond non-empty after trim (M005 will tighten).

`data-testid` for T05: `projects-section`, `create-project-button`, `create-project-name-input`, `create-project-repo-input`, `create-project-installation-select`, `create-project-submit`, `project-row-<id>`, `project-open-button-<id>`, `push-rule-button-<id>`, `push-rule-mode-auto`, `push-rule-mode-rule`, `push-rule-mode-manual_workflow`, `push-rule-branch-pattern-input`, `push-rule-workflow-id-input`, `push-rule-submit`, `push-rule-stored-badge`.

**Failure modes (Q5):** POST /projects 409 `project_name_taken` → surface inline form error rather than toast. POST /projects 404 `installation_not_in_team` → toast + refresh installations list (race: user might have uninstalled in another tab). POST /open 502 with `{detail, reason}` → toast carries the reason discriminator (S04 contract). POST /open 503 `orchestrator_unavailable` → toast `Orchestrator is unreachable — please try again in a moment`. PUT push-rule 422 on bad mode → impossible via UI (radio gates input); 404 `push_rule_not_found` → toast + refresh.

**Load profile (Q6):** Listing endpoint is paginated server-side (TBD — M004 returns all rows; if the list grows past ~50 the FE should add cursor pagination, but for M004 a flat list is fine). Open chain is the slowest hot path — 2-10s wall-clock — so the spinner state must be visible immediately, not only after first render.

**Negative tests (Q7):** team-member (non-admin) view must hide create/open/push-rule controls. Empty-name submit blocked. Repo without `/` blocked. Cancelling Create dialog leaves no row inserted.

## Inputs

- `frontend/src/client/sdk.gen.ts`
- `frontend/src/routes/_layout/teams_.$teamId.tsx`
- `frontend/src/components/Teams/CreateTeamDialog.tsx`
- `frontend/src/components/ui/select.tsx`
- `frontend/src/components/ui/loading-button.tsx`
- `.gsd/milestones/M004-guylpp/slices/S04/S04-SUMMARY.md`

## Expected Output

- `frontend/src/components/Teams/Projects/ProjectsList.tsx`
- `frontend/src/components/Teams/Projects/CreateProjectDialog.tsx`
- `frontend/src/components/Teams/Projects/OpenProjectButton.tsx`
- `frontend/src/components/Teams/Projects/PushRuleForm.tsx`
- `frontend/src/components/ui/radio-group.tsx`
- `frontend/src/routes/_layout/teams_.$teamId.tsx`

## Verification

1) `cd frontend && bun run build` exits 0. 2) `cd frontend && bun run lint` exits 0. 3) Manual smoke against live compose stack with backend + orchestrator + mock-github sidecars seeded per S04/T05: log in as team-admin, navigate to /teams/<id>, click Create Project, fill name + repo + installation, submit, assert row appears. Click Open, assert spinner then success toast. Open push-rule form, switch to rule mode, assert `Stored — executor lands in M005` badge renders, fill branch_pattern, submit, assert PUT succeeded and form re-renders with the saved mode. 4) `grep -E 'push-rule-stored-badge|project-open-button' frontend/src/components/Teams/Projects/` returns matches.

## Observability Impact

Open-project failure toast forwards the orchestrator's `{detail, reason}` payload verbatim — closes the operator UX gap from S04 where the discriminator was only visible in the orchestrator log. Push-rule mode=rule/manual_workflow render an explicit `Stored — executor lands in M005` badge so the user is not misled into expecting the rule to fire (D024 schema-now/executors-deferred contract surfaced to the UI).
