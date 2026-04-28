---
estimated_steps: 1
estimated_files: 7
skills_used: []
---

# T04: NotificationBell + NotificationPanel UI mounted in _layout.tsx header with 5s polling

Build the in-app bell + panel using existing design-system primitives so touch-target compliance is inherited automatically (MEM337). The repo has NO `popover.tsx` — use the existing `dropdown-menu.tsx` (radix `@radix-ui/react-dropdown-menu` is already a dep) for the bell trigger + panel. Files: `frontend/src/components/notifications/NotificationBell.tsx` — a Button (variant='ghost' size='icon') wrapping a `Bell` lucide icon with an absolute-positioned unread-count badge (red dot when count > 0; numeric pill when count >= 1; '99+' when count > 99). The bell is the trigger inside a DropdownMenu; the panel renders inside `DropdownMenuContent` (className='w-96 p-0' so we can use a custom layout). `NotificationPanel.tsx`: header with 'Notifications' title + a 'Show only unread' Switch + 'Mark all read' Button (only visible when unread_count > 0); body is a ScrollArea (max-h-96 overflow-auto) listing `NotificationItem` rows. Each row: kind icon (Bell for system, Users for team_invite_accepted, FolderGit2 for project_created, etc. — switch on kind), human-readable title built from kind + payload (e.g. team_invite_accepted → `Joined ${payload.team_name}`; project_created → `New project ${payload.project_name}`), relative time via a small inline helper that returns 'just now' / 'Nm ago' / 'Nh ago' / 'Nd ago' (NO new dependency — date-fns is fine if already installed; check `frontend/package.json`), unread → font-medium + a left border accent (border-l-2 border-primary) and an onClick that calls NotificationsService.read(id) and React-Query-invalidates [['notifications'], ['notifications','unreadCount']]. Empty state: muted-fg text 'No notifications yet'. Polling hook `frontend/src/hooks/useNotificationsPolling.ts`: returns `{ items, unreadCount, isFetching }` from two `useQuery` calls — `['notifications', { unreadOnly }]` → NotificationsService.list with `refetchInterval: 5000, refetchIntervalInBackground: false`, and `['notifications', 'unreadCount']` → NotificationsService.unreadCount with the same cadence. Both queries also invalidate on window focus (default React Query behavior) so a returning tab catches up faster than 5s. Theme tokens follow S01 conventions (bg-muted/40, bg-destructive). Mount the bell in `frontend/src/routes/_layout.tsx` header — to the RIGHT of the existing SidebarTrigger; wrap the bell in a div with `className='ml-auto flex items-center gap-2'` so it pushes to the far right and we can add S03's push-permission prompt + S04's mic affordance later without refactoring. The bell button MUST inherit min-h-11/min-w-11 from button.tsx (size='icon' variant) so the mobile-audit touch-target gate stays green automatically. Extend `frontend/tests/m005-oaptsz-mobile-audit.spec.ts` to add a single new check at one post-login route: the bell button is visible AND its boundingBox is ≥44×44 across all four projects. Add `frontend/tests/m005-oaptsz-notifications.spec.ts` that (a) uses the existing storageState login, (b) calls `POST /api/v1/notifications/test` via `request.fetch` (same cookie-auth context) to seed a system notification, (c) reloads, (d) asserts the bell badge shows '1' within 6s (5s poll + 1s buffer), (e) clicks the bell, (f) asserts the panel renders one NotificationItem with the seeded message, (g) clicks the item, (h) asserts the badge clears within 1s.

## Inputs

- ``frontend/src/client/sdk.gen.ts``
- ``frontend/src/components/ui/dropdown-menu.tsx``
- ``frontend/src/components/ui/button.tsx``
- ``frontend/src/routes/_layout.tsx``
- ``frontend/tests/utils/audit.ts``
- ``frontend/tests/m005-oaptsz-mobile-audit.spec.ts``
- ``frontend/playwright.config.ts``
- ``frontend/package.json``

## Expected Output

- ``frontend/src/components/notifications/NotificationBell.tsx``
- ``frontend/src/components/notifications/NotificationPanel.tsx``
- ``frontend/src/components/notifications/NotificationItem.tsx``
- ``frontend/src/hooks/useNotificationsPolling.ts``
- ``frontend/src/routes/_layout.tsx``
- ``frontend/tests/m005-oaptsz-notifications.spec.ts``
- ``frontend/tests/m005-oaptsz-mobile-audit.spec.ts``

## Verification

cd frontend && bun run lint && bun run build && bunx playwright test --project=chromium m005-oaptsz-notifications.spec.ts && bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts

## Observability Impact

Signals added: `notifications.poll.tick count=<n>` console.info from `useNotificationsPolling` gated on `?devtools=1` (MEM341) so default dev runs and Playwright audits don't see it but a developer can verify the cadence on demand. The DropdownMenu open/close emits a `notifications.panel.open` / `notifications.panel.close` console.info similarly gated. How a future agent inspects this: open `?devtools=1` URL in dev, watch the console for the tick cadence; React Query DevTools (already gated on devtools=1 per MEM341) shows the two query keys and their staleTime/refetchInterval. Failure state exposed: a 401 from /api/v1/notifications surfaces as React Query error state — the panel shows 'Failed to load notifications' with a retry button rather than a blank empty state.
