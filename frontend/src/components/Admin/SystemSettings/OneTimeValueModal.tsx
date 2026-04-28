import { Copy, ShieldAlert } from "lucide-react"
import { useState } from "react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

type Props = {
  /** Plaintext value to display once. Held only in props/local state for the
   * lifetime of the modal — when the parent unmounts this component the
   * value is gone (closure of the FE side of MEM232). */
  value: string
  settingKey: string
  open: boolean
  onAcknowledge: () => void
}

export function OneTimeValueModal({
  value,
  settingKey,
  open,
  onAcknowledge,
}: Props) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    if (typeof navigator === "undefined" || !navigator.clipboard) return
    await navigator.clipboard.writeText(value)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 2000)
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // Closing via overlay/Escape is treated the same as acknowledge —
        // the parent unmounts this component, taking `value` with it.
        if (!next) onAcknowledge()
      }}
    >
      <DialogContent
        className="sm:max-w-md"
        showCloseButton={false}
        data-testid="system-settings-one-time-modal"
      >
        <DialogHeader>
          <DialogTitle>New value for {settingKey}</DialogTitle>
          <DialogDescription>
            This value will not be shown again. Save it now.
          </DialogDescription>
        </DialogHeader>

        <Alert variant="destructive">
          <ShieldAlert className="h-4 w-4" />
          <AlertTitle>Copy this value before closing</AlertTitle>
          <AlertDescription>
            For your security, the plaintext is only displayed once. Store it in
            your password manager or paste it into github.com now.
          </AlertDescription>
        </Alert>

        <div className="flex flex-col gap-2">
          <div
            data-testid="system-settings-one-time-value"
            className="rounded-md border bg-muted px-3 py-2 font-mono text-sm break-all"
          >
            {value}
          </div>
          <Button
            type="button"
            variant="outline"
            onClick={handleCopy}
            data-testid="system-settings-one-time-copy"
          >
            <Copy className="mr-2 h-4 w-4" />
            {copied ? "Copied" : "Copy"}
          </Button>
        </div>

        <DialogFooter>
          <Button
            type="button"
            onClick={onAcknowledge}
            data-testid="system-settings-one-time-acknowledge"
          >
            I've saved it
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export default OneTimeValueModal
