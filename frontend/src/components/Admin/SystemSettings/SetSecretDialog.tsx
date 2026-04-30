import { zodResolver } from "@hookform/resolvers/zod"
import { useEffect } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"

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
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { LoadingButton } from "@/components/ui/loading-button"
import { cn } from "@/lib/utils"

type Variant = "pem" | "string"
type InputType = "number" | "text"

const pemSchema = z.object({
  value: z
    .string()
    .min(1, { message: "PEM cannot be empty" })
    .refine((s) => s.includes("BEGIN") && s.includes("PRIVATE KEY"), {
      message: "Value must be a PEM-encoded private key",
    }),
})

const stringSchema = z.object({
  value: z.string().min(1, { message: "Value cannot be empty" }),
})

const numberSchema = z.object({
  value: z
    .string()
    .min(1, { message: "Value cannot be empty" })
    .refine((s) => /^-?\d+$/.test(s.trim()), {
      message: "Must be a whole number (e.g. 10, 1800)",
    }),
})

type Props = {
  settingKey: string
  hasValue: boolean
  variant: Variant
  /** Whether the backend expects a numeric integer value. */
  inputType?: InputType
  open: boolean
  onOpenChange: (open: boolean) => void
  onSubmit: (value: string) => void
  isPending: boolean
}

/**
 * Operator-supplied secret entry. Used for both:
 *  - github_app_private_key (variant="pem", multiline textarea)
 *  - github_app_webhook_secret (variant="string", single-line) — the operator
 *    can paste their own secret here, or use Generate to seed one.
 *
 * The plaintext lives only in react-hook-form state for the lifetime of this
 * dialog — when the dialog closes (mutation success or Cancel) the form is
 * reset and the value is gone.
 */
export function SetSecretDialog({
  settingKey,
  hasValue,
  variant,
  inputType = "text",
  open,
  onOpenChange,
  onSubmit,
  isPending,
}: Props) {
  const schema =
    variant === "pem"
      ? pemSchema
      : inputType === "number"
        ? numberSchema
        : stringSchema
  type FormData = z.infer<typeof schema>

  const form = useForm<FormData>({
    resolver: zodResolver(schema),
    mode: "onSubmit",
    defaultValues: { value: "" },
  })

  useEffect(() => {
    if (!open) form.reset({ value: "" })
  }, [open, form])

  const handleSubmit = (data: FormData) => {
    onSubmit(data.value)
  }

  const verb = hasValue ? "Replace" : "Set"
  const defaultPlaceholder =
    variant === "pem"
      ? "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"
      : inputType === "number"
        ? "Enter a whole number"
        : "Paste value here"

  const isSensitive = variant === "pem" || inputType === "text"

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {verb} {settingKey}
          </DialogTitle>
          <DialogDescription>
            {isSensitive
              ? "The plaintext is encrypted at rest. After saving, this value will never be returned by the API again."
              : "Enter a value for this setting. The value will be stored as-is and can be replaced later."}
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(handleSubmit)}>
            <div className="grid gap-4 py-4">
              <FormField
                control={form.control}
                name="value"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      Value <span className="text-destructive">*</span>
                    </FormLabel>
                    <FormControl>
                      {variant === "pem" ? (
                        // M005-oaptsz/S04/T03: system-settings PEM payload is
                        // operator-supplied secret material. Stays a raw
                        // <textarea> with data-voice-disabled so the audit
                        // asserts no mic ever rendered next to it.
                        <textarea
                          {...field}
                          placeholder={defaultPlaceholder}
                          rows={10}
                          autoComplete="off"
                          spellCheck={false}
                          data-voice-disabled="true"
                          data-testid={`system-settings-set-input-${settingKey}`}
                          className={cn(
                            "border-input placeholder:text-muted-foreground focus-visible:ring-ring/50 flex w-full rounded-md border bg-transparent px-3 py-2 text-sm font-mono shadow-xs transition-[color,box-shadow] outline-none focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-50",
                          )}
                        />
                      ) : inputType === "number" ? (
                        // Numeric setting — render a number-mode text input.
                        // We use type="text" with inputMode="numeric" so the
                        // browser shows a numeric keyboard on mobile without
                        // the step arrows and browser-native number parsing
                        // interfering with react-hook-form's string schema.
                        <Input
                          {...field}
                          placeholder={defaultPlaceholder}
                          autoComplete="off"
                          spellCheck={false}
                          type="text"
                          inputMode="numeric"
                          pattern="-?\d*"
                          voice={false}
                          data-testid={`system-settings-set-input-${settingKey}`}
                        />
                      ) : (
                        // M005-oaptsz/S04/T03: single-line secret value
                        // (webhook secret, etc). voice={false} forces the
                        // Input primitive to render a raw <input> with no
                        // mic — operator secrets must never be dictated.
                        <Input
                          {...field}
                          placeholder={defaultPlaceholder}
                          autoComplete="off"
                          spellCheck={false}
                          type="text"
                          voice={false}
                          data-voice-disabled="true"
                          data-testid={`system-settings-set-input-${settingKey}`}
                        />
                      )}
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            <DialogFooter>
              <DialogClose asChild>
                <Button variant="outline" disabled={isPending}>
                  Cancel
                </Button>
              </DialogClose>
              <LoadingButton
                type="submit"
                loading={isPending}
                data-testid={`system-settings-set-submit-${settingKey}`}
              >
                Save
              </LoadingButton>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

export default SetSecretDialog
