import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { toast } from "sonner"

import {
  type ApiError,
  type ProjectPushRulePublic,
  type ProjectPushRulePut,
  ProjectsService,
} from "@/client"
import { Badge } from "@/components/ui/badge"
import { Card } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { LoadingButton } from "@/components/ui/loading-button"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Skeleton } from "@/components/ui/skeleton"

type Props = {
  projectId: string
}

type Mode = "auto" | "rule" | "manual_workflow"

function pushRuleQueryOptions(projectId: string) {
  return {
    queryKey: ["project", projectId, "push-rule"] as const,
    queryFn: async (): Promise<ProjectPushRulePublic> => {
      const res = await ProjectsService.getProjectPushRule({ projectId })
      return res as ProjectPushRulePublic
    },
  }
}

function extractDetail(err: unknown): { detail?: string; status?: number } {
  const apiErr = err as ApiError | undefined
  const status = apiErr?.status
  const body = apiErr?.body as { detail?: unknown } | undefined
  const detail = body?.detail
  if (typeof detail === "string") return { detail, status }
  if (Array.isArray(detail) && detail.length > 0) {
    return { detail: (detail[0] as { msg?: string })?.msg, status }
  }
  if (apiErr?.message) return { detail: apiErr.message, status }
  return { status }
}

function isMode(value: string): value is Mode {
  return value === "auto" || value === "rule" || value === "manual_workflow"
}

