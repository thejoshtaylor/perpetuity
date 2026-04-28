---
estimated_steps: 1
estimated_files: 5
skills_used: []
---

# T05: Notifications preferences tab in settings.tsx + cross-device 5s read-state Playwright contract gate

Ship the team-default-per-event-type preferences UI and prove the slice contract (cross-device read state within 6s + preference enforcement). The existing `frontend/src/routes/_layout/settings.tsx` is a Tabs UI with a `tabsConfig` array (my-profile, password, danger-zone). Append a fourth entry `{ value: 'notifications', title: 'Notifications', component: NotificationPreferences }` and ensure the system-admin slice (`tabsConfig.slice(0, 3)` line) is widened to include it for all users (the new tab is not admin-only — invert if needed by re-ordering or by changing the slice index). Create `frontend/src/components/notifications/NotificationPreferences.tsx`: render a Card titled 'Notification preferences (team default)' with a paragraph noting 'Per-workflow overrides ship in a future milestone; these defaults apply when no workflow override exists.' Below: a table with columns Event | In-app | Push, one row per NotificationKind (use the same human-readable mapping as T04's NotificationItem). The In-app cell is a Switch wired to `useMutation({ mutationFn: NotificationsService.preferenceUpdate })` that on success calls `queryClient.setQueryData(['notifications','preferences'], updaterFn)` for an immediate optimistic re-anchor + invalidates `['notifications','preferences']` (MEM305 cache-key shape). The Push cell renders a disabled Switch with helper text 'Available in S03'. Read state uses `useQuery({ queryKey: ['notifications','preferences'], queryFn: NotificationsService.preferences })`. Build the slice contract test `frontend/tests/m005-oaptsz-notifications-preferences.spec.ts` with TWO scenarios: SCENARIO A (cross-device 5s sync): two BrowserContexts both authenticated as the same user via storageState; ContextA seeds a system notification via `request.fetch('POST', '/api/v1/notifications/test', { data: { message: 'cross-device' } })`; wait 6s; both contexts' bells show 1 unread; ContextA clicks the bell + clicks the item to mark read; within 6s ContextB's badge clears. SCENARIO B (preference-off skips in_app insert): toggle team_invite_accepted in_app off via the preferences UI; trigger the test endpoint with a kind override (extend POST /notifications/test in this task to optionally accept a `kind: NotificationKind` body field gated to system_admin only — defaulting to system); fire kind=team_invite_accepted; GET /notifications and assert no row for that kind was inserted; toggle back on; fire again; assert the row appears. Final verification gate: `cd frontend && bunx playwright test --project=chromium m005-oaptsz-notifications.spec.ts m005-oaptsz-notifications-preferences.spec.ts && bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts && cd ../backend && set -a && source ../.env && set +a && uv run pytest tests/api/routes/test_notifications.py -x -k 'redact or preference'`. The redact + preference-skip unit tests from T02 prove redaction; the contract Playwright spec proves cross-device sync; the mobile-audit run proves the bell change didn't regress the touch-target gate.

## Inputs

- ``frontend/src/routes/_layout/settings.tsx``
- ``frontend/src/client/sdk.gen.ts``
- ``frontend/src/components/ui/switch.tsx``
- ``frontend/src/components/notifications/NotificationItem.tsx``
- ``frontend/tests/m005-oaptsz-notifications.spec.ts``
- ``backend/app/api/routes/notifications.py``
- ``backend/tests/api/routes/test_notifications.py``

## Expected Output

- ``frontend/src/routes/_layout/settings.tsx``
- ``frontend/src/components/notifications/NotificationPreferences.tsx``
- ``frontend/tests/m005-oaptsz-notifications-preferences.spec.ts``
- ``backend/app/api/routes/notifications.py``
- ``backend/tests/api/routes/test_notifications.py``

## Verification

cd frontend && bun run lint && bun run build && bunx playwright test --project=chromium m005-oaptsz-notifications.spec.ts m005-oaptsz-notifications-preferences.spec.ts && bunx playwright test --project=mobile-chrome m005-oaptsz-mobile-audit.spec.ts && cd ../backend && set -a && source ../.env && set +a && uv run pytest tests/api/routes/test_notifications.py -x -k 'redact or preference'

## Observability Impact

Signals added: `notifications.preference_updated user_id=<uuid> event_type=<type> in_app=<bool> push=<bool>` from PUT /preferences in the API layer (T02 scaffolded; T05 finalizes). Test endpoint extension emits `notifications.test_triggered actor_id=<uuid> target_user_id=<uuid> kind=<kind>` so the contract test's preference-off scenario is grep-stable in backend logs. How a future agent inspects this: `psql -c "SELECT user_id,event_type,in_app,push FROM notification_preferences WHERE workflow_id IS NULL ORDER BY updated_at DESC LIMIT 20"` shows the latest preference state. Failure state exposed: a failed PUT surfaces in React Query error state, the optimistic Switch reverts, and a Sonner toast 'Failed to save preference' renders.
