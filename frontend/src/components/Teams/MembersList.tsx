import {
  useMutation,
  useQueryClient,
  useSuspenseQuery,
} from "@tanstack/react-query"
import { MoreHorizontal } from "lucide-react"
import { useState } from "react"

import { type TeamMemberPublic, type TeamRole, TeamsService } from "@/client"
import RemoveMemberConfirm from "@/components/Teams/RemoveMemberConfirm"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Skeleton } from "@/components/ui/skeleton"
import useCustomToast from "@/hooks/useCustomToast"
import { getInitials } from "@/utils"

type Props = {
  teamId: string
  callerId: string
  callerIsAdmin: boolean
}

function membersQueryOptions(teamId: string) {
  return {
    queryKey: ["team", teamId, "members"] as const,
    queryFn: () => TeamsService.readTeamMembers({ teamId }),
  }
}

function RoleBadge({ role }: { role: TeamRole }) {
  return (
    <Badge
      data-testid="member-role-badge"
      data-role={role}
      variant={role === "admin" ? "default" : "secondary"}
    >
      {role === "admin" ? "Admin" : "Member"}
    </Badge>
  )
}

function MembersListContent({ teamId, callerId, callerIsAdmin }: Props) {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const { data } = useSuspenseQuery(membersQueryOptions(teamId))

  const [removeTarget, setRemoveTarget] = useState<TeamMemberPublic | null>(
    null,
  )

  const invalidateMembers = () => {
    queryClient.invalidateQueries({ queryKey: ["team", teamId, "members"] })
    queryClient.invalidateQueries({ queryKey: ["teams"] })
  }

  const roleMutation = useMutation({
    mutationFn: (vars: { userId: string; role: TeamRole }) =>
      TeamsService.updateMemberRole({
        teamId,
        userId: vars.userId,
        requestBody: { role: vars.role },
      }),
    onSuccess: () => {
      invalidateMembers()
      showSuccessToast("Role updated")
    },
    onError: (err) => {
      const e = err as { status?: number; body?: { detail?: string } }
      if (e.status === 400 && e.body?.detail) {
        showErrorToast(e.body.detail)
        return
      }
      if (e.status === 403) {
        showErrorToast("Only team admins can change roles")
        return
      }
      if (e.status === 404) {
        showErrorToast("Member already removed")
        invalidateMembers()
        return
      }
      showErrorToast(e.body?.detail || "Could not update role")
    },
  })

  const removeMutation = useMutation({
    mutationFn: (userId: string) =>
      TeamsService.removeMember({ teamId, userId }),
    onSuccess: () => {
      invalidateMembers()
      setRemoveTarget(null)
      showSuccessToast("Member removed")
    },
    onError: (err) => {
      const e = err as { status?: number; body?: { detail?: string } }
      if (e.status === 400 && e.body?.detail) {
        showErrorToast(e.body.detail)
        return
      }
      if (e.status === 403) {
        showErrorToast("Only team admins can remove members")
        return
      }
      if (e.status === 404) {
        showErrorToast("Member already removed")
        invalidateMembers()
        setRemoveTarget(null)
        return
      }
      showErrorToast(e.body?.detail || "Could not remove member")
    },
  })

  if (data.count === 0) {
    return (
      <p className="text-muted-foreground text-sm" data-testid="members-empty">
        No members yet.
      </p>
    )
  }

  return (
    <>
      <ul
        className="flex flex-col divide-y rounded-lg border bg-card"
        data-testid="members-list"
      >
        {data.data.map((m) => {
          const isSelf = m.user_id === callerId
          const display = m.full_name?.trim() || m.email
          const showActions = callerIsAdmin && !isSelf

          return (
            <li
              key={m.user_id}
              data-testid="member-row"
              data-user-id={m.user_id}
              className="flex flex-col gap-2 p-3 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="flex items-center gap-3 min-w-0">
                <Avatar>
                  <AvatarFallback>{getInitials(display)}</AvatarFallback>
                </Avatar>
                <div className="flex flex-col min-w-0">
                  <span
                    className="font-medium truncate"
                    data-testid="member-name"
                    title={display}
                  >
                    {display}
                    {isSelf && (
                      <span className="text-muted-foreground ml-1 text-xs">
                        (you)
                      </span>
                    )}
                  </span>
                  {m.full_name && (
                    <span
                      className="text-muted-foreground text-xs truncate"
                      title={m.email}
                    >
                      {m.email}
                    </span>
                  )}
                </div>
              </div>

              <div className="flex items-center justify-between gap-2 sm:justify-end">
                <RoleBadge role={m.role} />
                {showActions && (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        data-testid="member-actions"
                        aria-label={`Actions for ${display}`}
                        disabled={
                          (roleMutation.isPending &&
                            roleMutation.variables?.userId === m.user_id) ||
                          (removeMutation.isPending &&
                            removeMutation.variables === m.user_id)
                        }
                      >
                        <MoreHorizontal className="h-4 w-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      {m.role === "member" && (
                        <DropdownMenuItem
                          data-testid="member-promote"
                          onSelect={() =>
                            roleMutation.mutate({
                              userId: m.user_id,
                              role: "admin",
                            })
                          }
                        >
                          Promote to admin
                        </DropdownMenuItem>
                      )}
                      {m.role === "admin" && (
                        <DropdownMenuItem
                          data-testid="member-demote"
                          onSelect={() =>
                            roleMutation.mutate({
                              userId: m.user_id,
                              role: "member",
                            })
                          }
                        >
                          Demote to member
                        </DropdownMenuItem>
                      )}
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        data-testid="member-remove"
                        variant="destructive"
                        onSelect={() => setRemoveTarget(m)}
                      >
                        Remove from team
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
              </div>
            </li>
          )
        })}
      </ul>

      <RemoveMemberConfirm
        member={removeTarget}
        open={removeTarget !== null}
        onOpenChange={(next) => {
          if (removeMutation.isPending) return
          if (!next) setRemoveTarget(null)
        }}
        onConfirm={() => {
          if (!removeTarget) return
          removeMutation.mutate(removeTarget.user_id)
        }}
        isPending={removeMutation.isPending}
      />
    </>
  )
}

export function MembersListPending() {
  return (
    <div
      className="flex flex-col gap-2 rounded-lg border p-3"
      data-testid="members-loading"
    >
      {Array.from({ length: 3 }).map((_, i) => (
        <div
          key={`members-skeleton-${i + 1}`}
          className="flex items-center gap-3"
        >
          <Skeleton className="h-8 w-8 rounded-full" />
          <Skeleton className="h-4 w-40" />
        </div>
      ))}
    </div>
  )
}

const MembersList = (props: Props) => <MembersListContent {...props} />

export default MembersList
