import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { toast } from "sonner"

import {
  type NotificationKind,
  type NotificationPreferencePublic,
  NotificationsService,
} from "@/client"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Switch } from "@/components/ui/switch"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

// Human-readable labels per NotificationKind. Order matches the
// NotificationKind enum order on the backend so the rendered table matches
// `GET /notifications/preferences` row-for-row by index.
const KIND_LABELS: Record<NotificationKind, string> = {
  workflow_run_started: "Workflow run started",
  workflow_run_succeeded: "Workflow run succeeded",
  workflow_run_failed: "Workflow run failed",
  workflow_step_completed: "Workflow step completed",
  team_invite_accepted: "Team invite accepted",
  project_created: "Project created",
  system: "System notifications",
}

const PREFS_QUERY_KEY = ["notifications", "preferences"] as const

export function NotificationPreferences() {
  const queryClient = useQueryClient()

  const prefsQuery = useQuery<NotificationPreferencePublic[]>({
    queryKey: PREFS_QUERY_KEY,
    queryFn: () => NotificationsService.listPreferences(),
  })

  // PUT /preferences/{event_type}. On success we re-anchor the cache with
  // the server's updated row immediately (MEM305 pattern), then invalidate
  // so any later open of the panel/tab refetches.
  const updatePref = useMutation<
    NotificationPreferencePublic,
    Error,
    { eventType: NotificationKind; in_app: boolean; push: boolean }
  >({
    mutationFn: ({ eventType, in_app, push }) =>
      NotificationsService.upsertPreference({
        eventType,
        requestBody: { in_app, push },
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData<NotificationPreferencePublic[]>(
        PREFS_QUERY_KEY,
        (prev) => {
          if (!prev) return prev
          return prev.map((row) =>
            row.event_type === updated.event_type ? updated : row,
          )
        },
      )
      void queryClient.invalidateQueries({ queryKey: PREFS_QUERY_KEY })
    },
    onError: () => {
      toast.error("Failed to save preference")
      // Revert optimistic state by refetching fresh server values.
      void queryClient.invalidateQueries({ queryKey: PREFS_QUERY_KEY })
    },
  })

  return (
    <Card data-testid="notification-preferences">
      <CardHeader>
        <CardTitle>Notification preferences (team default)</CardTitle>
        <CardDescription>
          Per-workflow overrides ship in a future milestone; these defaults
          apply when no workflow override exists.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {prefsQuery.isError ? (
          <p className="text-sm text-muted-foreground">
            Failed to load preferences
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-1/2">Event</TableHead>
                <TableHead>In-app</TableHead>
                <TableHead>Push</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(prefsQuery.data ?? []).map((row) => {
                const kind = row.event_type as NotificationKind
                const label = KIND_LABELS[kind] ?? kind
                const switchId = `notification-pref-${kind}`
                return (
                  <TableRow
                    key={kind}
                    data-testid="notification-preference-row"
                    data-event-type={kind}
                  >
                    <TableCell className="font-medium">
                      <label htmlFor={switchId}>{label}</label>
                    </TableCell>
                    <TableCell>
                      <Switch
                        id={switchId}
                        data-testid={`notification-pref-in-app-${kind}`}
                        checked={row.in_app}
                        disabled={updatePref.isPending}
                        onCheckedChange={(checked) => {
                          updatePref.mutate({
                            eventType: kind,
                            in_app: checked,
                            push: row.push,
                          })
                        }}
                        aria-label={`In-app for ${label}`}
                      />
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <Switch
                          disabled
                          checked={false}
                          aria-label={`Push for ${label}, available in S03`}
                        />
                        <span className="text-xs text-muted-foreground">
                          Available in S03
                        </span>
                      </div>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}

export default NotificationPreferences
