import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"

import { NotificationsService } from "@/client"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { useNotificationsPolling } from "@/hooks/useNotificationsPolling"

import { NotificationItem } from "./NotificationItem"

interface NotificationPanelProps {
  open: boolean
}

export function NotificationPanel({ open }: NotificationPanelProps) {
  const [unreadOnly, setUnreadOnly] = useState(false)
  const queryClient = useQueryClient()

  const { items, unreadCount, isError, refetch } = useNotificationsPolling({
    unreadOnly,
    enabled: open,
  })

  const markAllRead = useMutation({
    mutationFn: () => NotificationsService.markAllRead(),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["notifications"] })
    },
  })

  return (
    <div data-testid="notification-panel" className="flex w-full flex-col">
      <header className="flex items-center justify-between gap-2 border-b px-4 py-2">
        <h2 className="text-sm font-semibold">Notifications</h2>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Switch
            id="notifications-unread-only"
            data-testid="notifications-unread-only"
            checked={unreadOnly}
            onCheckedChange={setUnreadOnly}
            aria-label="Show only unread"
          />
          <label htmlFor="notifications-unread-only" className="cursor-pointer">
            Unread only
          </label>
        </div>
      </header>
      {unreadCount > 0 ? (
        <div className="flex items-center justify-between gap-2 border-b bg-muted/40 px-4 py-1.5 text-xs text-muted-foreground">
          <span>
            {unreadCount} unread notification{unreadCount === 1 ? "" : "s"}
          </span>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            data-testid="notifications-mark-all-read"
            onClick={() => markAllRead.mutate()}
            disabled={markAllRead.isPending}
          >
            Mark all read
          </Button>
        </div>
      ) : null}

      <div
        className="max-h-96 overflow-y-auto"
        data-testid="notifications-list"
      >
        {isError ? (
          <div className="flex flex-col items-center gap-2 px-4 py-8 text-center text-sm text-muted-foreground">
            <p>Failed to load notifications</p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => refetch()}
            >
              Retry
            </Button>
          </div>
        ) : items.length === 0 ? (
          <p
            data-testid="notifications-empty"
            className="px-4 py-8 text-center text-sm text-muted-foreground"
          >
            No notifications yet
          </p>
        ) : (
          <ul className="flex flex-col divide-y">
            {items.map((n) => (
              <li key={n.id}>
                <NotificationItem notification={n} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

export default NotificationPanel
