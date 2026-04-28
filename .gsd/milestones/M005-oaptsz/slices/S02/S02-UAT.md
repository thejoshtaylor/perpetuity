# S02: Notification center + per-workflow preferences (in-app channel) — UAT

**Milestone:** M005-oaptsz
**Written:** 2026-04-28T11:13:27.773Z

## UAT — S02 Notification center + preferences (in-app channel)

**Scope:** in-app notification dispatch path, top-bar bell + panel, settings preferences tab, cross-device read-state via 5s polling, payload redaction. Push channel is stubbed and lands in S03.

### Preconditions

- Backend running and reachable; Postgres at head (s07_notifications). Run `cd backend && uv run alembic upgrade head` if needed.
- Superuser seeded: `admin@example.com` / `changethis` (or your `FIRST_SUPERUSER` / `FIRST_SUPERUSER_PASSWORD`).
- Frontend dev or preview server reachable (`bun run dev` on :5173 or `bun run preview` on :4173 against `bun run build` output).
- Playwright auth state file exists: `frontend/playwright/.auth/user.json` (auth setup creates it on first run).

### Scenario 1 — Bell appears, badges, panels, marks read (chromium)

1. Open the app authenticated as the seeded superuser; the top-bar bell icon (Lucide `Bell`) is visible to the right of the sidebar trigger.
   - Expected: bell button is rendered with no badge dot (no unread).
2. From the browser console, seed a notification: dynamically import the generated SDK and call `NotificationsService.testTrigger({ requestBody: { message: 'UAT bell test' } })`.
   - Expected: 200 response with the created NotificationPublic.
3. Wait ≤6s without manual refresh.
   - Expected: red unread dot/pill appears on the bell.
4. Click the bell.
   - Expected: dropdown panel opens; one row shows 'System' icon + the seeded message + relative time; row is rendered with a left-border accent and a `<output aria-label='unread'>` dot.
5. Click the row.
   - Expected: row visually transitions to read (dot disappears, accent removed); badge clears within ~1s.
6. Click 'Mark all read' (visible only when unread > 0).
   - Expected: button hides; all unread items in the panel become read.

### Scenario 2 — Cross-device read-state syncs within 5s (chromium, two BrowserContexts as same user)

1. Open the app in two BrowserContexts authenticated as the same superuser (same `storageState`).
2. From ContextA, seed a notification via the generated SDK.
   - Expected: ContextA's bell badge appears within 6s.
3. Wait ≤6s in ContextB without manual refresh.
   - Expected: ContextB's bell badge also shows 1 unread (5s polling cadence).
4. In ContextA, click the bell, click the new item.
   - Expected: ContextA's badge clears within 1s.
5. Wait ≤6s in ContextB.
   - Expected: ContextB's badge clears (the read state propagated via polling).

### Scenario 3 — Notifications preferences tab toggles team-default in-app routing

1. Navigate to `/settings` and click the **Notifications** tab.
   - Expected: Card titled 'Notification preferences (team default)' with a row per `NotificationKind` (7 rows). In-app cell is a Switch reflecting current state (defaults to ON for all kinds except `workflow_step_completed`); Push cell is a disabled Switch labelled 'Available in S03'.
2. Toggle the In-app Switch for `team_invite_accepted` to OFF.
   - Expected: Switch flips immediately (optimistic re-anchor via `setQueryData`); a subsequent React Query refetch confirms the persisted value.
3. From the browser console (or via the notifications/test endpoint with `kind=team_invite_accepted`), trigger a `team_invite_accepted` event for this user.
   - Expected: backend logs `notify.skipped_in_app reason=preference_off`; no row appears in the panel and the badge does not change.
4. Toggle the In-app Switch back to ON.
5. Re-fire the event.
   - Expected: backend logs `notify.dispatched`; the row appears in the panel within 6s; unread badge increments.

### Scenario 4 — Payload redaction (backend pytest contract)

1. Run `cd backend && pytest tests/api/routes/test_notifications.py -k 'redact'`.
   - Expected: passes. The test calls `notify()` with payload `{'token': 'xxx', 'email': 'a@b.com', 'team_name': 'Foo'}` and asserts the persisted `payload` stores `<redacted>` for `token` and `email` and `Foo` for `team_name`.

### Scenario 5 — Bell does not regress the mobile audit (mobile-chrome)

1. Run `cd frontend && bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts`.
   - Expected: all 16 tests (8 routes × 2 assertions) pass. The bell button inherits min-h-11/min-w-11 from the S01 design-system-primitive-floor pattern (MEM337) so touch-target compliance is automatic.

### Edge cases

- **Empty state:** with no notifications for the user, the panel shows `No notifications yet` muted-fg copy and 'Mark all read' is hidden.
- **Long lists:** the panel uses a ScrollArea with max-h-96 and limits the list query to 50; older notifications scroll within the panel.
- **DB hiccup in notify():** simulate by stopping Postgres briefly while the route runs; the team-invite-accept / project-create response should still succeed, backend logs `notify.insert_failed cause=…` ERROR, no notification row is created. The route's contract is preserved.
- **Non-superuser POST /notifications/test:** returns 403 (gated on `get_current_active_superuser`).
- **Parallel browsers without same user:** ContextA's seed for User1 must not appear in ContextB's bell when ContextB is User2 — verified by `mark-all-read affects only the calling user's unread rows` test in test_notifications.py.

### Out of scope (lands in later slices)

- Push channel delivery, VAPID generation, push_subscriptions hydration, SW push event handler — S03.
- Per-workflow override UI on a workflow detail page — schema is ready (workflow_id NULL = team-default, specific UUID = override) but the workflow detail page does not exist yet; lands when the workflow run engine ships.
- Voice input, microphone affordance — S04.