const PushRuleForm = ({ projectId }: Props) => {
  const queryClient = useQueryClient()
  const ruleQuery = useQuery(pushRuleQueryOptions(projectId))

  const [mode, setMode] = useState<Mode>("manual_workflow")
  const [branchPattern, setBranchPattern] = useState("")
  const [workflowId, setWorkflowId] = useState("")
  const [validationError, setValidationError] = useState<string | null>(null)

  // Anchor local form state to the persisted rule whenever the query refreshes.
  useEffect(() => {
    if (!ruleQuery.data) return
    const persistedMode = isMode(ruleQuery.data.mode)
      ? ruleQuery.data.mode
      : "manual_workflow"
    setMode(persistedMode)
    setBranchPattern(ruleQuery.data.branch_pattern ?? "")
    setWorkflowId(ruleQuery.data.workflow_id ?? "")
    setValidationError(null)
  }, [ruleQuery.data])

  const mutation = useMutation({
    mutationFn: (body: ProjectPushRulePut) =>
      ProjectsService.putProjectPushRule({
        projectId,
        requestBody: body,
      }),
    onSuccess: (data) => {
      toast.success("Push rule saved")
      queryClient.setQueryData(
        ["project", projectId, "push-rule"],
        data as ProjectPushRulePublic,
      )
      // Refresh the projects list — the row's last_push_status surface is
      // independent of mode, but auto↔non-auto transitions can change which
      // projects have a live hook.
      queryClient.invalidateQueries({ queryKey: ["project", projectId] })
    },
    onError: (err) => {
      const { status, detail } = extractDetail(err)
      if (status === 404 && detail === "push_rule_not_found") {
        // Race: project was deleted between read and write. Refresh the
        // rule cache so the form re-anchors (or 404s the row).
        queryClient.invalidateQueries({
          queryKey: ["project", projectId, "push-rule"],
        })
        toast.error("Push rule no longer exists", {
          description: "The project may have been deleted in another tab.",
        })
        return
      }
      toast.error("Failed to save push rule", {
        description: detail ?? "Unknown error",
      })
    },
  })

  const onSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (mutation.isPending) return

    // Mode-specific non-empty-after-trim guard. M005 will tighten — for
    // M004 the backend rejects empty branch_pattern/workflow_id with 422
    // and the FE simply mirrors that bar to keep round-trips cheap.
    if (mode === "rule" && branchPattern.trim() === "") {
      setValidationError("Branch pattern is required for rule mode")
      return
    }
    if (mode === "manual_workflow" && workflowId.trim() === "") {
      setValidationError("Workflow id is required for manual_workflow mode")
      return
    }
    setValidationError(null)

    const body: ProjectPushRulePut = {
      mode,
      branch_pattern: mode === "rule" ? branchPattern.trim() : null,
      workflow_id: mode === "manual_workflow" ? workflowId.trim() : null,
    }
    mutation.mutate(body)
  }

  if (ruleQuery.isLoading) {
    return (
      <Card className="p-3" data-testid={`push-rule-loading-${projectId}`}>
        <Skeleton className="h-24 w-full" />
      </Card>
    )
  }

  if (ruleQuery.error) {
    return (
      <Card className="border-destructive/50 bg-destructive/5 p-3 text-sm">
        <p className="font-medium">Could not load push rule</p>
        <p className="text-muted-foreground text-xs">
          {extractDetail(ruleQuery.error).detail ?? "Unknown error"}
        </p>
      </Card>
    )
  }

  return (
    <Card className="p-4" data-testid={`push-rule-card-${projectId}`}>
      <form className="flex flex-col gap-4" onSubmit={onSubmit}>
        <div className="flex flex-col gap-1">
          <Label className="font-medium">Push rule</Label>
          <p className="text-muted-foreground text-xs">
            Choose how pushes from this project's mirror reach GitHub.
          </p>
        </div>

        <RadioGroup
          value={mode}
          onValueChange={(value: string) => {
            if (isMode(value)) setMode(value)
          }}
          className="flex flex-col gap-3"
        >
          <div className="flex items-start gap-3">
            <RadioGroupItem
              value="auto"
              id={`push-rule-mode-auto-${projectId}`}
              data-testid="push-rule-mode-auto"
            />
            <div className="flex flex-col gap-0.5">
              <Label
                htmlFor={`push-rule-mode-auto-${projectId}`}
                className="font-medium"
              >
                Auto
              </Label>
              <span className="text-muted-foreground text-xs">
                Every push to the mirror is forwarded to GitHub immediately
                (live executor).
              </span>
            </div>
          </div>

          <div className="flex items-start gap-3">
            <RadioGroupItem
              value="rule"
              id={`push-rule-mode-rule-${projectId}`}
              data-testid="push-rule-mode-rule"
            />
            <div className="flex flex-col gap-0.5">
              <div className="flex items-center gap-2">
                <Label
                  htmlFor={`push-rule-mode-rule-${projectId}`}
                  className="font-medium"
                >
                  Rule
                </Label>
                {mode === "rule" && (
                  <Badge variant="outline" data-testid="push-rule-stored-badge">
                    Stored — executor lands in M005
                  </Badge>
                )}
              </div>
              <span className="text-muted-foreground text-xs">
                Forward only pushes whose branch matches a pattern.
              </span>
              {mode === "rule" && (
                <Input
                  type="text"
                  className="mt-2"
                  placeholder="main"
                  value={branchPattern}
                  onChange={(e) => setBranchPattern(e.target.value)}
                  data-testid="push-rule-branch-pattern-input"
                  aria-label="Branch pattern"
                />
              )}
            </div>
          </div>

          <div className="flex items-start gap-3">
            <RadioGroupItem
              value="manual_workflow"
              id={`push-rule-mode-manual-${projectId}`}
              data-testid="push-rule-mode-manual_workflow"
            />
            <div className="flex flex-col gap-0.5">
              <div className="flex items-center gap-2">
                <Label
                  htmlFor={`push-rule-mode-manual-${projectId}`}
                  className="font-medium"
                >
                  Manual workflow
                </Label>
                {mode === "manual_workflow" && (
                  <Badge variant="outline" data-testid="push-rule-stored-badge">
                    Stored — executor lands in M005
                  </Badge>
                )}
              </div>
              <span className="text-muted-foreground text-xs">
                Trigger a GitHub Actions workflow instead of pushing.
              </span>
              {mode === "manual_workflow" && (
                <Input
                  type="text"
                  className="mt-2"
                  placeholder="ci.yml"
                  value={workflowId}
                  onChange={(e) => setWorkflowId(e.target.value)}
                  data-testid="push-rule-workflow-id-input"
                  aria-label="Workflow id"
                />
              )}
            </div>
          </div>
        </RadioGroup>

        {validationError && (
          <p
            className="text-destructive text-sm"
            data-testid="push-rule-validation-error"
            role="alert"
          >
            {validationError}
          </p>
        )}

        <div className="flex justify-end">
          <LoadingButton
            type="submit"
            loading={mutation.isPending}
            data-testid="push-rule-submit"
          >
            Save push rule
          </LoadingButton>
        </div>
      </form>
    </Card>
  )
}

export default PushRuleForm
