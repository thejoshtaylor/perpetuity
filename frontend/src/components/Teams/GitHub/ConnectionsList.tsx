import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Github, Plus } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import {
  type ApiError,
  type GitHubAppInstallationPublic,
  GithubService,
} from "@/client"
import UninstallConfirm from "@/components/Teams/GitHub/UninstallConfirm"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { LoadingButton } from "@/components/ui/loading-button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"

type Props = {
  teamId: string
  callerIsAdmin: boolean
}

type InstallationsEnvelope = {
  data: GitHubAppInstallationPublic[]
  count: number
}

function installationsQueryOptions(teamId: string) {
  return {
    queryKey: ["team", teamId, "github", "installations"] as const,
    queryFn: async (): Promise<InstallationsEnvelope> => {
      const res = await GithubService.listGithubInstallations({ teamId })
      return res as unknown as InstallationsEnvelope
    },
  }
}

/** Probe the install-url endpoint so the CTA can be disabled when system
 * settings have not been seeded yet. The 404 `github_app_not_configured`
 * shape is operator-actionable without DevTools (closes the operator UX
 * gap from S04 — the FE renders the backend's discriminator verbatim). */
function installUrlProbeQueryOptions(teamId: string, enabled: boolean) {
  return {
    queryKey: ["team", teamId, "github", "install-url-probe"] as const,
    enabled,
    retry: false,
    refetchOnWindowFocus: false,
    queryFn: async () => {
      // We deliberately do NOT cache the install URL itself for click-time
      // use — it embeds a JWT with a 10-min expiry; a stale URL is worse
      // than a fresh fetch.
      await GithubService.getGithubInstallUrl({ teamId })
      return { configured: true } as const
    },
  }
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

function InstallationRow({
  installation,
  callerIsAdmin,
  onUninstall,
}: {
  installation: GitHubAppInstallationPublic
  callerIsAdmin: boolean
  onUninstall: (i: GitHubAppInstallationPublic) => void
}) {
  return (
    <li
      data-testid={`installation-row-${installation.installation_id}`}
      data-installation-id={installation.installation_id}
      className="flex flex-col gap-2 p-3 sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex items-center gap-3 min-w-0">
        <Github className="h-4 w-4 shrink-0 text-muted-foreground" />
        <div className="flex flex-col min-w-0">
          <span
            className="font-medium truncate"
            title={installation.account_login}
          >
            {installation.account_login}
          </span>
          <span className="text-muted-foreground text-xs truncate">
            {installation.account_type}
            {installation.created_at && (
              <>
                {" · installed "}
                {new Date(installation.created_at).toLocaleString()}
              </>
            )}
          </span>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <Badge variant="secondary">{installation.account_type}</Badge>
        {callerIsAdmin && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            data-testid={`installation-uninstall-${installation.installation_id}`}
            onClick={() => onUninstall(installation)}
          >
            Uninstall
          </Button>
        )}
      </div>
    </li>
  )
}

