import { useMutation, useQuery } from "@tanstack/react-query"
import { useNavigate } from "@tanstack/react-router"
import { Bot, Sparkles } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"
import {
  type DirectAIKind,
  findDirectAIWorkflow,
  teamWorkflowsQueryOptions,
} from "@/api/workflows"
import { type ApiError, WorkflowsService } from "@/client"
import PromptDialog from "@/components/dashboard/PromptDialog"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"

type Props = {
  teamId: string
}

type DialogState = { kind: DirectAIKind } | null

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

/**
 * Dashboard buttons that dispatch the auto-seeded `_direct_claude` /
 * `_direct_codex` system workflows for a team. Workflow ids are looked
 * up in the team's workflow list (the seed runs at team-create time —
 * MEM428), then `POST /api/v1/workflows/{id}/run` fires with the prompt
 * in `trigger_payload`. On 200, navigates to `/runs/{run_id}` where the
 * polled detail page shows the `pending → running → succeeded|failed`
 * transitions.
 *
 * Renders for any caller who can see the team — the parent gates on
 * membership (member or admin). The buttons are inert until the workflow
 * list resolves so the click handler always has a real workflow id.
 */
export function DirectAIButtons({ teamId }: Props) {
  const navigate = useNavigate()
  const [dialog, setDialog] = useState<DialogState>(null)

  const workflowsQuery = useQuery(teamWorkflowsQueryOptions(teamId))

  const dispatchMutation = useMutation({
    mutationFn: async (vars: { kind: DirectAIKind; prompt: string }) => {
      const list = workflowsQuery.data?.data ?? []
      const wf = findDirectAIWorkflow(list, vars.kind)
      if (!wf) {
        // The seed runs at team-create (MEM428); a missing row means the
        // backend boot order broke. Surface the discriminator the operator
        // can search for in compose logs.
        throw new Error(
          `direct_workflow_not_found: ${vars.kind === "claude" ? "_direct_claude" : "_direct_codex"}`,
        )
      }
      return WorkflowsService.dispatchWorkflowRun({
        workflowId: wf.id,
        requestBody: { trigger_payload: { prompt: vars.prompt } },
      })
    },
    onSuccess: (data) => {
      const runId = (data as { run_id: string }).run_id
      setDialog(null)
      navigate({ to: "/runs/$runId", params: { runId } })
    },
    onError: (err) => {
      toast.error("Failed to dispatch run", {
        description:
          extractDetail(err) ?? (err as Error).message ?? "Unknown error",
      })
    },
  })

  if (workflowsQuery.isLoading) {
    return (
      <div
        className="flex flex-col gap-2 sm:flex-row"
        data-testid="direct-ai-buttons-loading"
      >
        <Skeleton className="h-10 w-32" />
        <Skeleton className="h-10 w-32" />
      </div>
    )
  }

  const list = workflowsQuery.data?.data ?? []
  const claudeWf = findDirectAIWorkflow(list, "claude")
  const codexWf = findDirectAIWorkflow(list, "codex")

  return (
    <div
      className="flex flex-col gap-2 sm:flex-row"
      data-testid="direct-ai-buttons"
    >
      <Button
        type="button"
        onClick={() => setDialog({ kind: "claude" })}
        disabled={!claudeWf || dispatchMutation.isPending}
        data-testid="direct-ai-button-claude"
        data-workflow-id={claudeWf?.id ?? ""}
      >
        <Sparkles className="h-4 w-4" />
        Run Claude
      </Button>

      <Button
        type="button"
        variant="secondary"
        onClick={() => setDialog({ kind: "codex" })}
        disabled={!codexWf || dispatchMutation.isPending}
        data-testid="direct-ai-button-codex"
        data-workflow-id={codexWf?.id ?? ""}
      >
        <Bot className="h-4 w-4" />
        Run Codex
      </Button>

      {dialog !== null && (
        <PromptDialog
          kind={dialog.kind}
          open
          onOpenChange={(next) => {
            if (!next && !dispatchMutation.isPending) {
              setDialog(null)
            }
          }}
          onSubmit={(prompt) =>
            dispatchMutation.mutate({ kind: dialog.kind, prompt })
          }
          isPending={dispatchMutation.isPending}
          serverError={
            dispatchMutation.isError
              ? (extractDetail(dispatchMutation.error) ?? null)
              : null
          }
        />
      )}
    </div>
  )
}

export default DirectAIButtons
