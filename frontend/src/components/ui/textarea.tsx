import * as React from "react"

import { VoiceTextarea } from "@/components/voice/VoiceTextarea"
import { cn } from "@/lib/utils"

type TextareaProps = React.ComponentProps<"textarea"> & {
  voice?: boolean
  voiceSensitive?: boolean
}

const textareaClasses =
  "border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive dark:bg-input/30 field-sizing-content min-h-24 w-full rounded-md border bg-transparent px-3 py-2 text-base shadow-xs transition-[color,box-shadow] outline-none focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-50 md:text-sm"

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, voice, voiceSensitive, ...props }, ref) => {
    if (voice === false || voiceSensitive || props.disabled || props.readOnly) {
      return (
        <textarea
          data-slot="textarea"
          className={cn(textareaClasses, className)}
          ref={ref}
          {...props}
        />
      )
    }

    return (
      <VoiceTextarea
        className={className}
        ref={ref}
        voice={voice}
        voiceSensitive={voiceSensitive}
        {...props}
      />
    )
  }
)
Textarea.displayName = "Textarea"

export { Textarea }
