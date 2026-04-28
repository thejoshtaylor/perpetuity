import * as React from "react"
import { Mic, Square } from "lucide-react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { useVoiceRecorder } from "./useVoiceRecorder"
import { Waveform } from "./Waveform"

export type VoiceInputProps = React.ComponentProps<"input"> & {
  voice?: boolean
  voiceSensitive?: boolean
}

const inputClasses =
  "file:text-foreground placeholder:text-muted-foreground selection:bg-primary selection:text-primary-foreground dark:bg-input/30 border-input h-9 min-h-11 w-full min-w-0 rounded-md border bg-transparent px-3 py-1 text-base shadow-xs transition-[color,box-shadow] outline-none file:inline-flex file:h-7 file:border-0 file:bg-transparent file:text-sm file:font-medium disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 md:text-sm focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive"

function assignRef<T>(ref: React.Ref<T> | undefined, value: T | null) {
  if (typeof ref === "function") ref(value)
  else if (ref) ref.current = value
}

function buildChangeEvent<T extends HTMLInputElement | HTMLTextAreaElement>(
  element: T,
  value: string
): React.ChangeEvent<T> {
  Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set?.call(
    element,
    value
  )
  return {
    target: element,
    currentTarget: element,
    bubbles: true,
    cancelable: false,
    defaultPrevented: false,
    eventPhase: 3,
    isTrusted: false,
    nativeEvent: new Event("input", { bubbles: true }),
    preventDefault() {},
    isDefaultPrevented: () => false,
    stopPropagation() {},
    isPropagationStopped: () => false,
    persist() {},
    timeStamp: Date.now(),
    type: "change",
  } as React.ChangeEvent<T>
}

function appendTranscript(currentValue: string, transcript: string) {
  if (!currentValue.trim()) return transcript
  if (!transcript.trim()) return currentValue
  return `${currentValue.trimEnd()} ${transcript.trimStart()}`
}

function shouldHideVoiceControl(props: VoiceInputProps) {
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

const VoiceInput = React.forwardRef<HTMLInputElement, VoiceInputProps>(
  ({ className, type, onChange, voice: _voice, voiceSensitive: _voiceSensitive, ...props }, ref) => {
    const inputRef = React.useRef<HTMLInputElement | null>(null)
    const shouldHide = shouldHideVoiceControl({
      ...props,
      type,
      voice: _voice,
      voiceSensitive: _voiceSensitive,
    })

    const injectTranscript = React.useCallback(
      (transcript: string) => {
        const element = inputRef.current
        if (!element) return
        const currentValue = typeof props.value === "string" ? props.value : element.value
        const nextValue = appendTranscript(currentValue, transcript)
        onChange?.(buildChangeEvent(element, nextValue))
      },
      [onChange, props.value]
    )

    const recorder = useVoiceRecorder({ onTranscribed: injectTranscript })
    const describedBy = [props["aria-describedby"], recorder.error ? `${props.id ?? props.name ?? "voice-input"}-voice-error` : undefined]
      .filter(Boolean)
      .join(" ")

    if (shouldHide) {
      return (
        <input
          type={type}
          data-slot="input"
          className={cn(inputClasses, className)}
          ref={(node) => {
            inputRef.current = node
            assignRef(ref, node)
          }}
          onChange={onChange}
          {...props}
        />
      )
    }

    return (
      <div className="space-y-1" data-voice-input="true">
        <div className="relative flex items-center gap-2">
          <input
            type={type}
            data-slot="input"
            className={cn(inputClasses, "pr-14", className)}
            ref={(node) => {
              inputRef.current = node
              assignRef(ref, node)
            }}
            onChange={onChange}
            aria-describedby={describedBy || undefined}
            {...props}
          />
          <div className="absolute right-1 flex min-h-11 items-center gap-1">
            {(recorder.isRecording || recorder.isUploading) && (
              <Waveform active={recorder.isRecording} levels={recorder.levels} />
            )}
            <Button
              aria-label={recorder.isRecording ? "Stop voice dictation" : "Start voice dictation"}
              className="min-h-11 min-w-11"
              data-testid="voice-input-toggle"
              disabled={recorder.isUploading}
              onClick={recorder.isRecording ? recorder.stop : recorder.start}
              size="icon-sm"
              type="button"
              variant="ghost"
            >
              {recorder.isRecording ? <Square aria-hidden="true" /> : <Mic aria-hidden="true" />}
            </Button>
          </div>
        </div>
        {recorder.error && (
          <p
            className="text-destructive text-sm"
            data-testid="voice-input-error"
            id={`${props.id ?? props.name ?? "voice-input"}-voice-error`}
            role="status"
          >
            {recorder.error.message}
          </p>
        )}
      </div>
    )
  }
)

VoiceInput.displayName = "VoiceInput"

export { VoiceInput }
