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
  | {
      kind: "set"
      key: string
      hasValue: boolean
      variant: "pem" | "string"
      inputType: "number" | "text"
    }
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

// ---------------------------------------------------------------------------
// Settings metadata registry
// ---------------------------------------------------------------------------

type SettingInputType = "number" | "text"

type SettingMeta = {
  label: string
  description: string
  inputType: SettingInputType
  /** Hint shown inside the input placeholder / help text */
  placeholder?: string
}

const SETTING_META: Record<string, SettingMeta> = {
  // Workspace configuration
  workspace_volume_size_gb: {
    label: "Workspace Volume Size (GiB)",
    description:
      "Default persistent storage allocated to each workspace container. Accepts integers from 1 to 256. Changing this only affects new workspaces; existing volumes are not resized automatically.",
    inputType: "number",
    placeholder: "e.g. 10",
  },
  idle_timeout_seconds: {
    label: "Workspace Idle Timeout (seconds)",
    description:
      "How long an inactive workspace runs before the orchestrator stops it. Accepts integers from 1 to 86400 (24 hours). Shorter values reduce resource usage; longer values avoid disrupting users with slow-starting tools.",
    inputType: "number",
    placeholder: "e.g. 1800",
  },
  mirror_idle_timeout_seconds: {
    label: "Mirror Idle Timeout (seconds)",
    description:
      "How long an idle mirror container stays running before the reaper tears it down. Must be an integer from 60 to 86400. Default is 1800 (30 minutes). Setting this too low may cause mirrors to restart during brief pauses in activity.",
    inputType: "number",
    placeholder: "e.g. 1800",
  },
  // Voice / AI
  grok_stt_api_key: {
    label: "Grok Speech-to-Text API Key",
    description:
      "API key for the Grok speech-to-text service. This is a sensitive secret — it is encrypted at rest and never returned by the API. Obtain it from your Grok account dashboard.",
    inputType: "text",
    placeholder: "Paste your Grok STT API key",
  },
  max_voice_transcribes_per_hour_global: {
    label: "Max Voice Transcriptions / Hour (global)",
    description:
      "Platform-wide cap on the number of voice transcription requests allowed per hour. Accepts integers from 1 to 1,000,000. Setting this too low will cause transcription errors for users when the limit is reached.",
    inputType: "number",
    placeholder: "e.g. 1000",
  },
  // GitHub App integration
  github_app_id: {
    label: "GitHub App ID",
    description:
      "The numeric ID of your registered GitHub App, found on the GitHub App settings page under 'App ID'. This value is required for authenticating API requests made as the app.",
    inputType: "number",
    placeholder: "e.g. 123456",
  },
  github_app_client_id: {
    label: "GitHub App Client ID",
    description:
      "The OAuth client ID for your GitHub App, used during the user authorization flow. Found on the GitHub App settings page as 'Client ID'. Must be a non-empty ASCII string up to 255 characters.",
    inputType: "text",
    placeholder: "e.g. Iv1.abc123def456",
  },
  github_app_private_key: {
    label: "GitHub App Private Key (PEM)",
    description:
      "The RSA private key for your GitHub App, used to sign JWT authentication tokens. Download it from the GitHub App settings page. Paste the full PEM-encoded key including the BEGIN and END header lines.",
    inputType: "text",
    placeholder:
      "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----",
  },
  github_app_webhook_secret: {
    label: "GitHub App Webhook Secret",
    description:
      "The HMAC secret used to verify that webhook deliveries originate from GitHub. You can paste an existing secret or use Generate to create a cryptographically secure random value. Warning: regenerating this secret invalidates any in-flight webhook deliveries.",
    inputType: "text",
    placeholder: "Paste or generate a webhook secret",
  },
  // Web Push (VAPID)
  vapid_public_key: {
    label: "VAPID Public Key",
    description:
      "The public half of the VAPID keypair used for Web Push notifications. Browsers fetch this key unauthenticated. Use Generate VAPID Keys to create a matched keypair — do not paste a public key without the corresponding private key.",
    inputType: "text",
    placeholder: "URL-safe base64 encoded P-256 public key",
  },
  vapid_private_key: {
    label: "VAPID Private Key",
    description:
      "The private half of the VAPID keypair. This is a sensitive secret — it is encrypted at rest. Always generate this together with the public key using Generate VAPID Keys.",
    inputType: "text",
    placeholder: "URL-safe base64 encoded P-256 private key",
  },
}

// ---------------------------------------------------------------------------
// Grouping
// ---------------------------------------------------------------------------

type SettingGroup = {
  id: string
  label: string
  description: string
  keys: string[]
}

