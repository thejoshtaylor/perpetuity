import { useMutation } from "@tanstack/react-query"
import { Check, Copy, Link2 } from "lucide-react"
import { useState } from "react"

import { type InviteIssued, TeamsService } from "@/client"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { LoadingButton } from "@/components/ui/loading-button"
import { useCopyToClipboard } from "@/hooks/useCopyToClipboard"
import useCustomToast from "@/hooks/useCustomToast"
import { handleError } from "@/utils"

type Props = {
  teamId: string
}

function formatExpiry(expiresAt: string): string {
  const expires = new Date(expiresAt).getTime()
  if (Number.isNaN(expires)) return "expires soon"
  const diffMs = expires - Date.now()
  if (diffMs <= 0) return "expired"
  const days = Math.floor(diffMs / (1000 * 60 * 60 * 24))
  const hours = Math.floor((diffMs % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60))
  if (days >= 1) return `expires in ${days} day${days === 1 ? "" : "s"}`
  if (hours >= 1) return `expires in ${hours} hour${hours === 1 ? "" : "s"}`
  return "expires within the hour"
}

const InviteButton = ({ teamId }: Props) => {
  const [invite, setInvite] = useState<InviteIssued | null>(null)
  const [hidden, setHidden] = useState(false)
  const [, copy] = useCopyToClipboard()
  const [justCopied, setJustCopied] = useState(false)
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const mutation = useMutation({
    mutationFn: () => TeamsService.inviteToTeam({ teamId }),
    onSuccess: (data) => {
      // NEVER log invite codes/urls — mirrors backend MEM028 redaction rule.
      setInvite(data)
    },
    onError: (err) => {
      const status = (err as { status?: number })?.status
      if (status === 403) {
        // Caller is not admin — backend is the source of truth; hide UI as a
        // defensive measure if the React Query cache has gone stale.
        setHidden(true)
        showErrorToast("Only team admins can invite")
        return
      }
      handleError.call(showErrorToast, err as never)
    },
  })

  const handleCopy = async () => {
    if (!invite) return
    let ok = false
    try {
      ok = await copy(invite.url)
    } catch {
      ok = false
    }
    if (!ok) {
      // Fallback for non-HTTPS / unsupported clipboard API.
      try {
        const ta = document.createElement("textarea")
        ta.value = invite.url
        ta.setAttribute("readonly", "")
        ta.style.position = "absolute"
        ta.style.left = "-9999px"
        document.body.appendChild(ta)
        ta.select()
        ok = document.execCommand("copy")
        document.body.removeChild(ta)
      } catch {
        ok = false
      }
    }
    if (ok) {
      setJustCopied(true)
      showSuccessToast("Copied")
      setTimeout(() => setJustCopied(false), 2000)
    } else {
      showErrorToast("Copy failed — select and copy manually")
    }
  }

  if (hidden) return null

  return (
    <div className="flex flex-col gap-3">
      {!invite && (
        <LoadingButton
          data-testid="invite-button"
          loading={mutation.isPending}
          onClick={() => mutation.mutate()}
        >
          <Link2 className="mr-2" />
          Generate invite link
        </LoadingButton>
      )}

      {invite && (
        <div
          className="flex flex-col gap-2 rounded-lg border bg-muted/30 p-3"
          data-testid="invite-panel"
        >
          <div className="flex flex-col gap-1">
            <p className="text-sm font-medium">Invite link</p>
            <p className="text-xs text-muted-foreground">
              {formatExpiry(invite.expires_at)}
            </p>
          </div>
          <div className="flex gap-2">
            <Input
              data-testid="invite-url"
              readOnly
              value={invite.url}
              onFocus={(e) => e.currentTarget.select()}
              className="font-mono text-xs"
            />
            <Button
              type="button"
              variant="outline"
              data-testid="copy-invite-url"
              onClick={handleCopy}
              aria-label="Copy invite link"
            >
              {justCopied ? (
                <Check className="h-4 w-4" />
              ) : (
                <Copy className="h-4 w-4" />
              )}
            </Button>
          </div>
          <Button
            variant="ghost"
            size="sm"
            type="button"
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
          >
            Generate a new link
          </Button>
        </div>
      )}
    </div>
  )
}

export default InviteButton
