// M005-sqm8et/S03/T05 — workflow editor route (create = workflowId:'new', edit = real uuid)

import { useQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { z } from "zod"

import { WorkflowsService } from "@/client"
import { WorkflowEditor } from "@/components/workflows/WorkflowEditor"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"

const searchSchema = z.object({
  teamId: z.string().catch(""),
  admin: z.union([z.boolean(), z.string()]).optional().catch(undefined),
})

export const Route = createFileRoute("/_layout/workflows_/$workflowId")({
  validateSearch: (s) => searchSchema.parse(s),
  component: WorkflowEditorPage,
  head: () => ({
    meta: [{ title: "Workflow editor" }],
  }),
})

function WorkflowEditorPage() {
  const { workflowId } = Route.useParams()
  const { teamId, admin } = Route.useSearch()
  const isCreate = workflowId === "new"

  const workflowQuery = useQuery({
    queryKey: ["workflow", workflowId],
    queryFn: () => WorkflowsService.getWorkflow({ workflowId }),
    enabled: !isCreate,
  })

  return (
    <div className="flex flex-col gap-4" data-testid="workflow-editor-page">
      <div className="flex items-center gap-2">
        <Button asChild variant="ghost" size="sm">
          <Link to="/workflows" search={{ teamId, admin }}>
            ← Workflows
          </Link>
        </Button>
        <h1 className="text-2xl font-bold tracking-tight">
          {isCreate ? "New workflow" : "Edit workflow"}
        </h1>
      </div>

      {!isCreate && workflowQuery.isLoading ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-10 w-48" />
        </div>
      ) : !isCreate && (workflowQuery.isError || !workflowQuery.data) ? (
        <p className="text-destructive text-sm">Failed to load workflow.</p>
      ) : (
        <WorkflowEditor
          teamId={teamId}
          existingWorkflow={isCreate ? undefined : workflowQuery.data}
        />
      )}
    </div>
  )
}