const SETTING_GROUPS: SettingGroup[] = [
  {
    id: "workspace",
    label: "Workspace Configuration",
    description:
      "Controls resource limits and idle behaviour for user workspaces managed by the orchestrator.",
    keys: [
      "workspace_volume_size_gb",
      "idle_timeout_seconds",
      "mirror_idle_timeout_seconds",
    ],
  },
  {
    id: "voice",
    label: "Voice & AI",
    description:
      "Settings for the Grok speech-to-text integration and global transcription rate limits.",
    keys: ["grok_stt_api_key", "max_voice_transcribes_per_hour_global"],
  },
  {
    id: "github",
    label: "GitHub App Integration",
    description:
      "Credentials and configuration for the GitHub App used to authenticate API requests and receive webhooks.",
    keys: [
      "github_app_id",
      "github_app_client_id",
      "github_app_private_key",
      "github_app_webhook_secret",
    ],
  },
  {
    id: "webpush",
    label: "Web Push (VAPID)",
    description:
      "VAPID keypair used to send push notifications to browsers. Generate both keys together using the button below.",
    keys: ["vapid_public_key", "vapid_private_key"],
  },
]

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
  const meta = SETTING_META[setting.key]

  return (
    <li
      data-testid={`system-settings-row-${setting.key}`}
      data-key={setting.key}
      data-sensitive={setting.sensitive}
      data-has-value={setting.has_value}
      className="flex flex-col gap-4 p-4 sm:flex-row sm:items-start sm:justify-between"
    >
      <div className="flex items-start gap-3 min-w-0 flex-1">
        {setting.sensitive ? (
          <Lock
            className="h-4 w-4 shrink-0 text-muted-foreground mt-0.5"
            data-testid={`system-settings-lock-${setting.key}`}
            aria-label="Sensitive — encrypted at rest"
          />
        ) : (
          <span
            className="h-4 w-4 shrink-0 mt-0.5"
            aria-hidden
            data-testid={`system-settings-spacer-${setting.key}`}
          />
        )}
        <div className="flex flex-col min-w-0 gap-0.5">
          <span
            className="font-medium text-sm"
            data-testid={`system-settings-key-${setting.key}`}
          >
            {meta?.label ?? setting.key}
          </span>
          <span
            className="font-mono text-xs text-muted-foreground"
            aria-label="Setting key"
          >
            {setting.key}
          </span>
          {meta?.description && (
            <p className="text-muted-foreground text-xs mt-1 max-w-prose leading-relaxed">
              {meta.description}
            </p>
          )}
          {setting.updated_at && (
            <span className="text-muted-foreground text-xs mt-1">
              Last updated {new Date(setting.updated_at).toLocaleString()}
            </span>
          )}
        </div>
      </div>

      <div className="flex items-center gap-2 shrink-0 pl-7 sm:pl-0">
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
    const meta = SETTING_META[key]
    setDialog({
      kind: "set",
      key,
      hasValue,
      variant: PEM_KEYS.has(key) ? "pem" : "string",
      inputType: meta?.inputType ?? "text",
    })
  }

  const handleGenerate = (key: string) => {
    setDialog({ kind: "generate-confirm", key })
  }

  const handleSetSubmit = (value: string) => {
    if (dialog.kind !== "set") return
    // Numeric settings: the backend validator expects an integer in JSONB,
    // not a JSON string. Coerce before sending so the PUT body is `{"value": 10}`
    // rather than `{"value": "10"}`.
    const coerced: string | number =
      dialog.inputType === "number" ? parseInt(value.trim(), 10) : value
    putMutation.mutate({ key: dialog.key, value: coerced as unknown as string })
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

  // Build a lookup map for quick access by key
  const settingsByKey = Object.fromEntries(envelope.data.map((s) => [s.key, s]))

  // Collect keys that appear in no group (future-proofing)
  const groupedKeys = new Set(SETTING_GROUPS.flatMap((g) => g.keys))
  const ungroupedSettings = envelope.data.filter((s) => !groupedKeys.has(s.key))

  return (
    <>
      <div className="flex flex-col gap-6" data-testid="system-settings-list">
        {SETTING_GROUPS.map((group) => {
          const groupSettings = group.keys
            .map((k) => settingsByKey[k])
            .filter(Boolean) as SystemSettingPublic[]

          if (groupSettings.length === 0) return null

          return (
            <section key={group.id} data-testid={`system-settings-group-${group.id}`}>
              <div className="mb-3">
                <h2 className="text-base font-semibold">{group.label}</h2>
                <p className="text-sm text-muted-foreground mt-0.5">
                  {group.description}
                </p>
              </div>
              <ul className="flex flex-col divide-y rounded-lg border bg-card">
                {groupSettings.map((s) => (
                  <SettingRow
                    key={s.key}
                    setting={s}
                    onSet={handleSet}
                    onGenerate={handleGenerate}
                  />
                ))}
              </ul>
            </section>
          )
        })}

        {ungroupedSettings.length > 0 && (
          <section data-testid="system-settings-group-other">
            <div className="mb-3">
              <h2 className="text-base font-semibold">Other</h2>
              <p className="text-sm text-muted-foreground mt-0.5">
                Additional system settings.
              </p>
            </div>
            <ul className="flex flex-col divide-y rounded-lg border bg-card">
              {ungroupedSettings.map((s) => (
                <SettingRow
                  key={s.key}
                  setting={s}
                  onSet={handleSet}
                  onGenerate={handleGenerate}
                />
              ))}
            </ul>
          </section>
        )}
      </div>

      {dialog.kind === "set" && (
        <SetSecretDialog
          settingKey={dialog.key}
          hasValue={dialog.hasValue}
          variant={dialog.variant}
          inputType={dialog.inputType}
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
