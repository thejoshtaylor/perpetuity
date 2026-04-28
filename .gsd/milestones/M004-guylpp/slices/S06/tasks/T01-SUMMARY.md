---
id: T01
parent: S06
milestone: M004-guylpp
key_files:
  - frontend/openapi.json
  - frontend/src/client/sdk.gen.ts
  - frontend/src/client/types.gen.ts
  - frontend/src/client/schemas.gen.ts
  - frontend/src/lib/auth-guards.ts
  - frontend/src/components/ui/switch.tsx
  - frontend/package.json
  - frontend/bun.lock
key_decisions:
  - requireTeamAdmin reuses the existing ['teams'] React Query cache key + readTeams envelope shape rather than introducing a new ['team', id] key — keeps it consistent with the existing layout's getTeamsQueryOptions pattern in routes/_layout/teams_.$teamId.tsx.
  - Switch primitive uses the canonical shadcn v4 className shape (h-[1.15rem] w-8, data-state-driven thumb translate) — kept className surface identical to checkbox.tsx for consistency.
duration: 
verification_result: passed
completed_at: 2026-04-28T03:08:01.081Z
blocker_discovered: false
---

# T01: Regenerate frontend openapi client for M004 endpoints, scaffold requireTeamAdmin route guard, and install shadcn switch primitive

**Regenerate frontend openapi client for M004 endpoints, scaffold requireTeamAdmin route guard, and install shadcn switch primitive**

## What Happened

Foundation task for slice S06 — set up the scaffolding every later task depends on.

**1. Regenerated openapi client.** Ran `bash scripts/generate-client.sh` from repo root. The script imported `app.main` directly (no live backend needed), dumped openapi.json, ran `bun run --filter frontend generate-client`, and `bun run lint`. Both invocations exited 0. Confirmed all four expected M004 endpoint families landed in `frontend/src/client/sdk.gen.ts`:
- `AdminService.generateSystemSetting` (also `listSystemSettings`, `getSystemSetting`, `putSystemSetting`)
- `GithubService.getGithubInstallUrl`, `githubInstallCallback`, `listGithubInstallations`, `deleteGithubInstallation`
- `ProjectsService.openProject` (plus full CRUD + push-rule getters/putters)
- `TeamsService.updateTeamMirror`

The methodNameBuilder produced `getGithubInstallUrl`/`generateSystemSetting` rather than the planner's stripped guesses (`getInstallUrl`/`generateSetting`), so my verification grep used the actual rendered names.

**2. Added requireTeamAdmin to lib/auth-guards.ts.** Mirrored the existing `requireSystemAdmin` shape but accepts `params: { teamId: string }`. Calls `ensureQueryData` on the `["teams"]` cache key (matching the existing `getTeamsQueryOptions` in `routes/_layout/teams_.$teamId.tsx`), uses the `{data, count}` envelope shape returned by `TeamsService.readTeams()`, finds the team by id, redirects to `/teams` if missing, redirects to `/teams/$teamId` (read-only) if `team.role !== "admin"`. Re-exported from the same file.

**3. Installed shadcn switch primitive.** `bun add @radix-ui/react-switch` added v1.2.6 to package.json. Created `components/ui/switch.tsx` using the canonical shadcn v4 pattern — Radix `SwitchPrimitive.Root` + `SwitchPrimitive.Thumb` with the same `data-slot` attribute / `cn()` className shape as the existing `checkbox.tsx`.

**4. Verified.** `cd frontend && bun run build` exited 0 (1.94s; the regenerated client + new files all typecheck). `bun run lint` exited 0 with no fixes applied. Build emitted the expected vite chunk warning (>500kB index chunk) — pre-existing, not introduced by this task.

No deviations from the plan beyond the grep-pattern adjustment noted above. No DECISIONS.md or KNOWLEDGE.md changes — this slice consumes existing decisions D024/D025 without introducing new structural choices.

## Verification

All six verification checks from T01-PLAN executed:

1. `bash scripts/generate-client.sh` → exit 0, rewrote `frontend/src/client/{sdk,types,schemas}.gen.ts`. ✅
2. `cd frontend && bun run build` → exit 0 in 1.94s. ✅
3. `cd frontend && bun run lint` → exit 0, "Checked 84 files in 48ms. No fixes applied." ✅
4. `grep -E 'generate(System)?Setting|get(Github)?InstallUrl|(github)?InstallCallback|openProject|updateTeamMirror' frontend/src/client/sdk.gen.ts` → 6 matches across all four endpoint families (Admin, Github, Projects, Teams). ✅ (Used the actual rendered method names; the plan's stripped guesses missed the resource prefix the openapi-ts methodNameBuilder applies.)
5. `grep 'requireTeamAdmin' frontend/src/lib/auth-guards.ts` → returns the new export (`export async function requireTeamAdmin({`). ✅
6. `grep '@radix-ui/react-switch' frontend/package.json` → returns `"@radix-ui/react-switch": "^1.2.6"`. ✅

Slice-level verification: nothing changes for this scaffolding task — no toasts, no error paths, no observability surfaces. T03/T04/T05 will exercise the slice-level frontend behavior.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `bash scripts/generate-client.sh` | 0 | ✅ pass | 12000ms |
| 2 | `cd frontend && bun run build` | 0 | ✅ pass | 1940ms |
| 3 | `cd frontend && bun run lint` | 0 | ✅ pass | 50ms |
| 4 | `grep -E 'generate(System)?Setting|get(Github)?InstallUrl|(github)?InstallCallback|openProject|updateTeamMirror' frontend/src/client/sdk.gen.ts | wc -l` | 0 | ✅ pass (6 matches, ≥4 required) | 20ms |
| 5 | `grep 'requireTeamAdmin' frontend/src/lib/auth-guards.ts` | 0 | ✅ pass | 10ms |
| 6 | `grep '@radix-ui/react-switch' frontend/package.json` | 0 | ✅ pass | 10ms |

## Deviations

Verification grep #4 in T01-PLAN used approximate method names (`generateSetting`, `getInstallUrl`, `installCallback`). The openapi-ts methodNameBuilder emits the full names with resource prefix (`generateSystemSetting`, `getGithubInstallUrl`, `githubInstallCallback`). I ran the grep with the rendered names; all four endpoint families confirmed present (6 matches). Equivalent verdict, more accurate match.

## Known Issues

none

## Files Created/Modified

- `frontend/openapi.json`
- `frontend/src/client/sdk.gen.ts`
- `frontend/src/client/types.gen.ts`
- `frontend/src/client/schemas.gen.ts`
- `frontend/src/lib/auth-guards.ts`
- `frontend/src/components/ui/switch.tsx`
- `frontend/package.json`
- `frontend/bun.lock`
