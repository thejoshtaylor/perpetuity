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
import { Textarea } from "@/components/ui/textarea"

type Props = {
  /** "claude" or "codex" — drives the title + testid namespacing. */
  kind: "claude" | "codex"
  open: boolean
  onOpenChange: (open: boolean) => void
  onSubmit: (prompt: string) => void
  isPending: boolean
  /** Optional server-side error surfaced by the parent's mutation
   * (e.g. `missing_required_field` 400 from the dispatch route). */
  serverError?: string | null
}

const KIND_LABELS: Record<Props["kind"], string> = {
  claude: "Run Claude",
  codex: "Run Codex",
}

/**
 * Dispatch-once prompt entry for a `_direct_claude` / `_direct_codex`
 * workflow. The prompt body lives only inside this component's local
 * `useState`; on close it resets so the parent never holds it after the
 * mutation fires. Mirrors PasteSecretDialog's local-only-plaintext shape
 * because the prompt is a sensitive workload input (R018: stdout/stderr
 * are persisted, but the prompt body is NEVER logged).
 */
export function PromptDialog({
  kind,
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
    touched && trimmed.length === 0 ? "Prompt cannot be empty" : null

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setTouched(true)
    if (trimmed.length === 0) return
    onSubmit(value)
  }

  const title = KIND_LABELS[kind]

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="sm:max-w-lg"
        data-testid={`direct-ai-prompt-dialog-${kind}`}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            Enter a prompt to send to the{" "}
            {kind === "claude" ? "Claude" : "Codex"} CLI inside this team's
            workspace container.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit}>
          <div className="grid gap-2 py-4">
            <Label htmlFor={`direct-ai-prompt-input-${kind}`}>
              Prompt <span className="text-destructive">*</span>
            </Label>
            <Textarea
              id={`direct-ai-prompt-input-${kind}`}
              data-testid={`direct-ai-prompt-input-${kind}`}
              voice={false}
              autoComplete="off"
              spellCheck={true}
              placeholder="e.g. List the files in this repo"
              rows={5}
              value={value}
              onChange={(e) => {
                setValue(e.target.value)
                if (!touched) setTouched(true)
              }}
              aria-invalid={Boolean(clientError ?? serverError)}
            />
            {(clientError || serverError) && (
              <p
                className="text-destructive text-sm"
                data-testid={`direct-ai-prompt-error-${kind}`}
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
                data-testid={`direct-ai-prompt-cancel-${kind}`}
              >
                Cancel
              </Button>
            </DialogClose>
            <LoadingButton
              type="submit"
              loading={isPending}
              disabled={trimmed.length === 0}
              data-testid={`direct-ai-prompt-submit-${kind}`}
            >
              Submit
            </LoadingButton>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export default PromptDialog
