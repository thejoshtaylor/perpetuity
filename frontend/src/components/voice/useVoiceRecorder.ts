import * as React from "react"

import { ApiError } from "@/client/core/ApiError"
import { VoiceService } from "@/client/sdk.gen"

const MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
] as const

const UPLOAD_TIMEOUT_MS = 15_000
const WAVEFORM_BARS = 12

type VoiceRecorderStatus = "idle" | "recording" | "uploading" | "error"

type VoiceRecorderErrorKind =
  | "permission"
  | "codec"
  | "empty"
  | "rate_limit"
  | "api"
  | "timeout"
  | "bad_response"
  | "unsupported"
  | "cleanup"

export type VoiceRecorderError = {
  kind: VoiceRecorderErrorKind
  message: string
  retryAfter?: number
}

type UseVoiceRecorderOptions = {
  onTranscribed?: (text: string) => void
  onError?: (error: VoiceRecorderError) => void
}

type UseVoiceRecorderResult = {
  status: VoiceRecorderStatus
  isRecording: boolean
  isUploading: boolean
  error: VoiceRecorderError | null
  levels: number[]
  start: () => Promise<void>
  stop: () => void
  resetError: () => void
}

type RecorderRefs = {
  stream: MediaStream | null
  recorder: MediaRecorder | null
  audioContext: AudioContext | null
  analyser: AnalyserNode | null
  animationFrame: number | null
  uploadRequest: { cancel: () => void } | null
  chunks: Blob[]
}

const emptyLevels = Array.from({ length: WAVEFORM_BARS }, () => 0)

function emitVoiceDiagnostic(
  event: string,
  detail: Record<string, string | number | boolean | undefined> = {},
) {
  console.info(`voice.recorder.${event}`, detail)
}

function getSupportedMimeType(): string | null {
  if (typeof MediaRecorder === "undefined") return null
  if (typeof MediaRecorder.isTypeSupported !== "function") {
    return "audio/webm"
  }
  return (
    MIME_CANDIDATES.find((candidate) =>
      MediaRecorder.isTypeSupported(candidate),
    ) ?? null
  )
}

function normalizeError(error: unknown): VoiceRecorderError {
  if (error instanceof DOMException) {
    if (error.name === "NotAllowedError" || error.name === "SecurityError") {
      return {
        kind: "permission",
        message:
          "Microphone permission was denied. Enable mic access and try again.",
      }
    }
    return {
      kind: "api",
      message:
        "Microphone could not start. Check your input device and try again.",
    }
  }

  if (error instanceof ApiError) {
    if (error.status === 429) {
      return {
        kind: "rate_limit",
        message: "Voice transcription is rate limited. Try again in a minute.",
      }
    }
    return {
      kind: "api",
      message: "Voice transcription failed. Your typed text was preserved.",
    }
  }

  if (error instanceof Error && error.message === "voice_upload_timeout") {
    return {
      kind: "timeout",
      message: "Voice transcription timed out. Try again.",
    }
  }

  if (error instanceof Error && error.message === "voice_bad_response") {
    return {
      kind: "bad_response",
      message: "Voice transcription returned an unreadable response.",
    }
  }

  if (error instanceof Error && error.message === "voice_empty_audio") {
    return {
      kind: "empty",
      message: "No voice audio was captured. Try recording again.",
    }
  }

  if (error instanceof Error && error.message === "voice_unsupported_codec") {
    return {
      kind: "codec",
      message: "This browser cannot record a supported voice format.",
    }
  }

  return {
    kind: "api",
    message: "Voice transcription failed. Your typed text was preserved.",
  }
}

function fileExtensionForMime(mimeType: string) {
  return mimeType.includes("mp4") ? "m4a" : "webm"
}

