// M005-sqm8et/S03/T05 — dashboard row listing custom (non-system_owned) workflows
// Each workflow renders as a button. Click opens WorkflowFormDialog when
// form_schema has fields, or dispatches directly otherwise.

import { useMutation, useQuery } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { useState } from "react"
import { toast } from "sonner"

import type { ApiError, WorkflowPublic } from "@/client"
import { WorkflowsService } from "@/client"
import { teamWorkflowsQueryOptions } from "@/api/workflows"
import { WorkflowFormDialog } from "@/components/dashboard/WorkflowFormDialog"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"

type Props = {
  teamId: string
}

function extractDetail(err: unknown): string | undefined {
  const apiErr = err as ApiError | undefined
  const body = apiErr?.body as { detail?: unknown; field?: unknown } | undefined
  const detail = body?.detail
  if (typeof detail === "string") return detail
  if (detail && typeof detail === "object") {
    const inner = detail as { detail?: unknown; field?: unknown }
    if (typeof inner.detail === "string") {
      if (typeof inner.field === "string" && inner.field.length > 0) {
        return `${inner.detail}: ${inner.field}`
      }
      return inner.detail
    }
  }
  if (Array.isArray(detail) && detail.length > 0) {
    return (detail[0] as { msg?: string })?.msg
  }
  if (apiErr?.message) return apiErr.message
  return undefined
}

function hasFormFields(wf: WorkflowPublic): boolean {
  const schema = wf.form_schema
  if (!schema || !Array.isArray((schema as Record<string, unknown>).fields)) return false
  return ((schema as Record<string, unknown>).fields as unknown[]).length > 0
}

export function CustomWorkflowButtons({ teamId }: Props) {
  const navigate = useNavigate()
  const [openDialog, setOpenDialog] = useState<WorkflowPublic | null>(null)
  const [dialogError, setDialogError] = useState<string | null>(null)

  const workflowsQuery = useQuery(teamWorkflowsQueryOptions(teamId))

  const dispatchMutation = useMutation({
    mutationFn: async (vars: {
      workflowId: string
      payload: Record<string, unknown>
    }) =>
      WorkflowsService.dispatchWorkflowRun({
        workflowId: vars.workflowId,
        requestBody: { trigger_payload: vars.payload },
      }),
    onSuccess: (data) => {
      const runId = (data as { run_id: string }).run_id
      setOpenDialog(null)
      navigate({ to: "/runs/$runId", params: { runId } })
    },
    onError: (err) => {
      const msg = extractDetail(err) ?? (err as Error).message ?? "Unknown error"
      setDialogError(msg)
      if (!openDialog) {
        toast.error("Failed to dispatch run", { description: msg })
      }
    },
  })

  if (workflowsQuery.isLoading) {
    return (
      <div
        className="flex flex-wrap gap-2"
        data-testid="custom-workflow-buttons-loading"
      >
        <Skeleton className="h-10 w-32" />
        <Skeleton className="h-10 w-32" />
      </div>
    )
  }

  const userWorkflows =
    workflowsQuery.data?.data.filter((w) => !w.system_owned) ?? []

  if (userWorkflows.length === 0) {
    return null
  }

  const handleClick = (wf: WorkflowPublic) => {
    if (hasFormFields(wf)) {
      setDialogError(null)
      setOpenDialog(wf)
    } else {
      dispatchMutation.mutate({ workflowId: wf.id, payload: {} })
    }
  }

  return (
    <>
      <div
        className="flex flex-wrap gap-2"
        data-testid="custom-workflow-buttons"
      >
        {userWorkflows.map((wf) => (
          <Button
            key={wf.id}
            type="button"
            variant="secondary"
            onClick={() => handleClick(wf)}
            disabled={dispatchMutation.isPending}
            data-testid={`custom-workflow-button-${wf.id}`}
            data-workflow-id={wf.id}
          >
            {wf.name}
          </Button>
        ))}
      </div>

      {openDialog && (
        <WorkflowFormDialog
          workflow={openDialog}
          open
          onOpenChange={(next) => {
            if (!next && !dispatchMutation.isPending) {
              setOpenDialog(null)
            }
          }}
          onSubmit={(payload) =>
            dispatchMutation.mutate({
              workflowId: openDialog.id,
              payload,
            })
          }
          isPending={dispatchMutation.isPending}
          serverError={dialogError}
        />
      )}
    </>
  )
}

export default CustomWorkflowButtons
