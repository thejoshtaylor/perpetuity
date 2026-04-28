import { AlertTriangle } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
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
  settingKey: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: () => void
  isPending: boolean
}

/**
 * Destructive-by-design (D025/MEM232): generating a fresh webhook secret
 * invalidates every in-flight delivery from github.com until the upstream
 * secret is updated. The operator MUST rotate upstream first; this dialog
 * blocks the click until they confirm they understand.
 */
export function GenerateConfirmDialog({
  settingKey,
  open,
  onOpenChange,
  onConfirm,
  isPending,
}: Props) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Generate new {settingKey}?</DialogTitle>
          <DialogDescription>
            Re-generating breaks any existing GitHub webhook deliveries until
            you update the upstream secret on github.com — proceed?
          </DialogDescription>
        </DialogHeader>

        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Destructive by design</AlertTitle>
          <AlertDescription>
            The plaintext value is shown only once after generation. Save it
            into your password manager and update github.com before closing the
            next dialog.
          </AlertDescription>
        </Alert>

        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline" disabled={isPending}>
              Cancel
            </Button>
          </DialogClose>
          <LoadingButton
            type="button"
            variant="destructive"
            onClick={onConfirm}
            loading={isPending}
            data-testid="system-settings-generate-confirm"
          >
            Generate
          </LoadingButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default GenerateConfirmDialog
