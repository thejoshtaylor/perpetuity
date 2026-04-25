import { createFileRoute } from "@tanstack/react-router"

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

function TeamDetail() {
  const { teamId } = Route.useParams()
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-bold tracking-tight">Team</h1>
      <p
        className="text-muted-foreground"
        data-testid="team-detail-placeholder"
      >
        Team detail view for <code className="font-mono text-sm">{teamId}</code>{" "}
        lands here in T04 (members list, invite UI, promote/demote/remove).
      </p>
    </div>
  )
}
