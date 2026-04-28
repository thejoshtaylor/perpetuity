import { useMutation, useQueryClient } from "@tanstack/react-query"
import {
  Bell,
  CheckCircle2,
  CircleAlert,
  CircleX,
  FolderGit2,
  type LucideIcon,
  Play,
  Users,
} from "lucide-react"
import type { ComponentType } from "react"
import type { NotificationKind, NotificationPublic } from "@/client"
import { NotificationsService } from "@/client"
import { cn } from "@/lib/utils"

const KIND_ICON: Record<NotificationKind, LucideIcon> = {
  team_invite_accepted: Users,
  project_created: FolderGit2,
  workflow_run_started: Play,
  workflow_run_succeeded: CheckCircle2,
  workflow_run_failed: CircleX,
  workflow_step_completed: CircleAlert,
  system: Bell,
}

function titleFor(n: NotificationPublic): string {
  const p = n.payload ?? {}
  switch (n.kind) {
    case "team_invite_accepted":
      return `Joined ${(p.team_name as string) ?? "a team"}`
    case "project_created":
      return `New project ${(p.project_name as string) ?? ""}`.trim()
    case "workflow_run_started":
      return `Workflow run started${p.workflow_name ? ` — ${p.workflow_name}` : ""}`
    case "workflow_run_succeeded":
      return `Workflow run succeeded${p.workflow_name ? ` — ${p.workflow_name}` : ""}`
    case "workflow_run_failed":
      return `Workflow run failed${p.workflow_name ? ` — ${p.workflow_name}` : ""}`
    case "workflow_step_completed":
      return `Workflow step completed${p.step_name ? ` — ${p.step_name}` : ""}`
    case "system": {
      const message = p.message as string | undefined
      return message && message.length > 0 ? message : "System notification"
    }
    default:
      return "Notification"
  }
}

function relativeTime(iso: string | null | undefined): string {
  if (!iso) return ""
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ""
  const deltaSec = Math.max(0, Math.floor((Date.now() - then) / 1000))
  if (deltaSec < 45) return "just now"
  const min = Math.floor(deltaSec / 60)
  if (min < 60) return `${min}m ago`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr}h ago`
  const day = Math.floor(hr / 24)
  return `${day}d ago`
}

interface NotificationItemProps {
  notification: NotificationPublic
}

export function NotificationItem({ notification }: NotificationItemProps) {
  const queryClient = useQueryClient()
  const isUnread = !notification.read_at

  const Icon: ComponentType<{ className?: string }> =
    KIND_ICON[notification.kind] ?? Bell

  const markRead = useMutation({
    mutationFn: () =>
      NotificationsService.markRead({ notificationId: notification.id }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["notifications"] })
    },
  })

  const handleClick = () => {
    if (!isUnread || markRead.isPending) return
    markRead.mutate()
  }

  return (
    <button
      type="button"
      data-testid="notification-item"
      data-kind={notification.kind}
      data-unread={isUnread ? "true" : "false"}
      onClick={handleClick}
      className={cn(
        "flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/40",
        isUnread && "border-l-2 border-primary font-medium",
        !isUnread && "border-l-2 border-transparent",
      )}
    >
      <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm">{titleFor(notification)}</p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {relativeTime(notification.created_at)}
        </p>
      </div>
      {isUnread ? (
        <output
          aria-label="unread"
          className="mt-1.5 block size-2 shrink-0 rounded-full bg-primary"
        />
      ) : null}
    </button>
  )
}

export default NotificationItem
