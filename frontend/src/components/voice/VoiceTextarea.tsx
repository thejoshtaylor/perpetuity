import { Mic, Square } from "lucide-react"
import * as React from "react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { useVoiceRecorder } from "./useVoiceRecorder"
import { Waveform } from "./Waveform"

export type VoiceTextareaProps = React.ComponentProps<"textarea"> & {
  voice?: boolean
  voiceSensitive?: boolean
}

const textareaClasses =
  "border-input placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-ring/50 aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive dark:bg-input/30 field-sizing-content min-h-24 w-full rounded-md border bg-transparent px-3 py-2 text-base shadow-xs transition-[color,box-shadow] outline-none focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-50 md:text-sm"

function assignRef<T>(ref: React.Ref<T> | undefined, value: T | null) {
  if (typeof ref === "function") ref(value)
  else if (ref) ref.current = value
}

function buildTextareaChangeEvent(
  element: HTMLTextAreaElement,
  value: string,
): React.ChangeEvent<HTMLTextAreaElement> {
  Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype,
    "value",
  )?.set?.call(element, value)
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
  } as React.ChangeEvent<HTMLTextAreaElement>
}

function appendTranscript(currentValue: string, transcript: string) {
  if (!currentValue.trim()) return transcript
  if (!transcript.trim()) return currentValue
  return `${currentValue.trimEnd()} ${transcript.trimStart()}`
}

function shouldHideVoiceControl(props: VoiceTextareaProps) {
  if (props.voice === false || props.voiceSensitive) return true
  if (props.disabled || props.readOnly) return true

  const sensitiveText = [props.name, props.id, props.autoComplete]
    .filter(Boolean)
    .join(" ")
    .toLowerCase()
  return /password|otp|one-time|verification|secret|token|code/.test(
    sensitiveText,
  )
}

const VoiceTextarea = React.forwardRef<HTMLTextAreaElement, VoiceTextareaProps>(
  (
    {
      className,
      onChange,
      voice: _voice,
      voiceSensitive: _voiceSensitive,
      ...props
    },
    ref,
  ) => {
    const textareaRef = React.useRef<HTMLTextAreaElement | null>(null)
    const shouldHide = shouldHideVoiceControl({
      ...props,
      voice: _voice,
      voiceSensitive: _voiceSensitive,
    })

    const injectTranscript = React.useCallback(
      (transcript: string) => {
        const element = textareaRef.current
        if (!element) return
        const currentValue =
          typeof props.value === "string" ? props.value : element.value
        const nextValue = appendTranscript(currentValue, transcript)
        onChange?.(buildTextareaChangeEvent(element, nextValue))
      },
      [onChange, props.value],
    )

    const recorder = useVoiceRecorder({ onTranscribed: injectTranscript })
    const describedBy = [
      props["aria-describedby"],
      recorder.error
        ? `${props.id ?? props.name ?? "voice-textarea"}-voice-error`
        : undefined,
    ]
      .filter(Boolean)
      .join(" ")

    if (shouldHide) {
      return (
        <textarea
          data-slot="textarea"
          className={cn(textareaClasses, className)}
          ref={(node) => {
            textareaRef.current = node
            assignRef(ref, node)
          }}
          onChange={onChange}
          {...props}
        />
      )
    }

    return (
      <div className="space-y-1" data-voice-textarea="true">
        <div className="relative">
          <textarea
            data-slot="textarea"
            className={cn(textareaClasses, "pr-14", className)}
            ref={(node) => {
              textareaRef.current = node
              assignRef(ref, node)
            }}
            onChange={onChange}
            aria-describedby={describedBy || undefined}
            {...props}
          />
          <div className="absolute right-1 top-1 flex min-h-11 items-center gap-1">
            {(recorder.isRecording || recorder.isUploading) && (
              <Waveform
                active={recorder.isRecording}
                levels={recorder.levels}
              />
            )}
            <Button
              aria-label={
                recorder.isRecording
                  ? "Stop voice dictation"
                  : "Start voice dictation"
              }
              className="min-h-11 min-w-11"
              data-testid="voice-textarea-toggle"
              disabled={recorder.isUploading}
              onClick={recorder.isRecording ? recorder.stop : recorder.start}
              size="icon-sm"
              type="button"
              variant="ghost"
            >
              {recorder.isRecording ? (
                <Square aria-hidden="true" />
              ) : (
                <Mic aria-hidden="true" />
              )}
            </Button>
          </div>
        </div>
        {recorder.error && (
          <p
            className="text-destructive text-sm"
            data-testid="voice-textarea-error"
            id={`${props.id ?? props.name ?? "voice-textarea"}-voice-error`}
            role="status"
          >
            {recorder.error.message}
          </p>
        )}
      </div>
    )
  },
)

VoiceTextarea.displayName = "VoiceTextarea"

export { VoiceTextarea }
