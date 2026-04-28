import type { QueryClient } from "@tanstack/react-query"
import { redirect } from "@tanstack/react-router"

import { TeamsService, type TeamWithRole, UsersService } from "@/client"

type GuardContext = {
  context: { queryClient: QueryClient }
}

export async function requireSystemAdmin({ context }: GuardContext) {
  const user = await context.queryClient.ensureQueryData({
    queryKey: ["currentUser"],
    queryFn: UsersService.readUserMe,
  })
  if (user.role !== "system_admin") {
    throw redirect({ to: "/" })
  }
}

type ReadTeamsEnvelope = { data: TeamWithRole[]; count: number }

export async function requireTeamAdmin({
  context,
  params,
}: GuardContext & { params: { teamId: string } }) {
  const envelope = await context.queryClient.ensureQueryData({
    queryKey: ["teams"] as const,
    queryFn: async (): Promise<ReadTeamsEnvelope> => {
      const res = await TeamsService.readTeams()
      return res as unknown as ReadTeamsEnvelope
    },
  })
  const team = envelope.data.find((t) => t.id === params.teamId)
  if (!team) {
    throw redirect({ to: "/teams" })
  }
  if (team.role !== "admin") {
    throw redirect({
      to: "/teams/$teamId",
      params: { teamId: params.teamId },
    })
  }
}
