// M005-sqm8et/S03/T05 — Workflow list page
// Lists all team workflows where system_owned=false, with admin-only
// Create / Edit / Delete actions.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import { Plus, Pencil, Trash2, Workflow } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"
import { z } from "zod"

import type { ApiError, WorkflowPublic } from "@/client"
import { WorkflowsService } from "@/client"
import { teamWorkflowsQueryKey } from "@/api/workflows"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"

const searchSchema = z.object({
  teamId: z.string().catch(""),
  admin: z.union([z.boolean(), z.string()]).optional().catch(undefined),
})

export const Route = createFileRoute("/_layout/workflows")({
  validateSearch: (s) => searchSchema.parse(s),
  component: WorkflowsListPage,
  head: () => ({
    meta: [{ title: "Workflows" }],
  }),
})

function extractDetail(err: unknown): string | undefined {
  const apiErr = err as ApiError | undefined
  const body = apiErr?.body as { detail?: unknown } | undefined
  const detail = body?.detail
  if (typeof detail === "string") return detail
  if (detail && typeof detail === "object") {
    const inner = detail as { detail?: unknown }
    if (typeof inner.detail === "string") return inner.detail
  }
  if (Array.isArray(detail) && detail.length > 0) {
    return (detail[0] as { msg?: string })?.msg
  }
  return (err as Error)?.message
}

function WorkflowRow({
  workflow,
  teamId,
  callerIsAdmin,
}: {
  workflow: WorkflowPublic
  teamId: string
  callerIsAdmin: boolean
}) {
  const qc = useQueryClient()
  const [confirmDelete, setConfirmDelete] = useState(false)

  const deleteMutation = useMutation({
    mutationFn: () =>
      WorkflowsService.deleteWorkflow({ workflowId: workflow.id }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: teamWorkflowsQueryKey(teamId) })
      toast.success(`Deleted "${workflow.name}"`)
    },
    onError: (err) => {
      toast.error("Delete failed", {
        description: extractDetail(err) ?? "Unknown error",
      })
    },
  })

  if (confirmDelete) {
    return (
      <li
        className="flex flex-wrap items-center gap-3 p-4"
        data-testid={`workflow-row-${workflow.id}`}
      >
        <span className="text-sm">
          Delete <strong>{workflow.name}</strong>?
        </span>
        <Button
          size="sm"
          variant="destructive"
          data-testid={`workflow-delete-confirm-${workflow.id}`}
          onClick={() => deleteMutation.mutate()}
          disabled={deleteMutation.isPending}
        >
          Delete
        </Button>
        <Button
          size="sm"
          variant="outline"
          data-testid={`workflow-delete-cancel-${workflow.id}`}
          onClick={() => setConfirmDelete(false)}
          disabled={deleteMutation.isPending}
        >
          Cancel
        </Button>
      </li>
    )
  }

  return (
    <li
      className="flex flex-wrap items-center gap-3 p-4"
      data-testid={`workflow-row-${workflow.id}`}
    >
      <span
        className="font-medium flex-1 min-w-0 truncate"
        data-testid={`workflow-name-${workflow.id}`}
      >
        {workflow.name}
      </span>
      <Badge variant="outline" data-testid={`workflow-scope-${workflow.id}`}>
        {workflow.scope}
      </Badge>
      {callerIsAdmin && (
        <div className="flex gap-2">
          <Button
            asChild
            size="sm"
            variant="outline"
            data-testid={`workflow-edit-${workflow.id}`}
          >
            <Link
              to="/workflows/$workflowId"
              params={{ workflowId: workflow.id }}
              search={{ teamId, admin: callerIsAdmin ? "true" : undefined }}
            >
              <Pencil className="h-3.5 w-3.5" />
              Edit
            </Link>
          </Button>
          <Button
            size="sm"
            variant="outline"
            data-testid={`workflow-delete-${workflow.id}`}
            onClick={() => setConfirmDelete(true)}
          >
            <Trash2 className="h-3.5 w-3.5" />
            Delete
          </Button>
        </div>
      )}
    </li>
  )
}

function WorkflowsListContent() {
  const navigate = useNavigate()
  const { teamId, admin } = Route.useSearch()
  const callerIsAdmin = admin === true || admin === "true"

  const workflowsQuery = useQuery({
    queryKey: teamWorkflowsQueryKey(teamId),
    queryFn: async () => {
      const res = await WorkflowsService.listTeamWorkflows({ teamId })
      return res
    },
    enabled: Boolean(teamId),
  })

  const userWorkflows =
    workflowsQuery.data?.data.filter((w) => !w.system_owned) ?? []

  return (
    <div className="flex flex-col gap-4" data-testid="workflows-list-page">
      <div className="flex items-center justify-between gap-2">
        <h1 className="text-2xl font-bold tracking-tight">Workflows</h1>
        {callerIsAdmin && (
          <Button
            data-testid="workflow-create-button"
            onClick={() =>
              navigate({
                to: "/workflows/$workflowId",
                params: { workflowId: "new" },
                search: { teamId, admin: "true" },
              })
            }
          >
            <Plus className="h-4 w-4" />
            New workflow
          </Button>
        )}
      </div>

      {workflowsQuery.isLoading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : userWorkflows.length === 0 ? (
        <Card
          className="flex flex-col items-center justify-center text-center py-12 px-6 gap-3"
          data-testid="workflows-empty"
        >
          <Workflow className="h-8 w-8 text-muted-foreground" />
          <h2 className="text-base font-semibold">No custom workflows yet</h2>
          {callerIsAdmin && (
            <p className="text-muted-foreground text-sm max-w-sm">
              Create a workflow to add custom buttons to the team dashboard.
            </p>
          )}
        </Card>
      ) : (
        <ul
          className="flex flex-col divide-y rounded-lg border bg-card"
          data-testid="workflows-list"
        >
          {userWorkflows.map((wf) => (
            <WorkflowRow
              key={wf.id}
              workflow={wf}
              teamId={teamId}
              callerIsAdmin={callerIsAdmin}
            />
          ))}
        </ul>
      )}
    </div>
  )
}

function WorkflowsListPage() {
  return <WorkflowsListContent />
}
