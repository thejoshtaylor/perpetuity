import { useMutation, useQueryClient } from "@tanstack/react-query"

import { AdminService } from "@/client"
import { Button } from "@/components/ui/button"
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
import useCustomToast from "@/hooks/useCustomToast"

type Props = {
  userId: string
  email: string
  open: boolean
  onOpenChange: (next: boolean) => void
  onSuccess?: () => void
}

const PromoteSystemAdminDialog = ({
  userId,
  email,
  open,
  onOpenChange,
  onSuccess,
}: Props) => {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()

  const mutation = useMutation({
    mutationFn: () => AdminService.promoteSystemAdmin({ userId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["users"] })
      showSuccessToast("Promoted to system admin")
      onOpenChange(false)
      onSuccess?.()
    },
    onError: (err) => {
      const e = err as { status?: number; body?: { detail?: string } }
      if (e.status === 404) {
        showErrorToast("User not found")
        return
      }
      if (e.status === 403) {
        showErrorToast("You don't have permission to promote users")
        return
      }
      showErrorToast(e.body?.detail || "Could not promote user")
    },
  })

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (mutation.isPending) return
        onOpenChange(next)
      }}
    >
      <DialogContent
        data-testid="promote-system-admin-dialog"
        className="sm:max-w-md"
      >
        <DialogHeader>
          <DialogTitle>Promote to system admin</DialogTitle>
          <DialogDescription>
            Promote {email} to system admin? They will gain access to every team
            and the admin panel.
          </DialogDescription>
        </DialogHeader>

        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline" disabled={mutation.isPending}>
              Cancel
            </Button>
          </DialogClose>
          <LoadingButton
            type="button"
            data-testid="promote-system-admin-confirm"
            loading={mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            Promote
          </LoadingButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default PromoteSystemAdminDialog
