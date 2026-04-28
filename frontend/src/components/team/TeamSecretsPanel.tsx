import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { KeyRound, Lock } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import {
  type ApiError,
  type TeamSecretStatus,
  TeamSecretsService,
} from "@/client"
import {
  REGISTERED_TEAM_SECRET_KEYS,
  type RegisteredTeamSecretKey,
  teamSecretsQueryKey,
  teamSecretsQueryOptions,
} from "@/api/teamSecrets"
import PasteSecretDialog from "@/components/team/PasteSecretDialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { LoadingButton } from "@/components/ui/loading-button"
import { Skeleton } from "@/components/ui/skeleton"

type Props = {
  teamId: string
  callerIsAdmin: boolean
}

type DialogState =
  | { kind: "none" }
  | { kind: "paste"; key: RegisteredTeamSecretKey; hasValue: boolean }
  | { kind: "delete"; key: RegisteredTeamSecretKey }

/** Human-friendly label for each registered key. The key string itself is
 * a stable identifier; the title is what the operator scans. */
const KEY_LABELS: Record<RegisteredTeamSecretKey, string> = {
  claude_api_key: "Claude API key",
  openai_api_key: "OpenAI API key",
}

function extractDetail(err: unknown): string | undefined {
  const apiErr = err as ApiError | undefined
  const body = apiErr?.body as { detail?: unknown; hint?: unknown } | undefined
  const detail = body?.detail
  if (typeof detail === "string") {
    const hint = body?.hint
    if (typeof hint === "string" && hint.length > 0) {
      return `${detail}: ${hint}`
    }
    return detail
  }
  if (Array.isArray(detail) && detail.length > 0) {
    return (detail[0] as { msg?: string })?.msg
  }
  // T03 routes return shaped 400s as `{detail: {detail, key, hint}}` because
  // FastAPI's `HTTPException(detail=<dict>)` nests the operator-readable
  // discriminator one level deep. Surface the inner discriminator (and hint
  // when present) so the operator never needs DevTools to read it.
  if (detail && typeof detail === "object") {
    const inner = detail as { detail?: unknown; hint?: unknown }
    if (typeof inner.detail === "string") {
      if (typeof inner.hint === "string" && inner.hint.length > 0) {
        return `${inner.detail}: ${inner.hint}`
      }
      return inner.detail
    }
  }
  if (apiErr?.message) return apiErr.message
  return undefined
}

function HasValueBadge({ hasValue }: { hasValue: boolean }) {
  return (
    <Badge
      variant={hasValue ? "default" : "secondary"}
      data-testid="team-secret-has-value-badge"
      data-has-value={hasValue}
    >
      {hasValue ? "Set" : "Not set"}
    </Badge>
  )
}

function SecretRow({
  status,
  callerIsAdmin,
  onPaste,
  onDelete,
}: {
  status: TeamSecretStatus
  callerIsAdmin: boolean
  onPaste: (key: RegisteredTeamSecretKey, hasValue: boolean) => void
  onDelete: (key: RegisteredTeamSecretKey) => void
}) {
  const key = status.key as RegisteredTeamSecretKey
  return (
    <li
      data-testid={`team-secret-row-${status.key}`}
      data-key={status.key}
      data-has-value={status.has_value}
      className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex items-center gap-3 min-w-0">
        <Lock
          className="h-4 w-4 shrink-0 text-muted-foreground"
          aria-label="Sensitive — encrypted at rest"
        />
        <div className="flex flex-col min-w-0">
          <span
            className="font-medium truncate"
            data-testid={`team-secret-label-${status.key}`}
          >
            {KEY_LABELS[key] ?? status.key}
          </span>
          <span className="text-muted-foreground text-xs font-mono truncate">
            {status.key}
            {status.updated_at && (
              <>
                {" · updated "}
                {new Date(status.updated_at).toLocaleString()}
              </>
            )}
          </span>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <HasValueBadge hasValue={status.has_value} />
        {callerIsAdmin && (
          <>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => onPaste(key, status.has_value)}
              data-testid={`team-secret-set-button-${status.key}`}
            >
              {status.has_value ? "Replace" : "Set"}
            </Button>
            {status.has_value && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => onDelete(key)}
                data-testid={`team-secret-delete-button-${status.key}`}
              >
                Delete
              </Button>
            )}
          </>
        )}
      </div>
    </li>
  )
}

/** Stable, shape-complete fallback list rendered while the GET resolves
 * (or if the GET errors). Both registered keys appear with `has_value=false`
 * so the panel never collapses to empty. */
function placeholderStatuses(): TeamSecretStatus[] {
  return REGISTERED_TEAM_SECRET_KEYS.map((key) => ({
    key,
    has_value: false,
    sensitive: true,
    updated_at: null,
  }))
}

