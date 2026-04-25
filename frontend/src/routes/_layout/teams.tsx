import { useSuspenseQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { Users } from "lucide-react"
import { Suspense } from "react"

import { TeamsService, type TeamWithRole } from "@/client"
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
      // Backend returns { data: TeamWithRole[], count: number }; the generated
      // SDK types the response as `unknown` because the endpoint's Python
      // return annotation is dict[str, Any]. The runtime shape is stable.
      return res as unknown as ReadTeamsEnvelope
    },
  }
}

export const Route = createFileRoute("/_layout/teams")({
  component: Teams,
  head: () => ({
    meta: [
      {
        title: "Teams - FastAPI Template",
      },
    ],
  }),
})

function RoleBadge({ role }: { role: TeamWithRole["role"] }) {
  return (
    <Badge
      data-testid="role-badge"
      data-role={role}
      variant={role === "admin" ? "default" : "secondary"}
    >
      {role === "admin" ? "Admin" : "Member"}
    </Badge>
  )
}

function TeamCard({ team }: { team: TeamWithRole }) {
  return (
    <Link
      to="/teams/$teamId"
      params={{ teamId: team.id }}
      data-testid="team-card"
      data-team-id={team.id}
      className="block focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-xl"
    >
      <Card className="min-h-20 py-4 px-4 gap-2 hover:bg-accent/30 transition-colors">
        <div className="flex items-center gap-2 min-w-0">
          <h2
            className="text-base font-semibold truncate max-w-[60%]"
            title={team.name}
          >
            {team.name}
          </h2>
          <RoleBadge role={team.role} />
          {team.is_personal && (
            <Badge
              data-testid="personal-badge"
              variant="outline"
              className="ml-auto"
            >
              Personal
            </Badge>
          )}
        </div>
      </Card>
    </Link>
  )
}

function TeamsEmptyState() {
  return (
    <Card className="flex flex-col items-center justify-center text-center py-12 px-6 gap-4">
      <div className="rounded-full bg-muted p-4">
        <Users className="h-8 w-8 text-muted-foreground" />
      </div>
      <h3 className="text-lg font-semibold">No teams yet</h3>
      <p className="text-muted-foreground max-w-sm">
        Create a team to collaborate with others.
      </p>
      <Button data-testid="create-team-button" disabled>
        Create Team
      </Button>
    </Card>
  )
}

function TeamsListContent() {
  const { data } = useSuspenseQuery(getTeamsQueryOptions())

  if (data.data.length === 0) {
    return <TeamsEmptyState />
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      {data.data.map((team) => (
        <TeamCard key={team.id} team={team} />
      ))}
    </div>
  )
}

function TeamsListPending() {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      {[0, 1, 2, 3].map((i) => (
        <Card key={i} className="min-h-20 py-4 px-4 gap-2">
          <div className="flex items-center gap-2">
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-5 w-16 rounded-full" />
          </div>
        </Card>
      ))}
    </div>
  )
}

function TeamsList() {
  return (
    <Suspense fallback={<TeamsListPending />}>
      <TeamsListContent />
    </Suspense>
  )
}

function Teams() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold tracking-tight">Teams</h1>
        <p className="text-muted-foreground">
          Welcome back, nice to see you again!!!
        </p>
      </div>
      <TeamsList />
    </div>
  )
}
