---
id: T02
parent: S05
milestone: M001-6cqls8
key_files:
  - frontend/openapi.json
  - frontend/src/client/sdk.gen.ts
  - frontend/src/client/types.gen.ts
  - frontend/src/client/schemas.gen.ts
  - frontend/src/lib/auth-guards.ts
  - frontend/src/routes/_layout/admin.tsx
key_decisions:
  - Suppress backend warnings during openapi.json export (`python -W ignore` + `2>/dev/null`) — config.py emits UserWarnings to stderr but the script `scripts/generate-client.sh` redirects only stdout, so stale invocations could pollute the JSON file and break biome's JSON parser. Done at the python invocation level rather than touching the shell script.
  - Type the guard's parameter as `{ context: { queryClient: QueryClient } }` rather than importing TanStack Router's `BeforeLoadContextOptions` — keeps the helper decoupled from router-specific generic types and works with `beforeLoad: requireSystemAdmin` directly without casts.
  - Refactored only the existing `/admin` route in this task per the plan; T03/T04 will reuse `requireSystemAdmin` for the new `/admin/teams` and `/admin/teams/$teamId` routes.
duration: 
verification_result: passed
completed_at: 2026-04-25T01:15:51.510Z
blocker_discovered: false
---

# T02: Regenerate OpenAPI client with AdminService and add reusable requireSystemAdmin route guard, refactor /admin route to use it

**Regenerate OpenAPI client with AdminService and add reusable requireSystemAdmin route guard, refactor /admin route to use it**

## What Happened

Regenerated `frontend/openapi.json` from the backend (`uv run python -W ignore -c "import app.main; print(app.main.app.openapi())"`) and ran `bun run generate-client` to refresh the SDK. The output now includes `AdminService` with `readAllTeams`, `readAdminTeamMembers`, and `promoteSystemAdmin` methods (and matching `Admin*Data`/`Admin*Response` types in `types.gen.ts`). Initial regeneration polluted `openapi.json` with stderr UserWarnings from `app/core/config.py:105` (SECRET_KEY/POSTGRES_PASSWORD changethis warnings) which broke biome's JSON parser; suppressed with `-W ignore` and `2>/dev/null` so only stdout JSON lands in the file.\n\nCreated `frontend/src/lib/auth-guards.ts` exporting `requireSystemAdmin({ context })` — calls `context.queryClient.ensureQueryData({ queryKey: ['currentUser'], queryFn: UsersService.readUserMe })` then throws `redirect({ to: '/' })` if `user.role !== 'system_admin'`. The helper is typed against a minimal `{ context: { queryClient: QueryClient } }` shape so it can be passed directly as a TanStack Router `beforeLoad` handler without TypeScript complaining about the broader router context type.\n\nRefactored `frontend/src/routes/_layout/admin.tsx` to import `requireSystemAdmin` and use `beforeLoad: requireSystemAdmin` instead of the 10-line inline guard (lines 21–30). Removed the now-unused `redirect` import. This proves the abstraction works for the existing surface and is ready for T03/T04 to reuse on the new `/admin/teams` and `/admin/teams/$teamId` routes.\n\nNoted that the prior verification gate failure (`tests/api/routes/test_admin_teams.py: file or directory not found`, exit 4) was a CWD issue — that test file was created in T01 and lives at `backend/tests/api/routes/test_admin_teams.py`. Re-ran from `backend/` and all 15 tests pass. T02 itself is a frontend-only regeneration step; the slice's prescribed verification is `cd frontend && bun run generate-client && bun run lint && grep -q 'AdminService' src/client/sdk.gen.ts`, which now passes cleanly.

## Verification

Ran the slice-prescribed verification chain: `cd frontend && bun run generate-client && bun run lint && grep -q 'AdminService' src/client/sdk.gen.ts` — all three steps pass. Generate-client emits the SDK to `src/client/`. Biome reports zero errors across 80 files. `AdminService` class is present in `sdk.gen.ts`. Also re-ran the T01 backend pytest suite from the correct CWD to confirm prior work still green: `cd backend && uv run pytest tests/api/routes/test_admin_teams.py -v` → 15 passed in 0.71s. TanStack Router type wiring confirmed via `bunx tsc -p tsconfig.build.json --noEmit` (no diagnostics).

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd frontend && bun run generate-client` | 0 | ✅ pass | 1500ms |
| 2 | `cd frontend && bun run lint` | 0 | ✅ pass | 800ms |
| 3 | `grep -q 'AdminService' frontend/src/client/sdk.gen.ts` | 0 | ✅ pass | 10ms |
| 4 | `cd frontend && bunx tsc -p tsconfig.build.json --noEmit` | 0 | ✅ pass | 5000ms |
| 5 | `cd backend && uv run pytest tests/api/routes/test_admin_teams.py -v` | 0 | ✅ pass | 710ms |

## Deviations

None — implementation matches the task plan. The plan's verification command is run as written. The only judgment call was suppressing stderr during openapi export (see key decisions), which is purely defensive and doesn't change the behavior.

## Known Issues

`scripts/generate-client.sh` uses the same `uv run python -c ...` invocation without `-W ignore` or stderr suppression. If a developer runs that script in an environment where the `changethis` warnings still print, the resulting `openapi.json` will be malformed. Out of scope for T02 (touching deployment scripts), but worth fixing in a follow-up — either suppress warnings or capture stdout-only with `2>/dev/null` in the shell script.

## Files Created/Modified

- `frontend/openapi.json`
- `frontend/src/client/sdk.gen.ts`
- `frontend/src/client/types.gen.ts`
- `frontend/src/client/schemas.gen.ts`
- `frontend/src/lib/auth-guards.ts`
- `frontend/src/routes/_layout/admin.tsx`