export function TeamSecretsPanel({ teamId, callerIsAdmin }: Props) {
  const queryClient = useQueryClient()
  const [dialog, setDialog] = useState<DialogState>({ kind: "none" })

  const listQuery = useQuery(teamSecretsQueryOptions(teamId))

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: teamSecretsQueryKey(teamId) })
  }

  const putMutation = useMutation({
    mutationFn: (vars: { key: RegisteredTeamSecretKey; value: string }) =>
      TeamSecretsService.putTeamSecret({
        teamId,
        key: vars.key,
        requestBody: { value: vars.value },
      }),
    onSuccess: (_data, vars) => {
      toast.success("Secret saved", { description: KEY_LABELS[vars.key] })
      setDialog({ kind: "none" })
      invalidate()
    },
    onError: (err) => {
      toast.error("Failed to save secret", {
        description: extractDetail(err) ?? "Unknown error",
      })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (key: RegisteredTeamSecretKey) =>
      TeamSecretsService.deleteTeamSecretRoute({ teamId, key }),
    onSuccess: (_data, key) => {
      toast.success("Secret deleted", { description: KEY_LABELS[key] })
      setDialog({ kind: "none" })
      invalidate()
    },
    onError: (err) => {
      const status = (err as ApiError | undefined)?.status
      // Backend's DELETE is idempotent (404 if already gone) — surface as
      // success so the panel re-renders with has_value=false instead of
      // toasting a confusing error.
      if (status === 404) {
        setDialog({ kind: "none" })
        invalidate()
        return
      }
      toast.error("Failed to delete secret", {
        description: extractDetail(err) ?? "Unknown error",
      })
    },
  })

  if (listQuery.isLoading) {
    return (
      <div
        className="flex flex-col gap-2 rounded-lg border bg-card p-3"
        data-testid="team-secrets-loading"
      >
        {REGISTERED_TEAM_SECRET_KEYS.map((k) => (
          <Skeleton key={k} className="h-12 w-full" />
        ))}
      </div>
    )
  }

  // The backend returns one entry per registered key (T03 contract). On
  // error we still render placeholder rows so the operator sees the panel
  // shape and the error message together rather than a blank container.
  const statuses = listQuery.data ?? placeholderStatuses()
  const listError = listQuery.error as ApiError | null | undefined

  return (
    <div className="flex flex-col gap-3" data-testid="team-secrets-panel">
      <div className="flex items-center gap-2 text-muted-foreground text-sm">
        <KeyRound className="h-4 w-4 shrink-0" />
        <span>
          API keys for the AI assistants this team uses. Encrypted at rest;
          values are never returned by the API.
        </span>
      </div>

      {listError && (
        <Card className="border-destructive/50 bg-destructive/5 p-3 text-sm">
          <p className="font-medium">Could not load AI credentials</p>
          <p className="text-muted-foreground text-xs">
            {extractDetail(listError) ?? "Unknown error"}
          </p>
        </Card>
      )}

      <ul
        className="flex flex-col divide-y rounded-lg border bg-card"
        data-testid="team-secrets-list"
      >
        {statuses.map((s) => (
          <SecretRow
            key={s.key}
            status={s}
            callerIsAdmin={callerIsAdmin}
            onPaste={(key, hasValue) =>
              setDialog({ kind: "paste", key, hasValue })
            }
            onDelete={(key) => setDialog({ kind: "delete", key })}
          />
        ))}
      </ul>

      {dialog.kind === "paste" && (
        <PasteSecretDialog
          secretKey={dialog.key}
          hasValue={dialog.hasValue}
          open
          onOpenChange={(next) => {
            if (!next && !putMutation.isPending) {
              setDialog({ kind: "none" })
            }
          }}
          onSubmit={(value) =>
            putMutation.mutate({ key: dialog.key, value })
          }
          isPending={putMutation.isPending}
          serverError={
            putMutation.isError
              ? (extractDetail(putMutation.error) ?? null)
              : null
          }
        />
      )}

      {dialog.kind === "delete" && (
        <Dialog
          open
          onOpenChange={(next) => {
            if (!next && !deleteMutation.isPending) {
              setDialog({ kind: "none" })
            }
          }}
        >
          <DialogContent
            data-testid={`team-secret-delete-dialog-${dialog.key}`}
            className="sm:max-w-md"
          >
            <DialogHeader>
              <DialogTitle>Delete {KEY_LABELS[dialog.key]}?</DialogTitle>
              <DialogDescription>
                The encrypted value is removed immediately. Any team workflow
                that needs this key will fail until you set it again.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <DialogClose asChild>
                <Button
                  type="button"
                  variant="outline"
                  disabled={deleteMutation.isPending}
                >
                  Cancel
                </Button>
              </DialogClose>
              <LoadingButton
                type="button"
                variant="destructive"
                loading={deleteMutation.isPending}
                onClick={() => deleteMutation.mutate(dialog.key)}
                data-testid={`team-secret-delete-confirm-${dialog.key}`}
              >
                Delete
              </LoadingButton>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </div>
  )
}

export default TeamSecretsPanel
