import { useEffect, useState } from "react"

import type { TeamMemberPublic } from "@/client"
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
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"

type Props = {
  member: TeamMemberPublic | null
  open: boolean
  onOpenChange: (next: boolean) => void
  onConfirm: () => void
  isPending: boolean
}

const CONFIRM_PHRASE = "remove"

const RemoveMemberConfirm = ({
  member,
  open,
  onOpenChange,
  onConfirm,
  isPending,
}: Props) => {
  const [confirmText, setConfirmText] = useState("")

  useEffect(() => {
    if (!open) setConfirmText("")
  }, [open])

  const expected = member?.email ?? CONFIRM_PHRASE
  const display = member?.full_name?.trim() || member?.email || ""
  const canConfirm =
    confirmText === expected || confirmText.trim() === CONFIRM_PHRASE

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="remove-member-dialog" className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Remove member</DialogTitle>
          <DialogDescription>
            {display
              ? `Remove ${display} from this team?`
              : "Remove this member from the team?"}{" "}
            They will lose access immediately. To confirm, type their email or "
            {CONFIRM_PHRASE}".
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-2 py-2">
          <Label htmlFor="remove-member-confirm-input">Confirm</Label>
          <Input
            id="remove-member-confirm-input"
            data-testid="remove-member-confirm-input"
            autoFocus
            autoComplete="off"
            value={confirmText}
            placeholder={expected}
            onChange={(e) => setConfirmText(e.target.value)}
            disabled={isPending}
          />
        </div>

        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline" disabled={isPending}>
              Cancel
            </Button>
          </DialogClose>
          <LoadingButton
            type="button"
            variant="destructive"
            data-testid="remove-member-confirm"
            disabled={!canConfirm}
            loading={isPending}
            onClick={onConfirm}
          >
            Remove
          </LoadingButton>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default RemoveMemberConfirm
