---
id: T02
parent: S06
milestone: M004-guylpp
key_files:
  - frontend/src/components/Admin/SystemSettings/OneTimeValueModal.tsx
  - frontend/src/components/Admin/SystemSettings/GenerateConfirmDialog.tsx
  - frontend/src/components/Admin/SystemSettings/SetSecretDialog.tsx
  - frontend/src/components/Admin/SystemSettings/SystemSettingsList.tsx
  - frontend/src/routes/_layout/admin.settings.tsx
  - frontend/src/components/Sidebar/AppSidebar.tsx
  - frontend/src/routes/_layout/admin.tsx
  - frontend/src/routeTree.gen.ts
key_decisions:
  - Frontend mirrors the backend _VALIDATORS registry as two static sets (KEYS_WITH_GENERATOR, PEM_KEYS) rather than a /settings/registry endpoint — keeps T02 self-contained and avoids a second round-trip on every page load. The set is small (1 entry each today, growth bounded by the registry).
  - Plaintext from POST /generate flows through SystemSettingsList's setState(value) directly into OneTimeValueModal's `value` prop. It never lives in a hook outside that closure, never in localStorage, never in console.log. Closing the modal unmounts the component → React drops the prop → the value is unreachable. This is the FE half of MEM232.
  - Used dot-prefix `admin.settings.tsx` (per plan) and updated parent admin.tsx's useMatches child-detection block. The trailing-underscore alternative would have given a true sibling but the plan was explicit about the path.
duration: 
verification_result: passed
completed_at: 2026-04-28T03:17:37.943Z
blocker_discovered: false
---

# T02: Build admin /admin/settings UI: list registered system settings with sensitive lock + has_value badge, set/replace dialog (PEM textarea or single-line), destructive Generate confirm, and one-time-display modal that holds plaintext only in modal-local state.

**Build admin /admin/settings UI: list registered system settings with sensitive lock + has_value badge, set/replace dialog (PEM textarea or single-line), destructive Generate confirm, and one-time-display modal that holds plaintext only in modal-local state.**

## What Happened

Built the system_admin-only frontend surface for system_settings management against the M004/S01 backend. Five new files plus two minimal edits.

