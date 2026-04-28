---
estimated_steps: 1
estimated_files: 5
skills_used: []
---

# T02: Regenerate OpenAPI client and add reusable systemAdminGuard for TanStack Router

Bring the frontend SDK in sync with the new admin endpoints from T01 by regenerating `frontend/src/client/`. The project already uses `@hey-api/openapi-ts` (see `frontend/src/client/index.ts` header). Then add a tiny shared route-guard helper at `frontend/src/lib/auth-guards.ts` that exports `requireSystemAdmin({ context, location })` — runs `context.queryClient.ensureQueryData({ queryKey: ['currentUser'], queryFn: UsersService.readUserMe })`, then if `user.role !== 'system_admin'` throws `redirect({ to: '/' })`. Note: the existing `/admin` route inlines the same logic (see `frontend/src/routes/_layout/admin.tsx` lines 21–30) — refactor it to call the shared helper to prove the abstraction. Do NOT touch existing test files in this task — T05 covers Playwright. Run `bun run lint` after to catch import errors.

## Inputs

- ``backend/app/api/routes/admin.py` — generated from T01; defines the OpenAPI surface to be re-emitted`
- ``frontend/src/routes/_layout/admin.tsx` — existing inline system-admin guard to replace with helper`
- ``frontend/package.json` — has the `generate-client` script wired to @hey-api/openapi-ts`
- ``frontend/src/client/sdk.gen.ts` — current generated SDK (will be overwritten)`

## Expected Output

- ``frontend/src/client/sdk.gen.ts` — regenerated, contains `AdminService.readAllTeams`, `AdminService.readAdminTeamMembers`, `AdminService.promoteSystemAdmin` (or whatever names openapi-ts derives)`
- ``frontend/src/client/types.gen.ts` — regenerated with new request/response types`
- ``frontend/src/client/schemas.gen.ts` — regenerated`
- ``frontend/src/lib/auth-guards.ts` — exports `requireSystemAdmin` route-guard helper`
- ``frontend/src/routes/_layout/admin.tsx` — refactored to call `requireSystemAdmin` instead of the inline check`

## Verification

cd frontend && bun run generate-client && bun run lint && grep -q 'AdminService' src/client/sdk.gen.ts

## Observability Impact

None — pure type/regeneration step; no runtime signal change.
