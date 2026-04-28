---
estimated_steps: 9
estimated_files: 7
skills_used: []
---

# T01: Regenerate openapi client + scaffold team-admin guard + add switch UI primitive

Foundation task. Regenerate the frontend client against the current backend (openapi.json now carries all M004 endpoints from S01/S02/S04 — admin/settings + generate, github install-url + callback + installations list/delete, projects CRUD + push-rule + open, PATCH /teams/{id}/mirror). Add a `requireTeamAdmin` route guard mirroring `requireSystemAdmin` (lib/auth-guards.ts) so T03 + T04's team-admin routes can gate on it. Install the shadcn `switch` primitive into `components/ui/switch.tsx` so T03's mirror always-on toggle and T04's push-rule form have it. Verify `bun run build` green and `bun run lint` clean.

Steps:
1. Run `bash scripts/generate-client.sh` from repo root — boots backend Python to dump openapi.json then runs `bun run --filter frontend generate-client` and `bun run lint`. The script depends on backend imports working without an actual server, which it already does (it imports `app.main` directly).
2. After regeneration, scan `frontend/src/client/sdk.gen.ts` for the four expected service classes/methods: `AdminService.generateAdminSetting` (or similar — the methodNameBuilder strips the service prefix, so it lands as `generateAdminSetting` or `generateSetting`), the github install methods on a `GithubService`, `ProjectsService.openProject`, and `TeamsService.updateTeamMirror`. If any is missing, the backend's openapi tagging is wrong — surface that as an early failure.
3. Add `requireTeamAdmin` to `frontend/src/lib/auth-guards.ts`. Signature: `requireTeamAdmin({ context, params }: GuardContext & { params: { teamId: string } })`. Implementation: ensureQueryData on `['teams']` (calls TeamsService.readTeams), find the team by id from params, redirect to `/teams/$teamId` (read-only) if `team.role !== 'admin'` or to `/teams` if not found. Re-export from same file as `requireSystemAdmin`.
4. Install the shadcn switch primitive at `frontend/src/components/ui/switch.tsx`. Use the standard shadcn pattern (Radix `@radix-ui/react-switch` is NOT in package.json — `bun add @radix-ui/react-switch` first; then add the component file with the standard className-cva shape used by the existing `checkbox.tsx`).
5. Run `cd frontend && bun run build` and `bun run lint` to confirm.

This task is a context-window-frugal foundation — every later task in this slice depends on the regenerated client + guard + primitive.

Failure modes: openapi regeneration fails if backend imports break — re-run after fixing. Switch primitive type errors if Radix Switch types diverge from existing checkbox shape — copy the canonical shadcn `switch.tsx` verbatim from shadcn/ui v4 docs (Radix Switch root + thumb).

## Inputs

- `scripts/generate-client.sh`
- `frontend/openapi-ts.config.ts`
- `frontend/src/lib/auth-guards.ts`
- `backend/app/api/main.py`
- `frontend/src/components/ui/checkbox.tsx`
- `frontend/package.json`

## Expected Output

- `frontend/openapi.json`
- `frontend/src/client/sdk.gen.ts`
- `frontend/src/client/types.gen.ts`
- `frontend/src/client/schemas.gen.ts`
- `frontend/src/lib/auth-guards.ts`
- `frontend/src/components/ui/switch.tsx`
- `frontend/package.json`

## Verification

1) `bash scripts/generate-client.sh` exits 0 and rewrites frontend/src/client/*.gen.ts. 2) `cd frontend && bun run build` exits 0 (proves regenerated client + new files typecheck). 3) `cd frontend && bun run lint` exits 0. 4) `grep -E 'generateSetting|getInstallUrl|installCallback|openProject|updateTeamMirror' frontend/src/client/sdk.gen.ts` returns at least four matches (proves all four M004 endpoint families landed). 5) `grep 'requireTeamAdmin' frontend/src/lib/auth-guards.ts` returns the new export. 6) `grep '@radix-ui/react-switch' frontend/package.json` returns the new dep.

## Observability Impact

None — pure scaffolding. No new log lines, no new failure surfaces.