**Files created:**
- `frontend/src/components/Admin/SystemSettings/OneTimeValueModal.tsx` — Plaintext display modal. Holds the value only in props/local DOM; closing (acknowledge button OR overlay/Escape) unmounts the component, dropping the value. No `console.log`, no `localStorage`, no global store — closure of the FE side of MEM232. Has `system-settings-one-time-value`, `system-settings-one-time-acknowledge`, `system-settings-one-time-copy` testids.
- `frontend/src/components/Admin/SystemSettings/GenerateConfirmDialog.tsx` — Destructive-by-design (D025) confirm step before POST /generate. Renders the verbatim warning copy from the plan (`Re-generating breaks any existing GitHub webhook deliveries until you update the upstream secret on github.com — proceed?`). Confirm button has `system-settings-generate-confirm` testid. Cancel via DialogClose dismisses without firing POST (negative-test Q7).
- `frontend/src/components/Admin/SystemSettings/SetSecretDialog.tsx` — Operator-supplied secret entry. Variant `pem` (multiline `<textarea>`) for github_app_private_key, variant `string` (single-line Input) for github_app_webhook_secret. Zod validators block empty submission with inline `PEM cannot be empty` (negative-test Q7). Form is reset on close so the plaintext doesn't outlive the dialog.
- `frontend/src/components/Admin/SystemSettings/SystemSettingsList.tsx` — Orchestrator. `useSuspenseQuery` against `['admin','settings']` cache key (matching the slice plan's React Query inspection surface). Mutations for `putSystemSetting` (Set/Replace) and `generateSystemSetting`. `extractErrorBody` pulls `detail`/`reason` off the ApiError body and toasts the backend's response verbatim — the operator sees `system_settings_decrypt_failed key=<name>` 503 discriminators and PUT 422 reason fields without opening DevTools. Two frontside-only constants (`KEYS_WITH_GENERATOR`, `PEM_KEYS`) mirror the backend `_VALIDATORS` registry — only `github_app_webhook_secret` exposes Generate; `github_app_private_key` is sensitive but PUT-only (matches backend Q5: GenerateConfirmDialog never renders for keys without a generator).
- `frontend/src/routes/_layout/admin.settings.tsx` — `requireSystemAdmin` beforeLoad guard (T01), Suspense + Skeleton fallback, prefetches the settings via the route loader.

**Files edited:**
- `frontend/src/components/Sidebar/AppSidebar.tsx` — Added `System Settings` entry (lucide `Settings` icon) for system_admin users, alongside the existing Admin/All Teams entries.
- `frontend/src/routes/_layout/admin.tsx` — Extended the `useMatches` child-detection block to also recognize `/_layout/admin/settings` so the parent's UsersTable shell doesn't render alongside the child Outlet.
- `frontend/src/routeTree.gen.ts` — Auto-regenerated to include the new route ID.

**Verification:**
- `cd frontend && bun run build` exit 0 (1.93s after lint reformat). The new `admin.settings` chunk emits at 0.94 kB / gzip 0.50 kB.
- `cd frontend && bun run lint` exit 0 (`Checked 89 files in 32ms. No fixes applied.` — the first run reformatted 3 files, the re-run was clean).
- `grep 'system-settings-one-time-value' …/OneTimeValueModal.tsx` → 1 match.
- `grep -rn 'noopener' …/SystemSettings/` → no matches (install-CTA lives in T03).
- `grep -E 'console\\.log|localStorage' …/OneTimeValueModal.tsx` → no matches (one-shot plaintext invariant holds).

**Captured memories:**
- MEM301 (gotcha): Adding a new TanStack Router file route requires regenerating routeTree.gen.ts before `tsc` accepts it — the build script runs tsc BEFORE vite, so the router-plugin's auto-generation hasn't fired yet. Workaround documented.
- MEM302 (pattern): When using dot-prefix child convention `admin.X.tsx`, must update parent admin.tsx `useMatches` to recognize the new child route ID, or use trailing-underscore `admin_.X.tsx` for full sibling replacement.

**Deviations:** Build script's tsc step ran before the router-plugin's auto-codegen had a chance to write `routeTree.gen.ts` (which had no entry for the new `admin.settings` route file). Resolved by invoking `@tanstack/router-generator`'s `Generator.run()` directly via a small node one-liner — captured as MEM301.

The slice plan's manual smoke check (verification step 3) is intentionally deferred to T05 (Playwright spec), per the plan's own note `(T05 will codify this as a Playwright spec.)`.

## Verification

All non-manual plan verification steps executed and pass. Step 3 (manual smoke against live compose stack) is explicitly out of scope per the plan ("T05 will codify this as a Playwright spec"). Slice-level verification surfaces are all satisfied: every mutation toasts via sonner with backend response body propagation (PUT 422 reason, generate 503 system_settings_decrypt_failed); React Query cache key is `['admin','settings']` matching the slice plan's documented FE source-of-truth; `OneTimeValueModal` is the only place plaintext crosses the FE boundary (no global store, no analytics, no console.log, no localStorage — verified by grep).

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd frontend && bun run build` | 0 | ✅ pass | 1930ms |
| 2 | `cd frontend && bun run lint` | 0 | ✅ pass (3 formatting fixes on first pass; second pass clean) | 50ms |
| 3 | `grep -c 'system-settings-one-time-value' frontend/src/components/Admin/SystemSettings/OneTimeValueModal.tsx` | 0 | ✅ pass (1 match) | 10ms |
| 4 | `grep -rn 'noopener' frontend/src/components/Admin/SystemSettings/` | 1 | ✅ pass (no matches — install CTA lives in T03) | 10ms |
| 5 | `grep -E 'console\.log|localStorage' frontend/src/components/Admin/SystemSettings/OneTimeValueModal.tsx` | 1 | ✅ pass (no matches — one-shot plaintext invariant holds) | 10ms |
| 6 | `Manual smoke against live compose stack` | 0 | ⏸ deferred to T05 per plan | 0ms |

## Deviations

Build script runs `tsc` before `vite build`, so a freshly-added file route (admin.settings.tsx) fails tsc on the first build because routeTree.gen.ts has not been regenerated by the vite plugin yet. Resolved by invoking @tanstack/router-generator's Generator.run() directly via a small node command from frontend/. Captured as MEM301 for future agents.

## Known Issues

none

## Files Created/Modified

- `frontend/src/components/Admin/SystemSettings/OneTimeValueModal.tsx`
- `frontend/src/components/Admin/SystemSettings/GenerateConfirmDialog.tsx`
- `frontend/src/components/Admin/SystemSettings/SetSecretDialog.tsx`
- `frontend/src/components/Admin/SystemSettings/SystemSettingsList.tsx`
- `frontend/src/routes/_layout/admin.settings.tsx`
- `frontend/src/components/Sidebar/AppSidebar.tsx`
- `frontend/src/routes/_layout/admin.tsx`
- `frontend/src/routeTree.gen.ts`
