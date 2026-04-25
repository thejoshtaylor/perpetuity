import { useMutation, useQueryClient } from "@tanstack/react-query"
import {
  createFileRoute,
  isRedirect,
  Link as RouterLink,
  redirect,
  useNavigate,
} from "@tanstack/react-router"
import { Loader2 } from "lucide-react"
import { useEffect, useRef } from "react"

import { TeamsService, type TeamWithRole, UsersService } from "@/client"
import { AuthLayout } from "@/components/Common/AuthLayout"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import useCustomToast from "@/hooks/useCustomToast"

type InviteSearch = { next?: string }

export const Route = createFileRoute("/invite/$code")({
  component: AcceptInvite,
  validateSearch: (search): InviteSearch => {
    const next = (search as Record<string, unknown>).next
    return typeof next === "string" ? { next } : {}
  },
  beforeLoad: async ({ context, location }) => {
    try {
      await context.queryClient.ensureQueryData({
        queryKey: ["currentUser"],
        queryFn: UsersService.readUserMe,
      })
    } catch (err) {
      if (isRedirect(err)) throw err
      // Not logged in — bounce to /login with ?next= so we land back here.
      throw redirect({
        to: "/login",
        search: { next: location.href },
      })
    }
  },
  head: () => ({
    meta: [{ title: "Accept invite - FastAPI Template" }],
  }),
})

type FailureKind = "not-found" | "expired" | "already-member" | "other"

function getFailureKind(err: unknown): FailureKind {
  const status = (err as { status?: number })?.status
  if (status === 404) return "not-found"
  if (status === 410) return "expired"
  if (status === 409) return "already-member"
  return "other"
}

function AcceptInvite() {
  const { code } = Route.useParams()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const ranRef = useRef(false)

  const mutation = useMutation({
    mutationFn: (inviteCode: string) =>
      TeamsService.joinTeam({ code: inviteCode }),
    onSuccess: (team: TeamWithRole) => {
      queryClient.invalidateQueries({ queryKey: ["teams"] })
      showSuccessToast(`Joined ${team.name}`)
      navigate({ to: "/teams/$teamId", params: { teamId: team.id } })
    },
    onError: (err) => {
      const kind = getFailureKind(err)
      if (kind === "already-member") {
        // Backend's 409 detail body is opaque about the team id, so we cannot
        // navigate to that team here. Bounce to /teams after 2s instead.
        showErrorToast("You are already a member of this team")
        setTimeout(() => navigate({ to: "/teams" }), 2000)
      } else if (kind === "not-found") {
        showErrorToast("Invite not found")
      } else if (kind === "expired") {
        showErrorToast("This invite has expired or already been used")
      } else {
        const detail =
          (err as { body?: { detail?: string } })?.body?.detail ??
          "Could not accept invite"
        showErrorToast(detail)
      }
    },
  })

  useEffect(() => {
    if (ranRef.current) return
    ranRef.current = true
    mutation.mutate(code)
    // Run exactly once for the initial code; re-running is gated on a manual
    // retry below.
  }, [code, mutation.mutate, mutation])

  if (mutation.isPending || mutation.isIdle) {
    return (
      <AuthLayout>
        <div
          className="flex flex-col items-center justify-center gap-3 py-12"
          data-testid="invite-loading"
        >
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          <p className="text-muted-foreground">Joining team…</p>
        </div>
      </AuthLayout>
    )
  }

  if (mutation.isError) {
    const kind = getFailureKind(mutation.error)
    const title =
      kind === "not-found"
        ? "Invite not found"
        : kind === "expired"
          ? "Invite expired"
          : kind === "already-member"
            ? "Already a member"
            : "Couldn't join"
    const description =
      kind === "not-found"
        ? "This invite link doesn't exist or was revoked."
        : kind === "expired"
          ? "This invite has expired or already been used."
          : kind === "already-member"
            ? "You are already a member of this team. Redirecting to your teams…"
            : ((mutation.error as { body?: { detail?: string } })?.body
                ?.detail ?? "Something went wrong. Try again later.")
    return (
      <AuthLayout>
        <Card
          className="flex flex-col items-center justify-center text-center gap-3 py-10 px-6"
          data-testid={`invite-${kind}`}
        >
          <h2 className="text-xl font-semibold">{title}</h2>
          <p className="text-muted-foreground max-w-sm">{description}</p>
          <Button asChild variant="outline">
            <RouterLink to="/teams">Back to teams</RouterLink>
          </Button>
        </Card>
      </AuthLayout>
    )
  }

  return null
}
