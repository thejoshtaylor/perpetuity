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

type Props = {
  installation: { id: string; account_login: string } | null
  open: boolean
  onOpenChange: (next: boolean) => void
  onConfirm: () => void
  isPending: boolean
}

const UninstallConfirm = ({
  installation,
  open,
  onOpenChange,
  onConfirm,
  isPending,
}: Props) => {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="installation-uninstall-dialog"
        className="sm:max-w-md"
      >
        <DialogHeader>
          <DialogTitle>Forget GitHub installation</DialogTitle>
          <DialogDescription>
            {installation
              ? `Forget the local link to ${installation.account_login}? `
              : "Forget the local link to this installation? "}
            This only removes the binding on Perpetuity — the GitHub-side
            install must be revoked at github.com.
          </DialogDescription>
        </DialogHeader>

        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline" disabled={isPending}>
              Cancel
            </Button>
          </DialogClose>
          <LoadingButton
            type="button"
            variant="destructive"
            data-testid="installation-uninstall-confirm"
            loading={isPending}
            onClick={onConfirm}
          >
            Forget installation
          </LoadingButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default UninstallConfirm
