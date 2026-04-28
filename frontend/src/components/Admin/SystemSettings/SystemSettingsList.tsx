import {
  useMutation,
  useQueryClient,
  useSuspenseQuery,
} from "@tanstack/react-query"
import type { AxiosError } from "axios"
import { Lock, Sparkles } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { AdminService, type ApiError, type SystemSettingPublic } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"

import { GenerateConfirmDialog } from "./GenerateConfirmDialog"
import { OneTimeValueModal } from "./OneTimeValueModal"
import { SetSecretDialog } from "./SetSecretDialog"

type ListEnvelope = {
  data: SystemSettingPublic[]
  count: number
}

type DialogState =
  | { kind: "none" }
  | { kind: "set"; key: string; hasValue: boolean; variant: "pem" | "string" }
  | { kind: "generate-confirm"; key: string }
  | { kind: "one-time"; key: string; value: string }

/** Set of registered keys that expose a server-side generator. Mirrors the
 * `_VALIDATORS` registry in backend/app/api/routes/admin.py — only
 * github_app_webhook_secret has `generator=_generate_webhook_secret`.
 * github_app_private_key is sensitive but generator-less (operator pastes
 * the PEM via PUT). */
const KEYS_WITH_GENERATOR: ReadonlySet<string> = new Set([
  "github_app_webhook_secret",
])

/** Sensitive keys whose value is a multiline PEM rather than a single-line
 * string. Drives the SetSecretDialog `variant`. */
const PEM_KEYS: ReadonlySet<string> = new Set(["github_app_private_key"])

export function listSystemSettingsQueryOptions() {
  return {
    queryKey: ["admin", "settings"] as const,
    queryFn: async (): Promise<ListEnvelope> => {
      const res = await AdminService.listSystemSettings()
      return res as unknown as ListEnvelope
    },
  }
}

function extractErrorBody(err: unknown): string {
  // Surface the backend's response body verbatim so the operator sees
  // discriminators like `system_settings_decrypt_failed key=<name>` and
  // PUT 422 `reason` fields without having to open DevTools.
  const apiErr = err as ApiError | AxiosError | undefined
  const body = (apiErr as ApiError | undefined)?.body as
    | { detail?: unknown; reason?: string }
    | undefined
  if (body) {
    const reason = body.reason
    const detail = body.detail
    const detailStr =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail) && detail.length > 0
          ? // FastAPI validation array shape
            (detail[0] as { msg?: string })?.msg
          : detail && typeof detail === "object"
            ? JSON.stringify(detail)
            : undefined
    if (detailStr && reason) return `${detailStr} (${reason})`
    if (detailStr) return detailStr
    if (reason) return reason
  }
  if (apiErr?.message) return apiErr.message
  return "Unknown error"
}

function HasValueBadge({ hasValue }: { hasValue: boolean }) {
  return (
    <Badge
      variant={hasValue ? "default" : "secondary"}
      data-testid="system-settings-has-value-badge"
      data-has-value={hasValue}
    >
      {hasValue ? "Set" : "Empty"}
    </Badge>
  )
}

function SettingRow({
  setting,
  onSet,
  onGenerate,
}: {
  setting: SystemSettingPublic
  onSet: (key: string, hasValue: boolean) => void
  onGenerate: (key: string) => void
}) {
  const hasGenerator = KEYS_WITH_GENERATOR.has(setting.key)

  return (
    <li
      data-testid={`system-settings-row-${setting.key}`}
      data-key={setting.key}
      data-sensitive={setting.sensitive}
      data-has-value={setting.has_value}
      className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex items-center gap-3 min-w-0">
        {setting.sensitive ? (
          <Lock
            className="h-4 w-4 shrink-0 text-muted-foreground"
            data-testid={`system-settings-lock-${setting.key}`}
            aria-label="Sensitive — encrypted at rest"
          />
        ) : (
          <span
            className="h-4 w-4 shrink-0"
            aria-hidden
            data-testid={`system-settings-spacer-${setting.key}`}
          />
        )}
        <div className="flex flex-col min-w-0">
          <span
            className="font-mono text-sm truncate"
            data-testid={`system-settings-key-${setting.key}`}
          >
            {setting.key}
          </span>
          {setting.updated_at && (
            <span className="text-muted-foreground text-xs truncate">
              Updated {new Date(setting.updated_at).toLocaleString()}
            </span>
          )}
        </div>
      </div>

      <div className="flex items-center gap-2">
        <HasValueBadge hasValue={setting.has_value} />
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => onSet(setting.key, setting.has_value)}
          data-testid={`system-settings-set-button-${setting.key}`}
        >
          {setting.has_value ? "Replace" : "Set"}
        </Button>
        {hasGenerator && (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={() => onGenerate(setting.key)}
            data-testid={`system-settings-generate-button-${setting.key}`}
          >
            <Sparkles className="mr-2 h-4 w-4" />
            Generate
          </Button>
        )}
      </div>
    </li>
  )
}

