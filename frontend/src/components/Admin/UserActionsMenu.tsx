import { EllipsisVertical, ShieldCheck } from "lucide-react"
import { useState } from "react"

import type { UserPublic } from "@/client"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import useAuth from "@/hooks/useAuth"
import DeleteUser from "./DeleteUser"
import EditUser from "./EditUser"
import PromoteSystemAdminDialog from "./PromoteSystemAdminDialog"

interface UserActionsMenuProps {
  user: UserPublic
}

export const UserActionsMenu = ({ user }: UserActionsMenuProps) => {
  const [open, setOpen] = useState(false)
  const [promoteOpen, setPromoteOpen] = useState(false)
  const { user: currentUser } = useAuth()

  if (user.id === currentUser?.id) {
    return null
  }

  const canPromoteToSystemAdmin =
    currentUser?.role === "system_admin" && user.role !== "system_admin"

  return (
    <>
      <DropdownMenu open={open} onOpenChange={setOpen}>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon">
            <EllipsisVertical />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <EditUser user={user} onSuccess={() => setOpen(false)} />
          {canPromoteToSystemAdmin && (
            <DropdownMenuItem
              data-testid="promote-system-admin"
              onSelect={(e) => {
                e.preventDefault()
                setOpen(false)
                setPromoteOpen(true)
              }}
            >
              <ShieldCheck />
              Promote to system admin
            </DropdownMenuItem>
          )}
          <DeleteUser id={user.id} onSuccess={() => setOpen(false)} />
        </DropdownMenuContent>
      </DropdownMenu>
      {canPromoteToSystemAdmin && (
        <PromoteSystemAdminDialog
          userId={user.id}
          email={user.email}
          open={promoteOpen}
          onOpenChange={setPromoteOpen}
        />
      )}
    </>
  )
}
