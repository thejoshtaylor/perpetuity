import * as React from "react"

import { VoiceInput } from "@/components/voice/VoiceInput"
import { cn } from "@/lib/utils"

type InputProps = React.ComponentProps<"input"> & {
  voice?: boolean
  voiceSensitive?: boolean
}

const inputClasses =
  "file:text-foreground placeholder:text-muted-foreground selection:bg-primary selection:text-primary-foreground dark:bg-input/30 border-input h-9 min-h-11 w-full min-w-0 rounded-md border bg-transparent px-3 py-1 text-base shadow-xs transition-[color,box-shadow] outline-none file:inline-flex file:h-7 file:border-0 file:bg-transparent file:text-sm file:font-medium disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 md:text-sm focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive"

function shouldRenderRawInput(props: InputProps) {
  if (props.voice === false || props.voiceSensitive) return true
  if (props.disabled || props.readOnly) return true

  const type = props.type ?? "text"
  if (["password", "hidden", "file", "checkbox", "radio", "submit", "button"].includes(type)) {
    return true
  }

  const sensitiveText = [props.name, props.id, props.autoComplete, props.inputMode]
    .filter(Boolean)
    .join(" ")
    .toLowerCase()
  return /password|otp|one-time|verification|secret|token|code/.test(sensitiveText)
}

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, voice, voiceSensitive, ...props }, ref) => {
    const inputProps = { ...props, type, voice, voiceSensitive }
    if (shouldRenderRawInput(inputProps)) {
      return (
        <input
          type={type}
          data-slot="input"
          className={cn(inputClasses, className)}
          ref={ref}
          {...props}
        />
      )
    }

    return (
      <VoiceInput
        className={className}
        ref={ref}
        type={type}
        voice={voice}
        voiceSensitive={voiceSensitive}
        {...props}
      />
    )
  }
)
Input.displayName = "Input"

export { Input }