export function SystemSettingsList() {
  const queryClient = useQueryClient()
  const { data: envelope } = useSuspenseQuery(listSystemSettingsQueryOptions())
  const [dialog, setDialog] = useState<DialogState>({ kind: "none" })

  const putMutation = useMutation({
    mutationFn: (vars: { key: string; value: string }) =>
      AdminService.putSystemSetting({
        key: vars.key,
        requestBody: { value: vars.value },
      }),
    onSuccess: (_data, vars) => {
      toast.success("Setting saved", { description: vars.key })
      setDialog({ kind: "none" })
      queryClient.invalidateQueries({ queryKey: ["admin", "settings"] })
    },
    onError: (err) => {
      toast.error("Failed to save setting", {
        description: extractErrorBody(err),
      })
    },
  })

  const generateMutation = useMutation({
    mutationFn: (key: string) => AdminService.generateSystemSetting({ key }),
    onSuccess: (data) => {
      // The plaintext is in `data.value`; flow it directly into the
      // one-time-display modal. It never lives in any state outside this
      // closure → the modal's `value` prop → the modal's local DOM.
      setDialog({ kind: "one-time", key: data.key, value: data.value })
      queryClient.invalidateQueries({ queryKey: ["admin", "settings"] })
    },
    onError: (err) => {
      toast.error("Failed to generate setting", {
        description: extractErrorBody(err),
      })
      setDialog({ kind: "none" })
    },
  })

  const handleSet = (key: string, hasValue: boolean) => {
    setDialog({
      kind: "set",
      key,
      hasValue,
      variant: PEM_KEYS.has(key) ? "pem" : "string",
    })
  }

  const handleGenerate = (key: string) => {
    setDialog({ kind: "generate-confirm", key })
  }

  const handleSetSubmit = (value: string) => {
    if (dialog.kind !== "set") return
    putMutation.mutate({ key: dialog.key, value })
  }

  const handleConfirmGenerate = () => {
    if (dialog.kind !== "generate-confirm") return
    generateMutation.mutate(dialog.key)
  }

  const handleAcknowledgeOneTime = () => {
    // Closing the modal unmounts it; the `value` prop is no longer held by
    // any React component (closure of MEM232 at the FE).
    setDialog({ kind: "none" })
  }

  if (envelope.count === 0) {
    return (
      <Card
        data-testid="system-settings-empty"
        className="flex flex-col items-center justify-center text-center py-12 px-6 gap-2"
      >
        <h3 className="text-lg font-semibold">
          No system settings registered.
        </h3>
        <p className="text-muted-foreground max-w-sm">
          The backend's <code>_VALIDATORS</code> registry is empty.
        </p>
      </Card>
    )
  }

  return (
    <>
      <ul
        className="flex flex-col divide-y rounded-lg border bg-card"
        data-testid="system-settings-list"
      >
        {envelope.data.map((s) => (
          <SettingRow
            key={s.key}
            setting={s}
            onSet={handleSet}
            onGenerate={handleGenerate}
          />
        ))}
      </ul>

      {dialog.kind === "set" && (
        <SetSecretDialog
          settingKey={dialog.key}
          hasValue={dialog.hasValue}
          variant={dialog.variant}
          open
          onOpenChange={(next) => {
            if (!next) setDialog({ kind: "none" })
          }}
          onSubmit={handleSetSubmit}
          isPending={putMutation.isPending}
        />
      )}

      {dialog.kind === "generate-confirm" && (
        <GenerateConfirmDialog
          settingKey={dialog.key}
          open
          onOpenChange={(next) => {
            if (!next && !generateMutation.isPending) {
              setDialog({ kind: "none" })
            }
          }}
          onConfirm={handleConfirmGenerate}
          isPending={generateMutation.isPending}
        />
      )}

      {dialog.kind === "one-time" && (
        <OneTimeValueModal
          settingKey={dialog.key}
          value={dialog.value}
          open
          onAcknowledge={handleAcknowledgeOneTime}
        />
      )}
    </>
  )
}

export default SystemSettingsList
