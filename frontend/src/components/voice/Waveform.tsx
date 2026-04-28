import { cn } from "@/lib/utils"

type WaveformProps = {
  levels: number[]
  active?: boolean
  className?: string
}

function Waveform({ levels, active = false, className }: WaveformProps) {
  return (
    <div
      aria-hidden="true"
      className={cn("flex h-5 items-center gap-0.5", className)}
      data-testid="voice-waveform"
    >
      {levels.map((level, index) => (
        <span
          // Static bar count/order; index is stable and no audio data leaves memory.
          key={index}
          className={cn(
            "w-0.5 rounded-full bg-primary/60 transition-[height,opacity] duration-100",
            active ? "opacity-100" : "opacity-35"
          )}
          style={{ height: `${Math.max(4, Math.round(4 + level * 16))}px` }}
        />
      ))}
    </div>
  )
}

export { Waveform }
