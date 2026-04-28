---
id: T04
parent: S02
milestone: M005-oaptsz
key_files:
  - frontend/src/components/notifications/NotificationBell.tsx
  - frontend/src/components/notifications/NotificationPanel.tsx
  - frontend/src/components/notifications/NotificationItem.tsx
  - frontend/src/hooks/useNotificationsPolling.ts
  - frontend/src/routes/_layout.tsx
  - frontend/tests/m005-oaptsz-notifications.spec.ts
  - frontend/tests/m005-oaptsz-mobile-audit.spec.ts
key_decisions:
  - Used <output aria-label='unread'> for the unread dot — biome's useSemanticElements rejected both <span aria-label> (useAriaPropsSupportedByRole) and <span role='status'> (useSemanticElements) but allowed the semantic <output> element.
  - Split the 'Unread only' Switch label into a separate <label htmlFor='...'> so biome's noLabelWithoutControl is satisfied without nesting the Switch (a Radix component) inside a native label, which can confuse Radix's pointer handlers.
  - Polled unread count in NotificationBell independent of the panel's open state (separate hook call vs. lifting from NotificationPanel) so the badge stays current even when the user has not yet opened the panel — matches the slice plan's 5s cross-device sync contract.
  - Seeded the test notification via page.evaluate + dynamic import of the generated SDK rather than playwright's request.fetch — the SDK runs in page origin and inherits the storageState cookies regardless of FE/API origin split. Captured as MEM347.
duration: 
verification_result: mixed
completed_at: 2026-04-28T10:39:42.975Z
blocker_discovered: false
---

# T04: feat(notifications): mount NotificationBell + 5s-poll panel in _layout header and add notifications + mobile-audit playwright specs

**feat(notifications): mount NotificationBell + 5s-poll panel in _layout header and add notifications + mobile-audit playwright specs**

## What Happened

Built the in-app bell + dropdown panel using existing design-system primitives so the mobile-audit touch-target floor (MEM337) inherits automatically.

Components landed:
- `frontend/src/components/notifications/NotificationBell.tsx` — Button(variant='ghost', size='icon') wrapping a Bell lucide icon, controlled DropdownMenu with `align='end'` + `w-96 p-0` content. Absolute-positioned destructive badge: hidden when count=0, numeric pill at >=1, '99+' at >99. Always polls unread count (independent of panel-open) so the badge stays current. Emits `notifications.panel.open|close` console.info gated on `?devtools=1` (MEM341).
- `frontend/src/components/notifications/NotificationPanel.tsx` — header with 'Notifications' title + 'Unread only' Switch + a 'Mark all read' Button (only when unreadCount > 0). Body is a `max-h-96 overflow-y-auto` list with empty state and a 'Failed to load notifications' + Retry error state for the 401/500 path. Switch + label use `htmlFor` to satisfy biome's `noLabelWithoutControl` rule.
- `frontend/src/components/notifications/NotificationItem.tsx` — kind icon switch (Users / FolderGit2 / Bell / Play / CheckCircle2 / CircleX / CircleAlert), human-readable title from kind+payload (`Joined ${team_name}`, `New project ${project_name}`, system → payload.message), inline relativeTime helper (`just now / Nm / Nh / Nd ago` — no new dep), unread → `border-l-2 border-primary font-medium` and onClick → NotificationsService.markRead + invalidate `['notifications']`. Unread dot uses `<output aria-label='unread'>` to satisfy biome's `useSemanticElements` rule (initial `<span aria-label>` and `<span role='status'>` were both rejected).
- `frontend/src/hooks/useNotificationsPolling.ts` — two `useQuery` calls keyed `['notifications', { unreadOnly }]` and `['notifications', 'unreadCount']`, both with `refetchInterval: 5000, refetchIntervalInBackground: false`. Returns `{ items, total, unreadCount, isFetching, isError, refetch }`. Emits `notifications.poll.tick count=<n>` console.info on each fetch transition gated on `?devtools=1`.

Layout mount (`frontend/src/routes/_layout.tsx`): added `<div className='ml-auto flex items-center gap-2'><NotificationBell /></div>` to the right of the existing SidebarTrigger, so future S03 push-permission prompt + S04 mic affordance can slot in without refactoring the header.

Tests:
- `frontend/tests/m005-oaptsz-notifications.spec.ts` — uses storageState login, seeds a `system` notification by calling NotificationsService.triggerTestNotification from inside `page.evaluate(import('/src/client/sdk.gen.ts'))` (deviation/MEM347 — see below), reloads, asserts the badge shows a numeric value within 6s (5s poll + 1s buffer), opens the panel, asserts the seeded item renders with `data-unread='true'`, clicks it, asserts the badge clears within ~2s.
- `frontend/tests/m005-oaptsz-mobile-audit.spec.ts` — added a single new check before the per-route loop: navigate to `/teams`, locate `getByTestId('notification-bell')`, assert visibility and `boundingBox.width >= 44 && height >= 44`. Inherits all four projects (chromium, mobile-chrome, iphone-13-mobile-safari, desktop-firefox) automatically.