const ConnectionsList = ({ teamId, callerIsAdmin }: Props) => {
  const queryClient = useQueryClient()
  const [uninstallTarget, setUninstallTarget] =
    useState<GitHubAppInstallationPublic | null>(null)
  const [openingInstallUrl, setOpeningInstallUrl] = useState(false)

  const installationsQuery = useQuery(installationsQueryOptions(teamId))

  // Probe the install-url endpoint so we can disable the CTA when the
  // backend's system settings have not been seeded yet. Only run for
  // admins — non-admins never see the CTA at all.
  const probeQuery = useQuery(
    installUrlProbeQueryOptions(teamId, callerIsAdmin),
  )
  const probeStatus = (probeQuery.error as ApiError | null | undefined)?.status
  const probeDetail = extractDetail(probeQuery.error)
  const notConfigured =
    probeStatus === 404 && probeDetail === "github_app_not_configured"

  const invalidateInstallations = () => {
    queryClient.invalidateQueries({
      queryKey: ["team", teamId, "github", "installations"],
    })
  }

  const deleteMutation = useMutation({
    mutationFn: (installationRowId: string) =>
      GithubService.deleteGithubInstallation({
        teamId,
        installationRowId,
      }),
    onSuccess: () => {
      toast.success("Installation forgotten")
      setUninstallTarget(null)
      invalidateInstallations()
    },
    onError: (err) => {
      const status = (err as ApiError | undefined)?.status
      if (status === 404) {
        // Race-tolerant: row already removed elsewhere — silent invalidate.
        setUninstallTarget(null)
        invalidateInstallations()
        return
      }
      toast.error("Failed to forget installation", {
        description: extractDetail(err) ?? "Unknown error",
      })
    },
  })

  const handleInstallClick = async () => {
    setOpeningInstallUrl(true)
    try {
      // Always re-fetch — the JWT in the URL has a 10-min expiry, so a
      // cached URL would be a footgun.
      const resp = await GithubService.getGithubInstallUrl({ teamId })
      // T05's spec stubs window.open; the second arg keeps the popup
      // sandboxed (XSS hardening invariant — see verification step 5).
      window.open(resp.install_url, "_blank", "noopener,noreferrer")
    } catch (err) {
      const status = (err as ApiError | undefined)?.status
      if (status === 404) {
        // System settings dropped between probe and click — refresh probe
        // so the CTA flips to disabled and toast the operator-readable
        // discriminator.
        probeQuery.refetch()
        toast.error("GitHub App not configured", {
          description:
            "System admin must seed GitHub App credentials before installing.",
        })
        return
      }
      toast.error("Failed to open install URL", {
        description: extractDetail(err) ?? "Unknown error",
      })
    } finally {
      setOpeningInstallUrl(false)
    }
  }

  if (installationsQuery.isLoading || probeQuery.isLoading) {
    return (
      <div
        className="flex flex-col gap-2 rounded-lg border p-3"
        data-testid="connections-loading"
      >
        {Array.from({ length: 2 }).map((_, i) => (
          <Skeleton key={`conn-skel-${i + 1}`} className="h-10 w-full" />
        ))}
      </div>
    )
  }

  const installations = installationsQuery.data?.data ?? []
  const installationsError = installationsQuery.error as
    | ApiError
    | null
    | undefined

  return (
    <div className="flex flex-col gap-3">
      {callerIsAdmin && (
        <div className="flex items-center justify-between gap-2">
          {notConfigured ? (
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  {/* span wrapper lets the disabled button still trigger
                      the tooltip */}
                  <span>
                    <Button
                      type="button"
                      data-testid="install-github-cta"
                      data-disabled-reason="github_app_not_configured"
                      disabled
                    >
                      <Plus className="mr-2 h-4 w-4" />
                      Install GitHub App
                    </Button>
                  </span>
                </TooltipTrigger>
                <TooltipContent>
                  System admin must seed GitHub App credentials before
                  installing
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          ) : (
            <LoadingButton
              type="button"
              data-testid="install-github-cta"
              loading={openingInstallUrl}
              onClick={handleInstallClick}
            >
              <Plus className="mr-2 h-4 w-4" />
              Install GitHub App
            </LoadingButton>
          )}
        </div>
      )}

      {installationsError && (
        <Card className="border-destructive/50 bg-destructive/5 p-3 text-sm">
          <p className="font-medium">Could not load installations</p>
          <p className="text-muted-foreground text-xs">
            {extractDetail(installationsError) ?? "Unknown error"}
          </p>
        </Card>
      )}

      {!installationsError && installations.length === 0 && (
        <Card
          data-testid="connections-empty"
          className="flex flex-col items-center justify-center text-center py-8 px-6 gap-2"
        >
          <p className="text-sm font-medium">No GitHub installations yet</p>
          <p className="text-muted-foreground text-xs max-w-sm">
            {callerIsAdmin
              ? "Click Install GitHub App to connect a GitHub org or user account."
              : "A team admin must install the GitHub App before connecting repos."}
          </p>
        </Card>
      )}

      {installations.length > 0 && (
        <ul
          className="flex flex-col divide-y rounded-lg border bg-card"
          data-testid="connections-installations-list"
        >
          {installations.map((inst) => (
            <InstallationRow
              key={inst.id}
              installation={inst}
              callerIsAdmin={callerIsAdmin}
              onUninstall={setUninstallTarget}
            />
          ))}
        </ul>
      )}

      <UninstallConfirm
        installation={uninstallTarget}
        open={uninstallTarget !== null}
        onOpenChange={(next) => {
          if (deleteMutation.isPending) return
          if (!next) setUninstallTarget(null)
        }}
        onConfirm={() => {
          if (!uninstallTarget) return
          deleteMutation.mutate(uninstallTarget.id)
        }}
        isPending={deleteMutation.isPending}
      />
    </div>
  )
}

export default ConnectionsList
