import {
  useMutation,
  useQueryClient,
  useSuspenseQuery,
} from "@tanstack/react-query"
import type { AxiosError } from "axios"
import { Eye, EyeOff, Lock, RefreshCw, Sparkles } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { AdminService, type ApiError, type SystemSettingPublic } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"

import { OneTimeValueModal } from "./OneTimeValueModal"

type ListEnvelope = {
  data: SystemSettingPublic[]
  count: number
}

type OneTimeState =
  | { kind: "none" }
  | { kind: "one-time"; key: string; value: string }

/** Keys that expose the per-key server-side generator. */
const KEYS_WITH_GENERATOR: ReadonlySet<string> = new Set([
  "github_app_webhook_secret",
])

/** VAPID keys are generated atomically as a pair. */
const VAPID_KEYS: ReadonlySet<string> = new Set([
  "vapid_public_key",
  "vapid_private_key",
])

/** Sensitive keys whose input is a multiline textarea (PEM). */
const PEM_KEYS: ReadonlySet<string> = new Set(["github_app_private_key"])

// ---------------------------------------------------------------------------
// Settings metadata — labels, descriptions, input types, defaults
// ---------------------------------------------------------------------------

type SettingInputType = "number" | "text"

type SettingMeta = {
  label: string
  description: string
  inputType: SettingInputType
  placeholder?: string
  /** Default value to pre-populate when the setting is unset. */
  defaultValue?: string
}

