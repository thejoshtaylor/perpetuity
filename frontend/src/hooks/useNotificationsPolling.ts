import { useQuery } from "@tanstack/react-query"
import { useEffect, useRef } from "react"
import type {
  NotificationPublic,
  NotificationsPublic,
  NotificationUnreadCount,
} from "@/client"
import { NotificationsService } from "@/client"

const POLL_MS = 5000

function devtoolsEnabled(): boolean {
  if (typeof window === "undefined") return false
  return new URLSearchParams(window.location.search).has("devtools")
}

export interface UseNotificationsPollingResult {
  items: NotificationPublic[]
  total: number
  unreadCount: number
  isFetching: boolean
  isError: boolean
  refetch: () => void
}

export function useNotificationsPolling(options: {
  unreadOnly: boolean
  enabled?: boolean
}): UseNotificationsPollingResult {
  const { unreadOnly, enabled = true } = options

  const listQuery = useQuery<NotificationsPublic>({
    queryKey: ["notifications", { unreadOnly }],
    queryFn: () =>
      NotificationsService.listNotifications({
        unreadOnly,
        limit: 50,
      }),
    refetchInterval: 5000,
    refetchIntervalInBackground: false,
    enabled,
  })

  const countQuery = useQuery<NotificationUnreadCount>({
    queryKey: ["notifications", "unreadCount"],
    queryFn: () => NotificationsService.unreadCount(),
    refetchInterval: 5000,
    refetchIntervalInBackground: false,
    enabled,
  })

  const tickRef = useRef(0)
  useEffect(() => {
    if (!listQuery.isFetching && !countQuery.isFetching) return
    tickRef.current += 1
    if (devtoolsEnabled()) {
      // Observability hook (MEM341): only emit when ?devtools=1 so default
      // dev runs and Playwright audits don't see it.
      console.info(`notifications.poll.tick count=${tickRef.current}`)
    }
  }, [listQuery.isFetching, countQuery.isFetching])

  return {
    items: listQuery.data?.data ?? [],
    total: listQuery.data?.count ?? 0,
    unreadCount: countQuery.data?.count ?? 0,
    isFetching: listQuery.isFetching || countQuery.isFetching,
    isError: listQuery.isError || countQuery.isError,
    refetch: () => {
      void listQuery.refetch()
      void countQuery.refetch()
    },
  }
}

export const NOTIFICATIONS_POLL_MS = POLL_MS
