import { useMutation } from "@tanstack/react-query"
import { FolderOpen } from "lucide-react"
import { toast } from "sonner"

import { type ApiError, ProjectsService } from "@/client"
import { LoadingButton } from "@/components/ui/loading-button"

type Props = {
  projectId: string
}

type OpenErrorBody = {
  detail?: string
  reason?: string
}

/** Pull the orchestrator-derived `{detail, reason}` payload off an ApiError
 * verbatim so the operator-readable discriminator surfaces in the toast
 * description without needing DevTools. Closes the operator UX gap from
 * S04 (orchestrator log was the only place the discriminator lived). */
function extractOpenError(err: unknown): {
  status?: number
  detail?: string
  reason?: string
} {
  const apiErr = err as ApiError | undefined
  const status = apiErr?.status
  const body = apiErr?.body as OpenErrorBody | undefined
  if (body && typeof body === "object") {
    const { detail, reason } = body
    return {
      status,
      detail: typeof detail === "string" ? detail : undefined,
      reason: typeof reason === "string" ? reason : undefined,
    }
  }
  return { status, detail: apiErr?.message }
}

const OpenProjectButton = ({ projectId }: Props) => {
  const mutation = useMutation({
    mutationFn: () => ProjectsService.openProject({ projectId }),
    onSuccess: () => {
      toast.success("Project opened in your workspace")
    },
    onError: (err) => {
      const { status, detail, reason } = extractOpenError(err)
      // 503 orchestrator_unavailable — operator-friendly retry copy.
      if (status === 503) {
        toast.error(
          "Orchestrator is unreachable — please try again in a moment",
        )
        return
      }
      // 502 from the chain carries `{detail, reason}` from the failing hop
      // (github_clone_failed, user_clone_failed with reason=user_clone_exit_<code>,
      // clone_credential_leak — the S04 contract). Surface BOTH so the
      // operator sees the discriminator without opening DevTools.
      const description = reason
        ? `${detail ?? "open_failed"} (reason: ${reason})`
        : (detail ?? "Unknown error")
      toast.error("Failed to open project", { description })
    },
  })

  return (
    <LoadingButton
      type="button"
      size="sm"
      variant="default"
      data-testid={`project-open-button-${projectId}`}
      loading={mutation.isPending}
      onClick={() => {
        if (mutation.isPending) return
        mutation.mutate()
      }}
    >
      <FolderOpen className="mr-2 h-4 w-4" />
      Open
    </LoadingButton>
  )
}

export default OpenProjectButton
