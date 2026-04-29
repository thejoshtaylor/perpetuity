// M005-sqm8et/S03/T05 — step list editor for workflow definitions

import { Plus, Trash2, GripVertical } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"

export type WorkflowStep = {
  step_index: number
  action: "claude" | "codex" | "shell" | "git"
  config: Record<string, unknown>
  target_container: "user_workspace"
}

type Props = {
  steps: WorkflowStep[]
  onChange: (steps: WorkflowStep[]) => void
}

const ACTION_OPTIONS: WorkflowStep["action"][] = [
  "claude",
  "codex",
  "shell",
  "git",
]

export function StepsEditor({ steps, onChange }: Props) {
  const addStep = () => {
    onChange([
      ...steps,
      {
        step_index: steps.length,
        action: "shell",
        config: {},
        target_container: "user_workspace",
      },
    ])
  }

  const removeStep = (index: number) => {
    const updated = steps
      .filter((_, i) => i !== index)
      .map((s, i) => ({ ...s, step_index: i }))
    onChange(updated)
  }

  const updateStep = (index: number, patch: Partial<WorkflowStep>) => {
    onChange(steps.map((s, i) => (i === index ? { ...s, ...patch } : s)))
  }

  const updateConfig = (index: number, raw: string) => {
    try {
      const parsed = JSON.parse(raw) as Record<string, unknown>
      updateStep(index, { config: parsed })
    } catch {
      // keep invalid JSON in local state until user fixes it
    }
  }

  return (
    <div className="flex flex-col gap-3" data-testid="steps-editor">
      {steps.length === 0 ? (
        <p className="text-sm text-muted-foreground">No steps yet.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {steps.map((step, i) => (
            <li
              key={i}
              className="flex flex-wrap items-start gap-2 rounded border p-3"
              data-testid={`step-row-${i}`}
            >
              <div className="flex items-center gap-1 pt-7">
                <GripVertical
                  className="h-4 w-4 text-muted-foreground"
                  aria-hidden
                />
                <span className="font-mono text-xs text-muted-foreground">
                  #{i + 1}
                </span>
              </div>

              <div className="flex flex-col gap-1">
                <Label htmlFor={`step-action-${i}`}>Action</Label>
                <Select
                  value={step.action}
                  onValueChange={(v) =>
                    updateStep(i, { action: v as WorkflowStep["action"] })
                  }
                >
                  <SelectTrigger
                    id={`step-action-${i}`}
                    data-testid={`step-action-${i}`}
                    className="w-32"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {ACTION_OPTIONS.map((a) => (
                      <SelectItem key={a} value={a}>
                        {a}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="flex flex-col gap-1 flex-1 min-w-[16rem]">
                <Label htmlFor={`step-config-${i}`}>
                  Config{" "}
                  <span className="text-xs text-muted-foreground">(JSON)</span>
                </Label>
                <Input
                  id={`step-config-${i}`}
                  data-testid={`step-config-${i}`}
                  defaultValue={JSON.stringify(step.config)}
                  onBlur={(e) => updateConfig(i, e.target.value)}
                  placeholder="{}"
                  className="font-mono text-xs"
                />
              </div>

              <div className="flex flex-col gap-1">
                <Label htmlFor={`step-target-${i}`}>Target</Label>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div>
                      <Select value={step.target_container} disabled={false}>
                        <SelectTrigger
                          id={`step-target-${i}`}
                          data-testid={`step-target-${i}`}
                          className="w-36"
                        >
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="user_workspace">
                            user_workspace
                          </SelectItem>
                          <SelectItem value="team_mirror" disabled>
                            team_mirror
                          </SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                  </TooltipTrigger>
                  <TooltipContent side="top">
                    team_mirror: Reserved for S04
                  </TooltipContent>
                </Tooltip>
              </div>

              <div className="pt-6">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  data-testid={`step-remove-${i}`}
                  onClick={() => removeStep(i)}
                  aria-label="Remove step"
                >
                  <Trash2 className="h-4 w-4 text-destructive" />
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
      <Button
        type="button"
        variant="outline"
        size="sm"
        data-testid="add-step"
        onClick={addStep}
        className="self-start"
      >
        <Plus className="h-4 w-4" />
        Add step
      </Button>
    </div>
  )
}

export default StepsEditor
