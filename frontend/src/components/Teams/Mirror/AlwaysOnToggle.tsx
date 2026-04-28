import { useMutation } from "@tanstack/react-query"
import { useState } from "react"
import { toast } from "sonner"

import { type ApiError, TeamsService } from "@/client"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"

type Props = {
  teamId: string
  /** Initial value. The team list endpoint does not yet expose a
   * `mirror.always_on` field, so callers default to `false` — the PATCH
   * response gives us the canonical state. The backend auto-creates the
   * mirror row with a placeholder volume_path on first PATCH (MEM269), so
   * even unstarted mirrors can be pre-toggled. */
  initialAlwaysOn?: boolean
}

function extractDetail(err: unknown): string | undefined {
  const apiErr = err as ApiError | undefined
  const body = apiErr?.body as { detail?: unknown } | undefined
  const detail = body?.detail
  if (typeof detail === "string") return detail
  if (Array.isArray(detail) && detail.length > 0) {
    return (detail[0] as { msg?: string })?.msg
  }
  if (apiErr?.message) return apiErr.message
  return undefined
}

const AlwaysOnToggle = ({ teamId, initialAlwaysOn = false }: Props) => {
  const [alwaysOn, setAlwaysOn] = useState<boolean>(initialAlwaysOn)

  const mutation = useMutation({
    mutationFn: (next: boolean) =>
      TeamsService.updateTeamMirror({
        teamId,
        requestBody: { always_on: next },
      }),
    onMutate: (next) => {
      // Optimistic flip — the previous value is the rollback target.
      const prev = alwaysOn
      setAlwaysOn(next)
      return { prev }
    },
    onSuccess: (data) => {
      // Re-anchor to the server-confirmed value (idempotent — same value
      // twice returns 200 with no warning per the endpoint contract).
      setAlwaysOn(data.always_on)
      toast.success(
        data.always_on
          ? "Mirror always-on enabled"
          : "Mirror always-on disabled",
      )
    },
    onError: (err, _next, ctx) => {
      // Rollback to the prior state (failure modes Q5: PATCH 503
      // orchestrator-unavailable etc. — backend doesn't call orch on this
      // path today, but the rollback is the safe shape regardless).
      if (ctx) setAlwaysOn(ctx.prev)
      toast.error("Failed to update mirror", {
        description: extractDetail(err) ?? "Unknown error",
      })
    },
  })

  const handleChange = (next: boolean) => {
    if (mutation.isPending) return
    mutation.mutate(next)
  }

  return (
    <div className="flex items-center justify-between gap-4 rounded-lg border bg-card p-4">
      <div className="flex flex-col gap-1 min-w-0">
        <Label htmlFor="mirror-always-on-toggle" className="font-medium">
          Always-on mirror
        </Label>
        <p className="text-muted-foreground text-xs">
          Keep this team's mirror container running between uses. Disable to let
          the reaper stop it after the idle window.
        </p>
      </div>
      <Switch
        id="mirror-always-on-toggle"
        data-testid="mirror-always-on-toggle"
        data-state-checked={alwaysOn}
        checked={alwaysOn}
        disabled={mutation.isPending}
        onCheckedChange={handleChange}
        aria-label="Toggle mirror always-on"
      />
    </div>
  )
}

export default AlwaysOnToggle