const SETTING_META: Record<string, SettingMeta> = {
  workspace_volume_size_gb: {
    label: "Workspace Volume Size (GiB)",
    description:
      "Default persistent storage allocated to each workspace container. Accepts integers from 1 to 256. Changing this only affects new workspaces; existing volumes are not resized automatically.",
    inputType: "number",
    placeholder: "e.g. 10",
    defaultValue: "10",
  },
  idle_timeout_seconds: {
    label: "Workspace Idle Timeout (seconds)",
    description:
      "How long an inactive workspace runs before the orchestrator stops it. Accepts integers from 1 to 86400 (24 hours). Shorter values reduce resource usage; longer values avoid disrupting users with slow-starting tools.",
    inputType: "number",
    placeholder: "e.g. 1800",
    defaultValue: "1800",
  },
  mirror_idle_timeout_seconds: {
    label: "Mirror Idle Timeout (seconds)",
    description:
      "How long an idle mirror container stays running before the reaper tears it down. Must be an integer from 60 to 86400. Default is 1800 (30 minutes).",
    inputType: "number",
    placeholder: "e.g. 1800",
    defaultValue: "1800",
  },
  grok_stt_api_key: {
    label: "Grok Speech-to-Text API Key",
    description:
      "API key for the Grok speech-to-text service. Encrypted at rest and never returned by the API after saving.",
    inputType: "text",
    placeholder: "Paste your Grok STT API key",
  },
  max_voice_transcribes_per_hour_global: {
    label: "Max Voice Transcriptions / Hour (global)",
    description:
      "Platform-wide cap on the number of voice transcription requests allowed per hour. Accepts integers from 1 to 1,000,000.",
    inputType: "number",
    placeholder: "e.g. 1000",
    defaultValue: "1000",
  },
  github_app_id: {
    label: "GitHub App ID",
    description:
      "The numeric ID of your registered GitHub App, found on the GitHub App settings page under 'App ID'.",
    inputType: "number",
    placeholder: "e.g. 123456",
  },
  github_app_client_id: {
    label: "GitHub App Client ID",
    description:
      "The OAuth client ID for your GitHub App, used during the user authorization flow. Found on the GitHub App settings page as 'Client ID'.",
    inputType: "text",
    placeholder: "e.g. Iv1.abc123def456",
  },
  github_app_slug: {
    label: "GitHub App Slug",
    description:
      "The URL slug for your GitHub App — the short name shown in the install URL: github.com/apps/{slug}/installations/new. Found on the GitHub App settings page as the app name in lowercase with hyphens. This is different from the Client ID.",
    inputType: "text",
    placeholder: "e.g. my-company-app",
  },
  github_app_private_key: {
    label: "GitHub App Private Key (PEM)",
    description:
      "The RSA private key for your GitHub App. Paste the full PEM-encoded key including the BEGIN and END header lines. Encrypted at rest.",
    inputType: "text",
    placeholder:
      "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----",
  },
  github_app_webhook_secret: {
    label: "GitHub App Webhook Secret",
    description:
      "The HMAC secret used to verify that webhook deliveries originate from GitHub. Paste an existing secret or use Generate to create a cryptographically secure random value. Warning: regenerating invalidates in-flight deliveries.",
    inputType: "text",
    placeholder: "Paste or generate a webhook secret",
  },
  vapid_public_key: {
    label: "VAPID Public Key",
    description:
      "The public half of the VAPID keypair used for Web Push notifications. Use Generate VAPID Keys to create a matched keypair.",
    inputType: "text",
    placeholder: "URL-safe base64 encoded P-256 public key",
  },
  vapid_private_key: {
    label: "VAPID Private Key",
    description:
      "The private half of the VAPID keypair. Encrypted at rest. Always generate this together with the public key.",
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
      "github_app_slug",
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
          ? (detail[0] as { msg?: string })?.msg
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

// ---------------------------------------------------------------------------
// SettingField — inline input for a single setting
// ---------------------------------------------------------------------------

type SettingFieldProps = {
  setting: SystemSettingPublic
  onSave: (key: string, value: string) => Promise<void>
  onGenerate?: (key: string) => void
  isSaving: boolean
}

function SettingField({
  setting,
  onSave,
  onGenerate,
  isSaving,
}: SettingFieldProps) {
  const meta = SETTING_META[setting.key]
  const isPem = PEM_KEYS.has(setting.key)
  const isVapid = VAPID_KEYS.has(setting.key)
  const hasGenerator = KEYS_WITH_GENERATOR.has(setting.key)
  const [localValue, setLocalValue] = useState(() =>
    setting.has_value ? "" : (meta?.defaultValue ?? ""),
  )
  const [showSecret, setShowSecret] = useState(false)

  const inputId = `setting-${setting.key}`
  const isNumeric = meta?.inputType === "number"

  // Sensitive fields that are already set show masked placeholder; the input
  // remains empty so the operator must type to replace — never pre-fill a
  // secret value we don't have client-side.
  const maskedPlaceholder =
    setting.sensitive && setting.has_value
      ? "••••• (set — type to replace)"
      : (meta?.placeholder ?? "")

  const handleSave = async () => {
    const val = localValue.trim()
    if (!val) return
    await onSave(setting.key, val)
    setLocalValue("")
    setShowSecret(false)
  }

  const handleKeyDown = (
    e: React.KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>,
  ) => {
    if (e.key === "Enter" && !isPem && !e.shiftKey) {
      e.preventDefault()
      void handleSave()
    }
  }

  return (
    <div
      data-testid={`system-settings-field-${setting.key}`}
      className="flex flex-col gap-2"
    >
      <div className="flex items-center gap-2">
        {setting.sensitive && (
          <Lock
            className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
            aria-label="Sensitive — encrypted at rest"
          />
        )}
        <Label htmlFor={inputId} className="text-sm font-medium">
          {meta?.label ?? setting.key}
        </Label>
        <span className="font-mono text-xs text-muted-foreground">
          {setting.key}
        </span>
        <Badge
          variant={setting.has_value ? "default" : "secondary"}
          className="ml-auto text-xs"
          data-testid={`system-settings-has-value-badge-${setting.key}`}
        >
          {setting.has_value ? "Set" : "Empty"}
        </Badge>
      </div>

      {meta?.description && (
        <p className="text-xs text-muted-foreground leading-relaxed">
          {meta.description}
        </p>
      )}

      {/* VAPID keys: no direct user input — only server-side generation */}
      {isVapid ? (
        <div className="flex items-center gap-2 rounded-md border bg-muted/40 px-3 py-2 text-sm text-muted-foreground">
          {setting.has_value ? (
            <>
              <Lock className="h-3.5 w-3.5 shrink-0" />
              <span className="font-mono text-xs">
                ••••• (set — use Generate VAPID Keys to replace)
              </span>
            </>
          ) : (
            <span className="text-xs italic">
              Not set — use Generate VAPID Keys below
            </span>
          )}
        </div>
      ) : isPem ? (
        /* PEM textarea */
        <div className="relative flex flex-col gap-2">
          <textarea
            id={inputId}
            value={localValue}
            onChange={(e) => setLocalValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={maskedPlaceholder}
            rows={6}
            autoComplete="off"
            spellCheck={false}
            data-voice-disabled="true"
            data-testid={`system-settings-input-${setting.key}`}
            className="border-input placeholder:text-muted-foreground focus-visible:ring-ring/50 flex w-full rounded-md border bg-transparent px-3 py-2 text-sm font-mono shadow-xs transition-[color,box-shadow] outline-none focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-50 resize-y"
          />
          <div className="flex justify-end">
            <LoadingButton
              type="button"
              size="sm"
              loading={isSaving}
              disabled={!localValue.trim()}
              onClick={handleSave}
              data-testid={`system-settings-save-${setting.key}`}
            >
              {setting.has_value ? "Replace" : "Save"}
            </LoadingButton>
          </div>
        </div>
      ) : (
        /* Standard single-line input */
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Input
              id={inputId}
              type={
                setting.sensitive && !showSecret
                  ? "password"
                  : isNumeric
                    ? "text"
                    : "text"
              }
              inputMode={isNumeric ? "numeric" : undefined}
              pattern={isNumeric ? "-?\\d*" : undefined}
              value={localValue}
              onChange={(e) => setLocalValue(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={maskedPlaceholder}
              autoComplete="off"
              spellCheck={false}
              voice={false}
              data-voice-disabled={setting.sensitive ? "true" : undefined}
              data-testid={`system-settings-input-${setting.key}`}
            />
            {setting.sensitive && (
              <button
                type="button"
                onClick={() => setShowSecret((v) => !v)}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                aria-label={showSecret ? "Hide value" : "Show value"}
                tabIndex={-1}
              >
                {showSecret ? (
                  <EyeOff className="h-4 w-4" />
                ) : (
                  <Eye className="h-4 w-4" />
                )}
              </button>
            )}
          </div>
          {hasGenerator && onGenerate && (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => onGenerate(setting.key)}
              data-testid={`system-settings-generate-button-${setting.key}`}
            >
              <RefreshCw className="mr-2 h-3.5 w-3.5" />
              Generate
            </Button>
          )}
          <LoadingButton
            type="button"
            size="sm"
            loading={isSaving}
            disabled={!localValue.trim()}
            onClick={handleSave}
            data-testid={`system-settings-save-${setting.key}`}
          >
            {setting.has_value ? "Replace" : "Save"}
          </LoadingButton>
        </div>
      )}

      {setting.updated_at && (
        <p className="text-xs text-muted-foreground">
          Last updated {new Date(setting.updated_at).toLocaleString()}
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function SystemSettingsList() {
  const queryClient = useQueryClient()
  const { data: envelope } = useSuspenseQuery(listSystemSettingsQueryOptions())
  const [oneTime, setOneTime] = useState<OneTimeState>({ kind: "none" })
  // Track which key is currently saving for per-field loading state
  const [savingKey, setSavingKey] = useState<string | null>(null)

  const putMutation = useMutation({
    mutationFn: (vars: { key: string; value: string | number }) =>
      AdminService.putSystemSetting({
        key: vars.key,
        requestBody: { value: vars.value },
      }),
    onSuccess: (_data, vars) => {
      toast.success("Setting saved", { description: vars.key })
      setSavingKey(null)
      queryClient.invalidateQueries({ queryKey: ["admin", "settings"] })
    },
    onError: (err) => {
      toast.error("Failed to save setting", {
        description: extractErrorBody(err),
      })
      setSavingKey(null)
    },
  })

  const generateMutation = useMutation({
    mutationFn: (key: string) => AdminService.generateSystemSetting({ key }),
    onSuccess: (data) => {
      setOneTime({ kind: "one-time", key: data.key, value: data.value })
      setSavingKey(null)
      queryClient.invalidateQueries({ queryKey: ["admin", "settings"] })
    },
    onError: (err) => {
      toast.error("Failed to generate setting", {
        description: extractErrorBody(err),
      })
      setSavingKey(null)
    },
  })

  const generateVapidMutation = useMutation({
    mutationFn: () => AdminService.generateVapidKeys(),
    onSuccess: (data) => {
      const combined = `Public key:\n${data.public_key}\n\nPrivate key:\n${data.private_key}`
      setOneTime({
        kind: "one-time",
        key: "vapid_keys",
        value: combined,
      })
      setSavingKey(null)
      queryClient.invalidateQueries({ queryKey: ["admin", "settings"] })
    },
    onError: (err) => {
      toast.error("Failed to generate VAPID keys", {
        description: extractErrorBody(err),
      })
      setSavingKey(null)
    },
  })

  const handleSave = async (key: string, rawValue: string) => {
    const meta = SETTING_META[key]
    const coerced: string | number =
      meta?.inputType === "number" ? parseInt(rawValue.trim(), 10) : rawValue
    setSavingKey(key)
    putMutation.mutate({ key, value: coerced })
  }

  const handleGenerate = (key: string) => {
    setSavingKey(key)
    generateMutation.mutate(key)
  }

  const handleGenerateVapid = () => {
    setSavingKey("vapid_keys")
    generateVapidMutation.mutate()
  }

  const handleAcknowledgeOneTime = () => {
    setOneTime({ kind: "none" })
  }

  if (envelope.count === 0) {
    return (
      <Card
        data-testid="system-settings-empty"
        className="flex flex-col items-center justify-center text-center py-12 px-6 gap-2"
      >
        <h3 className="text-lg font-semibold">No system settings registered.</h3>
        <p className="text-muted-foreground max-w-sm">
          The backend's <code>_VALIDATORS</code> registry is empty.
        </p>
      </Card>
    )
  }

  const settingsByKey = Object.fromEntries(envelope.data.map((s) => [s.key, s]))
  const groupedKeys = new Set(SETTING_GROUPS.flatMap((g) => g.keys))
  const ungroupedSettings = envelope.data.filter((s) => !groupedKeys.has(s.key))

  return (
    <>
      <div className="flex flex-col gap-8" data-testid="system-settings-list">
        {SETTING_GROUPS.map((group) => {
          const groupSettings = group.keys
            .map((k) => settingsByKey[k])
            .filter(Boolean) as SystemSettingPublic[]

          if (groupSettings.length === 0) return null

          const isWebPush = group.id === "webpush"

          return (
            <section
              key={group.id}
              data-testid={`system-settings-group-${group.id}`}
            >
              <div className="mb-4">
                <h2 className="text-base font-semibold">{group.label}</h2>
                <p className="text-sm text-muted-foreground mt-0.5">
                  {group.description}
                </p>
              </div>
              <div className="rounded-lg border bg-card divide-y">
                {groupSettings.map((s) => (
                  <div key={s.key} className="p-4 sm:p-5">
                    <SettingField
                      setting={s}
                      onSave={handleSave}
                      onGenerate={
                        KEYS_WITH_GENERATOR.has(s.key)
                          ? handleGenerate
                          : undefined
                      }
                      isSaving={savingKey === s.key && putMutation.isPending}
                    />
                  </div>
                ))}
                {isWebPush && (
                  <div className="p-4 sm:p-5 bg-muted/30">
                    <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                      <div>
                        <p className="text-sm font-medium">Generate VAPID Keys</p>
                        <p className="text-xs text-muted-foreground mt-0.5">
                          Atomically generates a new matched public + private
                          keypair. The private key is shown once and never
                          returned again.{" "}
                          {settingsByKey["vapid_public_key"]?.has_value &&
                            "Warning: this will overwrite the existing keypair."}
                        </p>
                      </div>
                      <LoadingButton
                        type="button"
                        variant={
                          settingsByKey["vapid_public_key"]?.has_value
                            ? "destructive"
                            : "default"
                        }
                        size="sm"
                        loading={
                          savingKey === "vapid_keys" &&
                          generateVapidMutation.isPending
                        }
                        onClick={handleGenerateVapid}
                        data-testid="system-settings-generate-vapid"
                        className="mt-3 sm:mt-0 shrink-0"
                      >
                        <Sparkles className="mr-2 h-4 w-4" />
                        Generate VAPID Keys
                      </LoadingButton>
                    </div>
                  </div>
                )}
              </div>
            </section>
          )
        })}

        {ungroupedSettings.length > 0 && (
          <section data-testid="system-settings-group-other">
            <div className="mb-4">
              <h2 className="text-base font-semibold">Other</h2>
              <p className="text-sm text-muted-foreground mt-0.5">
                Additional system settings.
              </p>
            </div>
            <div className="rounded-lg border bg-card divide-y">
              {ungroupedSettings.map((s) => (
                <div key={s.key} className="p-4 sm:p-5">
                  <SettingField
                    setting={s}
                    onSave={handleSave}
                    onGenerate={
                      KEYS_WITH_GENERATOR.has(s.key) ? handleGenerate : undefined
                    }
                    isSaving={savingKey === s.key && putMutation.isPending}
                  />
                </div>
              ))}
            </div>
          </section>
        )}
      </div>

      {oneTime.kind === "one-time" && (
        <OneTimeValueModal
          settingKey={oneTime.key}
          value={oneTime.value}
          open
          onAcknowledge={handleAcknowledgeOneTime}
        />
      )}
    </>
  )
}

export default SystemSettingsList