Deviation from plan: the plan suggested `request.fetch` against `/api/v1/notifications/test`. With cookie-auth bound to the API host (which can differ from the FE host via VITE_API_URL), and no precedent for `request.fetch` in the existing test suite, I used `page.evaluate` + a dynamic `import('/src/client/sdk.gen.ts')` so the seed call carries the same cookies the app uses. Captured as MEM347 for reuse.

Lint deviations: biome's `noLabelWithoutControl`, `useAriaPropsSupportedByRole`, and `useSemanticElements` rules required reshaping the unread-only Switch (split label with htmlFor) and the unread dot (`<output>` instead of aria-labelled `<span>`). Initial unused `// biome-ignore lint/suspicious/noConsole` comments were removed because biome is silent on `console.info` here.

## Verification

- `cd frontend && bun run lint` → exit 0, 0 errors, 0 warnings.
- `cd frontend && bun run build` → exit 0, tsc + vite + injectManifest SW build all green; only the pre-existing chunk-size advisory.
- `bunx playwright test --project=chromium m005-oaptsz-notifications.spec.ts` → 2/2 passed (auth setup + the seed→badge→panel→mark-read flow).
- `bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts -g 'notification bell'` → 2/2 passed; bell visible, boundingBox 44x44.
- `bunx playwright test --project=chromium --project=iphone-13-mobile-safari m005-oaptsz-mobile-audit.spec.ts -g 'notification bell'` → 3/3 passed across the available projects.

Pre-existing failures unrelated to T04: (1) `mobile-chrome → admin-teams: no horizontal scroll + touch targets >=44px` fails on the DataTable's pagination/combobox controls (`Go to first/prev/next/last page` 32x44, `combobox '10'` 70x36) — these are existing app-shell defects in the admin DataTable, not introduced by the bell. (2) `mobile-chrome → admin-teams: visual-diff baseline` fails because the seed cycle now contains 20 admin teams (393x1286 vs 727 baseline) — also pre-existing/unrelated to the bell. (3) `desktop-firefox` project errored because the firefox browser binary isn't installed in this dev environment — environmental, not our code. The plan's verification command runs only chromium + mobile-chrome and the bell check itself passes on both.

Environment fix needed during verification: the running fastapi backend on :8000 had an empty `user` table (DB recreated since last seed). Ran `app.initial_data` once to seed `admin@example.com` so the auth.setup storageState login could succeed. This is environmental and not a deliverable.

## Verification Evidence

| # | Command | Exit Code | Verdict | Duration |
|---|---------|-----------|---------|----------|
| 1 | `cd frontend && bun run lint` | 0 | ✅ pass | 1100ms |
| 2 | `cd frontend && bun run build` | 0 | ✅ pass | 8000ms |
| 3 | `cd frontend && bunx playwright test --project=chromium m005-oaptsz-notifications.spec.ts` | 0 | ✅ pass | 11300ms |
| 4 | `cd frontend && bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts -g 'notification bell'` | 0 | ✅ pass | 9500ms |
| 5 | `cd frontend && bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts (full suite)` | 1 | ❌ fail (pre-existing admin-teams DataTable defects, not T04) | 12800ms |

## Deviations

Plan called for the seed POST in the playwright spec to use `request.fetch` against `/api/v1/notifications/test`. Substituted `page.evaluate` + dynamic `import('/src/client/sdk.gen.ts')` so the call inherits the page's cookie auth regardless of FE/API origin split (no precedent for request.fetch in the existing test suite). Captured as MEM347 for reuse. Verification gate's reported failures (`source ../.env` and a backend pytest path `tests/api/routes/test_teams.py::test_invite_accept_creates_notification`) reference T03's verification, not T04's — T04's plan-defined verification is `cd frontend && bun run lint && bun run build && bunx playwright test ...` which passes for the deliverable code paths.

## Known Issues

Pre-existing /admin-teams DataTable mobile-audit defects (pagination buttons 32x44, page-size combobox 70x36) and the /admin-teams visual-diff baseline (393x1286 vs stored 727) are unrelated to the bell and pre-date this task — separate fix-up task material. The desktop-firefox project errors locally because the firefox browser binary is not installed in this dev environment (`npx playwright install firefox` would fix), again environmental rather than a code defect.

## Files Created/Modified

- `frontend/src/components/notifications/NotificationBell.tsx`
- `frontend/src/components/notifications/NotificationPanel.tsx`
- `frontend/src/components/notifications/NotificationItem.tsx`
- `frontend/src/hooks/useNotificationsPolling.ts`
- `frontend/src/routes/_layout.tsx`
- `frontend/tests/m005-oaptsz-notifications.spec.ts`
- `frontend/tests/m005-oaptsz-mobile-audit.spec.ts`
