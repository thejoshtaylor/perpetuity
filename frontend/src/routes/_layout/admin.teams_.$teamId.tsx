import {
  type QueryClient,
  useQueryClient,
  useSuspenseQuery,
} from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { ChevronLeft } from "lucide-react"
import { Suspense } from "react"

import { AdminService, type TeamMemberPublic } from "@/client"
import type { AdminTeamRow } from "@/components/Admin/AdminTeamsColumns"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { requireSystemAdmin } from "@/lib/auth-guards"
import { getInitials } from "@/utils"

type AdminTeamMembersEnvelope = {
  data: TeamMemberPublic[]
  count: number
}

type AdminTeamsCacheEntry = {
  data: AdminTeamRow[]
  count: number
}

function membersQueryOptions(teamId: string) {
  return {
    queryKey: ["admin", "team", teamId, "members"] as const,
    queryFn: async (): Promise<AdminTeamMembersEnvelope> => {
      const res = await AdminService.readAdminTeamMembers({ teamId })
      return res as unknown as AdminTeamMembersEnvelope
    },
  }
}

export const Route = createFileRoute("/_layout/admin/teams_/$teamId")({
  component: AdminTeamMembers,
  beforeLoad: requireSystemAdmin,
  head: () => ({
    meta: [
      {
        title: "Team Members - FastAPI Template",
      },
    ],
  }),
})

function findCachedTeamName(
  queryClient: QueryClient,
  teamId: string,
): string | null {
  const entries = queryClient.getQueriesData<AdminTeamsCacheEntry>({
    queryKey: ["admin", "teams"],
  })
  for (const [, value] of entries) {
    const match = value?.data?.find((t) => t.id === teamId)
    if (match) return match.name
  }
  return null
}

function MemberRow({ member }: { member: TeamMemberPublic }) {
  const display = member.full_name?.trim() || member.email
  return (
    <li
      data-testid="admin-member-row"
      data-user-id={member.user_id}
      className="flex flex-col gap-2 p-3 sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex items-center gap-3 min-w-0">
        <Avatar>
          <AvatarFallback>{getInitials(display)}</AvatarFallback>
        </Avatar>
        <div className="flex flex-col min-w-0">
          <span
            className="font-medium truncate"
            data-testid="admin-member-name"
            title={display}
          >
            {display}
          </span>
          {member.full_name && (
            <span
              className="text-muted-foreground text-xs truncate"
              title={member.email}
            >
              {member.email}
            </span>
          )}
        </div>
      </div>

      <Badge
        data-testid="admin-member-role-badge"
        data-role={member.role}
        variant={member.role === "admin" ? "default" : "secondary"}
      >
        {member.role === "admin" ? "Admin" : "Member"}
      </Badge>
    </li>
  )
}

function AdminTeamMembersContent() {
  const { teamId } = Route.useParams()
  const queryClient = useQueryClient()
  const cachedName = findCachedTeamName(queryClient, teamId)
  const { data } = useSuspenseQuery(membersQueryOptions(teamId))

  return (
    <div className="flex flex-col gap-4" data-testid="admin-team-detail">
      <div>
        <Button asChild variant="ghost" size="sm" className="-ml-2">
          <Link to="/admin/teams" search={{ skip: 0, limit: 20 }}>
            <ChevronLeft className="h-4 w-4" />
            Back to teams
          </Link>
        </Button>
      </div>

      <div className="flex flex-col gap-1">
        <h1
          className="text-2xl font-bold tracking-tight"
          data-testid="admin-team-name"
        >
          Team members
        </h1>
        <p
          className="text-muted-foreground text-sm font-mono"
          data-testid="admin-team-identifier"
        >
          {cachedName ?? teamId}
        </p>
      </div>

      {data.count === 0 ? (
        <Card
          data-testid="admin-members-empty"
          className="flex flex-col items-center justify-center text-center py-12 px-6 gap-2"
        >
          <h3 className="text-lg font-semibold">No members yet.</h3>
          <p className="text-muted-foreground max-w-sm">
            This team has no members.
          </p>
        </Card>
      ) : (
        <ul
          className="flex flex-col divide-y rounded-lg border bg-card"
          data-testid="admin-members-list"
        >
          {data.data.map((m) => (
            <MemberRow key={m.user_id} member={m} />
          ))}
        </ul>
      )}
    </div>
  )
}

function AdminTeamMembersPending() {
  return (
    <div
      className="flex flex-col gap-2 rounded-lg border p-3"
      data-testid="admin-members-loading"
    >
      {Array.from({ length: 3 }).map((_, i) => (
        <div
          key={`admin-members-skeleton-${i + 1}`}
          className="flex items-center gap-3"
        >
          <Skeleton className="h-8 w-8 rounded-full" />
          <Skeleton className="h-4 w-40" />
        </div>
      ))}
    </div>
  )
}

function AdminTeamMembers() {
  return (
    <Suspense fallback={<AdminTeamMembersPending />}>
      <AdminTeamMembersContent />
    </Suspense>
  )
}
