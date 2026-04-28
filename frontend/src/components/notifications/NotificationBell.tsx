import { Bell } from "lucide-react"
import { useEffect, useState } from "react"

import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useNotificationsPolling } from "@/hooks/useNotificationsPolling"
import { cn } from "@/lib/utils"

import { NotificationPanel } from "./NotificationPanel"

function devtoolsEnabled(): boolean {
  if (typeof window === "undefined") return false
  return new URLSearchParams(window.location.search).has("devtools")
}

function badgeText(count: number): string {
  if (count <= 0) return ""
  if (count > 99) return "99+"
  return String(count)
}

export function NotificationBell() {
  const [open, setOpen] = useState(false)
  // Always poll the unread count (independent of panel open state) so the
  // badge stays current even when the user has not opened the panel.
  const { unreadCount } = useNotificationsPolling({ unreadOnly: false })

  useEffect(() => {
    if (!devtoolsEnabled()) return
    console.info(
      open ? "notifications.panel.open" : "notifications.panel.close",
    )
  }, [open])

  const label =
    unreadCount > 0 ? `Notifications, ${unreadCount} unread` : "Notifications"
  const showBadge = unreadCount > 0
  const text = badgeText(unreadCount)

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          data-testid="notification-bell"
          aria-label={label}
          className="relative"
        >
          <Bell className="size-5" />
          {showBadge ? (
            <span
              data-testid="notification-bell-badge"
              className={cn(
                "pointer-events-none absolute -top-0.5 -right-0.5 inline-flex min-w-4 items-center justify-center rounded-full bg-destructive px-1 text-[10px] font-semibold leading-4 text-white",
              )}
            >
              {text}
            </span>
          ) : null}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        sideOffset={8}
        className="w-96 p-0"
        data-testid="notification-dropdown"
      >
        <NotificationPanel open={open} />
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export default NotificationBell
