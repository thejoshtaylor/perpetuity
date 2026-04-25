import { useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { Suspense } from "react"

import { TeamsService, type TeamWithRole } from "@/client"
import InviteButton from "@/components/Teams/InviteButton"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"

type ReadTeamsEnvelope = { data: TeamWithRole[]; count: number }

function getTeamsQueryOptions() {
  return {
    queryKey: ["teams"] as const,
    queryFn: async (): Promise<ReadTeamsEnvelope> => {
      const res = await TeamsService.readTeams()
      return res as unknown as ReadTeamsEnvelope
    },
  }
}

export const Route = createFileRoute("/_layout/teams/$teamId")({
  component: TeamDetail,
  head: () => ({
    meta: [
      {
        title: "Team - FastAPI Template",
      },
    ],
  }),
})

function TeamDetailContent() {
  const { teamId } = Route.useParams()
  const { data } = useSuspenseQuery(getTeamsQueryOptions())
  const team = data.data.find((t) => t.id === teamId)

  if (!team) {
    return (
      <Card
        data-testid="team-not-found"
        className="flex flex-col items-center justify-center text-center py-12 px-6 gap-3"
      >
        <h2 className="text-lg font-semibold">Team not found</h2>
        <p className="text-muted-foreground max-w-sm">
          This team doesn't exist or you're no longer a member.
        </p>
        <Button asChild variant="outline">
          <Link to="/teams">Back to teams</Link>
        </Button>
      </Card>
    )
  }

  const canInvite = team.role === "admin" && !team.is_personal

  return (
    <div className="flex flex-col gap-4" data-testid="team-detail">
      <div className="flex flex-wrap items-center gap-2">
        <h1
          className="text-2xl font-bold tracking-tight max-w-full truncate"
          title={team.name}
          data-testid="team-name"
        >
          {team.name}
        </h1>
        <Badge
          data-testid="role-badge"
          data-role={team.role}
          variant={team.role === "admin" ? "default" : "secondary"}
        >
          {team.role === "admin" ? "Admin" : "Member"}
        </Badge>
        {team.is_personal && (
          <Badge data-testid="personal-badge" variant="outline">
            Personal
          </Badge>
        )}
      </div>

      {canInvite && (
        <section
          className="flex flex-col gap-2"
          data-testid="invite-section"
          aria-label="Invite teammates"
        >
          <h2 className="text-sm font-medium">Invite teammates</h2>
          <InviteButton teamId={team.id} />
        </section>
      )}

      <p className="text-muted-foreground text-sm">
        Members list and promote/demote/remove controls land in T04.
      </p>
    </div>
  )
}

function TeamDetailPending() {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <Skeleton className="h-7 w-48" />
        <Skeleton className="h-5 w-16 rounded-full" />
      </div>
      <Skeleton className="h-9 w-40" />
    </div>
  )
}

function TeamDetail() {
  return (
    <Suspense fallback={<TeamDetailPending />}>
      <TeamDetailContent />
    </Suspense>
  )
}