export function useVoiceRecorder({
  onTranscribed,
  onError,
}: UseVoiceRecorderOptions = {}): UseVoiceRecorderResult {
  const refs = React.useRef<RecorderRefs>({
    stream: null,
    recorder: null,
    audioContext: null,
    analyser: null,
    animationFrame: null,
    uploadRequest: null,
    chunks: [],
  })
  const [status, setStatus] = React.useState<VoiceRecorderStatus>("idle")
  const [levels, setLevels] = React.useState<number[]>(emptyLevels)
  const [error, setError] = React.useState<VoiceRecorderError | null>(null)

  const publishError = React.useCallback(
    (raw: unknown) => {
      const normalized = normalizeError(raw)
      setError(normalized)
      setStatus("error")
      emitVoiceDiagnostic(
        normalized.kind === "permission"
          ? "permission_denied"
          : "upload_failed",
        { kind: normalized.kind, retryAfter: normalized.retryAfter },
      )
      onError?.(normalized)
    },
    [onError],
  )

  const cleanup = React.useCallback(() => {
    const current = refs.current
    if (current.animationFrame !== null) {
      cancelAnimationFrame(current.animationFrame)
      current.animationFrame = null
    }

    current.recorder = null
    current.analyser = null
    current.chunks = []

    for (const track of current.stream?.getTracks() ?? []) {
      track.stop()
    }
    current.stream = null

    if (current.audioContext && current.audioContext.state !== "closed") {
      current.audioContext.close().catch(() => {
        emitVoiceDiagnostic("cleanup_failed", { resource: "audio_context" })
      })
    }
    current.audioContext = null
    setLevels(emptyLevels)
  }, [])

  React.useEffect(() => {
    return () => {
      refs.current.uploadRequest?.cancel()
      cleanup()
    }
  }, [cleanup])

  const updateWaveform = React.useCallback(() => {
    const analyser = refs.current.analyser
    if (!analyser) return

    const samples = new Uint8Array(analyser.frequencyBinCount)
    analyser.getByteTimeDomainData(samples)
    const chunkSize = Math.max(1, Math.floor(samples.length / WAVEFORM_BARS))
    const nextLevels = Array.from({ length: WAVEFORM_BARS }, (_, index) => {
      const start = index * chunkSize
      const end = Math.min(samples.length, start + chunkSize)
      let total = 0
      for (let cursor = start; cursor < end; cursor += 1) {
        total += Math.abs(samples[cursor] - 128)
      }
      return Math.min(1, total / Math.max(1, end - start) / 64)
    })
    setLevels(nextLevels)
    refs.current.animationFrame = requestAnimationFrame(updateWaveform)
  }, [])

  const upload = React.useCallback(
    async (blob: Blob, mimeType: string) => {
      if (!blob.size) throw new Error("voice_empty_audio")
      setStatus("uploading")
      const file = new File(
        [blob],
        `voice-recording.${fileExtensionForMime(mimeType)}`,
        {
          type: mimeType,
        },
      )
      const request = VoiceService.transcribeVoice({ formData: { file } })
      refs.current.uploadRequest = request
      const timeout = window.setTimeout(
        () => request.cancel(),
        UPLOAD_TIMEOUT_MS,
      )

      try {
        const result = await request
        if (!result || typeof result.text !== "string") {
          throw new Error("voice_bad_response")
        }
        onTranscribed?.(result.text)
        emitVoiceDiagnostic("transcribed", { bytes: blob.size, mime: mimeType })
        setStatus("idle")
        setError(null)
      } catch (uploadError) {
        if (request.isCancelled) {
          throw new Error("voice_upload_timeout")
        }
        throw uploadError
      } finally {
        window.clearTimeout(timeout)
        refs.current.uploadRequest = null
      }
    },
    [onTranscribed],
  )

  const start = React.useCallback(async () => {
    setError(null)
    if (
      typeof navigator === "undefined" ||
      !navigator.mediaDevices?.getUserMedia
    ) {
      publishError({ message: "voice_unsupported" })
      return
    }

    const mimeType = getSupportedMimeType()
    if (!mimeType) {
      publishError(new Error("voice_unsupported_codec"))
      return
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      refs.current.stream = stream
      refs.current.chunks = []

      const AudioContextClass = window.AudioContext || window.webkitAudioContext
      if (AudioContextClass) {
        const audioContext = new AudioContextClass()
        const analyser = audioContext.createAnalyser()
        analyser.fftSize = 256
        audioContext.createMediaStreamSource(stream).connect(analyser)
        refs.current.audioContext = audioContext
        refs.current.analyser = analyser
        refs.current.animationFrame = requestAnimationFrame(updateWaveform)
      }

      const recorder = new MediaRecorder(stream, { mimeType })
      refs.current.recorder = recorder
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) refs.current.chunks.push(event.data)
      }
      recorder.onerror = () => publishError(new Error("voice_recorder_error"))
      recorder.onstop = () => {
        const chunks = refs.current.chunks
        cleanup()
        upload(new Blob(chunks, { type: mimeType }), mimeType).catch(
          publishError,
        )
      }
      recorder.start()
      setStatus("recording")
      emitVoiceDiagnostic("started", { mime: mimeType })
    } catch (startError) {
      cleanup()
      publishError(startError)
    }
  }, [cleanup, publishError, updateWaveform, upload])

  const stop = React.useCallback(() => {
    const recorder = refs.current.recorder
    if (!recorder || recorder.state === "inactive") {
      cleanup()
      setStatus("idle")
      return
    }
    emitVoiceDiagnostic("stopped", { state: recorder.state })
    recorder.stop()
  }, [cleanup])

  return {
    status,
    isRecording: status === "recording",
    isUploading: status === "uploading",
    error,
    levels,
    start,
    stop,
    resetError: () => setError(null),
  }
}

declare global {
  interface Window {
    webkitAudioContext?: typeof AudioContext
  }
}
