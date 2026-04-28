import { useEffect, useState } from "react"

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
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"
import { PasswordInput } from "@/components/ui/password-input"

type Props = {
  secretKey: string
  hasValue: boolean
  open: boolean
  onOpenChange: (open: boolean) => void
  onSubmit: (value: string) => void
  isPending: boolean
  /** Optional server-side error surfaced by the parent's mutation
   * (e.g. `invalid_value_shape` 400 from the validator). Rendered inline
   * so the operator never needs DevTools to read the discriminator. */
  serverError?: string | null
}

/**
 * Paste-once entry for a team secret (`claude_api_key`, `openai_api_key`).
 *
 * Plaintext lives only inside this component's local `useState` for the
 * lifetime of the dialog. On close the value is reset; the parent never
 * holds it after the mutation fires. Mirrors M004's `SetSecretDialog`
 * but uses the `PasswordInput` primitive (show/hide eye toggle) because
 * an API key is a single-line bearer-style secret, not a PEM blob.
 */
export function PasteSecretDialog({
  secretKey,
  hasValue,
  open,
  onOpenChange,
  onSubmit,
  isPending,
  serverError,
}: Props) {
  const [value, setValue] = useState("")
  const [touched, setTouched] = useState(false)

  useEffect(() => {
    if (!open) {
      setValue("")
      setTouched(false)
    }
  }, [open])

  const trimmed = value.trim()
  const clientError =
    touched && trimmed.length === 0 ? "Value cannot be empty" : null

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setTouched(true)
    if (trimmed.length === 0) return
    onSubmit(value)
  }

  const verb = hasValue ? "Replace" : "Set"
  const placeholder =
    secretKey === "claude_api_key"
      ? "sk-ant-..."
      : secretKey === "openai_api_key"
        ? "sk-..."
        : "Paste secret"

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="sm:max-w-md"
        data-testid={`team-secret-paste-dialog-${secretKey}`}
      >
        <DialogHeader>
          <DialogTitle>
            {verb} {secretKey}
          </DialogTitle>
          <DialogDescription>
            The plaintext is encrypted at rest. After saving, this value will
            never be returned by the API again.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit}>
          <div className="grid gap-2 py-4">
            <Label htmlFor={`team-secret-input-${secretKey}`}>
              Value <span className="text-destructive">*</span>
            </Label>
            <PasswordInput
              id={`team-secret-input-${secretKey}`}
              data-testid={`team-secret-paste-input-${secretKey}`}
              autoComplete="off"
              spellCheck={false}
              placeholder={placeholder}
              value={value}
              onChange={(e) => {
                setValue(e.target.value)
                if (!touched) setTouched(true)
              }}
              error={clientError ?? serverError ?? undefined}
            />
            {(clientError || serverError) && (
              <p
                className="text-destructive text-sm"
                data-testid={`team-secret-paste-error-${secretKey}`}
              >
                {clientError ?? serverError}
              </p>
            )}
          </div>

          <DialogFooter>
            <DialogClose asChild>
              <Button
                type="button"
                variant="outline"
                disabled={isPending}
                data-testid={`team-secret-paste-cancel-${secretKey}`}
              >
                Cancel
              </Button>
            </DialogClose>
            <LoadingButton
              type="submit"
              loading={isPending}
              disabled={trimmed.length === 0}
              data-testid={`team-secret-paste-submit-${secretKey}`}
            >
              Save
            </LoadingButton>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export default PasteSecretDialog
